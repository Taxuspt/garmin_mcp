"""Contract tests for intent-level structured MCP tool results."""

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import activity_streams, coaching, fit_upload, workout_builders


@pytest.mark.asyncio
async def test_core_tools_publish_stable_pydantic_output_schemas():
    app = FastMCP("core output schemas")
    activity_streams.register_tools(app)
    workout_builders.register_tools(app)
    fit_upload.register_tools(app)
    coaching.register_tools(app)

    tools = {tool.name: tool for tool in await app.list_tools()}
    expected_core_fields = {
        "get_activity_streams": {"summary", "data", "pagination"},
        "analyze_decoupling": {"window", "quality", "decoupling_pct"},
        "reslice_zones": {"model", "zones", "missing_s"},
        "polarization_audit": {"time_distribution", "session_distribution", "activities"},
        "create_cycling_workout": {"workout", "read_back", "schedule"},
        "create_hr_target_ride": {"workout", "read_back", "schedule"},
        "create_interval_workout": {"workout", "read_back", "schedule"},
        "upload_fit": {"file", "upload_result", "source_unchanged"},
        "plan_training_block": {"weeks", "storage", "plan_id"},
        "apply_training_block": {"change_set", "created", "recovery_checklist"},
        "adapt_week": {"patched_week", "input_snapshot", "adaptation_id"},
        "apply_week_adaptation": {"new_sessions", "remote_result", "recovery_checklist"},
    }

    for name, core_fields in expected_core_fields.items():
        schema = tools[name].outputSchema
        assert schema is not None, name
        assert schema.get("type") == "object", name
        assert "status" in schema.get("required", []), name
        assert schema.get("properties", {}).get("status", {}).get("type") == "string", name
        assert core_fields <= set(schema.get("properties", {})), name
        assert schema.get("additionalProperties") is True, name


@pytest.mark.asyncio
async def test_activity_error_envelope_validates_against_declared_schema():
    app = FastMCP("activity output validation")
    activity_streams.configure(None)
    activity_streams.register_tools(app)

    _content, structured = await app.call_tool(
        "get_activity_streams",
        {"activity_id": 1, "fields": ["not-a-field"]},
    )

    assert structured["status"] == "error"
    assert structured["error"]["code"] == "not_configured"
    assert "Garmin client is not configured" in structured["error"]["message"]
