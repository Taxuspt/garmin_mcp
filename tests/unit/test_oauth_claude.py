"""
Unit tests for the Claude.ai OAuth interoperability shims (oauth_claude).

Covers the four discovery fixes:
  1. /.well-known/oauth-protected-resource metadata document (RFC 9728)
  2. WWW-Authenticate `resource_metadata` on 401s, distinguishing the
     no-token (bare challenge) case from the invalid-token case
  3. token_endpoint_auth_methods_supported advertising "none"
  4. Claude.ai static client pre-registration
"""

import json

import pytest
from starlette.authentication import AuthCredentials

import mcp.server.auth.routes as auth_routes
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from garmin_mcp import oauth_claude

pytestmark = pytest.mark.unit

SERVER_URL = "https://garmin.example.com"
METADATA_URL = "https://garmin.example.com/.well-known/oauth-protected-resource"


# ─── (1) Protected resource metadata ──────────────────────────────────────


def test_resource_metadata_url_strips_trailing_slash():
    assert oauth_claude.resource_metadata_url(SERVER_URL) == METADATA_URL
    assert oauth_claude.resource_metadata_url(SERVER_URL + "/") == METADATA_URL


def test_protected_resource_metadata_minimum_fields():
    data = oauth_claude.protected_resource_metadata(SERVER_URL)
    assert data["resource"] == SERVER_URL
    assert data["authorization_servers"] == [SERVER_URL]


def test_protected_resource_metadata_includes_scopes():
    data = oauth_claude.protected_resource_metadata(SERVER_URL + "/", ["garmin"])
    assert data["resource"] == SERVER_URL  # trailing slash normalized
    assert data["authorization_servers"] == [SERVER_URL]
    assert data["scopes_supported"] == ["garmin"]
    assert data["bearer_methods_supported"] == ["header"]


# ─── (3) token_endpoint_auth_methods_supported += "none" ───────────────────


def _build_metadata():
    # Resolve via the module attribute (the way create_auth_routes calls it) so
    # the patch is exercised, rather than a name bound at import time.
    return auth_routes.build_metadata(
        issuer_url="https://garmin.example.com",
        service_documentation_url=None,
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=["garmin"], default_scopes=["garmin"]
        ),
        revocation_options=RevocationOptions(enabled=True),
    )


def test_patch_adds_none_auth_method_idempotently():
    # Applying the patch twice must not duplicate the entry.
    oauth_claude.patch_token_endpoint_auth_methods()
    oauth_claude.patch_token_endpoint_auth_methods()

    metadata = _build_metadata()
    methods = metadata.token_endpoint_auth_methods_supported
    assert "none" in methods
    assert "client_secret_post" in methods  # original entries preserved
    assert methods.count("none") == 1


# ─── (4) Static client pre-registration ────────────────────────────────────


def test_build_static_clients_claude_ai():
    (client,) = oauth_claude.build_static_clients("garmin")
    assert client.client_id == oauth_claude.CLAUDE_AI_CLIENT_ID == "https://claude.ai"
    assert client.token_endpoint_auth_method == "none"
    assert str(client.redirect_uris[0]) == oauth_claude.CLAUDE_AI_REDIRECT_URI
    assert client.scope == "garmin"
    assert "authorization_code" in client.grant_types
    assert "refresh_token" in client.grant_types


# ─── (2) WWW-Authenticate middleware ───────────────────────────────────────


class _Capture:
    """Minimal ASGI send() collector."""

    def __init__(self):
        self.start = None
        self.body = b""

    async def __call__(self, message):
        if message["type"] == "http.response.start":
            self.start = message
        elif message["type"] == "http.response.body":
            self.body += message.get("body", b"")

    @property
    def status(self):
        return self.start["status"]

    @property
    def www_authenticate(self):
        for name, value in self.start["headers"]:
            if name == b"www-authenticate":
                return value.decode()
        return None

    @property
    def json(self):
        return json.loads(self.body)


def _make_middleware(downstream=None, required_scopes=("garmin",)):
    cls = oauth_claude._make_resource_aware_middleware(METADATA_URL)

    async def _ok(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return cls(downstream or _ok, list(required_scopes))


def _http_scope(headers=None, user=None, auth=None):
    scope = {"type": "http", "headers": headers or []}
    if user is not None:
        scope["user"] = user
    if auth is not None:
        scope["auth"] = auth
    return scope


async def _noop_receive():  # pragma: no cover - never awaited in these paths
    return {"type": "http.request"}


@pytest.mark.asyncio
async def test_no_token_emits_bare_challenge_with_resource_metadata():
    mw = _make_middleware()
    cap = _Capture()
    await mw(_http_scope(), _noop_receive, cap)

    assert cap.status == 401
    # Bare challenge: resource_metadata present, but NO error code (RFC 6750).
    assert cap.www_authenticate == f'Bearer resource_metadata="{METADATA_URL}"'
    assert "invalid_token" not in cap.www_authenticate


@pytest.mark.asyncio
async def test_invalid_token_emits_invalid_token_error():
    mw = _make_middleware()
    cap = _Capture()
    # An Authorization: Bearer header is present but the user is unauthenticated
    # (the verifier rejected the token).
    scope = _http_scope(headers=[(b"authorization", b"Bearer deadbeef")])
    await mw(scope, _noop_receive, cap)

    assert cap.status == 401
    assert 'error="invalid_token"' in cap.www_authenticate
    assert f'resource_metadata="{METADATA_URL}"' in cap.www_authenticate
    assert cap.json["error"] == "invalid_token"


@pytest.mark.asyncio
async def test_authenticated_request_passes_through():
    mw = _make_middleware()
    cap = _Capture()
    token = AccessToken(
        token="t", client_id="https://claude.ai", scopes=["garmin"], expires_at=None
    )
    scope = _http_scope(
        headers=[(b"authorization", b"Bearer t")],
        user=AuthenticatedUser(token),
        auth=AuthCredentials(["garmin"]),
    )
    await mw(scope, _noop_receive, cap)

    assert cap.status == 200
    assert cap.body == b"ok"


@pytest.mark.asyncio
async def test_insufficient_scope_returns_403():
    mw = _make_middleware(required_scopes=("garmin",))
    cap = _Capture()
    token = AccessToken(
        token="t", client_id="https://claude.ai", scopes=["other"], expires_at=None
    )
    scope = _http_scope(
        headers=[(b"authorization", b"Bearer t")],
        user=AuthenticatedUser(token),
        auth=AuthCredentials(["other"]),
    )
    await mw(scope, _noop_receive, cap)

    assert cap.status == 403
    assert 'error="insufficient_scope"' in cap.www_authenticate
    assert f'resource_metadata="{METADATA_URL}"' in cap.www_authenticate
