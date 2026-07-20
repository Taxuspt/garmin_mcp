import json
import zipfile

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import workout_templates
from garmin_mcp.workouts import (
    _validate_end_condition_steps,
    _validate_target_type_steps,
)


CYCLING_TEMPLATE_URI = "workout://templates/cycling-power-intervals"


@pytest.mark.asyncio
async def test_cycling_power_interval_resource_is_registered_and_valid() -> None:
    app = FastMCP("Workout Templates")
    workout_templates.register_resources(app)

    resources = await app.list_resources()
    assert CYCLING_TEMPLATE_URI in {str(resource.uri) for resource in resources}

    contents = await app.read_resource(CYCLING_TEMPLATE_URI)
    workout = json.loads(contents[0].content)

    assert workout["sportType"] == {
        "sportTypeId": 2,
        "sportTypeKey": "cycling",
    }
    assert workout["workoutSegments"][0]["sportType"] == workout["sportType"]

    repeat_group = workout["workoutSegments"][0]["workoutSteps"][1]
    assert repeat_group["type"] == "RepeatGroupDTO"
    assert repeat_group["endCondition"] == {
        "conditionTypeId": 7,
        "conditionTypeKey": "iterations",
    }
    assert repeat_group["endConditionValue"] == repeat_group["numberOfIterations"]

    for step in repeat_group["workoutSteps"]:
        assert step["targetType"] == {
            "workoutTargetTypeId": 9,
            "workoutTargetTypeKey": "power.lap",
        }

    _validate_end_condition_steps(workout)
    _validate_target_type_steps(workout)


def test_dxt_contains_current_manifest() -> None:
    with open("dxt/manifest.json", "rb") as manifest_file:
        expected = manifest_file.read()

    with zipfile.ZipFile("garmin-mcp.dxt") as extension:
        assert extension.namelist() == ["manifest.json"]
        assert extension.read("manifest.json") == expected
