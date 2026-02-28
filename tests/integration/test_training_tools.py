"""
Integration tests for training module MCP tools

Tests all 8 training tools using FastMCP integration with mocked Garmin API responses.
"""
import pytest
from unittest.mock import Mock
from mcp.server.fastmcp import FastMCP
from garmin_mcp.client_resolver import set_global_client

import json

from garmin_mcp import training
from tests.fixtures.garmin_responses import (
    MOCK_PROGRESS_SUMMARY,
    MOCK_HRV_DATA,
    MOCK_TRAINING_STATUS,
    MOCK_LACTATE_THRESHOLD,
    MOCK_LACTATE_THRESHOLD_RANGE,
    MOCK_ENDURANCE_SCORE,
    MOCK_ACTIVITY_TYPES,
)


@pytest.fixture
def app_with_training(mock_garmin_client):
    """Create FastMCP app with training tools registered"""
    training.configure(mock_garmin_client)
    set_global_client(mock_garmin_client)
    app = FastMCP("Test Training")
    app = training.register_tools(app)
    return app


@pytest.mark.asyncio
async def test_get_progress_summary_between_dates_tool(app_with_training, mock_garmin_client):
    """Test get_progress_summary_between_dates tool"""
    # Setup mock
    mock_garmin_client.get_progress_summary_between_dates.return_value = MOCK_PROGRESS_SUMMARY

    # Call tool
    result = await app_with_training.call_tool(
        "get_progress_summary_between_dates",
        {
            "start_date": "2024-01-08",
            "end_date": "2024-01-15",
            "metric": "duration"
        }
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_progress_summary_between_dates.assert_called_once_with(
        "2024-01-08", "2024-01-15", "duration"
    )


@pytest.mark.asyncio
async def test_get_hill_score_tool(app_with_training, mock_garmin_client):
    """Test get_hill_score tool"""
    # Setup mock
    hill_score = {
        "hillScore": 75,
        "dateRange": {"start": "2024-01-08", "end": "2024-01-15"}
    }
    mock_garmin_client.get_hill_score.return_value = hill_score

    # Call tool
    result = await app_with_training.call_tool(
        "get_hill_score",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_hill_score.assert_called_once_with("2024-01-08", "2024-01-15")


@pytest.mark.asyncio
async def test_get_endurance_score_tool(app_with_training, mock_garmin_client):
    """Test get_endurance_score tool with realistic API response"""
    # Setup mocks
    mock_garmin_client.get_endurance_score.return_value = MOCK_ENDURANCE_SCORE
    mock_garmin_client.get_activity_types.return_value = MOCK_ACTIVITY_TYPES

    # Reset the activity type cache to ensure fresh lookup
    training._activity_type_cache = None

    # Call tool
    result = await app_with_training.call_tool(
        "get_endurance_score",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify API was called correctly
    assert result is not None
    mock_garmin_client.get_endurance_score.assert_called_once_with("2024-01-08", "2024-01-15")

    # Parse the result and verify content
    data = json.loads(result[0][0].text)

    # Check period summary
    assert data["period_avg_score"] == 5631
    assert data["period_max_score"] == 5740

    # Check current score
    assert data["current_score"] == 5712
    assert data["current_date"] == "2024-01-15"
    assert data["classification"] == "intermediate"
    assert data["classification_id"] == 2

    # Check thresholds
    assert "thresholds" in data
    assert data["thresholds"]["trained"] == 5800
    assert data["thresholds"]["well_trained"] == 6500

    # Check contributors have activity type names
    assert "contributors" in data
    contributors = data["contributors"]
    assert len(contributors) == 4

    # Find the hiking contributor
    hiking_contributor = next(
        (c for c in contributors if c.get("activity_type") == "hiking"), None
    )
    assert hiking_contributor is not None
    assert hiking_contributor["contribution_percent"] == 5.49
    assert hiking_contributor["activity_type_id"] == 3

    # Find the yoga contributor
    yoga_contributor = next(
        (c for c in contributors if c.get("activity_type") == "yoga"), None
    )
    assert yoga_contributor is not None
    assert yoga_contributor["contribution_percent"] == 3.13

    # Check weekly breakdown exists
    assert "weekly_breakdown" in data
    assert len(data["weekly_breakdown"]) == 1
    week = data["weekly_breakdown"][0]
    assert week["week_start"] == "2024-01-08"
    assert week["avg_score"] == 5548
    assert week["max_score"] == 5561


@pytest.mark.asyncio
async def test_get_training_effect_tool(app_with_training, mock_garmin_client):
    """Test get_training_effect tool"""
    # Setup mock - get_training_effect uses get_activity internally
    activity_data = {
        "summaryDTO": {
            "trainingEffect": 3.5,
            "anaerobicTrainingEffect": 2.0,
            "trainingEffectLabel": "Highly Improving",
            "activityTrainingLoad": 150,
            "recoveryTime": 720,  # 12 hours in minutes
            "performanceCondition": 95,
        }
    }
    mock_garmin_client.get_activity.return_value = activity_data

    # Call tool
    result = await app_with_training.call_tool(
        "get_training_effect",
        {"activity_id": 12345678901}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_activity.assert_called_once_with(12345678901)


@pytest.mark.asyncio
async def test_get_hrv_data_tool(app_with_training, mock_garmin_client):
    """Test get_hrv_data tool"""
    # Setup mock
    mock_garmin_client.get_hrv_data.return_value = MOCK_HRV_DATA

    # Call tool
    result = await app_with_training.call_tool(
        "get_hrv_data",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_hrv_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_fitnessage_data_tool(app_with_training, mock_garmin_client):
    """Test get_fitnessage_data tool"""
    # Setup mock
    fitness_age = {
        "fitnessAge": 25,
        "chronologicalAge": 30,
        "vo2Max": 52.5,
        "date": "2024-01-15"
    }
    mock_garmin_client.get_fitnessage_data.return_value = fitness_age

    # Call tool
    result = await app_with_training.call_tool(
        "get_fitnessage_data",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_fitnessage_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_request_reload_tool(app_with_training, mock_garmin_client):
    """Test request_reload tool"""
    # Setup mock
    reload_response = {"status": "success", "message": "Data reload requested"}
    mock_garmin_client.request_reload.return_value = reload_response

    # Call tool
    result = await app_with_training.call_tool(
        "request_reload",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.request_reload.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_training_status_tool(app_with_training, mock_garmin_client):
    """Test get_training_status tool returns training status"""
    # Setup mock
    mock_garmin_client.get_training_status.return_value = MOCK_TRAINING_STATUS

    # Call tool
    result = await app_with_training.call_tool(
        "get_training_status",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_training_status.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_lactate_threshold_tool_latest(app_with_training, mock_garmin_client):
    """Test get_lactate_threshold tool returns latest lactate threshold data"""
    # Setup mock with latest=True response format
    mock_garmin_client.get_lactate_threshold.return_value = MOCK_LACTATE_THRESHOLD

    # Call tool with no dates (gets latest)
    result = await app_with_training.call_tool(
        "get_lactate_threshold",
        {}
    )

    # Verify API call
    assert result is not None
    mock_garmin_client.get_lactate_threshold.assert_called_once_with(latest=True)

    # Verify output structure
    data = json.loads(result[0][0].text)
    assert data["lactate_threshold_speed_mps"] == 0.32222132
    assert data["lactate_threshold_heart_rate_bpm"] == 169
    assert data["functional_threshold_power_watts"] == 334
    assert data["sport"] == "RUNNING"
    assert data["power_to_weight"] == 4.575


@pytest.mark.asyncio
async def test_get_lactate_threshold_tool_range(app_with_training, mock_garmin_client):
    """Test get_lactate_threshold tool returns lactate threshold data for date range"""
    # Setup mock with date range response format
    mock_garmin_client.get_lactate_threshold.return_value = MOCK_LACTATE_THRESHOLD_RANGE

    # Call tool with date range
    result = await app_with_training.call_tool(
        "get_lactate_threshold",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify API call
    assert result is not None
    mock_garmin_client.get_lactate_threshold.assert_called_once_with(
        latest=False,
        start_date="2024-01-08",
        end_date="2024-01-15",
    )

    # Verify output structure
    data = json.loads(result[0][0].text)
    assert data["start_date"] == "2024-01-08"
    assert data["end_date"] == "2024-01-15"
    assert "speed_history" in data
    assert len(data["speed_history"]) == 3
    assert data["speed_history"][0]["date"] == "2024-01-08"
    assert "heart_rate_history" in data
    assert len(data["heart_rate_history"]) == 3
    assert "power_history" in data


# Error handling tests
@pytest.mark.asyncio
async def test_get_hrv_data_no_data(app_with_training, mock_garmin_client):
    """Test get_hrv_data tool when no data available"""
    # Setup mock to return None
    mock_garmin_client.get_hrv_data.return_value = None

    # Call tool
    result = await app_with_training.call_tool(
        "get_hrv_data",
        {"date": "2024-01-15"}
    )

    # Verify error message is returned
    assert result is not None


@pytest.mark.asyncio
async def test_get_training_effect_exception(app_with_training, mock_garmin_client):
    """Test get_training_effect tool when API raises exception"""
    # Setup mock to raise exception - get_training_effect uses get_activity internally
    mock_garmin_client.get_activity.side_effect = Exception("API Error")

    # Call tool
    result = await app_with_training.call_tool(
        "get_training_effect",
        {"activity_id": 12345678901}
    )

    # Verify error is handled gracefully
    assert result is not None
