"""Server authentication must not consume the MCP transport's input stream."""
from unittest.mock import Mock

import pytest

import garmin_mcp


@pytest.mark.parametrize("interactive", [False, True])
def test_server_mfa_never_reads_transport_stdin(monkeypatch, capsys, interactive):
    monkeypatch.setattr(garmin_mcp, "is_interactive_terminal", lambda: interactive)
    read = Mock(side_effect=AssertionError("server must not read an MFA code from stdin"))
    monkeypatch.setattr("builtins.input", read)
    with pytest.raises(RuntimeError, match="garmin-mcp-auth"):
        garmin_mcp.get_mfa()
    read.assert_not_called()
    assert "garmin-mcp-auth" in capsys.readouterr().err


@pytest.mark.parametrize("email,password", [(None, None), ("user@example.invalid", None), (None, "unused-test-password")])
def test_server_tty_does_not_attempt_password_login_without_credentials(monkeypatch, tmp_path, email, password):
    from garminconnect import GarminConnectAuthenticationError

    garmin = Mock()
    garmin.login.side_effect = GarminConnectAuthenticationError("token file missing")
    garmin.client.login.side_effect = GarminConnectAuthenticationError("unexpected credential request")
    monkeypatch.setattr(garmin_mcp, "Garmin", Mock(return_value=garmin))
    monkeypatch.setattr(garmin_mcp, "is_interactive_terminal", lambda: True)
    assert garmin_mcp.init_api(email, password, tokenstore=str(tmp_path / "tokens")) is None
    garmin.client.login.assert_not_called()


@pytest.mark.parametrize("status", [401, 403])
def test_server_preserves_login_strategy_failure_without_guessing_credentials(monkeypatch, tmp_path, capsys, status):
    from garminconnect import GarminConnectAuthenticationError, GarminConnectConnectionError

    message = f"All login strategies exhausted: Portal login failed (non-JSON): HTTP {status}"
    garmin = Mock()
    garmin.login.side_effect = GarminConnectAuthenticationError("token file missing")
    garmin.client.login.side_effect = GarminConnectConnectionError(message)
    monkeypatch.setattr(garmin_mcp, "Garmin", Mock(return_value=garmin))
    assert garmin_mcp.init_api(
        "user@example.invalid", "unused-test-password", tokenstore=str(tmp_path / "tokens")
    ) is None
    err = capsys.readouterr().err
    assert message in err
    assert "Invalid credentials" not in err


@pytest.mark.parametrize("status", [401, 403])
def test_auth_cli_preserves_login_strategy_failure_without_guessing_credentials(monkeypatch, tmp_path, capsys, status):
    from garminconnect import GarminConnectConnectionError
    from garmin_mcp import auth_cli

    message = f"All login strategies exhausted: Portal login failed (non-JSON): HTTP {status}"
    garmin = Mock()
    garmin.client.login.side_effect = GarminConnectConnectionError(message)
    monkeypatch.setattr(auth_cli, "Garmin", Mock(return_value=garmin))
    monkeypatch.setattr(auth_cli, "token_exists", lambda _: False)
    monkeypatch.setattr(auth_cli, "get_credentials", lambda: ("user@example.invalid", "unused-test-password"))
    assert auth_cli.authenticate(str(tmp_path / "tokens"), str(tmp_path / "b64")) is False
    err = capsys.readouterr().err
    assert message in err
    assert "Invalid credentials" not in err
