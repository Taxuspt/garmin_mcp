"""
HTTP/SSE wrapper for the existing Garmin MCP stdio server.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse


AUTH_CODE_TTL_SECONDS = 300
ACCESS_TOKEN_TTL_SECONDS = 3600
SUPPORTED_TOKEN_AUTH_METHODS = {"none", "client_secret_post", "client_secret_basic"}
SUPPORTED_CODE_CHALLENGE_METHODS = {"S256"}
SESSION_HEADER = "MCP-Session-Id"
TOKEN_JSON_SECRET_ENV = "GARMIN_TOKENS_JSON_BASE64"
TOKEN_JSON_RAW_ENV = "GARMIN_TOKENS_JSON"
LEGACY_TOKENSTORE_BASE64_ENV = "GARMINTOKENS_BASE64"


@dataclass
class ClientRegistration:
    client_id: str
    redirect_uris: list[str]
    client_name: str | None
    token_endpoint_auth_method: str
    grant_types: list[str]
    response_types: list[str]
    client_id_issued_at: int
    client_secret: str | None = None
    client_secret_expires_at: int = 0


@dataclass
class AuthorizationCodeRecord:
    code: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    resource: str
    scope: str
    expires_at: int


@dataclass
class AccessTokenRecord:
    token: str
    client_id: str
    resource: str
    scope: str
    expires_at: int


def _now() -> int:
    return int(time.time())


def _normalize_base_url(base_url: str) -> str:
    cleaned = base_url.strip().rstrip("/")
    if not cleaned:
        raise ValueError("BASE_URL must not be empty")
    return cleaned


def _make_url(base_url: str, path: str) -> str:
    return f"{base_url}{path}"


def _json_rpc_id_key(message_id: Any) -> str:
    return json.dumps(message_id, separators=(",", ":"), sort_keys=True)


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _append_query_params(url: str, params: dict[str, str]) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(params)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            parts.fragment,
        )
    )


def _extract_token_json_from_env(resolved_env: dict[str, str]) -> tuple[str | None, str | None]:
    raw_json = resolved_env.get(TOKEN_JSON_RAW_ENV)
    if raw_json:
        return raw_json, TOKEN_JSON_RAW_ENV

    encoded = resolved_env.get(TOKEN_JSON_SECRET_ENV)
    if encoded:
        try:
            return base64.b64decode(encoded).decode("utf-8"), TOKEN_JSON_SECRET_ENV
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError(
                f"{TOKEN_JSON_SECRET_ENV} must contain base64-encoded garmin_tokens.json content"
            ) from exc

    legacy_value = resolved_env.get(LEGACY_TOKENSTORE_BASE64_ENV)
    if not legacy_value:
        return None, None

    legacy_path = Path(os.path.expanduser(legacy_value))
    if legacy_path.exists():
        try:
            return (
                base64.b64decode(legacy_path.read_text(encoding="utf-8")).decode("utf-8"),
                f"{LEGACY_TOKENSTORE_BASE64_ENV} file",
            )
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError(
                f"{LEGACY_TOKENSTORE_BASE64_ENV} file must contain base64-encoded garmin_tokens.json content"
            ) from exc

    try:
        return base64.b64decode(legacy_value).decode("utf-8"), LEGACY_TOKENSTORE_BASE64_ENV
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            f"{LEGACY_TOKENSTORE_BASE64_ENV} must be either an existing file path or base64-encoded garmin_tokens.json content"
        ) from exc


def _ensure_bootstrap_tokens(env: dict[str, str] | None = None) -> tuple[Path | None, str | None]:
    resolved_env = env or os.environ
    token_json, source = _extract_token_json_from_env(resolved_env)
    if not token_json:
        return None, None

    token_dir = Path(os.path.expanduser(resolved_env.get("GARMINTOKENS") or "~/.garminconnect"))
    token_dir.mkdir(parents=True, exist_ok=True)

    try:
        json.loads(token_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{TOKEN_JSON_SECRET_ENV} does not decode to valid JSON"
        ) from exc

    token_json_path = token_dir / "garmin_tokens.json"
    current = token_json_path.read_text(encoding="utf-8") if token_json_path.exists() else None
    if current != token_json:
        token_json_path.write_text(token_json, encoding="utf-8")

    with contextlib.suppress(PermissionError, OSError):
        os.chmod(token_dir, 0o700)
    with contextlib.suppress(PermissionError, OSError):
        os.chmod(token_json_path, 0o600)

    return token_json_path, source


def _oauth_error_response(
    error: str,
    *,
    description: str,
    status_code: int,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status_code,
        headers=headers,
    )


def _redirect_oauth_error(redirect_uri: str, error: str, state: str | None, description: str) -> RedirectResponse:
    params = {"error": error, "error_description": description}
    if state:
        params["state"] = state
    return RedirectResponse(_append_query_params(redirect_uri, params), status_code=302)


def _format_sse(data: dict[str, Any] | str | None, *, event_id: int | None = None) -> str:
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")

    if data is None:
        payload = ""
    elif isinstance(data, str):
        payload = data
    else:
        payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)

    for line in payload.splitlines() or [""]:
        lines.append(f"data: {line}")

    return "\n".join(lines) + "\n\n"


def _pop_newline_delimited_messages(buffer: bytearray) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    while True:
        newline_index = buffer.find(b"\n")
        if newline_index < 0:
            break

        raw_line = bytes(buffer[:newline_index]).rstrip(b"\r")
        del buffer[: newline_index + 1]

        if not raw_line:
            continue

        message = json.loads(raw_line.decode("utf-8"))
        if isinstance(message, dict):
            messages.append(message)

    return messages


def _parse_basic_auth(header_value: str | None) -> tuple[str, str] | None:
    if not header_value:
        return None

    scheme, _, encoded = header_value.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return None

    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None

    username, sep, password = decoded.partition(":")
    if not sep:
        return None
    return username, password


def _require_string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be a non-empty list of strings",
        )
    return list(value)


async def _read_form_body(request: Request) -> dict[str, str]:
    body = await request.body()
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


class StdioMcpSession:
    """One stdio MCP subprocess bound to a single MCP session id."""

    def __init__(
        self,
        session_id: str,
        process: asyncio.subprocess.Process,
    ) -> None:
        self.session_id = session_id
        self.process = process
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._listeners: set[asyncio.Queue[tuple[int, dict[str, Any]] | None]] = set()
        self._write_lock = asyncio.Lock()
        self._event_counter = 0
        self._closed = False
        self._reader_task = asyncio.create_task(self._stdout_reader())
        self._stderr_task = asyncio.create_task(self._stderr_reader())

    @classmethod
    async def create(cls, session_id: str) -> "StdioMcpSession":
        env = os.environ.copy()
        env["GARMIN_MCP_TRANSPORT"] = "stdio"
        env.pop("GARMIN_MCP_HOST", None)
        env.pop("GARMIN_MCP_PORT", None)
        _ensure_bootstrap_tokens(env)

        repo_root = Path(__file__).resolve().parents[2]
        src_root = repo_root / "src"
        python_path = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            f"{src_root}{os.pathsep}{python_path}" if python_path else str(src_root)
        )

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-m",
            "garmin_mcp",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(repo_root),
            env=env,
        )
        return cls(session_id, process)

    def subscribe(self) -> asyncio.Queue[tuple[int, dict[str, Any]] | None]:
        queue: asyncio.Queue[tuple[int, dict[str, Any]] | None] = asyncio.Queue()
        self._listeners.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[tuple[int, dict[str, Any]] | None]) -> None:
        self._listeners.discard(queue)

    async def send_message(self, message: dict[str, Any]) -> None:
        if self._closed or self.process.stdin is None:
            raise RuntimeError("MCP session is closed")

        payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
        async with self._write_lock:
            self.process.stdin.write(payload.encode("utf-8") + b"\n")
            await self.process.stdin.drain()

    async def send_request(self, message: dict[str, Any]) -> dict[str, Any]:
        if "id" not in message:
            raise ValueError("JSON-RPC request must include an id")

        request_id = _json_rpc_id_key(message["id"])
        if request_id in self._pending:
            raise RuntimeError(f"Duplicate in-flight JSON-RPC id: {message['id']!r}")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        try:
            await self.send_message(message)
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def close(self) -> None:
        if self._closed:
            return

        self._closed = True

        if self.process.stdin is not None:
            with contextlib.suppress(Exception):
                self.process.stdin.close()

        if self.process.returncode is None:
            self.process.terminate()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.process.wait(), timeout=5)
            if self.process.returncode is None:
                self.process.kill()
                with contextlib.suppress(Exception):
                    await self.process.wait()

        for task in (self._reader_task, self._stderr_task):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _stdout_reader(self) -> None:
        assert self.process.stdout is not None

        error: Exception | None = None
        buffer = bytearray()
        try:
            while True:
                chunk = await self.process.stdout.read(65536)
                if not chunk:
                    break

                buffer.extend(chunk)
                for message in _pop_newline_delimited_messages(buffer):
                    delivered = False
                    if "id" in message:
                        request_id = _json_rpc_id_key(message["id"])
                        future = self._pending.get(request_id)
                        if future and not future.done():
                            future.set_result(message)
                            delivered = True

                    if not delivered:
                        await self._broadcast(message)

            if buffer.strip():
                raise RuntimeError("MCP subprocess emitted an unterminated stdout frame")
        except Exception as exc:  # pragma: no cover - defensive path
            error = exc
        finally:
            self._closed = True
            process_error = error or RuntimeError(
                f"MCP subprocess exited for session {self.session_id}"
            )
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(process_error)

            for queue in list(self._listeners):
                await queue.put(None)

    async def _stderr_reader(self) -> None:
        assert self.process.stderr is not None

        try:
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    break
                sys.stderr.write(
                    f"[garmin-mcp:{self.session_id}] {line.decode('utf-8', errors='replace')}"
                )
                sys.stderr.flush()
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive logging path
            return

    async def _broadcast(self, message: dict[str, Any]) -> None:
        self._event_counter += 1
        item = (self._event_counter, message)
        for queue in list(self._listeners):
            await queue.put(item)


SessionFactory = Callable[[str], Awaitable[StdioMcpSession]]


class OAuthMcpBridge:
    def __init__(self, base_url: str, session_factory: SessionFactory | None = None) -> None:
        self.base_url = _normalize_base_url(base_url)
        self.session_factory = session_factory or StdioMcpSession.create
        self.clients: dict[str, ClientRegistration] = {}
        self.authorization_codes: dict[str, AuthorizationCodeRecord] = {}
        self.access_tokens: dict[str, AccessTokenRecord] = {}
        self.sessions: dict[str, StdioMcpSession] = {}

    def prune_expired(self) -> None:
        now = _now()
        self.authorization_codes = {
            code: record
            for code, record in self.authorization_codes.items()
            if record.expires_at > now
        }
        self.access_tokens = {
            token: record
            for token, record in self.access_tokens.items()
            if record.expires_at > now
        }

    def validate_resource(self, resource: str | None) -> str:
        if not resource:
            return self.base_url

        normalized = _normalize_base_url(resource)
        if normalized != self.base_url:
            raise HTTPException(status_code=400, detail="Unsupported resource")
        return normalized

    async def create_session(self) -> StdioMcpSession:
        session_id = secrets.token_urlsafe(24)
        session = await self.session_factory(session_id)
        self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str | None) -> StdioMcpSession:
        if not session_id:
            raise HTTPException(status_code=400, detail=f"{SESSION_HEADER} header is required")

        session = self.sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Unknown MCP session")
        return session

    async def drop_session(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session is not None:
            await session.close()

    async def shutdown(self) -> None:
        for session_id in list(self.sessions):
            await self.drop_session(session_id)


def _validate_client_auth(
    client: ClientRegistration,
    form: dict[str, str],
    authorization_header: str | None,
) -> bool:
    method = client.token_endpoint_auth_method
    if method == "none":
        return form.get("client_id") == client.client_id

    if method == "client_secret_post":
        return (
            form.get("client_id") == client.client_id
            and form.get("client_secret") == client.client_secret
        )

    if method == "client_secret_basic":
        creds = _parse_basic_auth(authorization_header)
        return creds == (client.client_id, client.client_secret or "")

    return False


def _extract_bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization")
    if not header:
        raise HTTPException(
            status_code=401,
            detail="Bearer token required",
            headers={"WWW-Authenticate": 'Bearer realm="garmin-mcp"'},
        )

    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
    return token


def _require_access_token(request: Request) -> AccessTokenRecord:
    bridge: OAuthMcpBridge = request.app.state.bridge
    bridge.prune_expired()
    token = _extract_bearer_token(request)
    record = bridge.access_tokens.get(token)
    if record is None or record.resource != bridge.base_url:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired bearer token",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
    return record


async def _read_json_rpc_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON") from exc

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON-RPC batch payloads are not supported")
    return body


def create_app(
    *,
    base_url: str,
    session_factory: SessionFactory | None = None,
) -> FastAPI:
    bridge = OAuthMcpBridge(base_url, session_factory=session_factory)
    app = FastAPI(title="Garmin MCP HTTP Bridge")
    app.state.bridge = bridge

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await bridge.shutdown()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/.well-known/oauth-protected-resource")
    async def oauth_protected_resource() -> dict[str, Any]:
        return {
            "resource": bridge.base_url,
            "authorization_servers": [bridge.base_url],
        }

    @app.get("/.well-known/oauth-protected-resource/sse")
    async def oauth_protected_resource_sse() -> dict[str, Any]:
        return {
            "resource": bridge.base_url,
            "authorization_servers": [bridge.base_url],
        }

    @app.get("/.well-known/oauth-authorization-server")
    async def oauth_authorization_server() -> dict[str, Any]:
        return {
            "issuer": bridge.base_url,
            "authorization_endpoint": _make_url(bridge.base_url, "/oauth/authorize"),
            "token_endpoint": _make_url(bridge.base_url, "/oauth/token"),
            "registration_endpoint": _make_url(bridge.base_url, "/oauth/register"),
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "token_endpoint_auth_methods_supported": sorted(SUPPORTED_TOKEN_AUTH_METHODS),
            "code_challenge_methods_supported": sorted(SUPPORTED_CODE_CHALLENGE_METHODS),
            "scopes_supported": ["mcp"],
            "bearer_methods_supported": ["header"],
            "resource_parameter_supported": True,
        }

    @app.post("/oauth/register")
    async def oauth_register(request: Request) -> JSONResponse:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Registration payload must be a JSON object")

        redirect_uris = _require_string_list(payload.get("redirect_uris"), "redirect_uris")

        token_auth_method = payload.get("token_endpoint_auth_method", "none")
        if token_auth_method not in SUPPORTED_TOKEN_AUTH_METHODS:
            raise HTTPException(status_code=400, detail="Unsupported token_endpoint_auth_method")

        grant_types = _require_string_list(
            payload.get("grant_types", ["authorization_code"]),
            "grant_types",
        )
        response_types = _require_string_list(
            payload.get("response_types", ["code"]),
            "response_types",
        )
        if "authorization_code" not in grant_types or "code" not in response_types:
            raise HTTPException(status_code=400, detail="Only authorization_code / code clients are supported")

        issued_at = _now()
        client_id = secrets.token_urlsafe(24)
        client_secret = (
            secrets.token_urlsafe(32) if token_auth_method != "none" else None
        )

        client = ClientRegistration(
            client_id=client_id,
            redirect_uris=redirect_uris,
            client_name=payload.get("client_name"),
            token_endpoint_auth_method=token_auth_method,
            grant_types=list(grant_types),
            response_types=list(response_types),
            client_id_issued_at=issued_at,
            client_secret=client_secret,
            client_secret_expires_at=0 if client_secret else 0,
        )
        bridge.clients[client_id] = client

        response: dict[str, Any] = {
            "client_id": client.client_id,
            "client_id_issued_at": client.client_id_issued_at,
            "redirect_uris": client.redirect_uris,
            "grant_types": client.grant_types,
            "response_types": client.response_types,
            "token_endpoint_auth_method": client.token_endpoint_auth_method,
        }
        if client.client_name:
            response["client_name"] = client.client_name
        if client.client_secret:
            response["client_secret"] = client.client_secret
            response["client_secret_expires_at"] = client.client_secret_expires_at

        return JSONResponse(response, status_code=201)

    @app.get("/oauth/authorize")
    async def oauth_authorize(request: Request) -> Response:
        bridge.prune_expired()

        client_id = request.query_params.get("client_id")
        redirect_uri = request.query_params.get("redirect_uri")
        response_type = request.query_params.get("response_type")
        state = request.query_params.get("state")
        code_challenge = request.query_params.get("code_challenge")
        code_challenge_method = request.query_params.get("code_challenge_method")
        scope = request.query_params.get("scope", "mcp")

        if not client_id:
            raise HTTPException(status_code=400, detail="client_id is required")
        client = bridge.clients.get(client_id)
        if client is None:
            raise HTTPException(status_code=400, detail="Unknown client_id")

        if not redirect_uri or redirect_uri not in client.redirect_uris:
            raise HTTPException(status_code=400, detail="redirect_uri is not registered")

        if response_type != "code":
            return _redirect_oauth_error(
                redirect_uri,
                "unsupported_response_type",
                state,
                "Only response_type=code is supported",
            )

        if not code_challenge or code_challenge_method not in SUPPORTED_CODE_CHALLENGE_METHODS:
            return _redirect_oauth_error(
                redirect_uri,
                "invalid_request",
                state,
                "PKCE with code_challenge_method=S256 is required",
            )

        try:
            resource = bridge.validate_resource(request.query_params.get("resource"))
        except HTTPException as exc:
            return _redirect_oauth_error(
                redirect_uri,
                "invalid_target",
                state,
                str(exc.detail),
            )
        code = secrets.token_urlsafe(32)
        bridge.authorization_codes[code] = AuthorizationCodeRecord(
            code=code,
            client_id=client.client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            resource=resource,
            scope=scope,
            expires_at=_now() + AUTH_CODE_TTL_SECONDS,
        )

        return RedirectResponse(
            _append_query_params(
                redirect_uri,
                {"code": code, **({"state": state} if state else {})},
            ),
            status_code=302,
        )

    @app.post("/oauth/token")
    async def oauth_token(request: Request) -> JSONResponse:
        bridge.prune_expired()
        form = await _read_form_body(request)

        if form.get("grant_type") != "authorization_code":
            return _oauth_error_response(
                "unsupported_grant_type",
                description="Only the authorization_code grant is supported",
                status_code=400,
            )

        code = form.get("code")
        client_id = form.get("client_id")
        redirect_uri = form.get("redirect_uri")
        code_verifier = form.get("code_verifier")

        if not code or not client_id or not redirect_uri or not code_verifier:
            return _oauth_error_response(
                "invalid_request",
                description="code, client_id, redirect_uri and code_verifier are required",
                status_code=400,
            )

        client = bridge.clients.get(client_id)
        if client is None:
            return _oauth_error_response(
                "invalid_client",
                description="Unknown client_id",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="garmin-mcp"'},
            )

        if not _validate_client_auth(client, form, request.headers.get("Authorization")):
            return _oauth_error_response(
                "invalid_client",
                description="Client authentication failed",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="garmin-mcp"'},
            )

        record = bridge.authorization_codes.get(code)
        if record is None:
            return _oauth_error_response(
                "invalid_grant",
                description="Authorization code is invalid or expired",
                status_code=400,
            )

        if (
            record.client_id != client.client_id
            or record.redirect_uri != redirect_uri
            or record.code_challenge_method != "S256"
        ):
            return _oauth_error_response(
                "invalid_grant",
                description="Authorization code validation failed",
                status_code=400,
            )

        try:
            requested_resource = bridge.validate_resource(form.get("resource"))
        except HTTPException as exc:
            return _oauth_error_response(
                "invalid_target",
                description=str(exc.detail),
                status_code=400,
            )
        if requested_resource != record.resource:
            return _oauth_error_response(
                "invalid_target",
                description="Token resource does not match authorization request",
                status_code=400,
            )

        if _pkce_challenge(code_verifier) != record.code_challenge:
            return _oauth_error_response(
                "invalid_grant",
                description="PKCE verification failed",
                status_code=400,
            )

        access_token = secrets.token_urlsafe(32)
        bridge.access_tokens[access_token] = AccessTokenRecord(
            token=access_token,
            client_id=client.client_id,
            resource=record.resource,
            scope=record.scope,
            expires_at=_now() + ACCESS_TOKEN_TTL_SECONDS,
        )
        bridge.authorization_codes.pop(code, None)

        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": ACCESS_TOKEN_TTL_SECONDS,
                "scope": record.scope,
                "resource": record.resource,
            }
        )

    @app.post("/sse")
    async def sse_post(request: Request) -> Response:
        _require_access_token(request)
        message = await _read_json_rpc_body(request)
        session_id = request.headers.get(SESSION_HEADER)

        is_request = "method" in message and "id" in message
        is_initialize = (
            is_request
            and message.get("method") == "initialize"
            and not session_id
        )

        if is_initialize:
            session = await bridge.create_session()
        else:
            session = bridge.get_session(session_id)

        try:
            if is_request:
                response = await session.send_request(message)
                return JSONResponse(
                    response,
                    headers={SESSION_HEADER: session.session_id},
                )

            await session.send_message(message)
            return Response(status_code=202, headers={SESSION_HEADER: session.session_id})
        except Exception:
            if is_initialize:
                await bridge.drop_session(session.session_id)
            raise

    @app.get("/sse")
    async def sse_get(request: Request) -> StreamingResponse:
        _require_access_token(request)
        session = bridge.get_session(request.headers.get(SESSION_HEADER))
        queue = session.subscribe()

        async def event_stream() -> AsyncIterator[str]:
            try:
                # Prime the client for reconnects with an initial event id.
                yield _format_sse("", event_id=0)
                while True:
                    if await request.is_disconnected():
                        break

                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue

                    if item is None:
                        break

                    event_id, message = item
                    yield _format_sse(message, event_id=event_id)
            finally:
                session.unsubscribe(queue)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                SESSION_HEADER: session.session_id,
            },
        )

    return app


def main() -> None:
    port = int(os.getenv("PORT", "3000"))
    base_url = os.getenv("BASE_URL", f"http://127.0.0.1:{port}")
    bootstrapped_token_path, token_source = _ensure_bootstrap_tokens()
    if bootstrapped_token_path is not None:
        print(
            f"Bootstrapped Garmin token store from {token_source} into {bootstrapped_token_path}",
            file=sys.stderr,
        )
    elif os.getenv("GARMIN_EMAIL") or os.getenv("GARMIN_PASSWORD"):
        print(
            "No pre-generated Garmin token secret detected; runtime credential login remains enabled and may hit Garmin 429 limits.",
            file=sys.stderr,
        )
    app = create_app(base_url=base_url)
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
