"""Saved tokens survive optional export errors; profile loading remains required."""
import builtins
import os
from pathlib import Path
from unittest.mock import Mock

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

import garmin_mcp
from garmin_mcp import auth_cli, token_utils


@pytest.fixture
def saved_token_login(monkeypatch, tmp_path):
    token_path = tmp_path / "tokens"
    base64_path = tmp_path / "tokens_base64"
    credential_client = Mock(display_name=None, unit_system=None)
    credential_client.client.login.return_value = (None, None)

    def dump(path):
        Path(path).mkdir(parents=True, exist_ok=True)
        (Path(path) / "garmin_tokens.json").write_text("{}")

    credential_client.client.dump.side_effect = dump
    initial_client = Mock()
    initial_client.login.side_effect = GarminConnectAuthenticationError("no saved tokens")
    ready_client = Mock(display_name="test-user", unit_system="metric")
    constructor = Mock(side_effect=[initial_client, credential_client, ready_client])
    monkeypatch.setattr(garmin_mcp, "Garmin", constructor)
    monkeypatch.setattr(token_utils, "get_token_path", lambda: str(token_path))
    monkeypatch.setattr(token_utils, "get_token_base64_path", lambda: str(base64_path))
    monkeypatch.setattr(auth_cli, "Garmin", Mock(return_value=credential_client))
    monkeypatch.setattr(auth_cli, "token_exists", lambda _: False)
    monkeypatch.setattr(auth_cli, "get_credentials", lambda: ("user@example.invalid", "unused-test-password"))
    verify = Mock(return_value=(True, "test-user"))
    monkeypatch.setattr(auth_cli, "_verify_saved_tokens", verify)
    return token_path, base64_path, credential_client, ready_client, verify


def _break_optional_export(monkeypatch, base64_path, failure):
    real_open = builtins.open
    real_chmod = os.chmod

    def open_file(path, *args, **kwargs):
        if str(path) == str(base64_path) and failure != "chmod":
            error = FileNotFoundError if failure == "missing_parent" else PermissionError
            raise error("optional base64 export unavailable")
        return real_open(path, *args, **kwargs)

    def chmod(path, mode, *args, **kwargs):
        if str(path) == str(base64_path) and failure == "chmod":
            raise PermissionError("optional base64 export unavailable")
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", open_file)
    monkeypatch.setattr(os, "chmod", chmod)


@pytest.mark.parametrize("failure", ["missing_parent", "write_permission", "chmod"])
def test_server_optional_export_failure_still_loads_profile(
    saved_token_login, monkeypatch, capsys, failure
):
    token_path, base64_path, credential_client, ready_client, _ = saved_token_login
    _break_optional_export(monkeypatch, base64_path, failure)

    result = garmin_mcp.init_api("user@example.invalid", "unused-test-password")

    assert result is ready_client
    assert result is not credential_client
    ready_client.login.assert_called_once_with(str(token_path))
    assert (token_path / "garmin_tokens.json").is_file()
    error = capsys.readouterr().err
    assert "WARNING" in error and "base64" in error
    assert "Authentication failed" not in error
    if failure == "chmod":
        assert base64_path.read_text() == ""


@pytest.mark.parametrize("error", [
    GarminConnectAuthenticationError("Failed to retrieve social profile"),
    GarminConnectAuthenticationError("Failed to retrieve user settings"),
    GarminConnectConnectionError("HTTP error: API Error 503 - profile unavailable"),
    GarminConnectTooManyRequestsError("profile rate limited"),
])
def test_server_post_save_profile_failure_preserves_tokens_and_diagnoses_stage(
    saved_token_login, capsys, error
):
    token_path, _, credential_client, ready_client, _ = saved_token_login
    ready_client.login.side_effect = error

    result = garmin_mcp.init_api("user@example.invalid", "unused-test-password")

    assert result is None
    assert (token_path / "garmin_tokens.json").is_file()
    ready_client.login.assert_called_once_with(str(token_path))
    assert credential_client.display_name is None
    message = capsys.readouterr().err
    assert "tokens were saved" in message.lower()
    assert str(token_path) in message
    assert "profile" in message.lower()
    assert str(error) in message
    assert "Invalid email or password" not in message
    assert "Invalid credentials" not in message


@pytest.mark.parametrize("failure", ["missing_parent", "write_permission", "chmod"])
def test_cli_optional_export_failure_still_verifies_tokens(
    saved_token_login, monkeypatch, capsys, failure
):
    token_path, base64_path, _, _, verify = saved_token_login
    _break_optional_export(monkeypatch, base64_path, failure)

    result = auth_cli.authenticate(str(token_path), str(base64_path))

    assert result is True
    assert (token_path / "garmin_tokens.json").is_file()
    verify.assert_called_once_with(str(token_path), False)
    error = capsys.readouterr().err
    assert "WARNING" in error and "base64" in error
    assert "Authentication failed" not in error
    if failure == "chmod":
        assert base64_path.read_text() == ""


@pytest.mark.parametrize("export_fails", [False, True])
def test_cli_post_save_verification_failure_retains_tokens_and_diagnoses_stage(
    saved_token_login, monkeypatch, capsys, export_fails
):
    token_path, base64_path, _, _, verify = saved_token_login
    verify.return_value = (False, "Failed to retrieve user settings")
    if export_fails:
        _break_optional_export(monkeypatch, base64_path, "missing_parent")

    result = auth_cli.authenticate(str(token_path), str(base64_path))

    assert result is False
    verify.assert_called_once_with(str(token_path), False)
    assert (token_path / "garmin_tokens.json").is_file()
    error = capsys.readouterr().err
    assert "tokens were saved" in error.lower()
    assert str(token_path) in error
    assert "profile" in error.lower()
    assert "Failed to retrieve user settings" in error
    assert "Invalid email or password" not in error
