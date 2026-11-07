"""Scheduling should expose Garmin's calendar-entry ID without another write.

Extracted from the scheduling support in PR #295. Transport is mocked; these
contracts do not assert that every Garmin deployment returns an ID.
"""

import json
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import workouts


@pytest.fixture
def scheduling_app(mock_garmin_client):
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {"workoutScheduleSummariesScalar": []}
    }
    workouts.configure(mock_garmin_client)
    return workouts.register_tools(FastMCP("Scheduling IDs"))


async def _schedule(app, tool):
    args = {"workout_id": 42, "calendar_date": "2026-11-10"}
    if tool == "schedule_workouts":
        args = {"schedules": [args]}
    result = await app.call_tool(tool, args)
    data = json.loads(result[0][0].text)
    return data if tool == "schedule_workout" else data["results"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["schedule_workout", "schedule_workouts"])
@pytest.mark.parametrize("payload", [
    {"workoutScheduleId": 701, "workoutId": 42},
    {"scheduledWorkoutId": 701},
    {"scheduled_workout_id": "701"},
    {"scheduleId": 701},
    {"id": 701},
    {"data": {"scheduledWorkoutId": 701}},
    {"result": {"scheduleId": 701}},
    [{"workoutScheduleId": 701}],
])
async def test_schedule_preserves_calendar_id(
    scheduling_app, mock_garmin_client, tool, payload
):
    response = mock_garmin_client.client.post.return_value
    response.status_code = 200
    response.json.return_value = payload

    entry = await _schedule(scheduling_app, tool)

    assert entry["status"] == "success"
    assert entry["workout_id"] == 42
    assert entry["scheduled_workout_id"] == "701"
    mock_garmin_client.client.post.assert_called_once_with(
        "connectapi", "workout-service/schedule/42", json={"date": "2026-11-10"}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["schedule_workout", "schedule_workouts"])
@pytest.mark.parametrize("payload", [
    {}, None, [], "", {"workoutId": 42}, {"id": 42, "workoutId": 42},
    {"data": {"workout": {"id": 42}}}, {"scheduledWorkoutId": None},
    {"scheduledWorkoutId": True}, {"scheduledWorkoutId": {}},
    {"scheduledWorkoutId": "  "}, {"scheduledWorkoutId": 1.5},
])
async def test_missing_or_invalid_id_does_not_invent_one(
    scheduling_app, mock_garmin_client, tool, payload
):
    response = mock_garmin_client.client.post.return_value
    response.status_code = 200
    response.json.return_value = payload

    entry = await _schedule(scheduling_app, tool)

    assert entry["status"] == "success"
    assert "scheduled_workout_id" not in entry
    mock_garmin_client.client.post.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["schedule_workout", "schedule_workouts"])
async def test_non_json_success_is_still_success(scheduling_app, mock_garmin_client, tool):
    response = mock_garmin_client.client.post.return_value
    response.status_code = 200
    response.json.side_effect = ValueError("No JSON body")

    entry = await _schedule(scheduling_app, tool)

    assert entry["status"] == "success"
    assert "scheduled_workout_id" not in entry
    mock_garmin_client.client.post.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["schedule_workout", "schedule_workouts"])
async def test_failed_post_does_not_report_a_calendar_id(
    scheduling_app, mock_garmin_client, tool
):
    response = mock_garmin_client.client.post.return_value
    response.status_code = 500
    response.json.return_value = {"scheduledWorkoutId": 701}

    entry = await _schedule(scheduling_app, tool)

    assert entry["status"] == "failed"
    assert "scheduled_workout_id" not in entry
    response.json.assert_not_called()


@pytest.mark.asyncio
async def test_batch_preserves_each_entries_own_id(scheduling_app, mock_garmin_client):
    mock_garmin_client.client.post.side_effect = [
        Mock(status_code=200, json=Mock(return_value={"workoutScheduleId": 701})),
        Mock(status_code=200, json=Mock(return_value={"workoutScheduleId": 702})),
    ]
    result = await scheduling_app.call_tool("schedule_workouts", {"schedules": [
        {"workout_id": 42, "calendar_date": "2026-11-10"},
        {"workout_id": 43, "calendar_date": "2026-11-11"},
    ]})
    entries = json.loads(result[0][0].text)["results"]

    assert [e["scheduled_workout_id"] for e in entries] == ["701", "702"]
    assert [e["workout_id"] for e in entries] == [42, 43]
    assert mock_garmin_client.client.post.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["schedule_workout", "schedule_workouts"])
@pytest.mark.parametrize("status", [201, 204])
async def test_all_successful_http_codes_keep_success(
    scheduling_app, mock_garmin_client, tool, status
):
    response = mock_garmin_client.client.post.return_value
    response.status_code = status
    response.json.side_effect = ValueError("Empty body")

    entry = await _schedule(scheduling_app, tool)

    assert entry["status"] == "success"
    assert "scheduled_workout_id" not in entry


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["schedule_workout", "schedule_workouts"])
async def test_existing_schedule_remains_a_noop(scheduling_app, mock_garmin_client, tool):
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {"workoutScheduleSummariesScalar": [
            {"workoutId": 42, "scheduleDate": "2026-11-10", "scheduledWorkoutId": 701}
        ]}
    }

    entry = await _schedule(scheduling_app, tool)

    assert entry["status"] == "success"
    assert entry["idempotent"] is True
    mock_garmin_client.client.post.assert_not_called()
