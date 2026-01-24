"""
Integration tests for activity_management module MCP tools

Tests all 10 activity management tools using FastMCP integration with mocked Garmin API responses.
"""
import pytest
from unittest.mock import Mock
from mcp.server.fastmcp import FastMCP

from garmin_mcp import activity_management
from tests.fixtures.garmin_responses import (
    MOCK_ACTIVITIES,
    MOCK_ACTIVITY_DETAILS,
    MOCK_ACTIVITY_SPLITS,
    MOCK_ACTIVITY_COUNT,
    MOCK_ACTIVITY_TYPES,
)


@pytest.fixture
def app_with_activity_management(mock_garmin_client):
    """Create FastMCP app with activity_management tools registered"""
    activity_management.configure(mock_garmin_client)
    app = FastMCP("Test Activity Management")
    app = activity_management.register_tools(app)
    return app


@pytest.mark.asyncio
async def test_get_activities_by_date_tool(app_with_activity_management, mock_garmin_client):
    """Test get_activities_by_date tool returns activities in date range"""
    # Setup mock
    mock_garmin_client.get_activities_by_date.return_value = MOCK_ACTIVITIES

    # Call tool
    result = await app_with_activity_management.call_tool(
        "get_activities_by_date",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_activities_by_date.assert_called_once_with("2024-01-08", "2024-01-15", "")


@pytest.mark.asyncio
async def test_get_activities_by_date_with_type(app_with_activity_management, mock_garmin_client):
    """Test get_activities_by_date tool with activity type filter"""
    # Setup mock
    filtered_activities = [MOCK_ACTIVITIES[0]]  # Only running activities
    mock_garmin_client.get_activities_by_date.return_value = filtered_activities

    # Call tool
    result = await app_with_activity_management.call_tool(
        "get_activities_by_date",
        {"start_date": "2024-01-08", "end_date": "2024-01-15", "activity_type": "running"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_activities_by_date.assert_called_once_with(
        "2024-01-08", "2024-01-15", "running"
    )


@pytest.mark.asyncio
async def test_get_activities_fordate_tool(app_with_activity_management, mock_garmin_client):
    """Test get_activities_fordate tool returns activities for specific date"""
    # Setup mock
    mock_garmin_client.get_activities_fordate.return_value = [MOCK_ACTIVITIES[0]]

    # Call tool
    result = await app_with_activity_management.call_tool(
        "get_activities_fordate",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_activities_fordate.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_activity_tool(app_with_activity_management, mock_garmin_client):
    """Test get_activity tool returns activity details by ID"""
    # Setup mock
    mock_garmin_client.get_activity.return_value = MOCK_ACTIVITY_DETAILS

    # Call tool
    activity_id = 12345678901
    result = await app_with_activity_management.call_tool(
        "get_activity",
        {"activity_id": activity_id}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_activity.assert_called_once_with(activity_id)


@pytest.mark.asyncio
async def test_get_activity_splits_tool(app_with_activity_management, mock_garmin_client):
    """Test get_activity_splits tool returns activity splits"""
    # Setup mock
    mock_garmin_client.get_activity_splits.return_value = MOCK_ACTIVITY_SPLITS

    # Call tool
    activity_id = 12345678901
    result = await app_with_activity_management.call_tool(
        "get_activity_splits",
        {"activity_id": activity_id}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_activity_splits.assert_called_once_with(activity_id)


@pytest.mark.asyncio
async def test_get_activity_typed_splits_tool(app_with_activity_management, mock_garmin_client):
    """Test get_activity_typed_splits tool returns typed splits"""
    # Setup mock
    typed_splits = {
        "runSplits": MOCK_ACTIVITY_SPLITS["lapDTOs"],
        "swimSplits": []
    }
    mock_garmin_client.get_activity_typed_splits.return_value = typed_splits

    # Call tool
    activity_id = 12345678901
    result = await app_with_activity_management.call_tool(
        "get_activity_typed_splits",
        {"activity_id": activity_id}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_activity_typed_splits.assert_called_once_with(activity_id)


@pytest.mark.asyncio
async def test_get_activity_split_summaries_tool(app_with_activity_management, mock_garmin_client):
    """Test get_activity_split_summaries tool returns split summaries"""
    # Setup mock
    split_summaries = {
        "totalDistance": 5000.0,
        "totalDuration": 1800.0,
        "avgSpeed": 2.78,
        "avgHR": 145
    }
    mock_garmin_client.get_activity_split_summaries.return_value = split_summaries

    # Call tool
    activity_id = 12345678901
    result = await app_with_activity_management.call_tool(
        "get_activity_split_summaries",
        {"activity_id": activity_id}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_activity_split_summaries.assert_called_once_with(activity_id)


@pytest.mark.asyncio
async def test_get_activity_weather_tool(app_with_activity_management, mock_garmin_client):
    """Test get_activity_weather tool returns weather data"""
    # Setup mock
    weather_data = {
        "temp": 18.0,
        "apparentTemp": 16.0,
        "dewPoint": 10.0,
        "relativeHumidity": 65,
        "windSpeed": 5.0,
        "windDirection": 180,
        "latitude": 40.7128,
        "longitude": -74.0060
    }
    mock_garmin_client.get_activity_weather.return_value = weather_data

    # Call tool
    activity_id = 12345678901
    result = await app_with_activity_management.call_tool(
        "get_activity_weather",
        {"activity_id": activity_id}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_activity_weather.assert_called_once_with(activity_id)


@pytest.mark.asyncio
async def test_get_activity_hr_in_timezones_tool(app_with_activity_management, mock_garmin_client):
    """Test get_activity_hr_in_timezones tool returns HR zone data"""
    # Setup mock
    hr_zones = {
        "zones": [
            {"zone": 1, "timeInZone": 300, "percentageInZone": 16.7},
            {"zone": 2, "timeInZone": 600, "percentageInZone": 33.3},
            {"zone": 3, "timeInZone": 900, "percentageInZone": 50.0}
        ]
    }
    mock_garmin_client.get_activity_hr_in_timezones.return_value = hr_zones

    # Call tool
    activity_id = 12345678901
    result = await app_with_activity_management.call_tool(
        "get_activity_hr_in_timezones",
        {"activity_id": activity_id}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_activity_hr_in_timezones.assert_called_once_with(activity_id)


@pytest.mark.asyncio
async def test_get_activity_gear_tool(app_with_activity_management, mock_garmin_client):
    """Test get_activity_gear tool returns gear data"""
    # Setup mock
    gear_data = {
        "gearId": 123,
        "displayName": "Running Shoes - Nike",
        "gearTypeName": "SHOE"
    }
    mock_garmin_client.get_activity_gear.return_value = gear_data

    # Call tool
    activity_id = 12345678901
    result = await app_with_activity_management.call_tool(
        "get_activity_gear",
        {"activity_id": activity_id}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_activity_gear.assert_called_once_with(activity_id)


@pytest.mark.asyncio
async def test_get_activity_exercise_sets_tool(app_with_activity_management, mock_garmin_client):
    """Test get_activity_exercise_sets tool returns exercise sets for strength training"""
    # Setup mock
    exercise_sets = {
        "exercises": [
            {
                "exerciseName": "Bench Press",
                "sets": [
                    {"setNumber": 1, "weight": 80.0, "reps": 10},
                    {"setNumber": 2, "weight": 80.0, "reps": 8},
                    {"setNumber": 3, "weight": 80.0, "reps": 6}
                ]
            }
        ]
    }
    mock_garmin_client.get_activity_exercise_sets.return_value = exercise_sets

    # Call tool
    activity_id = 12345678901
    result = await app_with_activity_management.call_tool(
        "get_activity_exercise_sets",
        {"activity_id": activity_id}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_activity_exercise_sets.assert_called_once_with(activity_id)


@pytest.mark.asyncio
async def test_count_activities_tool(app_with_activity_management, mock_garmin_client):
    """Test count_activities tool returns total activity count"""
    # Setup mock
    mock_garmin_client.count_activities.return_value = MOCK_ACTIVITY_COUNT

    # Call tool
    result = await app_with_activity_management.call_tool(
        "count_activities",
        {}
    )

    # Verify
    assert result is not None
    mock_garmin_client.count_activities.assert_called_once()


@pytest.mark.asyncio
async def test_get_activities_tool(app_with_activity_management, mock_garmin_client):
    """Test get_activities tool returns paginated activities"""
    # Setup mock
    mock_garmin_client.get_activities.return_value = MOCK_ACTIVITIES

    # Call tool
    result = await app_with_activity_management.call_tool(
        "get_activities",
        {"start": 0, "limit": 20}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_activities.assert_called_once_with(0, 20)


@pytest.mark.asyncio
async def test_get_activity_types_tool(app_with_activity_management, mock_garmin_client):
    """Test get_activity_types tool returns available activity types"""
    # Setup mock
    mock_garmin_client.get_activity_types.return_value = MOCK_ACTIVITY_TYPES

    # Call tool
    result = await app_with_activity_management.call_tool(
        "get_activity_types",
        {}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_activity_types.assert_called_once()


# Error handling tests
@pytest.mark.asyncio
async def test_get_activities_by_date_no_data(app_with_activity_management, mock_garmin_client):
    """Test get_activities_by_date tool when no activities found"""
    # Setup mock to return empty list
    mock_garmin_client.get_activities_by_date.return_value = []

    # Call tool
    result = await app_with_activity_management.call_tool(
        "get_activities_by_date",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify error message is returned
    assert result is not None
    # Should contain helpful message about no activities found


@pytest.mark.asyncio
async def test_get_activity_exception(app_with_activity_management, mock_garmin_client):
    """Test get_activity tool when API raises exception"""
    # Setup mock to raise exception
    mock_garmin_client.get_activity.side_effect = Exception("API Error")

    # Call tool
    result = await app_with_activity_management.call_tool(
        "get_activity",
        {"activity_id": 12345678901}
    )

    # Verify error is handled gracefully
    assert result is not None
    # Should return error message, not crash


@pytest.mark.asyncio
async def test_get_activity_not_found(app_with_activity_management, mock_garmin_client):
    """Test get_activity tool when activity doesn't exist"""
    # Setup mock to return None
    mock_garmin_client.get_activity.return_value = None

    # Call tool
    result = await app_with_activity_management.call_tool(
        "get_activity",
        {"activity_id": 99999999999}
    )

    # Verify helpful message is returned
    assert result is not None
    # Should indicate activity not found
