"""Startup smoke tests for the packaged MCP server."""

from unittest.mock import Mock

import garmin_mcp
from mcp.server.fastmcp import FastMCP


def test_main_registers_tools_and_starts_stdio(monkeypatch):
    """Run main() without real Garmin auth and stop before entering the server loop."""
    run_calls = []

    monkeypatch.delenv("GARMIN_MCP_TRANSPORT", raising=False)
    monkeypatch.delenv("GARMIN_MCP_HOST", raising=False)
    monkeypatch.delenv("GARMIN_MCP_PORT", raising=False)
    monkeypatch.setattr(garmin_mcp, "init_api", lambda _email, _password: Mock())
    monkeypatch.setattr(
        FastMCP,
        "run",
        lambda self, **kwargs: run_calls.append(
            {"transport": kwargs.get("transport"), "tool_count": len(self._tool_manager._tools)}
        ),
    )

    garmin_mcp.main()

    assert run_calls
    assert run_calls[0]["transport"] == "stdio"
    assert run_calls[0]["tool_count"] > 0
