import base64
import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from garmin_mcp.http_server import (
    AccessTokenRecord,
    _pkce_challenge,
    _extract_token_json_from_env,
    _ensure_bootstrap_tokens,
    _pop_newline_delimited_messages,
    create_app,
)


class FakeSession:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.request_messages: list[dict] = []
        self.sent_messages: list[dict] = []

    def subscribe(self):
        raise NotImplementedError

    def unsubscribe(self, _queue):
        return None

    async def send_request(self, message: dict) -> dict:
        self.request_messages.append(message)
        return {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "serverInfo": {"name": "fake-garmin", "version": "1.0.0"},
            },
        }

    async def send_message(self, message: dict) -> None:
        self.sent_messages.append(message)

    async def close(self) -> None:
        return None


def _make_client():
    created_sessions: dict[str, FakeSession] = {}

    async def factory(session_id: str) -> FakeSession:
        session = FakeSession(session_id)
        created_sessions[session_id] = session
        return session

    app = create_app(
        base_url="https://garmin-mcp.example.com",
        session_factory=factory,
    )
    app.state.bridge.access_tokens["valid-token"] = AccessTokenRecord(
        token="valid-token",
        client_id="test-client",
        resource="https://garmin-mcp.example.com",
        scope="mcp",
        expires_at=int(time.time()) + 3600,
    )
    return TestClient(app), created_sessions


def test_oauth_metadata_endpoints():
    client, _ = _make_client()
    with client:
        protected = client.get("/.well-known/oauth-protected-resource")
        assert protected.status_code == 200
        assert protected.json() == {
            "resource": "https://garmin-mcp.example.com",
            "authorization_servers": ["https://garmin-mcp.example.com"],
        }

        protected_sse = client.get("/.well-known/oauth-protected-resource/sse")
        assert protected_sse.status_code == 200
        assert protected_sse.json() == protected.json()

        metadata = client.get("/.well-known/oauth-authorization-server")
        assert metadata.status_code == 200
        body = metadata.json()
        assert body["issuer"] == "https://garmin-mcp.example.com"
        assert body["authorization_endpoint"].endswith("/oauth/authorize")
        assert body["token_endpoint"].endswith("/oauth/token")
        assert body["registration_endpoint"].endswith("/oauth/register")
        assert "S256" in body["code_challenge_methods_supported"]


def test_register_authorize_and_exchange_token():
    client, _ = _make_client()
    redirect_uri = "https://claude.ai/api/mcp/auth_callback"
    verifier = "pkce-verifier-123456789"
    challenge = _pkce_challenge(verifier)

    with client:
        registration = client.post(
            "/oauth/register",
            json={
                "client_name": "Claude",
                "redirect_uris": [redirect_uri],
            },
        )
        assert registration.status_code == 201
        client_id = registration.json()["client_id"]

        authorize = client.get(
            "/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": "state-123",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": "https://garmin-mcp.example.com",
            },
            follow_redirects=False,
        )
        assert authorize.status_code == 302

        location = authorize.headers["location"]
        query = parse_qs(urlsplit(location).query)
        assert query["state"] == ["state-123"]
        code = query["code"][0]

        token = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
                "resource": "https://garmin-mcp.example.com",
            },
        )
        assert token.status_code == 200
        body = token.json()
        assert body["token_type"] == "Bearer"
        assert body["resource"] == "https://garmin-mcp.example.com"
        assert body["access_token"]


def test_sse_requires_bearer_token():
    client, _ = _make_client()
    with client:
        response = client.post(
            "/sse",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert response.status_code == 401


def test_initialize_creates_stdio_session():
    client, created_sessions = _make_client()
    headers = {"Authorization": "Bearer valid-token"}
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
        },
    }

    with client:
        response = client.post("/sse", json=initialize, headers=headers)
        assert response.status_code == 200
        session_id = response.headers["MCP-Session-Id"]
        assert session_id in created_sessions
        assert created_sessions[session_id].request_messages == [initialize]

        follow_up = client.post(
            "/sse",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={**headers, "MCP-Session-Id": session_id},
        )
        assert follow_up.status_code == 202
        assert created_sessions[session_id].sent_messages == [
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        ]


def test_pop_newline_delimited_messages_handles_large_frames():
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"tools": ["x" * 70000]}}
    encoded = (
        b'{"jsonrpc":"2.0","method":"ping"}\n'
        + json.dumps(payload, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )
    buffer = bytearray(encoded)

    messages = _pop_newline_delimited_messages(buffer)

    assert buffer == bytearray()
    assert messages == [{"jsonrpc": "2.0", "method": "ping"}, payload]


def test_ensure_bootstrap_tokens_writes_token_store(tmp_path):
    token_json = json.dumps({"oauth1": {"token": "a"}, "oauth2": {"access_token": "b"}})
    env = {
        "GARMINTOKENS": str(tmp_path / "tokens"),
        "GARMIN_TOKENS_JSON_BASE64": base64.b64encode(
            token_json.encode("utf-8")
        ).decode("ascii"),
    }

    written, source = _ensure_bootstrap_tokens(env)

    assert written == Path(env["GARMINTOKENS"]) / "garmin_tokens.json"
    assert written.read_text(encoding="utf-8") == token_json
    assert source == "GARMIN_TOKENS_JSON_BASE64"


def test_extract_token_json_from_legacy_env_value():
    token_json = json.dumps({"oauth1": {"token": "a"}, "oauth2": {"access_token": "b"}})
    env = {
        "GARMINTOKENS_BASE64": base64.b64encode(token_json.encode("utf-8")).decode("ascii")
    }

    decoded, source = _extract_token_json_from_env(env)

    assert decoded == token_json
    assert source == "GARMINTOKENS_BASE64"
