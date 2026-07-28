"""Live regression test for target values misplaced inside targetType.

Run with:
pytest tests/e2e/test_nested_workout_target_values_live.py -m e2e -s
"""

import asyncio
import json
import sys
from io import BytesIO

import pytest
from fitparse import FitFile
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from garmin_mcp import email, init_api, password


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.timeout(90)
async def test_nested_pace_bounds_survive_inline_schedule_and_fit_export():
    """MCP repairs issue #210's payload before Garmin silently drops its bounds."""
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "garmin_mcp"],
        env=None,
    )
    workout_data = {
        "workoutName": "e2e nested target values - DELETE ME",
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
                    "targetValueOne": 2.0833333,
                    "targetValueTwo": 1.9607843,
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
                                "calendar_date": "2099-01-01",
                                "workout_data": workout_data,
                            }]
                        },
                    )
                result_data = json.loads(result.content[0].text)
                assert result_data["succeeded"] == 1
                assert result_data["failed"] == 0
                workout_id = result_data["results"][0].get("workout_id")
                assert workout_id is not None

                client = init_api(email, password)
                assert client is not None
                stored = client.get_workout_by_id(workout_id)
                stored_step = stored["workoutSegments"][0]["workoutSteps"][0]
                assert stored_step["targetValueOne"] == pytest.approx(
                    2.0833333,
                )
                assert stored_step["targetValueTwo"] == pytest.approx(
                    1.9607843,
                )

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
                if workout_id is not None:
                    async with asyncio.timeout(20):
                        delete_result = await session.call_tool(
                            "delete_workout",
                            arguments={"workout_id": workout_id},
                        )
                    delete_data = json.loads(delete_result.content[0].text)
                    assert delete_data["status"] == "success"
