"""Integration tests for the /files/{token} HTTP route registered by file_serving."""

import time

import pytest
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

from garmin_mcp import file_serving


@pytest.fixture(autouse=True)
def _clear_tokens():
    file_serving._TOKENS.clear()
    yield
    file_serving._TOKENS.clear()


@pytest.fixture
def client():
    fastmcp = FastMCP("Test Garmin MCP")
    file_serving.register_routes(fastmcp)
    with TestClient(fastmcp.streamable_http_app()) as test_client:
        yield test_client


def test_valid_token_serves_file_bytes(client, tmp_path):
    f = tmp_path / "123.fit"
    f.write_bytes(b"fit-file-bytes")
    token = file_serving.issue_token(str(f))

    response = client.get(f"/files/{token}")

    assert response.status_code == 200
    assert response.content == b"fit-file-bytes"


def test_unknown_token_returns_404(client):
    response = client.get("/files/does-not-exist")

    assert response.status_code == 404


def test_expired_token_returns_404(client, tmp_path, monkeypatch):
    f = tmp_path / "123.fit"
    f.write_bytes(b"fit-file-bytes")
    monkeypatch.setenv("GARMIN_MCP_FILE_TOKEN_TTL_SECONDS", "10")
    token = file_serving.issue_token(str(f))

    future = time.time() + 11
    monkeypatch.setattr(file_serving.time, "time", lambda: future)

    response = client.get(f"/files/{token}")

    assert response.status_code == 404


def test_traversal_shaped_token_returns_404(client, tmp_path):
    f = tmp_path / "secret.fit"
    f.write_bytes(b"fit-file-bytes")
    file_serving.issue_token(str(f))

    response = client.get("/files/..%2F..%2Fetc%2Fpasswd")

    assert response.status_code == 404
