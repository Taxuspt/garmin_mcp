"""Workout resources must describe the JSON they actually return."""
import json

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import workout_templates
from tests.fixtures.garmin_responses import MOCK_SWIM_WORKOUT_DETAILS


def test_swimming_reference_matches_captured_garmin_workout():
    sport = MOCK_SWIM_WORKOUT_DETAILS["sportType"]
    reference = workout_templates.WORKOUT_STRUCTURE_REFERENCE["sportType_values"]
    assert reference[str(sport["sportTypeId"])]["sportTypeKey"] == sport["sportTypeKey"]


@pytest.mark.asyncio
async def test_strength_resource_description_matches_reps_based_groups():
    app = workout_templates.register_resources(FastMCP("Workout resources"))
    resources = await app.list_resources()
    resource = next(r for r in resources if str(r.uri) == "workout://templates/strength-circuit")
    content = await app.read_resource("workout://templates/strength-circuit")
    template = json.loads(content[0].content)
    groups = template["workoutSegments"][0]["workoutSteps"][1:3]
    assert [g["numberOfIterations"] for g in groups] == [3, 3]
    assert [g["workoutSteps"][0]["endConditionValue"] for g in groups] == [10, 8]
    assert "10 reps" in resource.description
    assert "8 reps" in resource.description
    assert "10min work" not in resource.description
