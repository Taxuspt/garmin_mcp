"""
Integration tests for workouts module MCP tools

Tests workout tools using FastMCP integration with mocked Garmin API responses.
"""
import pytest
from unittest.mock import Mock
from mcp.server.fastmcp import FastMCP

from garmin_mcp import workouts
from garmin_mcp.workouts import _fix_repeat_group_step
from tests.fixtures.garmin_responses import (
    MOCK_WORKOUTS,
    MOCK_WORKOUT_DETAILS,
    MOCK_SWIM_WORKOUT_DETAILS,
)


@pytest.fixture
def app_with_workouts(mock_garmin_client):
    """Create FastMCP app with workouts tools registered"""
    # Default: pre-check used by schedule_* tools sees no existing schedule
    # so the POST path runs as before. Individual tests override this.
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {"workoutScheduleSummariesScalar": []}
    }
    workouts.configure(mock_garmin_client)
    app = FastMCP("Test Workouts")
    app = workouts.register_tools(app)
    return app


def _running_workout_with_steps(steps, name="Validation Workout"):
    return {
        "workoutName": name,
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": steps,
        }],
    }


def _timed_interval_step(target_type):
    return {
        "type": "ExecutableStepDTO",
        "stepOrder": 1,
        "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
        "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
        "endConditionValue": 300,
        "targetType": target_type,
        "targetValueOne": 143,
        "targetValueTwo": 157,
    }



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
    result_data = json_module.loads(result[0][0].text)
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
async def test_get_workout_by_id_tool_handles_swim_secondary_targets(
    app_with_workouts, mock_garmin_client
):
    """Test swim workouts with null primary targetType still expose secondary pace targets."""
    import json as json_module

    mock_garmin_client.get_workout_by_id.return_value = MOCK_SWIM_WORKOUT_DETAILS

    result = await app_with_workouts.call_tool(
        "get_workout_by_id",
        {"workout_id": 1528077786}
    )

    result_data = json_module.loads(result[0][0].text)
    assert result_data["id"] == 1528077786
    assert result_data["sport"] == "swimming"
    assert result_data["estimated_distance_meters"] == 3000.0

    segment = result_data["segments"][0]
    assert segment["step_count"] == 2

    warmup_step = segment["steps"][0]
    assert warmup_step["type"] == "warmup"
    assert warmup_step["secondary_target_type"] == "pace.zone"
    assert warmup_step["secondary_target_value_low"] == 0.45
    assert warmup_step["secondary_target_value_high"] == 0.6916667
    assert "target_type" not in warmup_step

    repeat_step = segment["steps"][1]
    assert repeat_step["type"] == "repeat"
    assert repeat_step["repeat_count"] == 2
    assert repeat_step["step_count"] == 2

    interval_step = repeat_step["steps"][0]
    assert interval_step["type"] == "interval"
    assert interval_step["secondary_target_type"] == "pace.zone"
    assert interval_step["secondary_target_value_low"] == 0.7751938
    assert interval_step["secondary_target_value_high"] == 0.8583333

    rest_step = repeat_step["steps"][1]
    assert rest_step["type"] == "rest"
    assert rest_step["end_condition"] == "fixed.rest"
    assert rest_step["end_condition_value"] == 60.0


@pytest.mark.asyncio
async def test_get_workout_by_id_tool_ignores_malformed_target_blocks(
    app_with_workouts, mock_garmin_client
):
    """Test malformed Garmin target blocks do not crash workout curation."""
    import json as json_module

    malformed_workout = {
        "workoutId": 123457,
        "workoutName": "Malformed Swim Workout",
        "sportType": {"sportTypeId": 4, "sportTypeKey": "swimming"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 4, "sportTypeKey": "swimming"},
            "workoutSteps": [{
                "type": "ExecutableStepDTO",
                "stepOrder": 1,
                "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
                "endCondition": {"conditionTypeId": 3, "conditionTypeKey": "distance"},
                "endConditionValue": 100.0,
                "targetType": "pace.zone",
                "secondaryTargetType": [],
            }]
        }],
    }
    mock_garmin_client.get_workout_by_id.return_value = malformed_workout

    result = await app_with_workouts.call_tool(
        "get_workout_by_id",
        {"workout_id": 123457}
    )

    result_data = json_module.loads(result[0][0].text)
    step = result_data["segments"][0]["steps"][0]
    assert step["type"] == "warmup"
    assert step["end_condition"] == "distance"
    assert step["end_condition_value"] == 100.0
    assert "target_type" not in step
    assert "secondary_target_type" not in step


@pytest.mark.asyncio
async def test_get_workout_by_uuid_tool(app_with_workouts, mock_garmin_client):
    """Test get_workout_by_id tool with UUID (training plan workout)"""
    import json as json_module

    # Setup mock for connectapi call (fbt-adaptive endpoint)
    mock_garmin_client.connectapi.return_value = {
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

    # Call tool with UUID (contains dashes)
    workout_uuid = "d7a5491b-42a5-4d2d-ba38-4e414fc03caf"
    result = await app_with_workouts.call_tool(
        "get_workout_by_id",
        {"workout_id": workout_uuid}
    )

    # Verify fbt-adaptive endpoint was called
    assert result is not None
    mock_garmin_client.connectapi.assert_called_once_with(
        f"workout-service/fbt-adaptive/{workout_uuid}"
    )

    # Parse the result and verify training plan workout fields
    result_data = json_module.loads(result[0][0].text)
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
async def test_upload_workout_fixes_hr_zone_target(app_with_workouts, mock_garmin_client):
    """Test upload_workout converts targetValueOne to zoneNumber for HR zone targets"""
    import json as json_module

    upload_response = {"workoutId": 123458, "workoutName": "HR Zone Workout"}
    mock_garmin_client.upload_workout.return_value = upload_response

    # Simulate the common LLM mistake: using targetValueOne instead of zoneNumber
    workout_data = {
        "workoutName": "HR Zone Workout",
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": [{
                "type": "ExecutableStepDTO",
                "stepOrder": 1,
                "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                "endConditionValue": 600,
                "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
                "targetValueOne": 3,
            }]
        }]
    }

    result = await app_with_workouts.call_tool(
        "upload_workout",
        {"workout_data": workout_data}
    )

    # Verify the data sent to Garmin API was fixed
    called_data = mock_garmin_client.upload_workout.call_args[0][0]
    step = called_data["workoutSegments"][0]["workoutSteps"][0]
    assert step["zoneNumber"] == 3
    assert "targetValueOne" not in step
    assert "targetValueTwo" not in step

    result_data = json_module.loads(result[0][0].text)
    assert result_data["status"] == "success"


@pytest.mark.asyncio
async def test_upload_workout_fixes_hr_zone_in_repeat_group(app_with_workouts, mock_garmin_client):
    """Test upload_workout fixes HR zone targets inside RepeatGroupDTO"""
    import json as json_module

    upload_response = {"workoutId": 123459, "workoutName": "Repeat HR Zone"}
    mock_garmin_client.upload_workout.return_value = upload_response

    workout_data = {
        "workoutName": "Repeat HR Zone",
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": [{
                "type": "RepeatGroupDTO",
                "stepOrder": 1,
                "numberOfIterations": 2,
                "workoutSteps": [
                    {
                        "type": "ExecutableStepDTO",
                        "stepOrder": 1,
                        "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                        "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                        "endConditionValue": 600,
                        "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
                        "targetValueOne": 3,
                        "targetValueTwo": 3,
                    },
                    {
                        "type": "ExecutableStepDTO",
                        "stepOrder": 2,
                        "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery"},
                        "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                        "endConditionValue": 240,
                        "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                    }
                ]
            }]
        }]
    }

    result = await app_with_workouts.call_tool(
        "upload_workout",
        {"workout_data": workout_data}
    )

    # Verify nested step was fixed
    called_data = mock_garmin_client.upload_workout.call_args[0][0]
    interval_step = called_data["workoutSegments"][0]["workoutSteps"][0]["workoutSteps"][0]
    assert interval_step["zoneNumber"] == 3
    assert "targetValueOne" not in interval_step
    assert "targetValueTwo" not in interval_step

    result_data = json_module.loads(result[0][0].text)
    assert result_data["status"] == "success"


@pytest.mark.asyncio
async def test_upload_workout_rejects_mismatched_end_condition_id(
    app_with_workouts, mock_garmin_client
):
    """Reject payloads Garmin would reinterpret using conditionTypeId."""
    workout_data = _running_workout_with_steps([{
        "type": "ExecutableStepDTO",
        "stepOrder": 1,
        "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery"},
        "endCondition": {"conditionTypeId": 4, "conditionTypeKey": "heart.rate"},
        "endConditionValue": 145.0,
        "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
    }])

    result = await app_with_workouts.call_tool(
        "upload_workout",
        {"workout_data": workout_data}
    )

    assert result is not None
    message = result[0][0].text
    assert "Error uploading workout" in message
    assert "conditionTypeKey 'heart.rate' requires conditionTypeId 6" in message
    assert "got 4 (calories)" in message
    mock_garmin_client.upload_workout.assert_not_called()


@pytest.mark.asyncio
async def test_upload_workout_rejects_target_type_mismatch(app_with_workouts, mock_garmin_client):
    """Reject targetType IDs that Garmin would reinterpret as another target."""
    workout_data = _running_workout_with_steps(
        [_timed_interval_step({"workoutTargetTypeId": 6, "workoutTargetTypeKey": "heart.rate"})],
        name="Bad HR Target",
    )

    result = await app_with_workouts.call_tool(
        "upload_workout",
        {"workout_data": workout_data}
    )

    assert "targetType mismatch" in result[0][0].text
    assert "workoutTargetTypeId 6 is 'pace.zone', not 'heart.rate'" in result[0][0].text
    mock_garmin_client.upload_workout.assert_not_called()


@pytest.mark.asyncio
async def test_upload_workout_accepts_heart_rate_end_condition_id(
    app_with_workouts, mock_garmin_client
):
    """Accept the canonical Garmin id/key pair for heart-rate end conditions."""
    workout_data = _running_workout_with_steps([{
        "type": "ExecutableStepDTO",
        "stepOrder": 1,
        "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery"},
        "endCondition": {"conditionTypeId": 6, "conditionTypeKey": "heart.rate"},
        "endConditionValue": 145.0,
        "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
    }])
    mock_garmin_client.upload_workout.return_value = {
        "workoutId": 123460,
        "workoutName": "Validation Workout",
    }

    result = await app_with_workouts.call_tool(
        "upload_workout",
        {"workout_data": workout_data}
    )

    assert result is not None
    mock_garmin_client.upload_workout.assert_called_once_with(workout_data)


@pytest.mark.asyncio
async def test_upload_workout_accepts_custom_hr_range(app_with_workouts, mock_garmin_client):
    """Custom HR bpm ranges use heart.rate.zone with targetValueOne/targetValueTwo."""
    import json as json_module

    mock_garmin_client.upload_workout.return_value = {
        "workoutId": 123460,
        "workoutName": "Custom HR Range",
    }
    workout_data = _running_workout_with_steps(
        [_timed_interval_step({"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"})],
        name="Custom HR Range",
    )

    result = await app_with_workouts.call_tool(
        "upload_workout",
        {"workout_data": workout_data}
    )

    called_data = mock_garmin_client.upload_workout.call_args[0][0]
    step = called_data["workoutSegments"][0]["workoutSteps"][0]
    assert step["targetValueOne"] == 143
    assert step["targetValueTwo"] == 157
    assert "zoneNumber" not in step

    result_data = json_module.loads(result[0][0].text)
    assert result_data["status"] == "success"


@pytest.mark.asyncio
async def test_upload_workout_rejects_nested_end_condition_mismatch(
    app_with_workouts, mock_garmin_client
):
    """Validate nested RepeatGroupDTO workout steps before upload."""
    workout_data = _running_workout_with_steps([{
        "type": "RepeatGroupDTO",
        "stepOrder": 1,
        "numberOfIterations": 2,
        "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations"},
        "workoutSteps": [{
            "type": "ExecutableStepDTO",
            "stepOrder": 1,
            "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery"},
            "endCondition": {"conditionTypeId": 4, "conditionTypeKey": "heart.rate"},
            "endConditionValue": 145.0,
            "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
        }],
    }])

    result = await app_with_workouts.call_tool(
        "upload_workout",
        {"workout_data": workout_data}
    )

    assert result is not None
    message = result[0][0].text
    assert "workoutSegments[0].workoutSteps[0].workoutSteps[0]" in message
    assert "conditionTypeKey 'heart.rate' requires conditionTypeId 6" in message
    mock_garmin_client.upload_workout.assert_not_called()


@pytest.mark.asyncio
async def test_upload_workout_rejects_nested_target_type_mismatch(app_with_workouts, mock_garmin_client):
    """Reject mismatched targetType blocks inside RepeatGroupDTO steps."""
    bad_step = _timed_interval_step({"workoutTargetTypeId": 6, "workoutTargetTypeKey": "heart.rate"})
    workout_data = _running_workout_with_steps(
        [{
            "type": "RepeatGroupDTO",
            "stepOrder": 1,
            "numberOfIterations": 2,
            "workoutSteps": [bad_step],
        }],
        name="Nested Bad HR Target",
    )

    result = await app_with_workouts.call_tool(
        "upload_workout",
        {"workout_data": workout_data}
    )

    assert "workoutSegments[0].workoutSteps[0].workoutSteps[0].targetType mismatch" in result[0][0].text
    mock_garmin_client.upload_workout.assert_not_called()


@pytest.mark.asyncio
async def test_upload_workout_rejects_missing_end_condition_id(
    app_with_workouts, mock_garmin_client
):
    """Return a local validation error instead of Garmin's id=0 API error."""
    workout_data = _running_workout_with_steps([{
        "type": "ExecutableStepDTO",
        "stepOrder": 1,
        "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery"},
        "endCondition": {"conditionTypeKey": "heart.rate"},
        "endConditionValue": 145.0,
        "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
    }])

    result = await app_with_workouts.call_tool(
        "upload_workout",
        {"workout_data": workout_data}
    )

    assert result is not None
    message = result[0][0].text
    assert "conditionTypeKey 'heart.rate' requires conditionTypeId 6" in message
    mock_garmin_client.upload_workout.assert_not_called()


@pytest.mark.asyncio
async def test_upload_workout_rejects_secondary_target_type_mismatch(app_with_workouts, mock_garmin_client):
    """Reject mismatched secondaryTargetType blocks before Garmin reinterprets them."""
    step = _timed_interval_step(None)
    step["secondaryTargetType"] = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "heart.rate"}
    step["secondaryTargetValueOne"] = 143
    step["secondaryTargetValueTwo"] = 157
    workout_data = _running_workout_with_steps(
        [step],
        name="Bad Secondary HR Target",
    )

    result = await app_with_workouts.call_tool(
        "upload_workout",
        {"workout_data": workout_data}
    )

    assert "secondaryTargetType mismatch" in result[0][0].text
    assert "workoutTargetTypeId 6 is 'pace.zone', not 'heart.rate'" in result[0][0].text
    mock_garmin_client.upload_workout.assert_not_called()


@pytest.mark.asyncio
async def test_upload_workout_accepts_secondary_target_type_with_null_primary(
    app_with_workouts, mock_garmin_client
):
    """Swim-style secondary targets may use targetType null."""
    import json as json_module

    mock_garmin_client.upload_workout.return_value = {
        "workoutId": 123461,
        "workoutName": "Secondary Pace Target",
    }
    step = _timed_interval_step(None)
    step["secondaryTargetType"] = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"}
    step["secondaryTargetValueOne"] = 0.45
    step["secondaryTargetValueTwo"] = 0.6916667
    workout_data = _running_workout_with_steps("Secondary Pace Target", [step])

    result = await app_with_workouts.call_tool(
        "upload_workout",
        {"workout_data": workout_data}
    )

    called_data = mock_garmin_client.upload_workout.call_args[0][0]
    called_step = called_data["workoutSegments"][0]["workoutSteps"][0]
    assert called_step["targetType"] is None
    assert called_step["secondaryTargetType"]["workoutTargetTypeKey"] == "pace.zone"

    result_data = json_module.loads(result[0][0].text)
    assert result_data["status"] == "success"


@pytest.mark.asyncio
async def test_upload_workout_rejects_nested_secondary_target_type_mismatch(
    app_with_workouts, mock_garmin_client
):
    """Reject mismatched secondaryTargetType blocks inside RepeatGroupDTO steps."""
    bad_step = _timed_interval_step(None)
    bad_step["secondaryTargetType"] = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "heart.rate"}
    workout_data = _running_workout_with_steps(
        [{
            "type": "RepeatGroupDTO",
            "stepOrder": 1,
            "numberOfIterations": 2,
            "workoutSteps": [bad_step],
        }],
        name="Nested Bad Secondary HR Target",
    )

    result = await app_with_workouts.call_tool(
        "upload_workout",
        {"workout_data": workout_data}
    )

    assert (
        "workoutSegments[0].workoutSteps[0].workoutSteps[0].secondaryTargetType mismatch"
        in result[0][0].text
    )
    mock_garmin_client.upload_workout.assert_not_called()


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
    result_data = json_module.loads(result[0][0].text)
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
    result_data = json_module.loads(result[0][0].text)
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


# Delete workout tests
@pytest.mark.asyncio
async def test_delete_workout_success(app_with_workouts, mock_garmin_client):
    """Test delete_workout tool when the library call succeeds"""
    import json as json_module

    # The MCP tool now delegates to garmin_client.delete_workout(id)
    # (high-level method). Success is signalled by absence of exception.
    mock_garmin_client.delete_workout.return_value = {}

    workout_id = 123456
    result = await app_with_workouts.call_tool(
        "delete_workout",
        {"workout_id": workout_id}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["status"] == "success"
    assert result_data["workout_id"] == 123456
    assert "deleted successfully" in result_data["message"]
    mock_garmin_client.delete_workout.assert_called_once_with(workout_id)


@pytest.mark.asyncio
async def test_delete_workout_failure(app_with_workouts, mock_garmin_client):
    """Test delete_workout tool when the library raises (e.g. 404)"""
    import json as json_module

    mock_garmin_client.delete_workout.side_effect = Exception("API Error 404")

    workout_id = 999999
    result = await app_with_workouts.call_tool(
        "delete_workout",
        {"workout_id": workout_id}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["status"] == "failed"
    assert result_data["workout_id"] == 999999
    assert "404" in result_data["message"]


@pytest.mark.asyncio
async def test_delete_workout_exception(app_with_workouts, mock_garmin_client):
    """Test delete_workout tool with a network-level exception"""
    import json as json_module

    mock_garmin_client.delete_workout.side_effect = Exception("Network error")

    result = await app_with_workouts.call_tool(
        "delete_workout",
        {"workout_id": 123456}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["status"] == "failed"
    assert "Network error" in result_data["message"]


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


# delete_workouts tests
@pytest.mark.asyncio
async def test_delete_workouts_single(app_with_workouts, mock_garmin_client):
    """Test delete_workouts with a single workout ID"""
    import json as json_module

    mock_garmin_client.delete_workout.return_value = {}

    result = await app_with_workouts.call_tool(
        "delete_workouts",
        {"workout_ids": [123456]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 1
    assert result_data["failed"] == 0
    assert result_data["results"][0]["status"] == "success"
    assert result_data["results"][0]["workout_id"] == 123456


@pytest.mark.asyncio
async def test_delete_workouts_multiple(app_with_workouts, mock_garmin_client):
    """Test delete_workouts with multiple workout IDs"""
    import json as json_module

    mock_garmin_client.delete_workout.return_value = {}

    result = await app_with_workouts.call_tool(
        "delete_workouts",
        {"workout_ids": [111, 222, 333]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 3
    assert result_data["succeeded"] == 3
    assert result_data["failed"] == 0
    assert mock_garmin_client.delete_workout.call_count == 3


@pytest.mark.asyncio
async def test_delete_workouts_partial_failure(app_with_workouts, mock_garmin_client):
    """Test delete_workouts when some deletions fail"""
    import json as json_module

    mock_garmin_client.delete_workout.side_effect = [
        {},
        Exception("API Error 404"),
    ]

    result = await app_with_workouts.call_tool(
        "delete_workouts",
        {"workout_ids": [111, 999]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 2
    assert result_data["succeeded"] == 1
    assert result_data["failed"] == 1
    assert result_data["results"][0]["status"] == "success"
    assert result_data["results"][1]["status"] == "error"
    assert "404" in result_data["results"][1]["message"]


@pytest.mark.asyncio
async def test_delete_workouts_exception(app_with_workouts, mock_garmin_client):
    """Test delete_workouts when an exception is raised"""
    import json as json_module

    mock_garmin_client.delete_workout.side_effect = Exception("Network error")

    result = await app_with_workouts.call_tool(
        "delete_workouts",
        {"workout_ids": [123456]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 0
    assert result_data["failed"] == 1
    assert result_data["results"][0]["status"] == "error"
    assert "Network error" in result_data["results"][0]["message"]


# upload_workouts tests
@pytest.mark.asyncio
async def test_upload_workouts_single(app_with_workouts, mock_garmin_client):
    """Test upload_workouts with a single workout"""
    import json as json_module

    mock_garmin_client.upload_workout.return_value = {"workoutId": 111, "workoutName": "Easy Run"}

    result = await app_with_workouts.call_tool(
        "upload_workouts",
        {"workouts": [{"workoutName": "Easy Run", "sportType": {"sportTypeId": 1, "sportTypeKey": "running"}}]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 1
    assert result_data["failed"] == 0
    assert result_data["results"][0]["status"] == "success"
    assert result_data["results"][0]["workout_id"] == 111
    assert result_data["results"][0]["name"] == "Easy Run"
    mock_garmin_client.upload_workout.assert_called_once()


@pytest.mark.asyncio
async def test_upload_workouts_multiple(app_with_workouts, mock_garmin_client):
    """Test upload_workouts with multiple workouts"""
    import json as json_module

    mock_garmin_client.upload_workout.side_effect = [
        {"workoutId": 111, "workoutName": "Easy Run"},
        {"workoutId": 222, "workoutName": "Tempo Run"},
        {"workoutId": 333, "workoutName": "Long Run"},
    ]

    workouts = [
        {"workoutName": "Easy Run"},
        {"workoutName": "Tempo Run"},
        {"workoutName": "Long Run"},
    ]
    result = await app_with_workouts.call_tool("upload_workouts", {"workouts": workouts})

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 3
    assert result_data["succeeded"] == 3
    assert result_data["failed"] == 0
    assert mock_garmin_client.upload_workout.call_count == 3


@pytest.mark.asyncio
async def test_upload_workouts_partial_failure(app_with_workouts, mock_garmin_client):
    """Test upload_workouts when some uploads fail"""
    import json as json_module

    mock_garmin_client.upload_workout.side_effect = [
        {"workoutId": 111, "workoutName": "Easy Run"},
        Exception("API error"),
    ]

    workouts = [
        {"workoutName": "Easy Run"},
        {"workoutName": "Bad Workout"},
    ]
    result = await app_with_workouts.call_tool("upload_workouts", {"workouts": workouts})

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 2
    assert result_data["succeeded"] == 1
    assert result_data["failed"] == 1
    assert result_data["results"][0]["status"] == "success"
    assert result_data["results"][1]["status"] == "error"
    assert "API error" in result_data["results"][1]["message"]
    assert result_data["results"][1]["name"] == "Bad Workout"


@pytest.mark.asyncio
async def test_upload_workouts_reports_end_condition_validation_error(
    app_with_workouts, mock_garmin_client
):
    """Batch uploads reject only the invalid workout and keep valid uploads."""
    import json as json_module

    mock_garmin_client.upload_workout.return_value = {
        "workoutId": 111,
        "workoutName": "Valid HR Workout",
    }

    valid = _running_workout_with_steps([{
        "type": "ExecutableStepDTO",
        "stepOrder": 1,
        "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery"},
        "endCondition": {"conditionTypeId": 6, "conditionTypeKey": "heart.rate"},
        "endConditionValue": 145.0,
        "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
    }], name="Valid HR Workout")
    invalid = _running_workout_with_steps([{
        "type": "ExecutableStepDTO",
        "stepOrder": 1,
        "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery"},
        "endCondition": {"conditionTypeId": 4, "conditionTypeKey": "heart.rate"},
        "endConditionValue": 145.0,
        "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
    }], name="Invalid HR Workout")

    result = await app_with_workouts.call_tool(
        "upload_workouts",
        {"workouts": [valid, invalid]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 2
    assert result_data["succeeded"] == 1
    assert result_data["failed"] == 1
    assert result_data["results"][0]["status"] == "success"
    assert result_data["results"][1]["status"] == "error"
    assert result_data["results"][1]["name"] == "Invalid HR Workout"
    assert "conditionTypeKey 'heart.rate' requires conditionTypeId 6" in result_data["results"][1]["message"]
    mock_garmin_client.upload_workout.assert_called_once_with(valid)


@pytest.mark.asyncio
async def test_upload_workouts_rejects_target_type_mismatch(app_with_workouts, mock_garmin_client):
    """Batch uploads reject malformed targetType blocks before calling Garmin."""
    import json as json_module

    good_workout = _running_workout_with_steps(
        [_timed_interval_step({"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"})],
        name="Good HR Range",
    )
    bad_workout = _running_workout_with_steps(
        [_timed_interval_step({"workoutTargetTypeId": 6, "workoutTargetTypeKey": "heart.rate"})],
        name="Bad HR Target",
    )
    mock_garmin_client.upload_workout.return_value = {
        "workoutId": 111,
        "workoutName": "Good HR Range",
    }

    result = await app_with_workouts.call_tool(
        "upload_workouts",
        {"workouts": [good_workout, bad_workout]},
    )

    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 2
    assert result_data["succeeded"] == 1
    assert result_data["failed"] == 1
    assert result_data["results"][0]["status"] == "success"
    assert result_data["results"][1]["status"] == "error"
    assert "targetType mismatch" in result_data["results"][1]["message"]
    mock_garmin_client.upload_workout.assert_called_once_with(good_workout)


# schedule_workouts tests
@pytest.mark.asyncio
async def test_schedule_workouts_single(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts with a single workout"""
    import json as json_module
    from unittest.mock import MagicMock

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_garmin_client.client.post.return_value = mock_response

    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": [{"workout_id": 123456, "calendar_date": "2024-01-15"}]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 1
    assert result_data["failed"] == 0
    assert result_data["results"][0]["status"] == "success"
    assert result_data["results"][0]["workout_id"] == 123456
    assert result_data["results"][0]["scheduled_date"] == "2024-01-15"
    mock_garmin_client.client.post.assert_called_once_with(
        "connectapi", "workout-service/schedule/123456", json={"date": "2024-01-15"}
    )


@pytest.mark.asyncio
async def test_schedule_workouts_multiple(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts with multiple workouts"""
    import json as json_module
    from unittest.mock import MagicMock

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_garmin_client.client.post.return_value = mock_response

    schedules = [
        {"workout_id": 111, "calendar_date": "2024-01-15"},
        {"workout_id": 222, "calendar_date": "2024-01-17"},
        {"workout_id": 333, "calendar_date": "2024-01-19"},
    ]
    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": schedules}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 3
    assert result_data["succeeded"] == 3
    assert result_data["failed"] == 0
    assert mock_garmin_client.client.post.call_count == 3


@pytest.mark.asyncio
async def test_schedule_workouts_partial_failure(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts when some workouts fail"""
    import json as json_module
    from unittest.mock import MagicMock

    ok_response = MagicMock()
    ok_response.status_code = 200
    err_response = MagicMock()
    err_response.status_code = 404

    mock_garmin_client.client.post.side_effect = [ok_response, err_response]

    schedules = [
        {"workout_id": 111, "calendar_date": "2024-01-15"},
        {"workout_id": 999, "calendar_date": "2024-01-17"},
    ]
    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": schedules}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 2
    assert result_data["succeeded"] == 1
    assert result_data["failed"] == 1
    assert result_data["results"][0]["status"] == "success"
    assert result_data["results"][1]["status"] == "failed"
    assert result_data["results"][1]["http_status"] == 404


@pytest.mark.asyncio
async def test_schedule_workouts_missing_fields(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts with missing required fields"""
    import json as json_module

    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": [{"workout_id": 123456}]}  # missing calendar_date
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 0
    assert result_data["failed"] == 1
    assert result_data["results"][0]["status"] == "failed"
    assert "Missing required field" in result_data["results"][0]["message"]
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_workouts_exception(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts when an exception is raised"""
    import json as json_module

    mock_garmin_client.client.post.side_effect = Exception("Network error")

    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": [{"workout_id": 123456, "calendar_date": "2024-01-15"}]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 0
    assert result_data["failed"] == 1
    assert result_data["results"][0]["status"] == "error"
    assert "Network error" in result_data["results"][0]["message"]


@pytest.mark.asyncio
async def test_schedule_workouts_idempotent(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts is a no-op when workout is already scheduled

    The schedule endpoint on Garmin is NOT idempotent — a second POST creates
    a duplicate calendar entry. The MCP tool pre-checks via GraphQL and skips
    the POST when the same workout_id is already on that date.
    """
    import json as json_module

    # GraphQL pre-check returns an existing schedule for this workout/date
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {
            "workoutScheduleSummariesScalar": [
                {
                    "workoutId": 123456,
                    "scheduleDate": "2024-01-15",
                    "workoutName": "Easy Run",
                }
            ]
        }
    }

    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": [{"workout_id": 123456, "calendar_date": "2024-01-15"}]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 1
    assert result_data["failed"] == 0
    assert result_data["results"][0]["status"] == "success"
    assert result_data["results"][0]["idempotent"] is True
    # Critically: the schedule POST must NOT be called
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_workouts_inline_upload(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts with inline workout_data uploads-and-schedules in one call"""
    import json as json_module
    from unittest.mock import MagicMock

    upload_result = {"workoutId": 999001, "workoutName": "Easy Run"}
    mock_garmin_client.upload_workout.return_value = upload_result

    schedule_response = MagicMock()
    schedule_response.status_code = 200
    mock_garmin_client.client.post.return_value = schedule_response

    inline_data = {"workoutName": "Easy Run", "sportType": {"sportTypeId": 1, "sportTypeKey": "running"}}
    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": [{"workout_data": inline_data, "calendar_date": "2024-02-01"}]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 1
    assert result_data["failed"] == 0
    entry = result_data["results"][0]
    assert entry["status"] == "success"
    assert entry["workout_id"] == 999001
    assert entry["scheduled_date"] == "2024-02-01"
    assert entry["workout_name"] == "Easy Run"
    mock_garmin_client.upload_workout.assert_called_once_with(inline_data)
    mock_garmin_client.client.post.assert_called_once_with(
        "connectapi", "workout-service/schedule/999001", json={"date": "2024-02-01"}
    )


@pytest.mark.asyncio
async def test_schedule_workouts_inline_upload_rejects_end_condition_mismatch(
    app_with_workouts, mock_garmin_client
):
    """Inline workout_data follows the same validation as upload_workout."""
    import json as json_module

    inline_data = _running_workout_with_steps([{
        "type": "ExecutableStepDTO",
        "stepOrder": 1,
        "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery"},
        "endCondition": {"conditionTypeId": 4, "conditionTypeKey": "heart.rate"},
        "endConditionValue": 145.0,
        "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
    }], name="Invalid Inline HR Workout")

    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": [{"workout_data": inline_data, "calendar_date": "2024-02-01"}]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 0
    assert result_data["failed"] == 1
    assert result_data["results"][0]["status"] == "error"
    assert "conditionTypeKey 'heart.rate' requires conditionTypeId 6" in result_data["results"][0]["message"]
    mock_garmin_client.upload_workout.assert_not_called()
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_workouts_rejects_inline_target_type_mismatch(app_with_workouts, mock_garmin_client):
    """Inline workout_data uses the same targetType validation as upload_workout."""
    import json as json_module

    inline_data = _running_workout_with_steps(
        [_timed_interval_step({"workoutTargetTypeId": 6, "workoutTargetTypeKey": "heart.rate"})],
        name="Bad Inline HR Target",
    )

    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": [{"workout_data": inline_data, "calendar_date": "2024-02-01"}]},
    )

    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 0
    assert result_data["failed"] == 1
    assert result_data["results"][0]["status"] == "error"
    assert "targetType mismatch" in result_data["results"][0]["message"]
    mock_garmin_client.upload_workout.assert_not_called()
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_workouts_mixed_inline_and_id(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts mixing inline workout_data and existing workout_id"""
    import json as json_module
    from unittest.mock import MagicMock

    upload_result = {"workoutId": 999002, "workoutName": "Tempo Run"}
    mock_garmin_client.upload_workout.return_value = upload_result

    schedule_response = MagicMock()
    schedule_response.status_code = 200
    mock_garmin_client.client.post.return_value = schedule_response

    inline_data = {"workoutName": "Tempo Run", "sportType": {"sportTypeId": 1, "sportTypeKey": "running"}}
    schedules = [
        {"workout_id": 111, "calendar_date": "2024-02-05"},
        {"workout_data": inline_data, "calendar_date": "2024-02-07"},
    ]
    result = await app_with_workouts.call_tool("schedule_workouts", {"schedules": schedules})

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 2
    assert result_data["succeeded"] == 2
    assert result_data["failed"] == 0
    assert result_data["results"][0]["workout_id"] == 111
    assert result_data["results"][1]["workout_id"] == 999002


@pytest.mark.asyncio
async def test_schedule_workouts_missing_both_id_and_data(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts fails when neither workout_id nor workout_data is provided"""
    import json as json_module

    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": [{"calendar_date": "2024-02-01"}]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 0
    assert result_data["failed"] == 1
    assert "workout_id" in result_data["results"][0]["message"] or "workout_data" in result_data["results"][0]["message"]
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_workouts_inline_upload_no_id_returned(app_with_workouts, mock_garmin_client):
    """Test schedule_workouts fails gracefully when upload returns no workout_id"""
    import json as json_module

    mock_garmin_client.upload_workout.return_value = {"workoutName": "Bad Response"}

    inline_data = {"workoutName": "Bad Response"}
    result = await app_with_workouts.call_tool(
        "schedule_workouts",
        {"schedules": [{"workout_data": inline_data, "calendar_date": "2024-02-01"}]}
    )

    assert result is not None
    result_data = json_module.loads(result[0][0].text)
    assert result_data["total"] == 1
    assert result_data["succeeded"] == 0
    assert result_data["failed"] == 1
    assert result_data["results"][0]["status"] == "failed"
    mock_garmin_client.client.post.assert_not_called()


# ---------------------------------------------------------------------------
# _fix_repeat_group_step (unit tests)
# ---------------------------------------------------------------------------

def test_fix_repeat_group_adds_missing_condition_type_id():
    """Adds conditionTypeId:7 when conditionTypeKey is 'iterations' but id is absent."""
    step = {
        "type": "RepeatGroupDTO",
        "numberOfIterations": 5,
        "endCondition": {"conditionTypeKey": "iterations"},
        "endConditionValue": 5,
        "workoutSteps": [],
    }
    _fix_repeat_group_step(step)
    assert step["endCondition"]["conditionTypeId"] == 7
    assert step["endCondition"]["conditionTypeKey"] == "iterations"


def test_fix_repeat_group_leaves_existing_condition_type_id_unchanged():
    """Does not overwrite conditionTypeId when already present."""
    step = {
        "type": "RepeatGroupDTO",
        "numberOfIterations": 3,
        "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations"},
        "endConditionValue": 3,
        "workoutSteps": [],
    }
    _fix_repeat_group_step(step)
    assert step["endCondition"]["conditionTypeId"] == 7


def test_fix_repeat_group_backfills_number_of_iterations_from_end_condition_value():
    """numberOfIterations is set from endConditionValue when missing."""
    step = {
        "type": "RepeatGroupDTO",
        "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations"},
        "endConditionValue": 4,
        "workoutSteps": [],
    }
    _fix_repeat_group_step(step)
    assert step["numberOfIterations"] == 4


def test_fix_repeat_group_does_not_modify_non_repeat_steps():
    """Steps that are not RepeatGroupDTO are not modified."""
    step = {
        "type": "ExecutableStepDTO",
        "endCondition": {"conditionTypeKey": "time"},
        "endConditionValue": 300.0,
    }
    _fix_repeat_group_step(step)
    assert "conditionTypeId" not in step["endCondition"]


def test_fix_repeat_group_recurses_into_nested_repeat_groups():
    """Nested RepeatGroupDTOs inside another are also fixed."""
    inner = {
        "type": "RepeatGroupDTO",
        "numberOfIterations": 2,
        "endCondition": {"conditionTypeKey": "iterations"},
        "endConditionValue": 2,
        "workoutSteps": [],
    }
    outer = {
        "type": "RepeatGroupDTO",
        "numberOfIterations": 3,
        "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations"},
        "endConditionValue": 3,
        "workoutSteps": [inner],
    }
    _fix_repeat_group_step(outer)
    assert inner["endCondition"]["conditionTypeId"] == 7
