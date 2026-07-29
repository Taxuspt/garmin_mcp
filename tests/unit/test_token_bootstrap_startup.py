"""Tests for token bootstrap integration at server startup."""

from unittest.mock import Mock

import pytest

import garmin_mcp


def test_main_exits_nonzero_when_token_bootstrap_fails(monkeypatch, capsys):
    bootstrap = Mock(
        side_effect=garmin_mcp.token_utils.TokenBootstrapError("invalid secret")
    )
    monkeypatch.setattr(garmin_mcp.token_utils, "bootstrap_tokens", bootstrap)

    with pytest.raises(SystemExit) as exc_info:
        garmin_mcp.main()

    assert exc_info.value.code == 1
    assert "Garmin token bootstrap failed: invalid secret" in capsys.readouterr().err
    bootstrap.assert_called_once_with(garmin_mcp.tokenstore)


def test_main_reports_installed_bootstrap_tokens(monkeypatch, capsys, tmp_path):
    installed = tmp_path / "garmin_tokens.json"
    bootstrap = Mock(
        return_value=garmin_mcp.token_utils.TokenBootstrapResult(
            installed, installed=True
        )
    )
    monkeypatch.setattr(garmin_mcp.token_utils, "bootstrap_tokens", bootstrap)
    monkeypatch.setattr(garmin_mcp, "init_api", Mock(return_value=None))
    monkeypatch.setenv("GARMIN_MCP_TRANSPORT", "stdio")

    with pytest.raises(SystemExit) as exc_info:
        garmin_mcp.main()

    assert exc_info.value.code == 1
    assert f"Garmin OAuth tokens bootstrapped into '{installed}'." in (
        capsys.readouterr().err
    )
    bootstrap.assert_called_once_with(garmin_mcp.tokenstore)


def test_main_reports_skipped_bootstrap_source(monkeypatch, capsys, tmp_path):
    existing = tmp_path / "garmin_tokens.json"
    bootstrap = Mock(
        return_value=garmin_mcp.token_utils.TokenBootstrapResult(
            existing, installed=False
        )
    )
    monkeypatch.setattr(garmin_mcp.token_utils, "bootstrap_tokens", bootstrap)
    monkeypatch.setattr(garmin_mcp, "init_api", Mock(return_value=None))
    monkeypatch.setenv("GARMIN_MCP_TRANSPORT", "stdio")

    with pytest.raises(SystemExit):
        garmin_mcp.main()

    assert (
        "Configured Garmin token bootstrap source skipped because "
        f"'{existing}' already exists."
    ) in capsys.readouterr().err


def test_main_exits_nonzero_when_garmin_initialization_fails(monkeypatch):
    monkeypatch.setattr(
        garmin_mcp.token_utils, "bootstrap_tokens", Mock(return_value=None)
    )
    monkeypatch.setattr(garmin_mcp, "init_api", Mock(return_value=None))
    monkeypatch.setenv("GARMIN_MCP_TRANSPORT", "stdio")

    with pytest.raises(SystemExit) as exc_info:
        garmin_mcp.main()

    assert exc_info.value.code == 1
