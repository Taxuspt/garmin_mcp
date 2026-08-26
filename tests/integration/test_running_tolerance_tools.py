"""
Integration tests for the Running Tolerance and acclimation MCP tools.

Covers get_running_tolerance and get_acclimation using FastMCP integration with
a mocked Garmin client. No real Garmin account or network access is used.
"""
import json

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import training


@pytest.fixture
def app_with_training(mock_garmin_client):
    """Create a FastMCP app with the training tools registered."""
    training.configure(mock_garmin_client)
    app = FastMCP("Test Training")
    app = training.register_tools(app)
    return app


def _result_text(result):
    """Extract the text payload from a FastMCP call_tool result."""
    return result[0][0].text


def _weekly(start_of_week, distance_m, impact, tolerance):
    return {
        "userProfilePK": 1,
        "calendarDate": start_of_week,
        "startOfWeek": start_of_week,
        "endOfWeek": start_of_week,
        "totalDistance": distance_m,
        "totalImpactLoad": impact,
        "tolerance": tolerance,
        "weekIndex": 1900,
    }


def _daily(date, distance_m, impact, tolerance, phrase="HIGH_LOAD"):
    return {
        "userProfilePK": 1,
        "calendarDate": date,
        "acuteDistance": distance_m,
        "acuteImpactLoad": impact,
        "acuteTolerance": tolerance,
        "runningToleranceFeedBackPhrase": phrase,
    }


@pytest.mark.asyncio
async def test_running_tolerance_weekly(app_with_training, mock_garmin_client):
    mock_garmin_client.get_running_tolerance.return_value = [
        _weekly("2026-08-16", 21829.0, 23660, 34571),
        _weekly("2026-08-09", 29400.0, 33710, 34630),
    ]

    result = await app_with_training.call_tool(
        "get_running_tolerance", {"start_date": "2026-08-09", "end_date": "2026-08-22"}
    )
    data = json.loads(_result_text(result))

    assert data["aggregation"] == "weekly"
    assert data["count"] == 2
    # sorted by week, oldest first, regardless of the order Garmin returned
    assert [e["week_of"] for e in data["entries"]] == ["2026-08-09", "2026-08-16"]

    latest = data["entries"][1]
    assert latest["distance_km"] == 21.83
    assert latest["impact_load"] == 23660
    assert latest["tolerance"] == 34571
    assert latest["pct_of_tolerance"] == 68  # 23660 / 34571


@pytest.mark.asyncio
async def test_running_tolerance_daily_carries_feedback(
    app_with_training, mock_garmin_client
):
    mock_garmin_client.get_running_tolerance.return_value = [
        _daily("2026-08-26", 33146.0, 37057, 34736, "ABOVE_TOLERANCE"),
    ]

    result = await app_with_training.call_tool(
        "get_running_tolerance",
        {"start_date": "2026-08-26", "end_date": "2026-08-26", "aggregation": "daily"},
    )
    data = json.loads(_result_text(result))

    entry = data["entries"][0]
    assert entry["date"] == "2026-08-26"
    assert entry["running_tolerance_feedback"] == "ABOVE_TOLERANCE"
    # over tolerance must read as over 100, not be clamped
    assert entry["pct_of_tolerance"] == 107


@pytest.mark.asyncio
async def test_running_tolerance_rejects_bad_aggregation(app_with_training):
    result = await app_with_training.call_tool(
        "get_running_tolerance",
        {"start_date": "2026-08-01", "end_date": "2026-08-26", "aggregation": "monthly"},
    )
    assert "Invalid aggregation" in _result_text(result)


@pytest.mark.asyncio
async def test_running_tolerance_survives_zero_tolerance(
    app_with_training, mock_garmin_client
):
    """A zero or missing tolerance must not raise ZeroDivisionError."""
    mock_garmin_client.get_running_tolerance.return_value = [
        _weekly("2026-07-26", 0.0, 0, 0),
        {"startOfWeek": "2026-08-02", "totalDistance": 9800.0},
    ]

    result = await app_with_training.call_tool(
        "get_running_tolerance", {"start_date": "2026-07-26", "end_date": "2026-08-08"}
    )
    data = json.loads(_result_text(result))

    assert data["count"] == 2
    assert "pct_of_tolerance" not in data["entries"][0]
    assert "pct_of_tolerance" not in data["entries"][1]
    assert data["entries"][1]["distance_km"] == 9.8


@pytest.mark.asyncio
async def test_running_tolerance_empty(app_with_training, mock_garmin_client):
    mock_garmin_client.get_running_tolerance.return_value = []
    result = await app_with_training.call_tool(
        "get_running_tolerance", {"start_date": "2026-08-01", "end_date": "2026-08-26"}
    )
    assert "No running tolerance data" in _result_text(result)


@pytest.mark.asyncio
async def test_acclimation(app_with_training, mock_garmin_client):
    mock_garmin_client.get_max_metrics.return_value = [
        {
            "heatAltitudeAcclimation": {
                "calendarDate": "2026-08-26",
                "heatAcclimationPercentage": 100,
                "previousHeatAcclimationPercentage": 88,
                "heatTrend": "ACCLIMATIZED",
                "heatAcclimationDate": "2026-08-25",
                "previousHeatAcclimationDate": "2026-08-15",
                "altitudeAcclimation": 0,
                "previousAltitudeAcclimation": 0,
                "altitudeTrend": None,
                "currentAltitude": 0,
            }
        }
    ]

    result = await app_with_training.call_tool("get_acclimation", {"date": "2026-08-26"})
    data = json.loads(_result_text(result))

    assert data["heat_acclimation_percent"] == 100
    assert data["heat_trend"] == "ACCLIMATIZED"
    assert data["heat_acclimation_change"] == 12
    # a null trend must be dropped rather than serialised as None
    assert "altitude_trend" not in data


@pytest.mark.asyncio
async def test_acclimation_absent(app_with_training, mock_garmin_client):
    """Garmin returns the envelope with no acclimation section for indoor-only users."""
    mock_garmin_client.get_max_metrics.return_value = [{"heatAltitudeAcclimation": None}]
    result = await app_with_training.call_tool("get_acclimation", {"date": "2026-08-26"})
    assert "No acclimation data found" in _result_text(result)
