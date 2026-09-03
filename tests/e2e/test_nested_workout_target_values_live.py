"""Live regression test for target values misplaced inside targetType.

Run with:
pytest tests/e2e/test_nested_workout_target_values_live.py -m e2e -s
"""

import asyncio
import json
import sys
import uuid
import warnings
from io import BytesIO

import pytest
from fitparse import FitFile
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from garmin_mcp import email, init_api, password

_CALENDAR_DATE = "2099-01-01"


def _decode_result(result):
    try:
        return json.loads(result.content[0].text)
    except (AttributeError, IndexError, json.JSONDecodeError, TypeError):
        return {}


async def _cleanup_scheduled_workout(session, workout_id, workout_name):
    """Clean only IDs/name created by this invocation, even after partial failure."""
    failures = []
    workout_ids = {str(workout_id)} if workout_id is not None else set()
    scheduled_workout_ids = set()

    try:
        for attempt in range(12):
            listed = await session.call_tool("get_workouts", arguments={})
            for workout in _decode_result(listed).get("workouts", []):
                if workout.get("name") == workout_name and workout.get("id") is not None:
                    workout_ids.add(str(workout["id"]))

            scheduled_result = await session.call_tool(
                "get_scheduled_workouts",
                arguments={
                    "start_date": _CALENDAR_DATE,
                    "end_date": _CALENDAR_DATE,
                },
            )
            for item in _decode_result(scheduled_result).get("scheduled_workouts", []):
                item_workout_id = item.get("workout_id")
                matches_id = (
                    item_workout_id is not None
                    and str(item_workout_id) in workout_ids
                )
                if item.get("name") != workout_name and not matches_id:
                    continue
                if item_workout_id is not None:
                    workout_ids.add(str(item_workout_id))
                if item.get("scheduled_workout_id") is not None:
                    scheduled_workout_ids.add(item["scheduled_workout_id"])
            if scheduled_workout_ids or attempt == 11:
                break
            await asyncio.sleep(1)
    except Exception as error:
        failures.append(f"cleanup lookup failed: {error}")

    safe_to_delete = bool(scheduled_workout_ids)
    for scheduled_workout_id in scheduled_workout_ids:
        disappeared = False
        for delete_attempt in range(3):
            try:
                removed = await session.call_tool(
                    "unschedule_workout",
                    arguments={"scheduled_workout_id": scheduled_workout_id},
                )
                if _decode_result(removed).get("read_back_absent") is True:
                    disappeared = True
            except Exception:
                pass
            if disappeared:
                break
            for read_attempt in range(5):
                scheduled_result = await session.call_tool(
                    "get_scheduled_workouts",
                    arguments={
                        "start_date": _CALENDAR_DATE,
                        "end_date": _CALENDAR_DATE,
                    },
                )
                remaining = _decode_result(scheduled_result).get(
                    "scheduled_workouts", []
                )
                if not any(
                    str(item.get("scheduled_workout_id"))
                    == str(scheduled_workout_id)
                    for item in remaining
                ):
                    disappeared = True
                    break
                if read_attempt < 4:
                    await asyncio.sleep(1)
            if disappeared:
                break
            if delete_attempt < 2:
                await asyncio.sleep(1)
        if not disappeared:
            safe_to_delete = False
            failures.append(
                f"calendar entry {scheduled_workout_id} remained after exact unschedule retries"
            )

    if not scheduled_workout_ids:
        failures.append(
            "schedule outcome stayed indeterminate; retained the unique workout template"
        )

    for generated_workout_id in workout_ids if safe_to_delete else ():
        try:
            result = await session.call_tool(
                "delete_workout",
                arguments={"workout_id": generated_workout_id},
            )
            data = _decode_result(result)
            if data.get("status") != "success":
                failures.append(f"delete {generated_workout_id} failed: {data}")
        except Exception as error:
            failures.append(f"delete {generated_workout_id} failed: {error}")

    if failures:
        warnings.warn("; ".join(failures), stacklevel=2)
    return failures


@pytest.mark.e2e
@pytest.mark.live_write
@pytest.mark.asyncio
@pytest.mark.timeout(90)
async def test_nested_pace_bounds_survive_inline_schedule_and_fit_export():
    """MCP repairs issue #210's payload before Garmin silently drops its bounds."""
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "garmin_mcp"],
        env=None,
    )
    workout_name = f"e2e nested target contract {uuid.uuid4().hex}"
    workout_data = {
        "workoutName": workout_name,
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": [{
                "type": "ExecutableStepDTO",
                "stepOrder": 1,
                "stepType": {
                    "stepTypeId": 3,
                    "stepTypeKey": "interval",
                },
                "endCondition": {
                    "conditionTypeId": 3,
                    "conditionTypeKey": "distance",
                },
                "endConditionValue": 400,
                "targetType": {
                    "workoutTargetTypeId": 6,
                    "workoutTargetTypeKey": "pace.zone",
                    "targetValueOne": 1.9607843,
                    "targetValueTwo": 2.0833333,
                },
            }],
        }],
    }

    workout_id = None
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            try:
                async with asyncio.timeout(60):
                    result = await session.call_tool(
                        "schedule_workouts",
                        arguments={
                            "schedules": [{
                                "calendar_date": _CALENDAR_DATE,
                                "workout_data": workout_data,
                            }]
                        },
                    )
                result_data = json.loads(result.content[0].text)
                assert result_data["succeeded"] == 1
                assert result_data["failed"] == 0
                workout_id = result_data["results"][0].get("workout_id")
                assert workout_id is not None

                stored_result = await session.call_tool(
                    "get_workout_by_id",
                    arguments={"workout_id": workout_id},
                )
                stored = json.loads(stored_result.content[0].text)
                stored_step = stored["segments"][0]["steps"][0]
                assert stored_step["target_value_low"] == pytest.approx(
                    1.9607843,
                )
                assert stored_step["target_value_high"] == pytest.approx(
                    2.0833333,
                )

                # The MCP download tool intentionally returns metadata rather
                # than FIT bytes, so use a direct client only for FIT parsing.
                # All MCP and direct-client calls remain sequential.
                client = init_api(email, password)
                assert client is not None
                fit_bytes = client.download_workout(workout_id)
                fit_steps = list(
                    FitFile(BytesIO(fit_bytes)).get_messages("workout_step")
                )
                assert len(fit_steps) == 1
                fields = {
                    field.name: field.value
                    for field in fit_steps[0]
                }
                assert fields["target_type"] == "speed"
                assert fields["custom_target_speed_low"] == pytest.approx(
                    1.961,
                    abs=0.001,
                )
                assert fields["custom_target_speed_high"] == pytest.approx(
                    2.083,
                    abs=0.001,
                )
            finally:
                async with asyncio.timeout(20):
                    cleanup_failures = await _cleanup_scheduled_workout(
                        session,
                        workout_id,
                        workout_name,
                    )
                assert not cleanup_failures, "; ".join(cleanup_failures)
