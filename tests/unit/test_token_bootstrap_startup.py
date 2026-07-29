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
    bootstrap = Mock(return_value=installed)
    monkeypatch.setattr(garmin_mcp.token_utils, "bootstrap_tokens", bootstrap)
    monkeypatch.setattr(garmin_mcp, "init_api", Mock(return_value=None))
    monkeypatch.setenv("GARMIN_MCP_TRANSPORT", "stdio")

    garmin_mcp.main()

    assert f"Garmin OAuth tokens bootstrapped into '{installed}'." in (
        capsys.readouterr().err
    )
    bootstrap.assert_called_once_with(garmin_mcp.tokenstore)
