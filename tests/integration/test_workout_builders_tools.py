"""
Integration tests for high-level workout builder tools (workout_builders.py).
"""
import json

import pytest
from mcp.server.fastmcp import FastMCP
from unittest.mock import MagicMock

from garmin_mcp import exercise_catalog, workouts, workout_builders


@pytest.fixture
def app_with_builders(mock_garmin_client):
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
async def test_create_strength_resolution_uploads_canonical_pair(
    app_with_builders, mock_garmin_client, monkeypatch
):
    monkeypatch.setattr(exercise_catalog, "resolve_strength_exercises", lambda exercises: {
        "status": "ready",
        "resolved_exercises": [{**exercises[0], "category": "CRUNCH", "exercise_name": "REVERSE_CRUNCH"}],
    })
    mock_garmin_client.upload_workout.return_value = {"workoutId": 42, "workoutName": "Core"}
    result = await app_with_builders.call_tool("create_strength_workout", {
        "name": "Core", "resolve_exercises": True,
        "exercises": [{"name": "Reverse Crunch", "sets": 3, "reps": 10}],
    })
    assert json.loads(result[0][0].text)["status"] == "success"
    step = mock_garmin_client.upload_workout.call_args.args[0]["workoutSegments"][0]["workoutSteps"][0]
    assert (step["category"], step["exerciseName"]) == ("CRUNCH", "REVERSE_CRUNCH")
    assert step["description"].startswith("Reverse Crunch:")


@pytest.mark.asyncio
async def test_create_strength_resolution_blocks_entire_upload(
    app_with_builders, mock_garmin_client, monkeypatch
):
    monkeypatch.setattr(exercise_catalog, "resolve_strength_exercises", lambda exercises: {
        "status": "needs_review", "resolved_exercises": [],
        "unresolved_items": [{"index": 0, "status": "ambiguous", "alternatives": []}],
    })
    result = await app_with_builders.call_tool("create_strength_workout", {
        "name": "Core", "resolve_exercises": True, "exercises": [{"name": "raise"}],
    })
    assert json.loads(result[0][0].text)["status"] == "needs_review"
    mock_garmin_client.upload_workout.assert_not_called()


@pytest.mark.asyncio
async def test_create_strength_catalog_outage_uses_legacy_upload(
    app_with_builders, mock_garmin_client, monkeypatch
):
    monkeypatch.setattr(exercise_catalog, "resolve_strength_exercises", lambda exercises: {
        "status": "catalog_unavailable", "resolved_exercises": [],
    })
    mock_garmin_client.upload_workout.return_value = {"workoutId": 43, "workoutName": "Legacy"}
    result = await app_with_builders.call_tool("create_strength_workout", {
        "name": "Legacy", "resolve_exercises": True,
        "exercises": [{"name": "Custom movement", "sets": 1, "reps": 2}],
    })
    payload = json.loads(result[0][0].text)
    assert payload["status"] == "success" and "catalog unavailable" in payload["warning"].lower()
    mock_garmin_client.upload_workout.assert_called_once()


@pytest.mark.asyncio
async def test_create_strength_default_does_not_resolve(
    app_with_builders, mock_garmin_client, monkeypatch
):
    resolve = MagicMock(side_effect=AssertionError("catalog should not load"))
    monkeypatch.setattr(exercise_catalog, "resolve_strength_exercises", resolve)
    mock_garmin_client.upload_workout.return_value = {"workoutId": 44, "workoutName": "Legacy"}
    await app_with_builders.call_tool("create_strength_workout", {
        "name": "Legacy", "exercises": [{"name": "Custom", "sets": 1, "reps": 2}],
    })
    resolve.assert_not_called()
    mock_garmin_client.upload_workout.assert_called_once()
