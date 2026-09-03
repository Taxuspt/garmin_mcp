from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import physiology, user_profile


@pytest.mark.asyncio
async def test_all_physiology_tools_publish_concrete_forward_compatible_schemas():
    app = physiology.register_tools(FastMCP("physiology schema test"))
    tools = {tool.name: tool for tool in await app.list_tools()}
    expected_key_fields = {
        "get_physiology_store_status": {"status", "enabled", "schema_version"},
        "configure_physiology_store": {"status", "enabled", "data_dir"},
        "add_physiology_observation": {"status", "metric", "value", "confidence"},
        "set_physiology_profile_value": {"status", "metric", "value", "observed_at"},
        "get_physiology_observations": {"status", "observations"},
        "get_physiology_profile": {"status", "values", "active_zone_models"},
        "create_zone_model": {"status", "zones", "active"},
        "get_zone_models": {"status", "zone_models"},
        "activate_zone_model": {"status", "id", "active"},
        "inspect_test_file": {"status", "sha256", "columns", "row_count"},
        "import_physiology_test": {"status", "dry_run", "threshold_observations"},
        "estimate_thresholds": {"status", "candidates", "activity_evidence"},
        "accept_threshold_estimate": {"status", "estimate", "profile_observation"},
        "sync_profile_to_garmin": {"status", "payload", "write_performed"},
    }

    for tool_name, required_properties in expected_key_fields.items():
        schema = tools[tool_name].outputSchema
        assert schema is not None
        assert not schema["title"].endswith("DictOutput")
        assert required_properties <= set(schema["properties"])
        assert schema["required"] == ["status"]
        # Result models keep future evidence/provider fields without reducing
        # the schema back to a completely untyped Dict[str, Any].
        assert schema["additionalProperties"] is True

    physiology.configure(data_dir="")
    _content, error = await app.call_tool("get_physiology_profile", {})
    assert error["status"] == "error"
    assert "GARMIN_DATA_DIR" in error["error"]


@pytest.mark.asyncio
async def test_profile_sync_is_structured_preview_first_and_confirmed(
    tmp_path, monkeypatch
):
    client = Mock()
    current = {
        "trainingMethod": "HR_MAX",
        "restingHeartRateUsed": 54,
        "lactateThresholdHeartRateUsed": 188,
        "zone1Floor": 100,
        "zone2Floor": 136,
        "zone3Floor": 150,
        "zone4Floor": 170,
        "zone5Floor": 188,
        "maxHeartRateUsed": 203,
        "restingHrAutoUpdateUsed": False,
        "sport": "CYCLING",
        "changeState": "UNCHANGED",
    }
    client.connectapi.return_value = [current]
    physiology.configure(client, str(tmp_path))
    user_profile.configure(client)
    physiology.set_profile_value(
        metric="max_hr",
        value=204,
        observed_at=datetime.now(timezone.utc).isoformat(),
    )
    app = physiology.register_tools(FastMCP("physiology test"))
    try:
        tools = {tool.name: tool for tool in await app.list_tools()}
        assert tools["sync_profile_to_garmin"].outputSchema is not None

        _content, preview = await app.call_tool(
            "sync_profile_to_garmin",
            {"sport": "cycling", "fields": ["max_hr"]},
        )
        assert preview["status"] == "preview"
        client.client.request.assert_not_called()

        monkeypatch.setattr(
            physiology.write_confirmation,
            "confirm_garmin_write",
            AsyncMock(return_value=(False, "user declined")),
        )
        _content, refused = await app.call_tool(
            "sync_profile_to_garmin",
            {"sport": "cycling", "fields": ["max_hr"], "dry_run": False},
        )
        assert refused["status"] == "needs_confirmation"
        assert refused["write_performed"] is False
        client.client.request.assert_not_called()
        summary = physiology.write_confirmation.confirm_garmin_write.call_args.kwargs[
            "summary"
        ]
        assert summary["changes"]["maxHeartRateUsed"] == {
            "current": 203,
            "target": 204,
        }
    finally:
        physiology.configure(data_dir="")
