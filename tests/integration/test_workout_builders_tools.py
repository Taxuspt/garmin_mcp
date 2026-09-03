"""
Integration tests for high-level workout builder tools (workout_builders.py).
"""
import json

import pytest
from mcp.server.fastmcp import FastMCP
from unittest.mock import AsyncMock, MagicMock

from garmin_mcp import workouts, workout_builders


@pytest.fixture
def app_with_builders(mock_garmin_client, monkeypatch):
    """FastMCP app with workout_builders registered.

    Also configures `workouts` because workout_builders.schedule_week reuses
    the `_is_already_scheduled` helper defined there, which reads from the
    `garmin_client` module-level global in workouts.py. Both modules must
    point at the same mock for the helper to see the right state.
    """
    # Default: pre-check finds no existing schedules so the POST path runs.
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {"workoutScheduleSummariesScalar": []}
    }
    workouts.configure(mock_garmin_client)
    workout_builders.configure(mock_garmin_client)
    monkeypatch.setattr(
        workout_builders.write_confirmation,
        "confirm_garmin_write",
        AsyncMock(return_value=(True, None)),
    )
    app = FastMCP("Test Workout Builders")
    app = workout_builders.register_tools(app)
    return app


@pytest.mark.asyncio
async def test_schedule_week_uses_client_post_not_garth(
    app_with_builders, mock_garmin_client
):
    """schedule_week must route through garmin_client.client.post

    Regression: garminconnect 0.3.2 removed the `.garth` attribute. The old
    code called `garmin_client.garth.post(...)` which raises AttributeError.
    This test pins the fix.
    """
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_garmin_client.client.post.return_value = mock_response

    result = await app_with_builders.call_tool(
        "schedule_week",
        {"week": [{"date": "2026-05-12", "workout_id": 1234567890}]},
    )

    assert result is not None
    payload = json.loads(result[0][0].text)
    assert payload["status"] == "complete"
    assert payload["scheduled"][0]["status"] == "scheduled"
    assert payload["scheduled"][0]["workout_id"] == 1234567890
    # Must call .client.post, never .garth.*
    mock_garmin_client.client.post.assert_called_once()


@pytest.mark.asyncio
async def test_schedule_week_is_idempotent(
    app_with_builders, mock_garmin_client
):
    """schedule_week skips the POST when the workout is already scheduled.

    Matches the idempotency behaviour of schedule_workout / schedule_workouts.
    """
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {
            "workoutScheduleSummariesScalar": [
                {
                    "workoutId": 1234567890,
                    "scheduleDate": "2026-05-12",
                    "workoutName": "Easy Run",
                }
            ]
        }
    }

    result = await app_with_builders.call_tool(
        "schedule_week",
        {"week": [{"date": "2026-05-12", "workout_id": 1234567890}]},
    )

    assert result is not None
    payload = json.loads(result[0][0].text)
    assert payload["status"] == "complete"
    assert payload["scheduled"][0]["status"] == "already_scheduled"
    assert payload["scheduled"][0]["idempotent"] is True
    # Critically: no POST happened
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_week_partial_idempotency(
    app_with_builders, mock_garmin_client
):
    """Mixed week: some entries already scheduled, others new.

    Verifies the pre-check runs per-item, not once for the whole batch.
    """
    def graphql_side_effect(query):
        # Return existing schedule only for 2026-05-12
        if "2026-05-12" in query["query"]:
            return {
                "data": {
                    "workoutScheduleSummariesScalar": [
                        {
                            "workoutId": 111,
                            "scheduleDate": "2026-05-12",
                            "workoutName": "Easy Run",
                        }
                    ]
                }
            }
        return {"data": {"workoutScheduleSummariesScalar": []}}

    mock_garmin_client.query_garmin_graphql.side_effect = graphql_side_effect

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_garmin_client.client.post.return_value = mock_response

    result = await app_with_builders.call_tool(
        "schedule_week",
        {
            "week": [
                {"date": "2026-05-12", "workout_id": 111},  # already scheduled
                {"date": "2026-05-14", "workout_id": 222},  # new
            ]
        },
    )

    payload = json.loads(result[0][0].text)
    scheduled = payload["scheduled"]
    assert scheduled[0]["status"] == "already_scheduled"
    assert scheduled[1]["status"] == "scheduled"
    # Only the new one triggered the POST
    assert mock_garmin_client.client.post.call_count == 1


@pytest.mark.asyncio
async def test_create_run_workout_success(app_with_builders, mock_garmin_client):
    """create_run_workout uploads the workout and returns the workout_id."""
    mock_garmin_client.upload_workout.return_value = {
        "workoutId": 9876543210,
        "workoutName": "Step 8 - 30min continuous",
    }

    result = await app_with_builders.call_tool(
        "create_run_workout",
        {
            "name": "Step 8 - 30min continuous",
            "run_seconds": 1800,
            "warmup_min": 5,
            "cooldown_min": 5,
            "hr_zone": "Z3",
        },
    )

    assert result is not None
    payload = json.loads(result[0][0].text)
    assert payload["status"] == "success"
    assert payload["workout_id"] == 9876543210
    mock_garmin_client.upload_workout.assert_called_once()


@pytest.mark.asyncio
async def test_create_run_workout_custom_hr_range(app_with_builders, mock_garmin_client):
    """hr_min/hr_max on the tool call produce a custom bpm-range target, not a zoneNumber."""
    mock_garmin_client.upload_workout.return_value = {
        "workoutId": 1111111111,
        "workoutName": "Base run - 7/20",
    }

    result = await app_with_builders.call_tool(
        "create_run_workout",
        {
            "name": "Base run - 7/20",
            "run_seconds": 1440,
            "warmup_min": 5,
            "cooldown_min": 5,
            "hr_min": 136,
            "hr_max": 148,
        },
    )

    assert result is not None
    payload = json.loads(result[0][0].text)
    assert payload["status"] == "success"

    uploaded_json = mock_garmin_client.upload_workout.call_args[0][0]
    interval_step = uploaded_json["workoutSegments"][0]["workoutSteps"][1]
    assert interval_step["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert interval_step["targetValueOne"] == 136.0
    assert interval_step["targetValueTwo"] == 148.0
    assert "zoneNumber" not in interval_step


@pytest.mark.asyncio
async def test_create_run_workout_exception(app_with_builders, mock_garmin_client):
    """create_run_workout returns an error string when the API raises an exception."""
    mock_garmin_client.upload_workout.side_effect = Exception("Upload failed")

    result = await app_with_builders.call_tool(
        "create_run_workout",
        {
            "name": "Step 8 - 30min continuous",
            "run_seconds": 1800,
            "warmup_min": 5,
            "cooldown_min": 5,
        },
    )

    assert result is not None
    assert "Error" in result[0][0].text
    assert "Upload failed" in result[0][0].text


@pytest.mark.asyncio
async def test_create_cycling_workout_previews_by_default(
    app_with_builders, mock_garmin_client
):
    tools = {tool.name: tool for tool in await app_with_builders.list_tools()}
    assert tools["create_cycling_workout"].outputSchema is not None
    assert tools["create_hr_target_ride"].outputSchema is not None
    assert tools["create_interval_workout"].outputSchema is not None

    _content, payload = await app_with_builders.call_tool(
        "create_cycling_workout",
        {
            "name": "Steady 160W",
            "steps": [{
                "type": "interval",
                "duration_seconds": 3600,
                "power_min": 155,
                "power_max": 165,
            }],
        },
    )
    assert payload["status"] == "preview"
    step = payload["workout"]["workoutSegments"][0]["workoutSteps"][0]
    assert step["targetType"]["workoutTargetTypeId"] == 2
    mock_garmin_client.upload_workout.assert_not_called()


@pytest.mark.asyncio
async def test_cycling_builder_commit_requires_interactive_confirmation(
    app_with_builders, mock_garmin_client
):
    workout_builders.write_confirmation.confirm_garmin_write.return_value = (
        False,
        "user declined",
    )

    _content, payload = await app_with_builders.call_tool(
        "create_hr_target_ride",
        {
            "name": "Needs approval",
            "duration_min": 40,
            "hr_zone": "Z2",
            "dry_run": False,
        },
    )

    assert payload["status"] == "needs_confirmation"
    assert payload["write_performed"] is False
    mock_garmin_client.upload_workout.assert_not_called()
    summary = workout_builders.write_confirmation.confirm_garmin_write.call_args.kwargs[
        "summary"
    ]
    assert summary["steps"][1]["target"]["type"] == "heart.rate.zone"
    assert summary["steps"][1]["target"]["zoneNumber"] == 2


@pytest.mark.asyncio
async def test_create_interval_workout_commit_uploads_and_reads_back(
    app_with_builders, mock_garmin_client
):
    mock_garmin_client.upload_workout.return_value = {
        "workoutId": 4321,
        "workoutName": "4x5",
    }
    mock_garmin_client.get_workout_by_id.side_effect = (
        lambda _workout_id: mock_garmin_client.upload_workout.call_args.args[0]
    )
    _content, payload = await app_with_builders.call_tool(
        "create_interval_workout",
        {
            "name": "4x5",
            "work_seconds": 300,
            "recovery_seconds": 180,
            "repeats": 4,
            "target_type": "power",
            "target_min": 250,
            "target_max": 275,
            "dry_run": False,
        },
    )
    assert payload["status"] == "uploaded"
    assert payload["workout_id"] == 4321
    assert payload["read_back_validated"] is True
    uploaded = mock_garmin_client.upload_workout.call_args.args[0]
    repeat = uploaded["workoutSegments"][0]["workoutSteps"][1]
    assert repeat["numberOfIterations"] == 4
    assert repeat["workoutSteps"][0]["targetType"]["workoutTargetTypeId"] == 2


@pytest.mark.asyncio
async def test_cycling_builder_rolls_back_when_readback_changes_target(
    app_with_builders, mock_garmin_client
):
    mock_garmin_client.upload_workout.return_value = {
        "workoutId": 4322,
        "workoutName": "Bad round trip",
    }
    mock_garmin_client.get_workout_by_id.return_value = {
        "workoutId": 4322,
        "workoutSegments": [],
    }

    _content, payload = await app_with_builders.call_tool(
        "create_hr_target_ride",
        {
            "name": "Bad round trip",
            "duration_min": 40,
            "hr_min": 140,
            "hr_max": 150,
            "dry_run": False,
        },
    )
    assert payload["status"] == "failed_rolled_back"
    mock_garmin_client.delete_workout.assert_called_once_with(4322)


@pytest.mark.asyncio
async def test_cycling_builder_schedule_failure_compensates_new_workout(
    app_with_builders, mock_garmin_client
):
    mock_garmin_client.upload_workout.return_value = {
        "workoutId": 4323,
        "workoutName": "Schedule failure",
    }
    mock_garmin_client.get_workout_by_id.side_effect = (
        lambda _workout_id: mock_garmin_client.upload_workout.call_args.args[0]
    )
    mock_garmin_client.schedule_workout.side_effect = RuntimeError("calendar unavailable")

    _content, payload = await app_with_builders.call_tool(
        "create_hr_target_ride",
        {
            "name": "Schedule failure",
            "duration_min": 40,
            "hr_zone": "Z2",
            "schedule_date": "2026-09-10",
            "dry_run": False,
        },
    )
    assert payload["status"] == "failed_recovery_required"
    assert payload["rollback"]["recovery_checklist"]
    mock_garmin_client.delete_workout.assert_not_called()


@pytest.mark.asyncio
async def test_cycling_builder_extracts_schedule_id_from_http_response(
    app_with_builders, mock_garmin_client
):
    mock_garmin_client.upload_workout.return_value = {
        "workoutId": 4325,
        "workoutName": "Response-backed schedule",
    }
    mock_garmin_client.get_workout_by_id.side_effect = (
        lambda _workout_id: mock_garmin_client.upload_workout.call_args.args[0]
    )
    response = MagicMock()
    response.json.return_value = {"workoutScheduleId": 9002}
    mock_garmin_client.schedule_workout.return_value = response

    _content, payload = await app_with_builders.call_tool(
        "create_hr_target_ride",
        {
            "name": "Response-backed schedule",
            "duration_min": 40,
            "hr_zone": "Z2",
            "schedule_date": "2026-09-10",
            "dry_run": False,
        },
    )

    assert payload["status"] == "uploaded"
    assert payload["schedule"]["scheduled_workout_id"] == "9002"
    mock_garmin_client.query_garmin_graphql.assert_not_called()


def test_cycling_compensation_keeps_template_until_unschedule_is_read_back(
    app_with_builders, mock_garmin_client, monkeypatch
):
    monkeypatch.setattr(
        workout_builders, "_wait_cycling_schedule_absent", lambda *_args: False
    )
    monkeypatch.setattr(workout_builders.time, "sleep", lambda _seconds: None)

    result = workout_builders._compensate_cycling_upload(
        4326,
        scheduled_id="9003",
        schedule_date="2026-09-10",
        schedule_attempted=True,
    )

    assert result["status"] == "recovery_required"
    assert any("Keep generated workout" in item for item in result["recovery_checklist"])
    assert mock_garmin_client.unschedule_workout.call_count == 3
    mock_garmin_client.unschedule_workout.assert_called_with("9003")
    mock_garmin_client.delete_workout.assert_not_called()


@pytest.mark.asyncio
async def test_cycling_builder_reads_back_schedule_id(
    app_with_builders, mock_garmin_client
):
    mock_garmin_client.upload_workout.return_value = {
        "workoutId": 4324,
        "workoutName": "Scheduled ride",
    }
    mock_garmin_client.get_workout_by_id.side_effect = (
        lambda _workout_id: mock_garmin_client.upload_workout.call_args.args[0]
    )
    mock_garmin_client.schedule_workout.return_value = {
        "scheduledWorkoutId": 9001
    }

    _content, payload = await app_with_builders.call_tool(
        "create_hr_target_ride",
        {
            "name": "Scheduled ride",
            "duration_min": 40,
            "hr_zone": "Z2",
            "schedule_date": "2026-09-10",
            "dry_run": False,
        },
    )

    assert payload["status"] == "uploaded"
    assert payload["schedule"]["scheduled_workout_id"] == "9001"
    mock_garmin_client.delete_workout.assert_not_called()


@pytest.mark.asyncio
async def test_cycling_builder_without_upload_id_is_indeterminate(
    app_with_builders, mock_garmin_client
):
    mock_garmin_client.upload_workout.return_value = {"workoutName": "Unknown"}

    _content, payload = await app_with_builders.call_tool(
        "create_hr_target_ride",
        {
            "name": "Unknown",
            "duration_min": 40,
            "hr_zone": "Z2",
            "dry_run": False,
        },
    )

    assert payload["status"] == "indeterminate_recovery_required"
    assert payload["recovery_checklist"]
    mock_garmin_client.delete_workout.assert_not_called()


@pytest.mark.asyncio
async def test_cycling_builder_lost_upload_response_is_indeterminate(
    app_with_builders, mock_garmin_client
):
    mock_garmin_client.upload_workout.side_effect = RuntimeError("lost response")

    _content, payload = await app_with_builders.call_tool(
        "create_hr_target_ride",
        {
            "name": "Maybe created",
            "duration_min": 40,
            "hr_zone": "Z2",
            "dry_run": False,
        },
    )

    assert payload["status"] == "indeterminate_recovery_required"
    assert payload["recovery_checklist"]
