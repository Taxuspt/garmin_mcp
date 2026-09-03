"""FastMCP integration tests for runtime-level tools."""

from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import runtime_tools
from garmin_mcp.runtime import GarminClientProvider, GarminGateway


def _app(client, tmp_path):
    provider = GarminClientProvider(lambda: client)
    gateway = GarminGateway(provider, sleep=lambda _seconds: None)
    runtime_tools.configure(
        provider,
        gateway,
        token_path=str(tmp_path / "tokens"),
        is_cn=False,
    )
    app = FastMCP("runtime test")
    return runtime_tools.register_tools(app), provider, gateway


@pytest.mark.asyncio
async def test_auth_check_is_local_only_unless_verify_is_requested(tmp_path):
    factory = Mock(return_value=Mock())
    provider = GarminClientProvider(factory)
    gateway = GarminGateway(provider)
    runtime_tools.configure(
        provider,
        gateway,
        token_path=str(tmp_path / "missing"),
        is_cn=False,
    )
    app = runtime_tools.register_tools(FastMCP("runtime test"))

    _content, result = await app.call_tool("check_garmin_auth", {"verify": False})
    assert result["status"] == "unverified"
    assert result["network_checked"] is False
    factory.assert_not_called()

    _content, result = await app.call_tool("check_garmin_auth", {"verify": True})
    assert result["status"] == "ready"
    assert result["authenticated"] is True
    factory.assert_called_once()
    factory.return_value.get_full_name.assert_called_once()


@pytest.mark.asyncio
async def test_briefing_uses_eight_bounded_calls_and_survives_partial_failure(tmp_path):
    client = Mock()
    client.get_training_readiness.return_value = [{"score": 72, "level": "HIGH"}]
    client.get_sleep_data.side_effect = RuntimeError("sleep endpoint unavailable")
    client.get_hrv_data.return_value = {
        "hrvSummary": {"lastNightAvg": 48, "weeklyAvg": 51, "status": "BALANCED"}
    }
    client.get_training_status.return_value = {
        "mostRecentTrainingStatus": {
            "latestTrainingStatusData": {
                "device": {
                    "primaryTrainingDevice": True,
                    "trainingStatus": 2,
                    "acuteTrainingLoadDTO": {
                        "dailyTrainingLoadAcute": 300,
                        "dailyTrainingLoadChronic": 330,
                    },
                }
            }
        }
    }
    client.get_activities.return_value = [
        {"activityId": 7, "activityName": "Ride", "activityType": {"typeKey": "cycling"}}
    ]
    client.get_stats.side_effect = [
        {"totalSteps": 12000, "restingHeartRate": 56},
        {"totalSteps": 8000, "restingHeartRate": 50},
        {"totalSteps": 10000, "restingHeartRate": 55},
    ]
    app, _provider, gateway = _app(client, tmp_path)

    _content, result = await app.call_tool(
        "get_briefing", {"date": "2024-02-01"}
    )

    assert result["sections"]["readiness"]["status"] == "ok"
    assert result["sections"]["sleep"]["status"] == "error"
    assert result["sections"]["training_state"]["data"]["tsb"] == 30
    assert result["sections"]["significant_changes"]["status"] == "ok"
    assert result["request_budget"]["logical_endpoint_calls_used"] == 8
    assert result["request_budget"]["logical_endpoint_calls_max"] == 8
    assert gateway.stats()["logical_calls"] == 8
