"""Typed-401 detection must reclassify a real 401 as GarminConnectAuthenticationError,
not the generic GarminConnectConnectionError Client._run_request raises for every
>=400 response in garminconnect==0.3.2.

Drives the real (monkeypatched) Client._run_request rather than reimplementing its
logic, with only requests.Session.request mocked -- see ai-docs/testing.md
("never assume") and garmin_session/errors.py's own docstring for why this repo's
garminconnect version needed a different instrumentation point than the sibling
services' copy of this module.
"""

from unittest.mock import Mock, patch

import pytest
from garminconnect import GarminConnectAuthenticationError, GarminConnectConnectionError
from garminconnect.client import Client

from garmin_mcp.garmin_session import errors


@pytest.fixture(autouse=True)
def _install_typed_errors():
    errors.install()


def _fake_response(status_code):
    resp = Mock()
    resp.status_code = status_code
    resp.json = Mock(return_value={})
    resp.text = ""
    resp.content = b"{}"
    return resp


def _authenticated_client(monkeypatch):
    client = Client()
    client.di_token = "dummy-token"
    monkeypatch.setattr(client, "_token_expires_soon", lambda: False)
    return client


def test_persistent_401_raises_typed_auth_error(monkeypatch):
    client = _authenticated_client(monkeypatch)

    with (
        patch("requests.Session.request", return_value=_fake_response(401)),
        pytest.raises(GarminConnectAuthenticationError),
    ):
        client._run_request("GET", "some/path")


def test_non_401_error_still_raises_generic_connection_error(monkeypatch):
    client = _authenticated_client(monkeypatch)

    with (
        patch("requests.Session.request", return_value=_fake_response(500)),
        pytest.raises(GarminConnectConnectionError) as excinfo,
    ):
        client._run_request("GET", "some/path")

    assert not isinstance(excinfo.value, GarminConnectAuthenticationError)


def test_successful_retry_after_transient_401_raises_nothing(monkeypatch):
    """_run_request already retries once on 401 (refresh + retry). If the
    retry succeeds, no exception should surface at all."""
    client = _authenticated_client(monkeypatch)
    monkeypatch.setattr(client, "_refresh_session", lambda: None)

    responses = iter([_fake_response(401), _fake_response(200)])

    with patch("requests.Session.request", side_effect=lambda *a, **k: next(responses)):
        result = client._run_request("GET", "some/path")

    assert result.status_code == 200
