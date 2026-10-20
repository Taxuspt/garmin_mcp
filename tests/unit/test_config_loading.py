"""Credentials and token settings are read at startup, not at import (#343).

Importing garmin_mcp used to read GARMIN_EMAIL(_FILE) / GARMIN_PASSWORD(_FILE),
GARMINTOKENS and GARMIN_IS_CN as a side effect, raising ValueError or
FileNotFoundError from the import itself when the environment was wrong.
"""

import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from mcp.server.fastmcp import FastMCP

import garmin_mcp
from garmin_mcp import init_api, load_credentials

SRC_DIR = Path(garmin_mcp.__file__).resolve().parents[1]


@pytest.fixture
def clean_env(monkeypatch):
    for name in (
        "GARMIN_EMAIL",
        "GARMIN_EMAIL_FILE",
        "GARMIN_PASSWORD",
        "GARMIN_PASSWORD_FILE",
        "GARMIN_MCP_TRANSPORT",
        "GARMIN_MCP_HOST",
        "GARMIN_MCP_PORT",
        "GARMIN_ENABLED_TOOLS",
        "GARMIN_DISABLED_TOOLS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_importing_the_package_has_no_credential_side_effects(tmp_path):
    """A conflicting or unreadable credential environment must not break import."""
    env = {
        **os.environ,
        "PYTHONPATH": str(SRC_DIR),
        "GARMIN_EMAIL": "a@example.com",
        "GARMIN_EMAIL_FILE": str(tmp_path / "missing-email"),
        "GARMIN_PASSWORD_FILE": str(tmp_path / "missing-password"),
    }
    env.pop("GARMIN_PASSWORD", None)
    completed = subprocess.run(
        [sys.executable, "-c", "import garmin_mcp; print('imported')"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert "imported" in completed.stdout


def test_load_credentials_reads_values_and_files(clean_env, monkeypatch, tmp_path):
    email_file = tmp_path / "email"
    email_file.write_text("me@example.com\n")
    monkeypatch.setenv("GARMIN_EMAIL_FILE", str(email_file))
    monkeypatch.setenv("GARMIN_PASSWORD", "secret")

    assert load_credentials() == ("me@example.com", "secret")


def test_load_credentials_returns_none_when_unset(clean_env):
    assert load_credentials() == (None, None)


@pytest.mark.parametrize("name", ["GARMIN_EMAIL", "GARMIN_PASSWORD"])
def test_load_credentials_rejects_value_and_file_together(clean_env, monkeypatch, tmp_path, name):
    monkeypatch.setenv(name, "x")
    monkeypatch.setenv(f"{name}_FILE", str(tmp_path / "f"))

    with pytest.raises(ValueError, match=f"Must only provide one of {name} and {name}_FILE"):
        load_credentials()


def test_load_credentials_reports_unreadable_file(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("GARMIN_EMAIL_FILE", str(tmp_path / "missing"))

    with pytest.raises(ValueError, match="Cannot read GARMIN_EMAIL_FILE"):
        load_credentials()


def test_main_reports_credential_env_errors_and_exits(clean_env, monkeypatch, capsys):
    init_api_mock = Mock()
    monkeypatch.setattr(garmin_mcp, "init_api", init_api_mock)
    monkeypatch.setattr(FastMCP, "run", lambda _self, **_kwargs: None)
    monkeypatch.setenv("GARMIN_EMAIL", "a@example.com")
    monkeypatch.setenv("GARMIN_EMAIL_FILE", "/nonexistent/email")

    with pytest.raises(SystemExit) as exc_info:
        garmin_mcp.main()

    assert exc_info.value.code == 1
    assert (
        "Must only provide one of GARMIN_EMAIL and GARMIN_EMAIL_FILE"
        in capsys.readouterr().err
    )
    init_api_mock.assert_not_called()


def test_main_passes_credentials_read_at_startup(clean_env, monkeypatch, tmp_path):
    """main() hands init_api the credentials from the environment it starts in."""
    seen = {}
    called = threading.Event()

    def fake_init_api(email, password):
        seen.update(email=email, password=password)
        called.set()
        return Mock()

    monkeypatch.setattr(garmin_mcp, "init_api", fake_init_api)
    monkeypatch.setattr(FastMCP, "run", lambda _self, **_kwargs: None)
    password_file = tmp_path / "password"
    password_file.write_text("hunter2\n")
    monkeypatch.setenv("GARMIN_EMAIL", "me@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD_FILE", str(password_file))

    garmin_mcp.main()

    assert called.wait(timeout=10), "init_api was never called"
    assert seen == {"email": "me@example.com", "password": "hunter2"}


@patch("garmin_mcp.is_interactive_terminal", return_value=False)
@patch("garmin_mcp.Garmin")
def test_init_api_reads_token_settings_when_called(mock_garmin, _mock_terminal, monkeypatch, tmp_path):
    """GARMINTOKENS / GARMIN_IS_CN are read per call, so reconfiguring needs no reload."""
    mock_garmin.return_value.login.side_effect = FileNotFoundError
    token_dir = str(tmp_path / "tokens-set-after-import")
    monkeypatch.setenv("GARMINTOKENS", token_dir)
    monkeypatch.setenv("GARMIN_IS_CN", "true")

    assert init_api(None, None) is None

    mock_garmin.assert_called_once_with(is_cn=True)
    mock_garmin.return_value.login.assert_called_once_with(token_dir)


@patch("garmin_mcp.is_interactive_terminal", return_value=False)
@patch("garmin_mcp.Garmin")
def test_init_api_explicit_token_settings_win(mock_garmin, _mock_terminal, monkeypatch, tmp_path):
    mock_garmin.return_value.login.side_effect = FileNotFoundError
    monkeypatch.setenv("GARMINTOKENS", str(tmp_path / "from-env"))
    monkeypatch.setenv("GARMIN_IS_CN", "true")
    explicit = str(tmp_path / "explicit")

    assert init_api(None, None, tokenstore=explicit, is_cn=False) is None

    mock_garmin.assert_called_once_with(is_cn=False)
    mock_garmin.return_value.login.assert_called_once_with(explicit)
