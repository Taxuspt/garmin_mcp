"""init_api() must fail cleanly, not crash, when MFA is required in a
non-interactive process (the normal case: an MCP client launches this as a
subprocess with no attached terminal).

See ai-docs/RAG.md — get_mfa() raises RuntimeError when non-interactive, and
init_api()'s credential-login exception handler did not catch it.
"""

from unittest.mock import Mock, patch

from garmin_mcp import init_api


@patch("garmin_mcp.is_interactive_terminal", return_value=False)
@patch("garmin_mcp.Garmin")
def test_mfa_required_non_interactive_returns_none_instead_of_crashing(mock_garmin, _mock_terminal):
    token_login = Mock()
    token_login.login.side_effect = FileNotFoundError

    credential_login = Mock()
    credential_login.login.return_value = ("needs_mfa", "context")

    mock_garmin.side_effect = [token_login, credential_login]

    result = init_api("user@example.com", "hunter2")

    assert result is None
