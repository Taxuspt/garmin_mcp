"""Tests for Garmin Connect China authentication compatibility."""

import pytest


def test_cn_login_prefers_portal_flow_before_mobile_flow(monkeypatch):
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


def test_cn_token_exchange_uses_cn_diauth_endpoint(monkeypatch):
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
