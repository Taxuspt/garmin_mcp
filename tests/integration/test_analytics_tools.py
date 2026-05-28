"""
Integration tests for historical analytics MCP tools.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import analytics
from garmin_mcp.client_resolver import set_global_client


@pytest.fixture
def app_with_analytics(mock_garmin_client):
    """Create FastMCP app with analytics tools registered."""
    analytics.configure(mock_garmin_client)
    set_global_client(mock_garmin_client)
    app = FastMCP("Test Analytics")
    return analytics.register_tools(app)


def tool_json(result):
    """Extract JSON payload from FastMCP call_tool output."""
    return json.loads(result[0][0].text)


@pytest.fixture
def analytics_history(mock_garmin_client):
    """Mock 35 days of daily Garmin data with a few clear trends."""
    start = date(2024, 1, 1)
    stats_by_date = {}
    sleep_by_date = {}
    readiness_by_date = {}

    for index in range(35):
        current = start + timedelta(days=index)
        date_text = current.isoformat()
        hard_block = index >= 28
        stats_by_date[date_text] = {
            "calendarDate": date_text,
            "totalSteps": 8000 + (index * 100),
            "activeKilocalories": 420 + (index * 5),
            "totalDistanceMeters": 6000 + (index * 80),
            "restingHeartRate": 52 + (4 if hard_block else 0),
            "averageStressLevel": 28 + (12 if hard_block else 0),
            "maxStressLevel": 65 + (8 if hard_block else 0),
            "bodyBatteryHighestValue": 86 - (12 if hard_block else 0),
            "bodyBatteryLowestValue": 28 + (6 if hard_block else 0),
            "bodyBatteryChargedValue": 58 - (8 if hard_block else 0),
            "bodyBatteryDrainedValue": 42 + (9 if hard_block else 0),
        }
        sleep_by_date[date_text] = {
            "dailySleepDTO": {
                "sleepTimeSeconds": (8 * 3600) - (1800 if hard_block else 0),
                "deepSleepSeconds": 7200 - (900 if hard_block else 0),
                "remSleepSeconds": 5400 - (600 if hard_block else 0),
                "avgSleepStress": 14 + (8 if hard_block else 0),
                "sleepScores": {"overall": {"value": 84 - (13 if hard_block else 0)}},
            },
            "avgOvernightHrv": 48 - (6 if hard_block else 0),
        }
        readiness_by_date[date_text] = [{"score": 82 - (15 if hard_block else 0)}]

    stats_by_date["2024-01-31"]["averageStressLevel"] = 75

    def get_stats(date_text):
        return stats_by_date.get(date_text, {})

    def get_sleep_data(date_text):
        return sleep_by_date.get(date_text, {})

    def get_training_readiness(date_text):
        return readiness_by_date.get(date_text, [])

    mock_garmin_client.get_stats.side_effect = get_stats
    mock_garmin_client.get_sleep_data.side_effect = get_sleep_data
    mock_garmin_client.get_training_readiness.side_effect = get_training_readiness
    mock_garmin_client.get_activities_by_date.return_value = [
        {
            "startTimeLocal": "2024-01-29 07:00:00",
            "distance": 10000,
            "duration": 3600,
            "calories": 700,
        },
        {
            "startTimeLocal": "2024-01-30 07:00:00",
            "distance": 12000,
            "duration": 4000,
            "calories": 760,
        },
    ]


@pytest.mark.asyncio
async def test_get_health_baselines(app_with_analytics, analytics_history):
    """Test baseline tool returns latest values and deltas."""
    result = await app_with_analytics.call_tool(
        "get_health_baselines",
        {"end_date": "2024-02-04", "days": 35, "baseline_window": 28},
    )
    payload = tool_json(result)

    assert payload["window"]["days"] == 35
    assert payload["latest_date"] == "2024-02-04"
    assert payload["metrics"]["resting_hr"]["latest"] == 56
    assert payload["metrics"]["sleep_score"]["baseline_avg"] is not None


@pytest.mark.asyncio
async def test_get_wellness_anomalies(app_with_analytics, analytics_history):
    """Test anomaly tool finds the stress spike."""
    result = await app_with_analytics.call_tool(
        "get_wellness_anomalies",
        {"end_date": "2024-02-04", "days": 35, "baseline_window": 21, "z_threshold": 1.5},
    )
    payload = tool_json(result)

    assert payload["count"] > 0
    assert any(item["metric"] == "stress_avg" for item in payload["anomalies"])


@pytest.mark.asyncio
async def test_get_lagged_health_correlations(app_with_analytics, analytics_history):
    """Test lagged correlation tool returns ranked relationships."""
    result = await app_with_analytics.call_tool(
        "get_lagged_health_correlations",
        {"end_date": "2024-02-04", "days": 35, "max_lag_days": 3},
    )
    payload = tool_json(result)

    assert payload["strongest"]
    assert payload["strongest"][0]["pairs"] >= 8


@pytest.mark.asyncio
async def test_get_weekly_health_review(app_with_analytics, analytics_history):
    """Test weekly review includes comparisons and notable signals."""
    result = await app_with_analytics.call_tool(
        "get_weekly_health_review",
        {"end_date": "2024-02-04"},
    )
    payload = tool_json(result)

    assert payload["window"] == {"start": "2024-01-29", "end": "2024-02-04", "days": 7}
    assert "comparisons" in payload
    assert payload["summary_notes"]


@pytest.mark.asyncio
async def test_custom_health_report_groups_metrics(app_with_analytics, analytics_history):
    """Test custom report tool groups selected metrics."""
    result = await app_with_analytics.call_tool(
        "run_custom_health_report",
        {
            "end_date": "2024-02-04",
            "days": 35,
            "metrics": "steps,sleep_score,recovery_pressure",
            "group_by": "week",
            "aggregation": "avg",
            "include_daily_rows": True,
        },
    )
    payload = tool_json(result)

    assert payload["columns"] == ["group", "days", "steps", "sleep_score", "recovery_pressure"]
    assert payload["rows"]
    assert payload["daily_rows"]
    assert payload["chart_hint"]["series"][0]["metric"] == "steps"


@pytest.mark.asyncio
async def test_saved_custom_health_report_round_trip(
    app_with_analytics, analytics_history, monkeypatch, tmp_path
):
    """Test saving, listing, and running a custom report definition."""
    monkeypatch.setenv("GARMIN_REPORTS_PATH", str(tmp_path / "reports.json"))

    save_result = await app_with_analytics.call_tool(
        "save_custom_health_report",
        {
            "name": "Recovery Watch",
            "description": "Weekly recovery report",
            "metrics": "overnight_hrv,resting_hr,recovery_pressure",
            "group_by": "week",
            "aggregation": "latest",
            "days": 35,
            "end_date": "2024-02-04",
        },
    )
    save_payload = tool_json(save_result)
    assert save_payload["saved"] is True

    list_result = await app_with_analytics.call_tool("list_saved_health_reports", {})
    list_payload = tool_json(list_result)
    assert list_payload["count"] == 1
    assert list_payload["reports"][0]["name"] == "Recovery Watch"

    run_result = await app_with_analytics.call_tool(
        "run_custom_health_report",
        {"saved_report_name": "Recovery Watch"},
    )
    run_payload = tool_json(run_result)
    assert run_payload["definition"]["name"] == "Recovery Watch"
    assert run_payload["definition"]["aggregation"] == "latest"
    assert run_payload["rows"]
