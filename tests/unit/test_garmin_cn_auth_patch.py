"""Tests for Garmin Connect China authentication compatibility."""

import importlib
import importlib.metadata

import pytest


def _reload_client_module():
    from garminconnect import client as client_mod

    return importlib.reload(client_mod)


def test_cn_login_prefers_portal_flow_before_mobile_flow(monkeypatch):
    _reload_client_module()
    from garmin_mcp.garmin_cn_auth_patch import apply_garmin_cn_auth_patch
    from garminconnect.client import Client

    apply_garmin_cn_auth_patch()

    calls = []
    client = Client(domain="garmin.cn")

    def portal_success(email, password):
        calls.append("portal")

    def mobile_success(email, password):
        calls.append("mobile")

    monkeypatch.setattr(client, "_portal_web_login_cffi", portal_success)
    monkeypatch.setattr(client, "_mobile_login_cffi", mobile_success)

    client.login("user@example.com", "secret")

    assert calls == ["portal"]


def test_cn_login_tries_widget_before_mobile_when_portal_fails(monkeypatch):
    _reload_client_module()
    from garmin_mcp.garmin_cn_auth_patch import apply_garmin_cn_auth_patch
    from garminconnect.client import Client

    apply_garmin_cn_auth_patch()

    calls = []
    client = Client(domain="garmin.cn")

    def fail(name):
        def inner(email, password):
            calls.append(name)
            raise RuntimeError(f"{name} failed")

        return inner

    def widget_success(email, password):
        calls.append("widget")

    monkeypatch.setattr(client, "_portal_web_login_cffi", fail("portal+cffi"))
    monkeypatch.setattr(client, "_portal_web_login_requests", fail("portal+requests"))
    monkeypatch.setattr(client, "_widget_web_login", widget_success)
    monkeypatch.setattr(client, "_mobile_login_cffi", fail("mobile+cffi"))
    monkeypatch.setattr(client, "_mobile_login_requests", fail("mobile+requests"))

    client.login("user@example.com", "secret")

    assert calls == ["portal+cffi", "portal+requests", "widget"]


def test_cn_token_exchange_uses_cn_diauth_endpoint(monkeypatch):
    _reload_client_module()
    from garmin_mcp.garmin_cn_auth_patch import apply_garmin_cn_auth_patch
    from garminconnect import GarminConnectAuthenticationError
    from garminconnect import client as client_mod
    from garminconnect.client import Client

    apply_garmin_cn_auth_patch()

    urls = []
    client = Client(domain="garmin.cn")
    monkeypatch.setattr(client_mod, "HAS_CFFI", False)

    class Response:
        status_code = 400
        ok = False
        text = "bad request"

    def fake_post(url, **kwargs):
        assert (
            client_mod.DI_TOKEN_URL
            == "https://diauth.garmin.com/di-oauth2-service/oauth/token"
        )
        urls.append(url)
        return Response()

    monkeypatch.setattr(client_mod.requests, "post", fake_post)

    with pytest.raises(GarminConnectAuthenticationError):
        client._exchange_service_ticket(
            "ticket", service_url=client._portal_service_url
        )

    assert urls
    assert set(urls) == {"https://diauth.garmin.cn/di-oauth2-service/oauth/token"}


def test_non_cn_token_exchange_keeps_default_diauth_endpoint(monkeypatch):
    _reload_client_module()
    from garmin_mcp.garmin_cn_auth_patch import apply_garmin_cn_auth_patch
    from garminconnect import GarminConnectAuthenticationError
    from garminconnect import client as client_mod
    from garminconnect.client import Client

    apply_garmin_cn_auth_patch()

    urls = []
    client = Client(domain="garmin.com")
    monkeypatch.setattr(client_mod, "HAS_CFFI", False)

    class Response:
        status_code = 400
        ok = False
        text = "bad request"

    def fake_post(url, **kwargs):
        urls.append(url)
        return Response()

    monkeypatch.setattr(client_mod.requests, "post", fake_post)

    with pytest.raises(GarminConnectAuthenticationError):
        client._exchange_service_ticket("ticket")

    assert urls
    assert set(urls) == {"https://diauth.garmin.com/di-oauth2-service/oauth/token"}


def test_patch_noops_for_unsupported_garminconnect_version(monkeypatch):
    client_mod = _reload_client_module()
    from garmin_mcp.garmin_cn_auth_patch import apply_garmin_cn_auth_patch

    original_login = client_mod.Client.login
    original_http_post = client_mod.Client._http_post
    original_version = importlib.metadata.version

    def fake_version(name):
        if name == "garminconnect":
            return "9.9.9"
        return original_version(name)

    monkeypatch.setattr(importlib.metadata, "version", fake_version)

    apply_garmin_cn_auth_patch()

    assert client_mod.Client.login is original_login
    assert client_mod.Client._http_post is original_http_post
