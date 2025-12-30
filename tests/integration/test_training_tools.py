"""
Integration tests for training module MCP tools

Tests all 8 training tools using FastMCP integration with mocked Garmin API responses.
"""
import pytest
from unittest.mock import Mock
from mcp.server.fastmcp import FastMCP

from garmin_mcp import training
from tests.fixtures.garmin_responses import (
    MOCK_PROGRESS_SUMMARY,
    MOCK_HRV_DATA,
)


@pytest.fixture
def app_with_training(mock_garmin_client):
    """Create FastMCP app with training tools registered"""
    training.configure(mock_garmin_client)
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
    """Test get_endurance_score tool"""
    # Setup mock
    endurance_score = {
        "enduranceScore": 65,
        "dateRange": {"start": "2024-01-08", "end": "2024-01-15"}
    }
    mock_garmin_client.get_endurance_score.return_value = endurance_score

    # Call tool
    result = await app_with_training.call_tool(
        "get_endurance_score",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_endurance_score.assert_called_once_with("2024-01-08", "2024-01-15")


@pytest.mark.asyncio
async def test_get_training_effect_tool(app_with_training, mock_garmin_client):
    """Test get_training_effect tool"""
    # Setup mock
    training_effect = {
        "aerobicEffect": 3.5,
        "anaerobicEffect": 2.0,
        "activityId": 12345678901
    }
    mock_garmin_client.get_training_effect.return_value = training_effect

    # Call tool
    result = await app_with_training.call_tool(
        "get_training_effect",
        {"activity_id": 12345678901}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_training_effect.assert_called_once_with(12345678901)


@pytest.mark.asyncio
async def test_get_max_metrics_tool(app_with_training, mock_garmin_client):
    """Test get_max_metrics tool"""
    # Setup mock
    max_metrics = {
        "maxHeartRate": 180,
        "maxSpeed": 4.5,
        "maxPower": 350,
        "date": "2024-01-15"
    }
    mock_garmin_client.get_max_metrics.return_value = max_metrics

    # Call tool
    result = await app_with_training.call_tool(
        "get_max_metrics",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_max_metrics.assert_called_once_with("2024-01-15")


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
    # Setup mock to raise exception
    mock_garmin_client.get_training_effect.side_effect = Exception("API Error")

    # Call tool
    result = await app_with_training.call_tool(
        "get_training_effect",
        {"activity_id": 12345678901}
    )

    # Verify error is handled gracefully
    assert result is not None
