"""Tests for opt-in, single-owner ChatGPT OAuth.

No network access is used; ChatGPT metadata and JWKS are mocked.
"""

import base64
import hashlib
import time
from unittest.mock import Mock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

import garmin_mcp
from garmin_mcp import chatgpt_oauth

TOKEN = "0123456789abcdef" * 4
OWNER_PASSWORD = "correct horse battery staple 123"
ORIGIN = "https://garmin.example.com"
REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
VERIFIER = "v" * 43


@pytest.fixture
def oauth(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update({"kid": "test-key", "use": "sig"})

    def metadata(url):
        if url == chatgpt_oauth.CLIENT_ID:
            return {
                "client_id": chatgpt_oauth.CLIENT_ID,
                "redirect_uris": [REDIRECT],
                "jwks_uri": chatgpt_oauth.JWKS_URL,
                "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"],
            }
        if url == chatgpt_oauth.JWKS_URL:
            return {"keys": [jwk]}
        raise AssertionError("unexpected metadata URL")

    monkeypatch.setattr(chatgpt_oauth, "_get_json", metadata)
    instance = chatgpt_oauth.ChatGPTOAuth(ORIGIN, OWNER_PASSWORD, TOKEN)
    return instance, key


@pytest.fixture
def oauth_app(oauth):
    instance, _key = oauth
    fastmcp = FastMCP("Garmin test", stateless_http=True)
    instance.register_routes(fastmcp)
    from garmin_mcp.http_auth import install_http_bearer_auth

    install_http_bearer_auth(fastmcp, TOKEN, instance)
    return fastmcp.streamable_http_app()


def _assertion(private_key, audience=ORIGIN + "/oauth/token"):
    now = int(time.time())
    return jwt.encode(
        {
            "iss": chatgpt_oauth.CLIENT_ID,
            "sub": chatgpt_oauth.CLIENT_ID,
            "aud": audience,
            "iat": now,
            "exp": now + 180,
            "jti": "assertion-" + str(time.time_ns()),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


def _authorization_params():
    return {
        "response_type": "code",
        "client_id": chatgpt_oauth.CLIENT_ID,
        "redirect_uri": REDIRECT,
        "code_challenge_method": "S256",
        "code_challenge": base64.urlsafe_b64encode(
            hashlib.sha256(VERIFIER.encode()).digest()
        ).rstrip(b"=").decode(),
        "resource": ORIGIN + "/mcp",
        "scope": "garmin:all",
        "state": "state123",
    }


def test_oauth_not_enabled_by_default(monkeypatch):
    monkeypatch.delenv("GARMIN_MCP_OAUTH_PUBLIC_URL", raising=False)
    assert chatgpt_oauth.ChatGPTOAuth.from_env(TOKEN) is None


def test_oauth_refuses_missing_or_short_secrets(monkeypatch):
    monkeypatch.setenv("GARMIN_MCP_OAUTH_PUBLIC_URL", ORIGIN)
    monkeypatch.delenv("GARMIN_MCP_OAUTH_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="GARMIN_MCP_OAUTH_PASSWORD"):
        chatgpt_oauth.ChatGPTOAuth.from_env(TOKEN)
    monkeypatch.setenv("GARMIN_MCP_OAUTH_PASSWORD", OWNER_PASSWORD)
    with pytest.raises(ValueError, match="GARMIN_MCP_HTTP_TOKEN"):
        chatgpt_oauth.ChatGPTOAuth.from_env("short")
    with pytest.raises(ValueError, match="HTTPS"):
        chatgpt_oauth.ChatGPTOAuth("http://garmin.example.com", OWNER_PASSWORD, TOKEN)


def test_oauth_discovery_and_gate(oauth_app):
    with TestClient(oauth_app) as client:
        protected = client.get("/.well-known/oauth-protected-resource")
        issuer = client.get("/.well-known/oauth-authorization-server")
        unauthenticated = client.post("/mcp", json={})
        legacy = client.post("/mcp", json={}, headers={"Authorization": f"Bearer {TOKEN}"})

    assert protected.status_code == 200
    assert protected.json()["resource"] == ORIGIN + "/mcp"
    assert issuer.status_code == 200
    assert "S256" in issuer.json()["code_challenge_methods_supported"]
    assert issuer.json()["client_id_metadata_document_supported"]
    assert unauthenticated.status_code == 401
    assert "resource_metadata=" in unauthenticated.headers["WWW-Authenticate"]
    assert legacy.status_code != 401


def test_authorization_code_exchange_and_refresh(oauth, oauth_app):
    instance, key = oauth
    with TestClient(oauth_app) as client:
        consent = client.get("/oauth/authorize", params=_authorization_params())
        assert consent.status_code == 200
        import re
        token = re.search(r'name="request_token" value="([^"]+)"', consent.text).group(1)
        denied = client.post(
            "/oauth/authorize",
            data={"request_token": token, "password": "not-the-password"},
        )
        assert denied.status_code == 200
        granted = client.post(
            "/oauth/authorize",
            data={"request_token": token, "password": OWNER_PASSWORD},
            follow_redirects=False,
        )
        assert granted.status_code == 303
        from urllib.parse import parse_qs, urlsplit
        query = parse_qs(urlsplit(granted.headers["location"]).query)
        assert query["iss"] == [ORIGIN]
        assert query["state"] == ["state123"]
        code = query["code"][0]
        form = {
            "grant_type": "authorization_code",
            "client_id": chatgpt_oauth.CLIENT_ID,
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": _assertion(key),
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": VERIFIER,
            "resource": ORIGIN + "/mcp",
        }
        response = client.post("/oauth/token", data=form)
        assert response.status_code == 200
        tokens = response.json()
        assert instance.validate_access_token(tokens["access_token"])
        authenticated = client.post(
            "/mcp", json={}, headers={"Authorization": "Bearer " + tokens["access_token"]}
        )
        assert authenticated.status_code != 401
        duplicate = client.post("/oauth/token", data={**form, "client_assertion": _assertion(key)})
        assert duplicate.status_code == 400
        refresh = client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": chatgpt_oauth.CLIENT_ID,
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": _assertion(key),
                "refresh_token": tokens["refresh_token"],
            },
        )
        assert refresh.status_code == 200
        assert instance.validate_access_token(refresh.json()["access_token"])


def test_bad_redirect_and_pkce_rejected(oauth_app):
    with TestClient(oauth_app) as client:
        params = _authorization_params()
        params["redirect_uri"] = "https://evil.example.com/steal"
        bad = client.get("/oauth/authorize", params=params)
        assert bad.status_code == 400
        params = _authorization_params()
        params["code_challenge_method"] = "plain"
        bad_pkce = client.get("/oauth/authorize", params=params)
        assert bad_pkce.status_code == 400


def test_access_signature_and_audience_checks(oauth):
    instance, _key = oauth
    response = instance._issue_tokens().body
    import json

    token = json.loads(response)["access_token"]
    assert instance.validate_access_token(token)
    assert not instance.validate_access_token(token + "altered")
    payload = jwt.decode(
        token, options={"verify_signature": False}
    )
    payload["aud"] = "https://another.example.com/mcp"
    wrong_audience = instance._sign(payload)
    assert not instance.validate_access_token(wrong_audience)
    instance_after_restart = chatgpt_oauth.ChatGPTOAuth(ORIGIN, OWNER_PASSWORD, TOKEN)
    assert instance_after_restart.validate_access_token(token)

def test_per_connector_client_ids_are_supported(monkeypatch):
    client_id = "https://chatgpt.com/oauth/BpZIpxL-aggt/client.json"
    redirect = "https://chatgpt.com/connector/oauth/BpZIpxL-aggt"

    def metadata(url):
        if url == client_id:
            return {
                "client_id": client_id,
                "redirect_uris": [redirect],
                "jwks_uri": chatgpt_oauth.JWKS_URL,
                "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"],
            }
        raise AssertionError("Unexpected metadata request")

    monkeypatch.setattr(chatgpt_oauth, "_get_json", metadata)
    oauth = chatgpt_oauth.ChatGPTOAuth(ORIGIN, OWNER_PASSWORD, TOKEN)
    params = _authorization_params()
    params["client_id"] = client_id
    params["redirect_uri"] = redirect

    assert oauth._validate_authorize(params)["client_id"] == client_id
    assert oauth.validate_access_token(
        __import__("json").loads(oauth._issue_tokens(client_id).body)["access_token"]
    )
    params["client_id"] = "https://evil.example.com/oauth/client.json"
    with pytest.raises(ValueError):
        oauth._validate_authorize(params)
