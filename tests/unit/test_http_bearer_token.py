"""GARMIN_MCP_HTTP_TOKEN gates the HTTP transports with a bearer token (#339)."""

from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

import garmin_mcp
from garmin_mcp import http_auth

TOKEN = "s3cret-token"


@pytest.fixture
def http_env(monkeypatch):
    for name in ("GARMIN_MCP_HOST", "GARMIN_MCP_PORT", "GARMIN_MCP_HTTP_TOKEN",
                 "GARMIN_MCP_STATELESS_HTTP", "GARMIN_ENABLED_TOOLS", "GARMIN_DISABLED_TOOLS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(garmin_mcp, "init_api", lambda _email, _password: Mock())
    return monkeypatch


def _run_main_and_build(monkeypatch, transport):
    """Run main() up to app.run() and return the Starlette app it would serve."""
    built = {}

    def capture_run(self, **_kwargs):
        built["app"] = self.streamable_http_app() if transport == "streamable-http" else self.sse_app()

    monkeypatch.setenv("GARMIN_MCP_TRANSPORT", transport)
    monkeypatch.setattr(FastMCP, "run", capture_run)
    garmin_mcp.main()
    return built["app"]


def test_streamable_http_requires_the_token(http_env):
    http_env.setenv("GARMIN_MCP_HTTP_TOKEN", TOKEN)
    app = _run_main_and_build(http_env, "streamable-http")

    with TestClient(app) as client:
        anonymous = client.post("/mcp", json={})
        wrong_token = client.post("/mcp", json={}, headers={"Authorization": f"Bearer {TOKEN}x"})
        wrong_scheme = client.post("/mcp", json={}, headers={"Authorization": f"Basic {TOKEN}"})
        with_token = client.post("/mcp", json={}, headers={"Authorization": f"Bearer {TOKEN}"})
        probe = client.get("/healthz")
        ready = client.get("/readyz")

    assert anonymous.status_code == 401
    assert anonymous.headers["WWW-Authenticate"].startswith("Bearer")
    assert anonymous.json()["error"] == "unauthorized"
    assert wrong_token.status_code == 401
    assert wrong_scheme.status_code == 401
    # The handshake itself rejects an empty body, but auth let it through.
    assert with_token.status_code != 401
    assert probe.status_code == 200 and probe.text == "ok"
    assert ready.status_code in (200, 503)


def test_sse_requires_the_token(http_env):
    http_env.setenv("GARMIN_MCP_HTTP_TOKEN", TOKEN)
    app = _run_main_and_build(http_env, "sse")

    with TestClient(app) as client:
        anonymous = client.get("/sse")
        messages = client.post("/messages/", json={})
        with_token = client.post("/messages/", json={}, headers={"Authorization": f"Bearer {TOKEN}"})
        probe = client.get("/healthz")

    assert anonymous.status_code == 401
    assert messages.status_code == 401
    assert with_token.status_code != 401
    assert probe.status_code == 200


def test_without_token_the_endpoint_stays_open(http_env):
    app = _run_main_and_build(http_env, "streamable-http")

    with TestClient(app) as client:
        response = client.post("/mcp", json={})

    assert response.status_code != 401


def test_bearer_scheme_is_case_insensitive_and_token_is_not(http_env):
    http_env.setenv("GARMIN_MCP_HTTP_TOKEN", TOKEN)
    app = _run_main_and_build(http_env, "streamable-http")

    with TestClient(app) as client:
        lower_scheme = client.post("/mcp", json={}, headers={"Authorization": f"bearer {TOKEN}"})
        upper_token = client.post("/mcp", json={}, headers={"Authorization": f"Bearer {TOKEN.upper()}"})

    assert lower_scheme.status_code != 401
    assert upper_token.status_code == 401


def test_warns_when_bound_beyond_loopback_without_token(http_env, capsys):
    http_env.setenv("GARMIN_MCP_HOST", "0.0.0.0")
    _run_main_and_build(http_env, "streamable-http")

    err = capsys.readouterr().err
    assert "WARNING: GARMIN_MCP_HOST=0.0.0.0" in err
    assert "GARMIN_MCP_HTTP_TOKEN" in err


def test_no_warning_on_loopback_or_with_token(http_env, capsys):
    _run_main_and_build(http_env, "streamable-http")
    assert "WARNING" not in capsys.readouterr().err

    http_env.setenv("GARMIN_MCP_HOST", "0.0.0.0")
    http_env.setenv("GARMIN_MCP_HTTP_TOKEN", TOKEN)
    _run_main_and_build(http_env, "streamable-http")
    err = capsys.readouterr().err
    assert "WARNING" not in err
    assert "Authorization: Bearer" in err


def test_stdio_ignores_the_token(http_env, capsys):
    http_env.setenv("GARMIN_MCP_HTTP_TOKEN", TOKEN)
    http_env.setenv("GARMIN_MCP_HOST", "0.0.0.0")
    http_env.setenv("GARMIN_MCP_TRANSPORT", "stdio")
    http_env.setattr(FastMCP, "run", lambda _self, **_kwargs: None)

    garmin_mcp.main()

    err = capsys.readouterr().err
    assert "WARNING" not in err
    assert "Authorization" not in err


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("127.1.2.3", True),
        ("localhost", True),
        ("::1", True),
        ("[::1]", True),
        ("0.0.0.0", False),
        ("::", False),
        ("192.168.1.10", False),
        ("garmin.internal", False),
    ],
)
def test_is_loopback(host, expected):
    assert http_auth.is_loopback(host) is expected


def test_resolve_http_token_treats_blank_as_unset(monkeypatch):
    monkeypatch.setenv("GARMIN_MCP_HTTP_TOKEN", "   ")
    assert http_auth.resolve_http_token() is None
    monkeypatch.setenv("GARMIN_MCP_HTTP_TOKEN", " abc ")
    assert http_auth.resolve_http_token() == "abc"
