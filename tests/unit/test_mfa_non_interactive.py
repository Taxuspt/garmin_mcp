"""init_api() must fail cleanly, not crash, when MFA is required in a
non-interactive process (the normal case: an MCP client launches this as a
subprocess with no attached terminal).

See ai-docs/RAG.md — get_mfa() raises RuntimeError when non-interactive, and
init_api()'s wrapping exception handler did not catch it.
"""

from unittest.mock import patch

from garmin_mcp import init_api


@patch("garmin_mcp.is_interactive_terminal", return_value=False)
@patch("garmin_mcp.garmin_session.session.Garmin")
def test_mfa_required_non_interactive_returns_none_instead_of_crashing(
    mock_garmin_cls, _mock_terminal, tmp_path, monkeypatch
):
    # Isolate from real/parallel-worker state -- see ai-docs/testing.md
    # ("tests run in parallel... share no on-disk state").
    monkeypatch.setattr("garmin_mcp.tokenstore", str(tmp_path / "tokens"))
    monkeypatch.setattr("garmin_mcp._scratch_dir", tmp_path / "scratch")

    def _login(*_args, **_kwargs):
        # Simulate garminconnect's real login(): when MFA is required it
        # invokes the prompt_mfa callback synchronously (verified against
        # the installed 0.3.2 source -- see ai-docs/testing.md).
        prompt_mfa = mock_garmin_cls.call_args.kwargs["prompt_mfa"]
        prompt_mfa()

    mock_garmin_cls.return_value.login.side_effect = _login

    result = init_api("user@example.com", "hunter2")

    assert result is None
