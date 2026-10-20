"""init_api says why saved tokens failed to load (#335).

Every token failure used to print "OAuth tokens not found", including the case
where tokens exist but Garmin rejects them, and the case where garmin-mcp-auth
saved to a different directory than the server reads.
"""

from unittest.mock import patch

import pytest
from garminconnect import GarminConnectAuthenticationError, GarminConnectConnectionError

import garmin_mcp


@pytest.fixture
def non_interactive(monkeypatch):
    monkeypatch.setattr(garmin_mcp, "is_interactive_terminal", lambda: False)


def _init_api_with_failure(monkeypatch, token_dir, error):
    """Run init_api with no credentials and a token login that raises `error`."""
    monkeypatch.setenv("GARMINTOKENS", str(token_dir))
    # The module caches the token path at import on older mains.
    monkeypatch.setattr(garmin_mcp, "tokenstore", str(token_dir), raising=False)
    with patch("garmin_mcp.Garmin") as mock_garmin:
        mock_garmin.return_value.login.side_effect = error
        return garmin_mcp.init_api(None, None)


def _write_tokens(store):
    store.mkdir(parents=True, exist_ok=True)
    (store / "garmin_tokens.json").write_text(
        '{"di_token": "a", "di_refresh_token": "b", "di_client_id": "c"}'
    )


def test_missing_directory_names_the_resolved_path(non_interactive, monkeypatch, tmp_path, capsys):
    token_dir = tmp_path / "never-written"

    result = _init_api_with_failure(
        monkeypatch, token_dir, GarminConnectAuthenticationError("Username and password are required")
    )

    err = capsys.readouterr().err
    assert result is None
    assert "Garmin login from saved tokens failed" in err
    assert f"No token directory at '{token_dir}' (from GARMINTOKENS={token_dir})" in err
    assert "garmin-mcp-auth --diagnose" in err
    assert "OAuth tokens not found" not in err


def test_rejected_tokens_are_not_reported_as_missing(non_interactive, monkeypatch, tmp_path, capsys):
    token_dir = tmp_path / "tokens"
    _write_tokens(token_dir)

    result = _init_api_with_failure(
        monkeypatch, token_dir, GarminConnectAuthenticationError("Failed to retrieve social profile")
    )

    err = capsys.readouterr().err
    assert result is None
    assert "loaded but Garmin rejected them" in err
    assert "Failed to retrieve social profile" in err
    assert "--force-reauth" in err


def test_corrupt_token_file_is_named(non_interactive, monkeypatch, tmp_path, capsys):
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    (token_dir / "garmin_tokens.json").write_text("not json")

    result = _init_api_with_failure(
        monkeypatch, token_dir, GarminConnectAuthenticationError("Username and password are required")
    )

    err = capsys.readouterr().err
    assert result is None
    assert f"{token_dir / 'garmin_tokens.json'} could not be loaded" in err


def test_network_failure_is_not_blamed_on_tokens(non_interactive, monkeypatch, tmp_path, capsys):
    token_dir = tmp_path / "tokens"
    _write_tokens(token_dir)

    result = _init_api_with_failure(
        monkeypatch, token_dir, GarminConnectConnectionError("Connection refused")
    )

    err = capsys.readouterr().err
    assert result is None
    assert "Could not reach Garmin Connect" in err
    assert "Garmin rejected them" not in err


def test_interactive_fallback_explains_before_password_login(monkeypatch, tmp_path, capsys):
    """With credentials, the explanation still prints before the password login."""
    monkeypatch.setattr(garmin_mcp, "is_interactive_terminal", lambda: True)
    token_dir = tmp_path / "never-written"
    monkeypatch.setenv("GARMINTOKENS", str(token_dir))
    monkeypatch.setattr(garmin_mcp, "tokenstore", str(token_dir), raising=False)
    monkeypatch.setattr(garmin_mcp, "tokenstore_base64", str(tmp_path / "b64"), raising=False)
    monkeypatch.setenv("GARMINTOKENS_BASE64", str(tmp_path / "b64"))

    with patch("garmin_mcp.Garmin") as mock_garmin:
        mock_garmin.return_value.login.side_effect = [
            GarminConnectAuthenticationError("Username and password are required"),
            GarminConnectConnectionError("Connection refused"),
        ]
        mock_garmin.return_value.client.login.side_effect = GarminConnectConnectionError("offline")
        result = garmin_mcp.init_api("me@example.com", "secret")

    err = capsys.readouterr().err
    assert result is None
    assert f"No token directory at '{token_dir}'" in err
    assert "Logging in with your Garmin Connect credentials" in err
