"""
Integration tests for health_wellness module MCP tools

Tests all 22 health and wellness tools using FastMCP integration with mocked Garmin API responses.
"""
import pytest
from unittest.mock import Mock
from mcp.server.fastmcp import FastMCP

from garmin_mcp import health_wellness
from tests.fixtures.garmin_responses import (
    MOCK_STATS,
    MOCK_USER_SUMMARY,
    MOCK_BODY_COMPOSITION,
    MOCK_STEPS_DATA,
    MOCK_DAILY_STEPS,
    MOCK_TRAINING_READINESS,
    MOCK_BODY_BATTERY,
    MOCK_BODY_BATTERY_EVENTS,
    MOCK_BLOOD_PRESSURE,
    MOCK_FLOORS,
    MOCK_RHR_DAY,
    MOCK_HEART_RATES,
    MOCK_HYDRATION_DATA,
    MOCK_SLEEP_DATA,
    MOCK_STRESS_DATA,
    MOCK_RESPIRATION_DATA,
    MOCK_SPO2_DATA,
)


@pytest.fixture
def app_with_health_wellness(mock_garmin_client):
    """Create FastMCP app with health_wellness tools registered"""
    health_wellness.configure(mock_garmin_client)
    app = FastMCP("Test Health Wellness")
    app = health_wellness.register_tools(app)
    return app


@pytest.mark.asyncio
async def test_get_stats_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_stats tool returns daily activity stats"""
    # Setup mock
    mock_garmin_client.get_stats.return_value = MOCK_STATS

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_stats",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_stats.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_user_summary_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_user_summary tool returns user summary data"""
    # Setup mock
    mock_garmin_client.get_user_summary.return_value = MOCK_USER_SUMMARY

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_user_summary",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_user_summary.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_body_composition_single_date(app_with_health_wellness, mock_garmin_client):
    """Test get_body_composition tool with single date"""
    # Setup mock
    mock_garmin_client.get_body_composition.return_value = MOCK_BODY_COMPOSITION

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_body_composition",
        {"start_date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_body_composition.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_body_composition_date_range(app_with_health_wellness, mock_garmin_client):
    """Test get_body_composition tool with date range"""
    # Setup mock
    mock_garmin_client.get_body_composition.return_value = MOCK_BODY_COMPOSITION

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_body_composition",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_body_composition.assert_called_once_with("2024-01-08", "2024-01-15")


@pytest.mark.asyncio
async def test_get_stats_and_body_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_stats_and_body tool returns combined data"""
    # Setup mock
    combined_data = {**MOCK_STATS, **MOCK_BODY_COMPOSITION}
    mock_garmin_client.get_stats_and_body.return_value = combined_data

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_stats_and_body",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_stats_and_body.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_steps_data_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_steps_data tool returns steps data"""
    # Setup mock
    mock_garmin_client.get_steps_data.return_value = MOCK_STEPS_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_steps_data",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_steps_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_daily_steps_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_daily_steps tool returns steps for date range"""
    # Setup mock
    mock_garmin_client.get_daily_steps.return_value = MOCK_DAILY_STEPS

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_daily_steps",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_daily_steps.assert_called_once_with("2024-01-08", "2024-01-15")


@pytest.mark.asyncio
async def test_get_training_readiness_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_training_readiness tool returns readiness data"""
    # Setup mock
    mock_garmin_client.get_training_readiness.return_value = MOCK_TRAINING_READINESS

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_training_readiness",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_training_readiness.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_body_battery_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_body_battery tool returns battery data"""
    # Setup mock
    mock_garmin_client.get_body_battery.return_value = MOCK_BODY_BATTERY

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_body_battery",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_body_battery.assert_called_once_with("2024-01-08", "2024-01-15")


@pytest.mark.asyncio
async def test_get_body_battery_events_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_body_battery_events tool returns battery events"""
    # Setup mock
    mock_garmin_client.get_body_battery_events.return_value = MOCK_BODY_BATTERY_EVENTS

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_body_battery_events",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_body_battery_events.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_blood_pressure_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_blood_pressure tool returns blood pressure data"""
    # Setup mock
    mock_garmin_client.get_blood_pressure.return_value = MOCK_BLOOD_PRESSURE

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_blood_pressure",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_blood_pressure.assert_called_once_with("2024-01-08", "2024-01-15")


@pytest.mark.asyncio
async def test_get_floors_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_floors tool returns floors climbed data"""
    # Setup mock
    mock_garmin_client.get_floors.return_value = MOCK_FLOORS

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_floors",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_floors.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_rhr_day_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_rhr_day tool returns resting heart rate"""
    # Setup mock
    mock_garmin_client.get_rhr_day.return_value = MOCK_RHR_DAY

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_rhr_day",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_rhr_day.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_heart_rates_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_heart_rates tool returns heart rate data"""
    # Setup mock
    mock_garmin_client.get_heart_rates.return_value = MOCK_HEART_RATES

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_heart_rates",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_heart_rates.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_heart_rates_summary_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_heart_rates_summary tool returns lightweight heart rate summary"""
    # Setup mock
    mock_garmin_client.get_heart_rates.return_value = MOCK_HEART_RATES

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_heart_rates_summary",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    # Note: get_heart_rates_summary calls get_heart_rates internally
    mock_garmin_client.get_heart_rates.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_hydration_data_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_hydration_data tool returns hydration data"""
    # Setup mock
    mock_garmin_client.get_hydration_data.return_value = MOCK_HYDRATION_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_hydration_data",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_hydration_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_sleep_data_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_sleep_data tool returns sleep data"""
    # Setup mock
    mock_garmin_client.get_sleep_data.return_value = MOCK_SLEEP_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_sleep_data",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_sleep_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_sleep_summary_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_sleep_summary tool returns lightweight sleep summary"""
    # Setup mock
    mock_garmin_client.get_sleep_data.return_value = MOCK_SLEEP_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_sleep_summary",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    # Note: get_sleep_summary calls get_sleep_data internally
    mock_garmin_client.get_sleep_data.assert_called_once_with("2024-01-15")

    # Verify it's a summary (smaller than full sleep data)
    # The summary should contain key metrics but not the full time-series data


@pytest.mark.asyncio
async def test_get_stress_data_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_stress_data tool returns stress data"""
    # Setup mock
    mock_garmin_client.get_stress_data.return_value = MOCK_STRESS_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_stress_data",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_stress_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_stress_summary_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_stress_summary tool returns lightweight stress summary"""
    # Setup mock
    mock_garmin_client.get_stress_data.return_value = MOCK_STRESS_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_stress_summary",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    # Note: get_stress_summary calls get_stress_data internally
    mock_garmin_client.get_stress_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_respiration_data_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_respiration_data tool returns respiration data"""
    # Setup mock
    mock_garmin_client.get_respiration_data.return_value = MOCK_RESPIRATION_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_respiration_data",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_respiration_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_respiration_summary_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_respiration_summary tool returns lightweight respiration summary"""
    # Setup mock
    mock_garmin_client.get_respiration_data.return_value = MOCK_RESPIRATION_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_respiration_summary",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    # Note: get_respiration_summary calls get_respiration_data internally
    mock_garmin_client.get_respiration_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_spo2_data_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_spo2_data tool returns SpO2 data"""
    # Setup mock
    mock_garmin_client.get_spo2_data.return_value = MOCK_SPO2_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_spo2_data",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_spo2_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_all_day_stress_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_all_day_stress tool returns all-day stress data"""
    # Setup mock
    mock_garmin_client.get_all_day_stress.return_value = MOCK_STRESS_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_all_day_stress",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_all_day_stress.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_all_day_events_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_all_day_events tool returns daily wellness events"""
    # Setup mock
    mock_events = {"events": [{"type": "STRESS", "timestamp": 1705276800000}]}
    mock_garmin_client.get_all_day_events.return_value = mock_events

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_all_day_events",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_all_day_events.assert_called_once_with("2024-01-15")


# Error handling tests
@pytest.mark.asyncio
async def test_get_steps_data_no_data(app_with_health_wellness, mock_garmin_client):
    """Test get_steps_data tool when no data is available"""
    # Setup mock to return None
    mock_garmin_client.get_steps_data.return_value = None

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_steps_data",
        {"date": "2024-01-15"}
    )

    # Verify error message is returned
    assert result is not None
    # The tool should return a helpful message when no data is found


@pytest.mark.asyncio
async def test_get_sleep_data_exception(app_with_health_wellness, mock_garmin_client):
    """Test get_sleep_data tool when API raises exception"""
    # Setup mock to raise exception
    mock_garmin_client.get_sleep_data.side_effect = Exception("API Error")

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_sleep_data",
        {"date": "2024-01-15"}
    )

    # Verify error is handled gracefully
    assert result is not None
    # The tool should return an error message, not crash
