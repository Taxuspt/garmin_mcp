"""Live canonicalization test for running pace targets (GitHub issue #210).

Requires valid Garmin tokens at ~/.garminconnect/garmin_tokens.json.
The test uploads one temporary workout through the real MCP server, verifies
Garmin's stored JSON and generated FIT workout, and always deletes the workout.
The stored-JSON assertion gates the canonicalization. The FIT assertion verifies
that canonicalization preserves the intended range; it does not reproduce the
Connect preview or physical-device symptom reported in the issue.

Run with:
    pytest tests/e2e/test_running_pace_target_live.py -m e2e -s
"""

import asyncio
from io import BytesIO
import json
import os
import sys

from fitparse import FitFile
from garminconnect import Garmin
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest


TOKEN_PATH = os.path.expanduser("~/.garminconnect")

pytestmark = pytest.mark.e2e


def _issue_210_workout() -> dict:
    pace_target = {
        "workoutTargetTypeId": 6,
        "workoutTargetTypeKey": "pace.zone",
    }
    return {
        "workoutName": "e2e issue 210 pace target - DELETE ME",
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": [
                {
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
                    "targetType": pace_target,
                    "targetValueOne": 4.651,
                    "targetValueTwo": 4.878,
                },
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 2,
                    "stepType": {
                        "stepTypeId": 3,
                        "stepTypeKey": "interval",
                    },
                    "endCondition": {
                        "conditionTypeId": 2,
                        "conditionTypeKey": "time",
                    },
                    "endConditionValue": 1500,
                    "targetType": pace_target,
                    "targetValueOne": 4.651,
                    "targetValueTwo": 4.878,
                },
            ],
        }],
    }


def _fit_pace_steps(fit_data: bytes) -> list[dict]:
    steps = []
    for message in FitFile(BytesIO(fit_data)).get_messages("workout_step"):
        fields = {field.name: field.value for field in message}
        if fields.get("target_type") == "speed":
            steps.append(fields)
    return steps


@pytest.mark.asyncio
@pytest.mark.timeout(90)
async def test_running_pace_bounds_are_canonicalized_without_changing_fit_range():
    if not os.path.isdir(TOKEN_PATH):
        pytest.skip("No Garmin token store found")

    garmin = Garmin()
    garmin.login(TOKEN_PATH)
    workout_id = None
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "garmin_mcp"],
        env=None,
    )

    try:
        async with asyncio.timeout(75):
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "upload_workout",
                        arguments={"workout_data": _issue_210_workout()},
                    )

        assert result is not None
        assert result.content
        result_data = json.loads(result.content[0].text)
        workout_id = result_data.get("workout_id")
        assert result_data["status"] == "success"
        assert workout_id is not None

        stored = garmin.get_workout_by_id(workout_id)
        stored_steps = stored["workoutSegments"][0]["workoutSteps"]
        assert len(stored_steps) == 2
        for step in stored_steps:
            assert step["targetType"]["workoutTargetTypeKey"] == "pace.zone"
            assert step["targetValueOne"] == pytest.approx(4.878)
            assert step["targetValueTwo"] == pytest.approx(4.651)

        fit_steps = _fit_pace_steps(garmin.download_workout(workout_id))
        assert len(fit_steps) == 2
        for step in fit_steps:
            assert step["custom_target_speed_low"] == pytest.approx(
                4.651, abs=0.001
            )
            assert step["custom_target_speed_high"] == pytest.approx(
                4.878, abs=0.001
            )
    finally:
        if workout_id is not None:
            garmin.delete_workout(workout_id)
