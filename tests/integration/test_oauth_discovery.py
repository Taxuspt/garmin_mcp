"""
Integration tests for Claude.ai OAuth discovery wiring on the remote server.

Builds a FastMCP app the same way ``garmin_mcp.remote.main`` does (auth enabled,
patches applied, static client seeded, protected-resource route registered) and
drives it through an in-process ASGI transport to verify the discovery contract
Claude.ai depends on.
"""

import httpx
import pytest
from pydantic import AnyHttpUrl

from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)

from garmin_mcp import oauth_claude
from garmin_mcp.oauth_provider import GarminOAuthProvider

pytestmark = pytest.mark.integration

SERVER_URL = "https://garmin.example.com"
SCOPE = "garmin"
MCP_PATH = "/mcp"


@pytest.fixture
def remote_app(tmp_path):
    """A FastMCP app configured like remote.main(), minus the tool modules."""
    provider = GarminOAuthProvider(
        db_path=str(tmp_path / "oauth.db"),
        server_url=SERVER_URL,
        session_manager=None,
    )
    provider.seed_clients(oauth_claude.build_static_clients(SCOPE))

    oauth_claude.patch_token_endpoint_auth_methods()
    oauth_claude.patch_require_auth_middleware(
        oauth_claude.resource_metadata_url(SERVER_URL)
    )

    app = FastMCP(
        name="Garmin Connect Test",
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(SERVER_URL),
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
            ),
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=[SCOPE],
            resource_server_url=None,
        ),
        streamable_http_path=MCP_PATH,
    )

    @app.custom_route(
        oauth_claude.WELL_KNOWN_PROTECTED_RESOURCE, methods=["GET", "OPTIONS"]
    )
    async def protected_resource_metadata(request):
        return oauth_claude.protected_resource_response(request, SERVER_URL, [SCOPE])

    return app, provider


@pytest.fixture
def client(remote_app):
    app, _ = remote_app
    transport = httpx.ASGITransport(app=app.streamable_http_app())
    return httpx.AsyncClient(transport=transport, base_url=SERVER_URL)


# ─── (1) Protected resource metadata endpoint ──────────────────────────────


@pytest.mark.asyncio
async def test_protected_resource_metadata_endpoint(client):
    async with client:
        resp = await client.get(oauth_claude.WELL_KNOWN_PROTECTED_RESOURCE)
    assert resp.status_code == 200
    data = resp.json()
    assert data["resource"] == SERVER_URL
    assert data["authorization_servers"] == [SERVER_URL]
    # CORS so browser-side discovery works.
    assert resp.headers["access-control-allow-origin"] == "*"


# ─── (3) Discovery document advertises "none" ──────────────────────────────


@pytest.mark.asyncio
async def test_authorization_server_metadata_advertises_none(client):
    async with client:
        resp = await client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200
    methods = resp.json()["token_endpoint_auth_methods_supported"]
    assert "none" in methods
    assert "client_secret_post" in methods


# ─── (2) WWW-Authenticate on the MCP endpoint ──────────────────────────────


@pytest.mark.asyncio
async def test_unauthenticated_mcp_request_points_to_resource_metadata(client):
    async with client:
        resp = await client.post(MCP_PATH, json={"jsonrpc": "2.0", "method": "ping"})
    assert resp.status_code == 401
    www_auth = resp.headers["www-authenticate"]
    assert oauth_claude.resource_metadata_url(SERVER_URL) in www_auth
    # No credentials presented -> must NOT claim an invalid token.
    assert "invalid_token" not in www_auth


@pytest.mark.asyncio
async def test_invalid_token_mcp_request_reports_invalid_token(client):
    async with client:
        resp = await client.post(
            MCP_PATH,
            json={"jsonrpc": "2.0", "method": "ping"},
            headers={"Authorization": "Bearer not-a-real-token"},
        )
    assert resp.status_code == 401
    www_auth = resp.headers["www-authenticate"]
    assert 'error="invalid_token"' in www_auth
    assert oauth_claude.resource_metadata_url(SERVER_URL) in www_auth


# ─── (4) Static Claude.ai client is registered ─────────────────────────────


@pytest.mark.asyncio
async def test_claude_ai_client_is_preregistered(remote_app):
    _, provider = remote_app
    client = await provider.get_client(oauth_claude.CLAUDE_AI_CLIENT_ID)
    assert client is not None
    assert client.token_endpoint_auth_method == "none"
    assert str(client.redirect_uris[0]) == oauth_claude.CLAUDE_AI_REDIRECT_URI
