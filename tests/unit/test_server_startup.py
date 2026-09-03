"""Startup smoke tests for the packaged MCP server."""

import asyncio
from unittest.mock import Mock

import garmin_mcp
from mcp.server.fastmcp import FastMCP


def test_main_registers_tools_and_starts_stdio(monkeypatch):
    """Run main() without real Garmin auth and stop before entering the server loop."""
    run_calls = []
    init_api = Mock(return_value=Mock())

    monkeypatch.delenv("GARMIN_MCP_TRANSPORT", raising=False)
    monkeypatch.delenv("GARMIN_MCP_HOST", raising=False)
    monkeypatch.delenv("GARMIN_MCP_PORT", raising=False)
    monkeypatch.setattr(garmin_mcp, "init_api", init_api)

    def capture_run(self, **kwargs):
        tools = asyncio.run(self.list_tools())
        run_calls.append(
            {
                "transport": kwargs.get("transport"),
                "tool_count": len(tools),
                "tool_names": [tool.name for tool in tools],
                "annotations": [tool.annotations for tool in tools],
                "annotations_by_name": {tool.name: tool.annotations for tool in tools},
                "output_schemas": {tool.name: tool.outputSchema for tool in tools},
            }
        )

    monkeypatch.setattr(FastMCP, "run", capture_run)

    garmin_mcp.main()

    assert run_calls
    assert run_calls[0]["transport"] == "stdio"
    assert run_calls[0]["tool_count"] >= 160
    assert "get_devices" in run_calls[0]["tool_names"]
    assert "get_workouts" in run_calls[0]["tool_names"]
    assert "check_garmin_auth" in run_calls[0]["tool_names"]
    assert "get_briefing" in run_calls[0]["tool_names"]
    assert "get_activity_streams" in run_calls[0]["tool_names"]
    assert "analyze_decoupling" in run_calls[0]["tool_names"]
    assert "reslice_zones" in run_calls[0]["tool_names"]
    assert "polarization_audit" in run_calls[0]["tool_names"]
    assert "estimate_thresholds" in run_calls[0]["tool_names"]
    assert "create_cycling_workout" in run_calls[0]["tool_names"]
    assert "plan_training_block" in run_calls[0]["tool_names"]
    assert "adapt_week" in run_calls[0]["tool_names"]
    assert all(annotation is not None for annotation in run_calls[0]["annotations"])
    hints = run_calls[0]["annotations_by_name"]
    assert hints["analyze_decoupling"].readOnlyHint is True
    assert hints["reslice_zones"].readOnlyHint is True
    assert hints["apply_training_block"].readOnlyHint is False
    assert hints["upload_fit"].readOnlyHint is False
    schemas = run_calls[0]["output_schemas"]
    for name in (
        "check_garmin_auth",
        "get_briefing",
        "get_activity_streams",
        "analyze_decoupling",
        "reslice_zones",
        "polarization_audit",
        "estimate_thresholds",
        "sync_profile_to_garmin",
        "create_cycling_workout",
        "create_hr_target_ride",
        "create_interval_workout",
        "upload_fit",
        "plan_training_block",
        "adapt_week",
    ):
        assert schemas[name] is not None
    # Server startup and tool discovery must not perform a Garmin login.
    init_api.assert_not_called()
