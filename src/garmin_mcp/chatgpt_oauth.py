"""Single-owner OAuth 2.1 bridge for ChatGPT's remote MCP client.

Opt-in only: GARMIN_MCP_OAUTH_PUBLIC_URL plus GARMIN_MCP_OAUTH_PASSWORD.
The existing GARMIN_MCP_HTTP_TOKEN remains valid and is used to derive a
separate signing key. This keeps OAuth tokens valid across App Platform
deployments without a database or ephemeral token storage.

This bridge intentionally supports only OpenAI's published ChatGPT CIMD
client with private_key_jwt authentication. It is not a general-purpose
identity provider. Do not use it for multiple Garmin accounts.
"""

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import time
from collections import deque
from urllib.parse import urlencode, urlsplit

import jwt
import requests
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

CLIENT_ID = "https://chatgpt.com/oauth/client.json"
CLIENT_ID_RE = re.compile(
    r"^https://chatgpt\.com/oauth/(?:[A-Za-z0-9_-]{8,64}/)?client\.json$"
)
JWKS_URL = "https://chatgpt.com/oauth/jwks.json"
SCOPE = "garmin:all"
PKCE_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
FORM_LIMIT = 16384
ACCESS_LIFETIME = 3600
REFRESH_LIFETIME = 30 * 86400
CODE_LIFETIME = 300


def _get_json(url: str) -> dict:
    """Fetch *only* the two pinned ChatGPT metadata URLs. Never follow redirects."""
    if url != JWKS_URL and not CLIENT_ID_RE.fullmatch(url):
        raise ValueError("Untrusted client metadata URL")
    response = requests.get(url, timeout=5, allow_redirects=False)
    response.raise_for_status()
    if len(response.content) > 65536:
        raise ValueError("OAuth metadata too large")
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError("Invalid OAuth metadata")
    return result


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": message},
        status_code=status,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


class ChatGPTOAuth:
    """OAuth authorization server for one Garmin account and ChatGPT."""

    def __init__(self, base_url: str, owner_password: str, http_token: str):
        base_url = base_url.rstrip("/")
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("GARMIN_MCP_OAUTH_PUBLIC_URL must be an HTTPS origin (no path)")
        if len(owner_password) < 20:
            raise ValueError("GARMIN_MCP_OAUTH_PASSWORD must contain at least 20 characters")
        if len(http_token) < 32:
            raise ValueError("GARMIN_MCP_HTTP_TOKEN must contain at least 32 characters for OAuth")

        self.issuer = base_url
        self.resource = base_url + "/mcp"
        self.authorize_url = base_url + "/oauth/authorize"
        self.token_url = base_url + "/oauth/token"
        self.metadata_url = base_url + "/.well-known/oauth-protected-resource"
        self._password_digest = hashlib.sha256(owner_password.encode()).digest()
        self._signing_key = hmac.new(
            http_token.encode(), b"garmin-mcp-oauth-jwt-v1", hashlib.sha256
        ).digest()
        self._codes = {}
        self._used_client_assertions = {}
        self._failed_attempts = deque()
        self._metadata_cache = {}
        self._jwks_cache = {}

    @classmethod
    def from_env(cls, http_token: str | None):
        origin = os.getenv("GARMIN_MCP_OAUTH_PUBLIC_URL", "").strip()
        if not origin:
            return None
        if not http_token:
            raise ValueError("GARMIN_MCP_HTTP_TOKEN is required when OAuth is enabled")
        password = os.getenv("GARMIN_MCP_OAUTH_PASSWORD", "")
        return cls(origin, password, http_token)

    def _cached_document(self, url: str, ttl: int) -> dict:
        now = time.time()
        cached = self._jwks_cache if url == JWKS_URL else self._metadata_cache
        entry = cached.get(url)
        if entry and entry[0] > now:
            return entry[1]
        document = _get_json(url)
        cached[url] = (now + ttl, document)
        return document

    def _client_metadata(self, client_id: str) -> dict:
        if not CLIENT_ID_RE.fullmatch(client_id):
            raise ValueError("Unsupported OAuth client")
        data = self._cached_document(client_id, 600)
        if (
            data.get("client_id") != client_id
            or "private_key_jwt" not in data.get("token_endpoint_auth_methods_supported", [])
            or data.get("jwks_uri") != JWKS_URL
        ):
            raise ValueError("Unexpected ChatGPT client metadata")
        return data

    def _validate_authorize(self, parameters) -> dict:
        client_id = parameters.get("client_id", "")
        redirect_uri = parameters.get("redirect_uri", "")
        challenge = parameters.get("code_challenge", "")
        resource = parameters.get("resource", self.resource)
        scope = parameters.get("scope") or SCOPE
        state = parameters.get("state", "")
        if (
            not CLIENT_ID_RE.fullmatch(client_id)
            or parameters.get("response_type") != "code"
            or parameters.get("code_challenge_method") != "S256"
            or not CHALLENGE_RE.fullmatch(challenge)
            or resource != self.resource
            or scope != SCOPE
            or len(state) > 2048
        ):
            raise ValueError("Invalid OAuth authorization request")
        allowed_redirects = self._client_metadata(client_id).get("redirect_uris", [])
        if redirect_uri not in allowed_redirects:
            raise ValueError("Redirect URI is not registered by ChatGPT")
        return {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "challenge": challenge,
            "resource": resource,
            "scope": scope,
            "state": state,
        }

    def _sign(self, payload: dict) -> str:
        return jwt.encode(payload, self._signing_key, algorithm="HS256")

    def _verify(self, token: str, kind: str, audience: str) -> dict:
        data = jwt.decode(
            token,
            self._signing_key,
            algorithms=["HS256"],
            audience=audience,
            issuer=self.issuer,
            options={"require": ["iss", "aud", "iat", "exp", "sub", "jti", "kind"]},
            leeway=10,
        )
        if data.get("kind") != kind or data.get("sub") != "garmin-owner":
            raise ValueError("Invalid token purpose")
        return data

    def validate_access_token(self, token: str) -> bool:
        try:
            data = self._verify(token, "access", self.resource)
            return (
                isinstance(data.get("client_id"), str)
                and bool(CLIENT_ID_RE.fullmatch(data["client_id"]))
                and data.get("scope") == SCOPE
            )
        except (jwt.PyJWTError, ValueError, TypeError):
            return False

    def _issue_tokens(self, client_id: str = CLIENT_ID) -> JSONResponse:
        now = int(time.time())
        common = {
            "iss": self.issuer,
            "sub": "garmin-owner",
            "client_id": client_id,
            "scope": SCOPE,
            "iat": now,
        }
        access = self._sign({
            **common,
            "aud": self.resource,
            "kind": "access",
            "exp": now + ACCESS_LIFETIME,
            "jti": secrets.token_urlsafe(24),
        })
        refresh = self._sign({
            **common,
            "aud": self.token_url,
            "kind": "refresh",
            "exp": now + REFRESH_LIFETIME,
            "jti": secrets.token_urlsafe(24),
        })
        return JSONResponse(
            {
                "access_token": access,
                "token_type": "Bearer",
                "expires_in": ACCESS_LIFETIME,
                "refresh_token": refresh,
                "scope": SCOPE,
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    def _verify_client_assertion(self, form) -> None:
        client_id = form.get("client_id", "")
        if not isinstance(client_id, str) or not CLIENT_ID_RE.fullmatch(client_id):
            raise ValueError("Unsupported OAuth client")
        if (
            form.get("client_assertion_type")
            != "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        ):
            raise ValueError("Client assertion required")
        assertion = form.get("client_assertion", "")
        if not isinstance(assertion, str) or len(assertion) > 16384:
            raise ValueError("Invalid client assertion")
        header = jwt.get_unverified_header(assertion)
        if header.get("alg") != "RS256":
            raise ValueError("Unsupported client assertion algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str) or len(kid) > 256:
            raise ValueError("Missing client assertion key ID")
        self._client_metadata(client_id)
        jwks = self._cached_document(JWKS_URL, 3600).get("keys", [])
        key_data = next(
            (
                key
                for key in jwks
                if key.get("kid") == kid and key.get("kty") == "RSA"
                and key.get("use", "sig") == "sig"
            ),
            None,
        )
        if key_data is None:
            self._jwks_cache.clear()
            jwks = self._cached_document(JWKS_URL, 3600).get("keys", [])
            key_data = next(
                (
                    key for key in jwks
                    if key.get("kid") == kid and key.get("kty") == "RSA"
                    and key.get("use", "sig") == "sig"
                ),
                None,
            )
        if key_data is None:
            raise ValueError("Unrecognized ChatGPT signing key")
        key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
        data = jwt.decode(
            assertion,
            key,
            algorithms=["RS256"],
            issuer=client_id,
            audience=[self.token_url, self.issuer],
            options={"require": ["iss", "sub", "aud", "iat", "exp", "jti"]},
            leeway=30,
        )
        now = int(time.time())
        if (
            data.get("sub") != client_id
            or int(data["exp"]) - int(data["iat"]) > 300
            or int(data["iat"]) > now + 30
        ):
            raise ValueError("Invalid ChatGPT client assertion claims")
        jti = data.get("jti")
        if not isinstance(jti, str) or not jti or len(jti) > 256:
            raise ValueError("Invalid ChatGPT assertion ID")
        self._used_client_assertions = {
            key: until for key, until in self._used_client_assertions.items()
            if until > now
        }
        replay_key = hashlib.sha256((client_id + jti).encode()).hexdigest()
        if replay_key in self._used_client_assertions:
            raise ValueError("Replayed client assertion")
        self._used_client_assertions[replay_key] = int(data["exp"]) + 30

    async def protected_resource_metadata(self, _request):
        return JSONResponse({
            "resource": self.resource,
            "authorization_servers": [self.issuer],
            "scopes_supported": [SCOPE],
            "bearer_methods_supported": ["header"],
        })

    async def authorization_server_metadata(self, _request):
        return JSONResponse({
            "issuer": self.issuer,
            "authorization_endpoint": self.authorize_url,
            "token_endpoint": self.token_url,
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["private_key_jwt"],
            "client_id_metadata_document_supported": True,
            "authorization_response_iss_parameter_supported": True,
            "scopes_supported": [SCOPE],
        })

    def _render_consent(self, request_token: str, error: str = "") -> HTMLResponse:
        warning = (
            '<p role="alert" style="color:#a22">Incorrect password or too many attempts.</p>'
            if error else ""
        )
        page = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Authorize ChatGPT for Garmin MCP</title>'
            '<style>body{font:16px system-ui;margin:3rem auto;max-width:30rem;padding:0 1rem}'
            'input,button{box-sizing:border-box;width:100%;padding:0.75rem;margin-top:0.75rem}'
            'button{background:#1458ab;color:white;border:0;border-radius:6px}</style>'
            '</head><body><h1>Connect Garmin MCP to ChatGPT</h1>'
            '<p>Grant ChatGPT read and write access to your Garmin Connect account. '
            'Only continue if you started this connection in ChatGPT.</p>'
            + warning
            + '<form method="post" action="/oauth/authorize">'
            + '<input type="hidden" name="request_token" value="'
            + html.escape(request_token, quote=True) + '">'
            + '<label for="password">Garmin MCP OAuth password</label>'
            + '<input type="password" id="password" name="password" required autocomplete="off">'
            + '<button type="submit">Authorize ChatGPT</button></form></body></html>'
        )
        return HTMLResponse(
            page,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
            },
        )

    async def authorize(self, request):
        if request.method == "GET":
            try:
                params = self._validate_authorize(request.query_params)
            except (ValueError, requests.RequestException, KeyError, TypeError):
                return _error("invalid_request")
            now = int(time.time())
            request_token = self._sign({
                **params,
                "iss": self.issuer,
                "aud": self.authorize_url,
                "sub": "garmin-owner",
                "kind": "consent",
                "iat": now,
                "exp": now + CODE_LIFETIME,
                "jti": secrets.token_urlsafe(24),
            })
            return self._render_consent(request_token)

        if int(request.headers.get("content-length", "0") or 0) > FORM_LIMIT:
            return _error("invalid_request", 413)
        form = await request.form()
        request_token = form.get("request_token", "")
        try:
            data = self._verify(request_token, "consent", self.authorize_url)
        except (jwt.PyJWTError, ValueError, TypeError):
            return _error("invalid_request")
        if (
            not CLIENT_ID_RE.fullmatch(data.get("client_id", ""))
            or data.get("resource") != self.resource
        ):
            return _error("invalid_request")
        now = time.time()
        while self._failed_attempts and self._failed_attempts[0] < now - 300:
            self._failed_attempts.popleft()
        password = form.get("password", "")
        password_bytes = hashlib.sha256(str(password).encode()).digest()
        if len(self._failed_attempts) >= 10 or not hmac.compare_digest(
            password_bytes, self._password_digest
        ):
            self._failed_attempts.append(now)
            return self._render_consent(request_token, error="invalid_password")
        self._failed_attempts.clear()
        code = secrets.token_urlsafe(36)
        self._codes[hashlib.sha256(code.encode()).hexdigest()] = {
            **{key: data[key] for key in ("client_id", "redirect_uri", "challenge", "resource", "scope")},
            "expires": time.time() + CODE_LIFETIME,
        }
        self._codes = {
            key: item for key, item in self._codes.items() if item["expires"] > time.time()
        }
        args = {"code": code, "iss": self.issuer}
        if data.get("state"):
            args["state"] = data["state"]
        joiner = "&" if "?" in data["redirect_uri"] else "?"
        response = RedirectResponse(
            data["redirect_uri"] + joiner + urlencode(args),
            status_code=303,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    async def token(self, request):
        if int(request.headers.get("content-length", "0") or 0) > FORM_LIMIT:
            return _error("invalid_request", 413)
        form = await request.form()
        try:
            self._verify_client_assertion(form)
        except (jwt.PyJWTError, ValueError, requests.RequestException, KeyError, TypeError):
            return _error("invalid_client", 401)
        if form.get("resource", self.resource) != self.resource:
            return _error("invalid_target")
        grant = form.get("grant_type")
        if grant == "authorization_code":
            code = form.get("code", "")
            key = hashlib.sha256(str(code).encode()).hexdigest()
            item = self._codes.pop(key, None)
            if item is None or item["expires"] < time.time():
                return _error("invalid_grant")
            verifier = form.get("code_verifier", "")
            if not isinstance(verifier, str) or not PKCE_RE.fullmatch(verifier):
                return _error("invalid_grant")
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode()).digest()
            ).rstrip(b"=").decode()
            if (
                not hmac.compare_digest(item["challenge"], challenge)
                or item["redirect_uri"] != form.get("redirect_uri")
                or item["client_id"] != form.get("client_id")
                or item["resource"] != self.resource
            ):
                return _error("invalid_grant")
            return self._issue_tokens(item["client_id"])
        if grant == "refresh_token":
            try:
                data = self._verify(
                    str(form.get("refresh_token", "")), "refresh", self.token_url
                )
                if (
                    data.get("client_id") != form.get("client_id")
                    or data.get("scope") != SCOPE
                ):
                    raise ValueError("Incorrect refresh token")
            except (jwt.PyJWTError, ValueError, TypeError):
                return _error("invalid_grant")
            # Refresh tokens are tied to ChatGPT's verified private_key_jwt
            # identity, so no persistent token store is needed.
            return self._issue_tokens(data["client_id"])
        return _error("unsupported_grant_type")

    def register_routes(self, fastmcp):
        fastmcp.custom_route(
            "/.well-known/oauth-protected-resource", methods=["GET"]
        )(self.protected_resource_metadata)
        fastmcp.custom_route(
            "/.well-known/oauth-protected-resource/mcp", methods=["GET"]
        )(self.protected_resource_metadata)
        fastmcp.custom_route(
            "/.well-known/oauth-authorization-server", methods=["GET"]
        )(self.authorization_server_metadata)
        fastmcp.custom_route(
            "/oauth/authorize", methods=["GET", "POST"]
        )(self.authorize)
        fastmcp.custom_route("/oauth/token", methods=["POST"])(self.token)
