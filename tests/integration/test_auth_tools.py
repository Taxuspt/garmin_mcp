"""
Integration tests for MCP Garmin authentication tools.
"""

from __future__ import annotations

import json
from unittest.mock import Mock, mock_open, patch

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import auth_tools


@pytest.fixture
def app_with_auth_tools():
    """Create FastMCP app with auth tools registered."""
    app = FastMCP("Test Auth Tools")
    auth_tools.configure(lambda client: None)
    return auth_tools.register_tools(app)


def tool_json(result):
    """Extract JSON payload from FastMCP call_tool output."""
    return json.loads(result[0][0].text)


@pytest.mark.asyncio
@patch("garmin_mcp.auth_tools.get_token_info")
async def test_check_garmin_auth_reports_valid_tokens(mock_info, app_with_auth_tools):
    """Test auth status returns valid token state."""
    mock_info.return_value = {
        "path": "/tmp/tokens",
        "expanded_path": "/tmp/tokens",
        "exists": True,
        "valid": True,
        "error": "",
    }

    result = await app_with_auth_tools.call_tool("check_garmin_auth", {"token_path": "/tmp/tokens"})
    payload = tool_json(result)

    assert payload["authenticated"] is True
    assert payload["next_step"] == "You can use Garmin tools now."


@pytest.mark.asyncio
@patch("garmin_mcp.auth_tools.get_token_info")
@patch("garmin_mcp.auth_tools.Garmin")
async def test_login_to_garmin_saves_tokens_without_otp(mock_garmin, mock_info, app_with_auth_tools):
    """Test successful non-OTP login saves tokens."""
    mock_info.return_value = {"exists": False, "valid": False, "expanded_path": "/tmp/tokens"}
    garmin = Mock()
    garmin.garth.dumps.return_value = "base64-token"
    mock_garmin.return_value = garmin

    with patch("builtins.open", mock_open()) as open_mock:
        result = await app_with_auth_tools.call_tool(
            "login_to_garmin",
            {
                "email": "test@example.com",
                "password": "secret",
                "token_path": "/tmp/tokens",
                "token_base64_path": "/tmp/tokens.b64",
            },
        )

    payload = tool_json(result)
    assert payload["status"] == "authenticated"
    garmin.login.assert_called_once()
    garmin.garth.dump.assert_called_once_with("/tmp/tokens")
    open_mock().write.assert_called_once_with("base64-token")


@pytest.mark.asyncio
@patch("garmin_mcp.auth_tools._prompt_local_input", return_value="")
@patch("garmin_mcp.auth_tools.get_token_info")
@patch("garmin_mcp.auth_tools.Garmin")
async def test_login_to_garmin_returns_mfa_required_without_otp(
    mock_garmin, mock_info, mock_prompt, app_with_auth_tools
):
    """Test OTP accounts get an actionable mfa_required response."""
    mock_info.return_value = {"exists": False, "valid": False, "expanded_path": "/tmp/tokens"}
    garmin = Mock()
    mfa_prompt = None

    def create_garmin(**kwargs):
        nonlocal mfa_prompt
        mfa_prompt = kwargs["prompt_mfa"]
        return garmin

    def require_mfa():
        mfa_prompt()

    mock_garmin.side_effect = create_garmin
    garmin.login.side_effect = require_mfa

    result = await app_with_auth_tools.call_tool(
        "login_to_garmin",
        {"email": "test@example.com", "password": "secret", "token_path": "/tmp/tokens"},
    )

    payload = tool_json(result)
    assert payload["status"] == "mfa_required"
    assert "one-time code" in payload["message"]


@pytest.mark.asyncio
@patch("garmin_mcp.auth_tools.get_token_info")
@patch("garmin_mcp.auth_tools.Garmin")
async def test_login_to_garmin_uses_otp_code(mock_garmin, mock_info, app_with_auth_tools):
    """Test OTP code can satisfy Garmin MFA prompt and save tokens."""
    mock_info.return_value = {"exists": False, "valid": False, "expanded_path": "/tmp/tokens"}
    garmin = Mock()
    garmin.garth.dumps.return_value = "base64-token"
    mfa_prompt = None

    def create_garmin(**kwargs):
        nonlocal mfa_prompt
        mfa_prompt = kwargs["prompt_mfa"]
        return garmin

    def use_mfa():
        assert mfa_prompt() == "123456"

    mock_garmin.side_effect = create_garmin
    garmin.login.side_effect = use_mfa

    with patch("builtins.open", mock_open()):
        result = await app_with_auth_tools.call_tool(
            "login_to_garmin",
            {
                "email": "test@example.com",
                "password": "secret",
                "otp_code": "123456",
                "token_path": "/tmp/tokens",
            },
        )

    payload = tool_json(result)
    assert payload["status"] == "authenticated"
    garmin.garth.dump.assert_called_once_with("/tmp/tokens")


@pytest.mark.asyncio
@patch("garmin_mcp.auth_tools.get_token_info")
@patch("garmin_mcp.auth_tools._prompt_local_input")
async def test_login_to_garmin_uses_local_password_prompt(
    mock_prompt, mock_info, app_with_auth_tools
):
    """Test local dialog prompt can provide missing password without chat."""
    mock_info.return_value = {"exists": False, "valid": False, "expanded_path": "/tmp/tokens"}
    mock_prompt.side_effect = ["local-secret"]

    with patch.dict("os.environ", {"GARMIN_EMAIL": "test@example.com"}, clear=False):
        with patch("garmin_mcp.auth_tools.Garmin") as mock_garmin:
            garmin = Mock()
            garmin.garth.dumps.return_value = "base64-token"
            mock_garmin.return_value = garmin

            with patch("builtins.open", mock_open()):
                result = await app_with_auth_tools.call_tool(
                    "login_to_garmin",
                    {"token_path": "/tmp/tokens"},
                )

    payload = tool_json(result)
    assert payload["status"] == "authenticated"
    mock_garmin.assert_called_once()
    assert mock_garmin.call_args.kwargs["password"] == "local-secret"


@pytest.mark.asyncio
@patch("garmin_mcp.auth_tools.get_token_info")
@patch("garmin_mcp.auth_tools._prompt_local_input", return_value="")
async def test_login_to_garmin_reports_missing_credentials_after_cancelled_prompt(
    mock_prompt, mock_info, app_with_auth_tools
):
    """Test cancelled local prompt returns missing credentials."""
    mock_info.return_value = {"exists": False, "valid": False, "expanded_path": "/tmp/tokens"}

    with patch.dict("os.environ", {}, clear=True):
        result = await app_with_auth_tools.call_tool(
            "login_to_garmin",
            {"token_path": "/tmp/tokens"},
        )

    payload = tool_json(result)
    assert payload["status"] == "missing_credentials"
