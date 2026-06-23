"""
Integration tests for activity_management module MCP tools

Tests activity management tools using FastMCP integration with mocked Garmin API responses.
"""
import json
import pytest
from unittest.mock import Mock
from mcp.server.fastmcp import FastMCP

from garmin_mcp import activity_management
from tests.fixtures.garmin_responses import (
    MOCK_ACTIVITIES,
    MOCK_ACTIVITY_DETAILS,
    MOCK_ACTIVITY_SPLITS,
    MOCK_SWIM_ACTIVITY_SPLITS,
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
    # Tool now calls connectapi directly with explicit pagination params
    mock_garmin_client.connectapi.return_value = MOCK_ACTIVITIES

    result = await app_with_activity_management.call_tool(
        "get_activities_by_date",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    assert result is not None
    mock_garmin_client.connectapi.assert_called_once_with(
        mock_garmin_client.garmin_connect_activities,
        params={
            "startDate": "2024-01-08",
            "endDate": "2024-01-15",
            "start": "0",
            "limit": "100",
        },
    )


@pytest.mark.asyncio
async def test_get_activities_by_date_with_type(app_with_activity_management, mock_garmin_client):
    """Test get_activities_by_date tool with activity type filter"""
    filtered_activities = [MOCK_ACTIVITIES[0]]  # Only running activities
    mock_garmin_client.connectapi.return_value = filtered_activities

    result = await app_with_activity_management.call_tool(
        "get_activities_by_date",
        {"start_date": "2024-01-08", "end_date": "2024-01-15", "activity_type": "running"}
    )

    assert result is not None
    mock_garmin_client.connectapi.assert_called_once_with(
        mock_garmin_client.garmin_connect_activities,
        params={
            "startDate": "2024-01-08",
            "endDate": "2024-01-15",
            "start": "0",
            "limit": "100",
            "activityType": "running",
        },
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
async def test_get_activity_accepts_string_id(app_with_activity_management, mock_garmin_client):
    """Test get_activity accepts large IDs serialized as strings

    Some MCP clients stringify large numeric IDs in tool call arguments
    (Zod schema receives string instead of integer). The tool must coerce
    to int internally so the underlying Garmin call still gets an integer.
    Regression test for the 'Expected number, received string' bug.
    """
    mock_garmin_client.get_activity.return_value = MOCK_ACTIVITY_DETAILS

    result = await app_with_activity_management.call_tool(
        "get_activity",
        {"activity_id": "22833209898"}  # string, not int
    )

    assert result is not None
    # Library must have been called with an int, not a string
    mock_garmin_client.get_activity.assert_called_once_with(22833209898)


@pytest.mark.asyncio
async def test_set_activity_name_tool(app_with_activity_management, mock_garmin_client):
    """Test set_activity_name tool updates activity name"""
    activity_id = 12345678901
    mock_garmin_client.set_activity_name.return_value = {}

    result = await app_with_activity_management.call_tool(
        "set_activity_name",
        {"activity_id": activity_id, "activity_name": "Morning Run - Easy"},
    )

    assert result is not None
    mock_garmin_client.set_activity_name.assert_called_once_with(
        activity_id, "Morning Run - Easy"
    )

    data = json.loads(result[0][0].text)
    assert data["success"] is True
    assert data["activity_id"] == activity_id
    assert data["activity_name"] == "Morning Run - Easy"


@pytest.mark.asyncio
async def test_set_activity_name_tool_rejects_blank_name(
    app_with_activity_management, mock_garmin_client
):
    """Test set_activity_name tool rejects blank names"""
    result = await app_with_activity_management.call_tool(
        "set_activity_name",
        {"activity_id": 12345678901, "activity_name": "   "},
    )

    assert result is not None
    mock_garmin_client.set_activity_name.assert_not_called()
    assert result[0][0].text == "Activity name cannot be empty"


# ── Activity write tools ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_activity_type_tool(app_with_activity_management, mock_garmin_client):
    """Test set_activity_type resolves the type key and calls the library"""
    mock_garmin_client.get_activity_types.return_value = MOCK_ACTIVITY_TYPES
    mock_garmin_client.set_activity_type.return_value = {}

    result = await app_with_activity_management.call_tool(
        "set_activity_type",
        {"activity_id": 12345678901, "type_key": "hiking"},
    )

    mock_garmin_client.set_activity_type.assert_called_once_with(
        12345678901, 3, "hiking", 17
    )
    data = json.loads(result[0][0].text)
    assert data["success"] is True
    assert data["type_key"] == "hiking"


@pytest.mark.asyncio
async def test_set_activity_type_rejects_unknown_key(
    app_with_activity_management, mock_garmin_client
):
    """Test set_activity_type rejects an unknown type key without calling the API"""
    mock_garmin_client.get_activity_types.return_value = MOCK_ACTIVITY_TYPES

    result = await app_with_activity_management.call_tool(
        "set_activity_type",
        {"activity_id": 12345678901, "type_key": "moonwalking"},
    )

    mock_garmin_client.set_activity_type.assert_not_called()
    assert "Unknown activity type 'moonwalking'" in result[0][0].text


@pytest.mark.asyncio
async def test_set_activity_description_tool(
    app_with_activity_management, mock_garmin_client
):
    """Test set_activity_description PUTs the description in a partial DTO"""
    mock_garmin_client.garmin_connect_activity = "/activity-service/activity"
    mock_garmin_client.client.put.return_value = {}

    result = await app_with_activity_management.call_tool(
        "set_activity_description",
        {"activity_id": 12345678901, "description": "Felt strong. New shoes."},
    )

    mock_garmin_client.client.put.assert_called_once_with(
        "connectapi",
        "/activity-service/activity/12345678901",
        json={"activityId": 12345678901, "description": "Felt strong. New shoes."},
        api=True,
    )
    data = json.loads(result[0][0].text)
    assert data["success"] is True
    assert data["description"] == "Felt strong. New shoes."


@pytest.mark.asyncio
async def test_set_activity_event_type_tool(
    app_with_activity_management, mock_garmin_client
):
    """Test set_activity_event_type resolves the key and PUTs eventTypeDTO"""
    mock_garmin_client.garmin_connect_activity = "/activity-service/activity"
    mock_garmin_client.connectapi.return_value = [
        {"typeId": 1, "typeKey": "race", "sortOrder": 5},
        {"typeId": 4, "typeKey": "training", "sortOrder": 7},
    ]
    mock_garmin_client.client.put.return_value = {}

    result = await app_with_activity_management.call_tool(
        "set_activity_event_type",
        {"activity_id": 12345678901, "event_type": "race"},
    )

    mock_garmin_client.client.put.assert_called_once_with(
        "connectapi",
        "/activity-service/activity/12345678901",
        json={
            "activityId": 12345678901,
            "eventTypeDTO": {"typeId": 1, "typeKey": "race", "sortOrder": 5},
        },
        api=True,
    )
    data = json.loads(result[0][0].text)
    assert data["event_type"] == "race"


@pytest.mark.asyncio
async def test_set_activity_event_type_rejects_unknown(
    app_with_activity_management, mock_garmin_client
):
    """Test set_activity_event_type rejects an unknown event type"""
    mock_garmin_client.connectapi.return_value = [
        {"typeId": 1, "typeKey": "race", "sortOrder": 5},
    ]

    result = await app_with_activity_management.call_tool(
        "set_activity_event_type",
        {"activity_id": 12345678901, "event_type": "wedding"},
    )

    mock_garmin_client.client.put.assert_not_called()
    assert "Unknown event type 'wedding'" in result[0][0].text


@pytest.mark.asyncio
async def test_set_perceived_effort_tool(
    app_with_activity_management, mock_garmin_client
):
    """Test set_perceived_effort sends a partial summaryDTO with RPE×10"""
    mock_garmin_client.garmin_connect_activity = "/activity-service/activity"
    mock_garmin_client.client.put.return_value = {}

    result = await app_with_activity_management.call_tool(
        "set_perceived_effort",
        {"activity_id": 12345678901, "rpe": 7},
    )

    # Only the changed field is sent; Garmin merges it (7 -> 70)
    mock_garmin_client.client.put.assert_called_once_with(
        "connectapi",
        "/activity-service/activity/12345678901",
        json={
            "activityId": 12345678901,
            "summaryDTO": {"directWorkoutRpe": 70},
        },
        api=True,
    )
    data = json.loads(result[0][0].text)
    assert data["rpe"] == 7


@pytest.mark.asyncio
async def test_set_perceived_effort_rejects_out_of_range(
    app_with_activity_management, mock_garmin_client
):
    """Test set_perceived_effort rejects values outside 0-10"""
    result = await app_with_activity_management.call_tool(
        "set_perceived_effort",
        {"activity_id": 12345678901, "rpe": 11},
    )

    mock_garmin_client.client.put.assert_not_called()
    assert result[0][0].text == "rpe must be between 0 and 10"


@pytest.mark.asyncio
async def test_set_activity_feel_tool(app_with_activity_management, mock_garmin_client):
    """Test set_activity_feel sends a partial summaryDTO with the feel value"""
    mock_garmin_client.garmin_connect_activity = "/activity-service/activity"
    mock_garmin_client.client.put.return_value = {}

    result = await app_with_activity_management.call_tool(
        "set_activity_feel",
        {"activity_id": 12345678901, "feel": 75},
    )

    mock_garmin_client.client.put.assert_called_once_with(
        "connectapi",
        "/activity-service/activity/12345678901",
        json={
            "activityId": 12345678901,
            "summaryDTO": {"directWorkoutFeel": 75},
        },
        api=True,
    )
    data = json.loads(result[0][0].text)
    assert data["feel"] == 75


@pytest.mark.asyncio
async def test_set_activity_feel_rejects_invalid_value(
    app_with_activity_management, mock_garmin_client
):
    """Test set_activity_feel rejects values not on the 5-point scale"""
    result = await app_with_activity_management.call_tool(
        "set_activity_feel",
        {"activity_id": 12345678901, "feel": 60},
    )

    mock_garmin_client.client.put.assert_not_called()
    assert result[0][0].text == "feel must be one of 0, 25, 50, 75, 100"


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
async def test_get_activity_splits_elevation_fields(app_with_activity_management, mock_garmin_client):
    """Test get_activity_splits tool includes elevation gain and loss"""
    import json

    # Setup mock
    mock_garmin_client.get_activity_splits.return_value = MOCK_ACTIVITY_SPLITS

    # Call tool
    activity_id = 12345678901
    result = await app_with_activity_management.call_tool(
        "get_activity_splits",
        {"activity_id": activity_id}
    )

    # Parse and verify elevation fields
    data = json.loads(result[0][0].text)
    assert "laps" in data
    assert len(data["laps"]) == 2

    # First lap elevation
    assert data["laps"][0]["elevation_gain_meters"] == 25.5
    assert data["laps"][0]["elevation_loss_meters"] == 10.2

    # Second lap elevation
    assert data["laps"][1]["elevation_gain_meters"] == 15.0
    assert data["laps"][1]["elevation_loss_meters"] == 30.8


@pytest.mark.asyncio
async def test_get_activity_splits_includes_swim_lengths(app_with_activity_management, mock_garmin_client):
    """Test get_activity_splits tool preserves swim lap and nested length data."""
    import json

    mock_garmin_client.get_activity_splits.return_value = MOCK_SWIM_ACTIVITY_SPLITS

    result = await app_with_activity_management.call_tool(
        "get_activity_splits",
        {"activity_id": 22526515067}
    )

    data = json.loads(result[0][0].text)
    assert data["activity_id"] == 22526515067
    assert data["lap_count"] == 1
    assert data["laps"][0]["avg_swim_cadence"] == 22.0
    assert data["laps"][0]["active_length_count"] == 92
    assert data["laps"][0]["total_strokes"] == 1104
    assert data["laps"][0]["avg_strokes"] == 12.0
    assert data["laps"][0]["avg_swolf"] == 45.0
    assert data["laps"][0]["moving_duration_seconds"] == 2995.565
    assert data["laps"][0]["elapsed_duration_seconds"] == 2995.565
    assert data["laps"][0]["avg_moving_speed_mps"] == 0.67566553875138
    assert data["laps"][0]["bmr_calories"] == 85.0
    assert data["laps"][0]["avg_stroke_distance"] == 0.0
    assert data["laps"][0]["workout_step_index"] == 0
    assert len(data["laps"][0]["lengths"]) == 2
    assert data["laps"][0]["lengths"][0]["length_number"] == 1
    assert data["laps"][0]["lengths"][0]["distance_meters"] == 22.0
    assert data["laps"][0]["lengths"][0]["duration_seconds"] == 31.0
    assert data["laps"][0]["lengths"][0]["stroke"] == "FREESTYLE"


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


@pytest.mark.asyncio
async def test_get_activities_includes_event_type(app_with_activity_management, mock_garmin_client):
    """Test get_activities returns event_type field for each activity"""
    mock_garmin_client.get_activities.return_value = MOCK_ACTIVITIES

    result = await app_with_activity_management.call_tool(
        "get_activities",
        {"start": 0, "limit": 20}
    )

    data = json.loads(result[0][0].text)
    assert data["activities"][0]["event_type"] == "race"
    assert data["activities"][1]["event_type"] == "training"


@pytest.mark.asyncio
async def test_get_activities_by_date_includes_event_type(app_with_activity_management, mock_garmin_client):
    """Test get_activities_by_date returns event_type field for each activity"""
    mock_garmin_client.connectapi.return_value = MOCK_ACTIVITIES

    result = await app_with_activity_management.call_tool(
        "get_activities_by_date",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    data = json.loads(result[0][0].text)
    assert data["activities"][0]["event_type"] == "race"
    assert data["activities"][1]["event_type"] == "training"


@pytest.mark.asyncio
async def test_get_activities_omits_event_type_when_absent(app_with_activity_management, mock_garmin_client):
    """Test that event_type is omitted gracefully when not present in the API response"""
    activity_without_event_type = {
        "activityId": 99999,
        "activityName": "Old Activity",
        "activityType": {"typeKey": "running", "typeId": 1},
        "startTimeLocal": "2024-01-01 08:00:00",
        "duration": 1200.0,
    }
    mock_garmin_client.get_activities.return_value = [activity_without_event_type]

    result = await app_with_activity_management.call_tool(
        "get_activities",
        {"start": 0, "limit": 20}
    )

    data = json.loads(result[0][0].text)
    assert "event_type" not in data["activities"][0]


@pytest.mark.asyncio
async def test_get_activity_includes_event_type(app_with_activity_management, mock_garmin_client):
    """Test get_activity detail view returns event_type field"""
    mock_garmin_client.get_activity.return_value = MOCK_ACTIVITY_DETAILS

    result = await app_with_activity_management.call_tool(
        "get_activity",
        {"activity_id": 12345678901}
    )

    data = json.loads(result[0][0].text)
    assert data["event_type"] == "race"


@pytest.mark.asyncio
async def test_get_activity_includes_description(app_with_activity_management, mock_garmin_client):
    """Test get_activity surfaces the free-text description field.

    Regression: the detail view previously never extracted 'description', so a
    description set via set_activity_description could be written but not read
    back through this tool.
    """
    mock_garmin_client.get_activity.return_value = MOCK_ACTIVITY_DETAILS

    result = await app_with_activity_management.call_tool(
        "get_activity",
        {"activity_id": 12345678901}
    )

    data = json.loads(result[0][0].text)
    assert data["description"] == "Felt strong throughout. New shoes."


@pytest.mark.asyncio
async def test_get_activity_event_type_uses_event_type_dto(
    app_with_activity_management, mock_garmin_client
):
    """Test get_activity reads event type from eventTypeDTO, not eventType.

    Regression: the single-activity detail endpoint returns eventTypeDTO (the
    list endpoint returns eventType). The detail tool read the wrong key, so
    event_type was always missing from get_activity against the real API.
    """
    mock_garmin_client.get_activity.return_value = {
        "activityId": 1,
        "activityName": "Race",
        "eventTypeDTO": {"typeKey": "race", "typeId": 1},
        "eventType": None,  # the wrong key the detail API does not populate
        "summaryDTO": {},
    }

    result = await app_with_activity_management.call_tool(
        "get_activity",
        {"activity_id": 1}
    )

    data = json.loads(result[0][0].text)
    assert data["event_type"] == "race"


# Error handling tests
@pytest.mark.asyncio
async def test_get_activities_by_date_no_data(app_with_activity_management, mock_garmin_client):
    """Test get_activities_by_date tool when no activities found returns empty JSON"""
    mock_garmin_client.connectapi.return_value = []

    result = await app_with_activity_management.call_tool(
        "get_activities_by_date",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    assert result is not None
    data = json.loads(result[0][0].text)
    assert data["count"] == 0
    assert data["has_more"] is False
    assert data["activities"] == []


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


@pytest.mark.asyncio
async def test_set_activity_name_exception(app_with_activity_management, mock_garmin_client):
    """Test set_activity_name tool when API raises exception"""
    mock_garmin_client.set_activity_name.side_effect = Exception("API Error")

    result = await app_with_activity_management.call_tool(
        "set_activity_name",
        {"activity_id": 12345678901, "activity_name": "Morning Run"},
    )

    assert result is not None
    assert result[0][0].text == "Error updating activity name: API Error"


# ── Pagination tests ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_activities_by_date_pagination_metadata_first_page(
    app_with_activity_management, mock_garmin_client
):
    """Test that pagination metadata is included on the first page"""
    mock_garmin_client.connectapi.return_value = MOCK_ACTIVITIES

    result = await app_with_activity_management.call_tool(
        "get_activities_by_date",
        {"start_date": "2024-01-01", "end_date": "2024-01-31"}
    )

    data = json.loads(result[0][0].text)
    assert data["page"] == 0
    assert data["page_size"] == 100
    assert data["count"] == len(MOCK_ACTIVITIES)
    assert "date_range" in data
    assert data["date_range"]["start"] == "2024-01-01"
    assert data["date_range"]["end"] == "2024-01-31"


@pytest.mark.asyncio
async def test_get_activities_by_date_has_more_when_full_page(
    app_with_activity_management, mock_garmin_client
):
    """Test has_more=True and next_page present when a full page is returned"""
    # Return exactly page_size activities to simulate a full page
    page_size = 3
    full_page = [MOCK_ACTIVITIES[0], MOCK_ACTIVITIES[1], MOCK_ACTIVITIES[0]]
    mock_garmin_client.connectapi.return_value = full_page

    result = await app_with_activity_management.call_tool(
        "get_activities_by_date",
        {"start_date": "2024-01-01", "end_date": "2024-12-31", "page_size": page_size}
    )

    data = json.loads(result[0][0].text)
    assert data["has_more"] is True
    assert data["next_page"] == 1
    assert data["count"] == page_size


@pytest.mark.asyncio
async def test_get_activities_by_date_no_more_on_partial_page(
    app_with_activity_management, mock_garmin_client
):
    """Test has_more=False and no next_page when fewer than page_size results returned"""
    mock_garmin_client.connectapi.return_value = MOCK_ACTIVITIES  # 2 activities < page_size 100

    result = await app_with_activity_management.call_tool(
        "get_activities_by_date",
        {"start_date": "2024-01-01", "end_date": "2024-01-31"}
    )

    data = json.loads(result[0][0].text)
    assert data["has_more"] is False
    assert "next_page" not in data


@pytest.mark.asyncio
async def test_get_activities_by_date_second_page_offset(
    app_with_activity_management, mock_garmin_client
):
    """Test that page=1 sends the correct start offset to the Garmin API"""
    mock_garmin_client.connectapi.return_value = MOCK_ACTIVITIES

    await app_with_activity_management.call_tool(
        "get_activities_by_date",
        {"start_date": "2024-01-01", "end_date": "2024-12-31", "page": 1, "page_size": 50}
    )

    mock_garmin_client.connectapi.assert_called_once_with(
        mock_garmin_client.garmin_connect_activities,
        params={
            "startDate": "2024-01-01",
            "endDate": "2024-12-31",
            "start": "50",   # page=1 * page_size=50
            "limit": "50",
        },
    )


@pytest.mark.asyncio
async def test_get_activities_by_date_page_size_capped_at_200(
    app_with_activity_management, mock_garmin_client
):
    """Test that page_size is silently capped at 200"""
    mock_garmin_client.connectapi.return_value = MOCK_ACTIVITIES

    await app_with_activity_management.call_tool(
        "get_activities_by_date",
        {"start_date": "2024-01-01", "end_date": "2024-12-31", "page_size": 999}
    )

    call_params = mock_garmin_client.connectapi.call_args[1]["params"]
    assert call_params["limit"] == "200"


@pytest.mark.asyncio
async def test_get_activities_by_date_default_page_size_is_100(
    app_with_activity_management, mock_garmin_client
):
    """Test that the default page_size of 100 is used when not specified"""
    mock_garmin_client.connectapi.return_value = MOCK_ACTIVITIES

    await app_with_activity_management.call_tool(
        "get_activities_by_date",
        {"start_date": "2024-01-01", "end_date": "2024-01-31"}
    )

    call_params = mock_garmin_client.connectapi.call_args[1]["params"]
    assert call_params["limit"] == "100"
    assert call_params["start"] == "0"


# --- create_manual_activity --------------------------------------------------

@pytest.mark.asyncio
async def test_create_manual_activity_success(app_with_activity_management, mock_garmin_client):
    """Test create_manual_activity returns success with the API response."""
    mock_garmin_client.create_manual_activity.return_value = {"activityId": 999}

    result = await app_with_activity_management.call_tool(
        "create_manual_activity",
        {
            "type_key": "yoga",
            "date": "2024-03-01",
            "duration_minutes": 60,
        },
    )

    assert result is not None
    data = json.loads(result[0][0].text)
    assert data["success"] is True
    assert data["activity"] == {"activityId": 999}

    mock_garmin_client.create_manual_activity.assert_called_once_with(
        start_datetime="2024-03-01T09:00:00.000",
        time_zone="UTC",
        type_key="yoga",
        distance_km=0.0,
        duration_min=60,
        activity_name="Yoga",
    )


@pytest.mark.asyncio
async def test_create_manual_activity_custom_fields(app_with_activity_management, mock_garmin_client):
    """Test create_manual_activity forwards all optional fields."""
    mock_garmin_client.create_manual_activity.return_value = {"activityId": 123}

    await app_with_activity_management.call_tool(
        "create_manual_activity",
        {
            "type_key": "strength_training",
            "date": "2024-03-15",
            "duration_minutes": 45,
            "start_time": "07:30",
            "activity_name": "Morning Weights",
            "distance_km": 0.0,
            "time_zone": "Europe/Lisbon",
        },
    )

    mock_garmin_client.create_manual_activity.assert_called_once_with(
        start_datetime="2024-03-15T07:30:00.000",
        time_zone="Europe/Lisbon",
        type_key="strength_training",
        distance_km=0.0,
        duration_min=45,
        activity_name="Morning Weights",
    )


@pytest.mark.asyncio
async def test_create_manual_activity_default_name_from_type_key(
    app_with_activity_management, mock_garmin_client
):
    """Test that activity_name defaults to a prettified type_key when omitted."""
    mock_garmin_client.create_manual_activity.return_value = {}

    await app_with_activity_management.call_tool(
        "create_manual_activity",
        {"type_key": "indoor_cycling", "date": "2024-04-01", "duration_minutes": 30},
    )

    call_kwargs = mock_garmin_client.create_manual_activity.call_args[1]
    assert call_kwargs["activity_name"] == "Indoor Cycling"


@pytest.mark.asyncio
async def test_create_manual_activity_rejects_zero_duration(
    app_with_activity_management, mock_garmin_client
):
    """Test that duration_minutes <= 0 is rejected before calling the API."""
    result = await app_with_activity_management.call_tool(
        "create_manual_activity",
        {"type_key": "yoga", "date": "2024-03-01", "duration_minutes": 0},
    )
    assert "Error" in result[0][0].text
    mock_garmin_client.create_manual_activity.assert_not_called()


@pytest.mark.asyncio
async def test_create_manual_activity_rejects_empty_type_key(
    app_with_activity_management, mock_garmin_client
):
    """Test that an empty type_key is rejected before calling the API."""
    result = await app_with_activity_management.call_tool(
        "create_manual_activity",
        {"type_key": "  ", "date": "2024-03-01", "duration_minutes": 30},
    )
    assert "Error" in result[0][0].text
    mock_garmin_client.create_manual_activity.assert_not_called()


@pytest.mark.asyncio
async def test_create_manual_activity_exception(app_with_activity_management, mock_garmin_client):
    """Test that API exceptions are returned as error strings."""
    mock_garmin_client.create_manual_activity.side_effect = Exception("Garmin API error")

    result = await app_with_activity_management.call_tool(
        "create_manual_activity",
        {"type_key": "yoga", "date": "2024-03-01", "duration_minutes": 60},
    )
    assert "Error" in result[0][0].text
    assert "Garmin API error" in result[0][0].text
