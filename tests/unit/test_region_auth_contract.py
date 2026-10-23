"""Exercise the dependency's real ticket exchange without contacting Garmin.

The old 0.3.2 pin sent China service tickets and refresh tokens to .com.
Keep these request contracts alongside the pin so a dependency change cannot
silently reintroduce the authentication failure from #257 / #345.
"""

import json
from unittest.mock import Mock

import pytest
from garminconnect import Garmin


@pytest.fixture(params=[False, True], ids=["garmin.com", "garmin.cn"])
def region_client(request):
    """Use the real Garmin constructor to propagate the selected region."""
    is_cn = request.param
    return Garmin(is_cn=is_cn).client, "garmin.cn" if is_cn else "garmin.com"


def _token_response(access_token="synthetic-access-token"):
    response = Mock(status_code=200, ok=True)
    response.json.return_value = {
        "access_token": access_token,
        "refresh_token": "synthetic-refresh-token",
    }
    return response


def test_mobile_login_consumes_ticket_in_selected_region(region_client, monkeypatch):
    """SSO and DI must use the same region and the same service URL."""
    client, domain = region_client
    session = Mock()
    session.post.return_value.status_code = 200
    session.post.return_value.json.return_value = {
        "responseStatus": {"type": "SUCCESSFUL"},
        "serviceTicketId": "synthetic-service-ticket",
    }
    token_post = Mock(return_value=_token_response())
    monkeypatch.setattr(client, "_http_post", token_post)

    client._do_mobile_login(session, "test@example.invalid", "unused-test-password")

    session.post.assert_called_once()
    assert session.post.call_args.args[0] == f"https://sso.{domain}/mobile/api/login"
    service_url = f"https://mobile.integration.{domain}/gcm/ios"
    assert session.post.call_args.kwargs["params"]["service"] == service_url
    token_post.assert_called_once()
    assert token_post.call_args.args[0] == (
        f"https://diauth.{domain}/di-oauth2-service/oauth/token"
    )
    assert token_post.call_args.kwargs["data"]["service_url"] == service_url
    assert token_post.call_args.kwargs["data"]["service_ticket"] == "synthetic-service-ticket"
    assert client.di_token == "synthetic-access-token"
    assert client.di_refresh_token == "synthetic-refresh-token"
    # A successful DI exchange must not enter the JWT_WEB fallback which
    # produced the unresolved mobile.integration.garmin.com error for CN.
    session.get.assert_not_called()


def test_default_ticket_exchange_uses_selected_region(region_client, monkeypatch):
    """The no-explicit-service path must also preserve the China domain."""
    client, domain = region_client
    token_post = Mock(return_value=_token_response())
    monkeypatch.setattr(client, "_http_post", token_post)

    client._exchange_service_ticket("synthetic-service-ticket")

    token_post.assert_called_once()
    assert token_post.call_args.args[0] == (
        f"https://diauth.{domain}/di-oauth2-service/oauth/token"
    )
    assert token_post.call_args.kwargs["data"]["service_url"] == (
        f"https://mobile.integration.{domain}/gcm/android"
    )


def test_saved_native_tokens_refresh_in_selected_region(
    region_client, monkeypatch, tmp_path
):
    """Native token files remain loadable, and refresh stays in their region."""
    client, domain = region_client
    monkeypatch.setattr(client, "_http_post", Mock(return_value=_token_response()))
    client._exchange_service_ticket("synthetic-service-ticket")
    token_path = str(tmp_path / "tokens")
    client.dump(token_path)

    token_data = json.loads((tmp_path / "tokens" / "garmin_tokens.json").read_text())
    assert token_data["di_token"] == "synthetic-access-token"
    assert token_data["di_refresh_token"] == "synthetic-refresh-token"
    assert token_data["di_client_id"]

    # The CLI and server both construct Garmin with is_cn and use these
    # native dump/load methods; no legacy garth token files are involved.
    restored = Garmin(is_cn=domain == "garmin.cn").client
    restored.load(token_path)
    refresh_post = Mock(return_value=_token_response("synthetic-refreshed-token"))
    monkeypatch.setattr(restored, "_http_post", refresh_post)

    restored._refresh_di_token()

    refresh_post.assert_called_once()
    assert refresh_post.call_args.args[0] == (
        f"https://diauth.{domain}/di-oauth2-service/oauth/token"
    )
    assert refresh_post.call_args.kwargs["data"] == {
        "grant_type": "refresh_token",
        "client_id": token_data["di_client_id"],
        "refresh_token": "synthetic-refresh-token",
    }
    assert restored.di_token == "synthetic-refreshed-token"
