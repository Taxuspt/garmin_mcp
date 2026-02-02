"""
Integration tests for workouts module MCP tools

Tests workout tools using FastMCP integration with mocked Garmin API responses.
"""
import pytest
from unittest.mock import Mock
from mcp.server.fastmcp import FastMCP

from garmin_mcp import workouts
from tests.fixtures.garmin_responses import (
    MOCK_WORKOUTS,
    MOCK_WORKOUT_DETAILS,
)


@pytest.fixture
def app_with_workouts(mock_garmin_client):
    """Create FastMCP app with workouts tools registered"""
    workouts.configure(mock_garmin_client)
    app = FastMCP("Test Workouts")
    app = workouts.register_tools(app)
    return app


@pytest.mark.asyncio
async def test_get_workouts_tool(app_with_workouts, mock_garmin_client):
    """Test get_workouts tool returns all workouts"""
    # Setup mock
    mock_garmin_client.get_workouts.return_value = MOCK_WORKOUTS

    # Call tool
    result = await app_with_workouts.call_tool(
        "get_workouts",
        {}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_workouts.assert_called_once()


@pytest.mark.asyncio
async def test_get_workout_by_id_tool(app_with_workouts, mock_garmin_client):
    """Test get_workout_by_id tool returns specific workout with step details (numeric ID)"""
    import json as json_module

    # Setup mock
    mock_garmin_client.get_workout_by_id.return_value = MOCK_WORKOUT_DETAILS

    # Call tool with numeric ID (FastMCP passes numeric strings as int)
    workout_id = 123456
    result = await app_with_workouts.call_tool(
        "get_workout_by_id",
        {"workout_id": workout_id}
    )

    # Verify - tool converts to int for numeric IDs
    assert result is not None
    mock_garmin_client.get_workout_by_id.assert_called_once_with(123456)

    # Parse the result and verify curation includes steps
    result_data = json_module.loads(result[0].text)
    assert result_data["id"] == 123456
    assert result_data["name"] == "5K Tempo Run"
    assert result_data["sport"] == "running"

    # Verify segments include steps
    assert "segments" in result_data
    segment = result_data["segments"][0]
    assert "steps" in segment
    assert segment["step_count"] == 3

    # Verify step details are curated correctly
    warmup_step = segment["steps"][0]
    assert warmup_step["type"] == "warmup"
    assert warmup_step["end_condition"] == "time"
    assert warmup_step["end_condition_value"] == 600.0

    # Verify interval step with target zone
    interval_step = segment["steps"][1]
    assert interval_step["type"] == "interval"
    assert interval_step["target_type"] == "pace.zone"
    assert interval_step["target_zone"] == 4


@pytest.mark.asyncio
async def test_get_workout_by_uuid_tool(app_with_workouts, mock_garmin_client):
    """Test get_workout_by_id tool with UUID (training plan workout)"""
    import json as json_module

    # Setup mock for garth.get call (fbt-adaptive endpoint)
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "workoutId": None,
        "workoutUuid": "d7a5491b-42a5-4d2d-ba38-4e414fc03caf",
        "workoutName": "Base",
        "description": "6:20/km",
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "estimatedDurationInSecs": 2160,
        "workoutPhrase": "AEROBIC_LOW_SHORTAGE_BASE",
        "trainingEffectLabel": "AEROBIC_BASE",
        "estimatedTrainingEffect": 2.3,
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": [{
                "type": "ExecutableStepDTO",
                "stepOrder": 1,
                "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                "endConditionValue": 2160.0,
                "targetType": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"},
                "targetValueOne": 2.777,
                "targetValueTwo": 2.472
            }]
        }]
    }
    mock_garmin_client.garth.get.return_value = mock_response

    # Call tool with UUID (contains dashes)
    workout_uuid = "d7a5491b-42a5-4d2d-ba38-4e414fc03caf"
    result = await app_with_workouts.call_tool(
        "get_workout_by_id",
        {"workout_id": workout_uuid}
    )

    # Verify fbt-adaptive endpoint was called
    assert result is not None
    mock_garmin_client.garth.get.assert_called_once_with(
        "connectapi",
        f"workout-service/fbt-adaptive/{workout_uuid}"
    )

    # Parse the result and verify training plan workout fields
    result_data = json_module.loads(result[0].text)
    assert result_data["uuid"] == workout_uuid
    assert result_data["name"] == "Base"
    assert result_data["sport"] == "running"
    assert result_data["workout_type"] == "AEROBIC_LOW_SHORTAGE_BASE"
    assert result_data["training_effect_label"] == "AEROBIC_BASE"
    assert result_data["estimated_training_effect"] == 2.3
    assert result_data["estimated_duration_seconds"] == 2160

    # Verify segments include steps
    assert "segments" in result_data
    segment = result_data["segments"][0]
    assert "steps" in segment
    assert segment["step_count"] == 1


@pytest.mark.asyncio
async def test_download_workout_tool(app_with_workouts, mock_garmin_client):
    """Test download_workout tool downloads workout data"""
    # Setup mock
    workout_data = {
        "workoutId": 123456,
        "workoutName": "5K Tempo Run",
        "data": "...workout file content..."
    }
    mock_garmin_client.download_workout.return_value = workout_data

    # Call tool
    workout_id = 123456
    result = await app_with_workouts.call_tool(
        "download_workout",
        {"workout_id": workout_id}
    )

    # Verify
    assert result is not None
    mock_garmin_client.download_workout.assert_called_once_with(workout_id)


@pytest.mark.asyncio
async def test_upload_workout_tool(app_with_workouts, mock_garmin_client):
    """Test upload_workout tool uploads new workout"""
    # Setup mock
    upload_response = {
        "workoutId": 123457,
        "workoutName": "New Workout"
    }
    mock_garmin_client.upload_workout.return_value = upload_response

    # Call tool - pass dict which is passed directly to API
    workout_data = {"workoutName": "New Workout", "sportType": {"sportTypeId": 1}}
    result = await app_with_workouts.call_tool(
        "upload_workout",
        {"workout_data": workout_data}
    )

    # Verify - dict is passed directly to the API
    assert result is not None
    mock_garmin_client.upload_workout.assert_called_once_with(workout_data)


@pytest.mark.asyncio
async def test_get_scheduled_workouts_tool(app_with_workouts, mock_garmin_client):
    """Test get_scheduled_workouts tool - uses GraphQL query"""
    import json as json_module

    # Setup mock for GraphQL query - matches actual API response structure
    graphql_response = {
        "data": {
            "workoutScheduleSummariesScalar": [
                {
                    "workoutUuid": "abc-123-def",
                    "workoutId": 123456,
                    "workoutName": "5K Tempo Run",
                    "workoutType": "running",
                    "scheduleDate": "2024-01-15",
                    "tpPlanName": "5K Training Plan",
                    "associatedActivityId": None,
                    "estimatedDurationInSecs": 1800,
                    "estimatedDistanceInMeters": 5000.0
                }
            ]
        }
    }
    mock_garmin_client.query_garmin_graphql.return_value = graphql_response

    # Call tool
    result = await app_with_workouts.call_tool(
        "get_scheduled_workouts",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify curation extracts correct fields
    result_data = json_module.loads(result[0].text)
    assert result_data["count"] == 1
    workout = result_data["scheduled_workouts"][0]
    assert workout["name"] == "5K Tempo Run"
    assert workout["sport"] == "running"
    assert workout["completed"] is False
    assert workout["training_plan"] == "5K Training Plan"
    assert workout["estimated_duration_seconds"] == 1800

    # Verify
    assert result is not None
    mock_garmin_client.query_garmin_graphql.assert_called_once()


@pytest.mark.asyncio
async def test_get_training_plan_workouts_tool(app_with_workouts, mock_garmin_client):
    """Test get_training_plan_workouts tool - uses GraphQL query"""
    import json as json_module

    # Setup mock for GraphQL query - matches actual API response structure
    graphql_response = {
        "data": {
            "trainingPlanScalar": {
                "trainingPlanWorkoutScheduleDTOS": [
                    {
                        "planName": "5K Training Plan",
                        "trainingPlanDetailsDTO": {
                            "athletePlanId": 12345,
                            "workoutsPerWeek": 4
                        },
                        "workoutScheduleSummaries": [
                            {
                                "workoutUuid": "abc-123-def",
                                "workoutId": None,
                                "workoutName": "Base Run",
                                "workoutType": "running",
                                "scheduleDate": "2024-01-15",
                                "tpPlanName": "5K Training Plan",
                                "associatedActivityId": None,
                                "estimatedDurationInSecs": 1800
                            },
                            {
                                "workoutUuid": "xyz-456-ghi",
                                "workoutId": None,
                                "workoutName": "Strength",
                                "workoutType": "strength_training",
                                "scheduleDate": "2024-01-15",
                                "tpPlanName": "5K Training Plan",
                                "associatedActivityId": 987654,
                                "estimatedDurationInSecs": 1200
                            }
                        ]
                    }
                ]
            }
        }
    }
    mock_garmin_client.query_garmin_graphql.return_value = graphql_response

    # Call tool
    result = await app_with_workouts.call_tool(
        "get_training_plan_workouts",
        {"calendar_date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.query_garmin_graphql.assert_called_once()

    # Verify curation extracts correct fields
    result_data = json_module.loads(result[0].text)
    assert result_data["date"] == "2024-01-15"
    assert result_data["training_plans"] == ["5K Training Plan"]
    assert result_data["count"] == 2

    # Verify workouts are curated correctly
    workouts = result_data["workouts"]
    assert workouts[0]["name"] == "Base Run"
    assert workouts[0]["sport"] == "running"
    assert workouts[0]["completed"] is False

    # Verify completed workout has activity_id
    assert workouts[1]["name"] == "Strength"
    assert workouts[1]["completed"] is True
    assert workouts[1]["activity_id"] == 987654


# Error handling tests
@pytest.mark.asyncio
async def test_get_workouts_no_data(app_with_workouts, mock_garmin_client):
    """Test get_workouts tool when no workouts found"""
    # Setup mock to return None
    mock_garmin_client.get_workouts.return_value = None

    # Call tool
    result = await app_with_workouts.call_tool(
        "get_workouts",
        {}
    )

    # Verify error message is returned
    assert result is not None


@pytest.mark.asyncio
async def test_upload_workout_exception(app_with_workouts, mock_garmin_client):
    """Test upload_workout tool when upload fails"""
    # Setup mock to raise exception
    mock_garmin_client.upload_workout.side_effect = Exception("Upload failed")

    # Call tool with valid workout data
    result = await app_with_workouts.call_tool(
        "upload_workout",
        {"workout_data": {}}
    )

    # Verify error is handled gracefully
    assert result is not None
