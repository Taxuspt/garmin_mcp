import json
from unittest.mock import Mock
import pytest
from mcp.server.fastmcp import FastMCP
from garmin_mcp import workouts, workout_builders
from garmin_mcp.swimming import pool_template


async def invoke(app, name, args):
    result = await app.call_tool(name, args)
    return json.loads(result[0][0].text)


@pytest.mark.asyncio
async def test_create_and_lint():
    client = Mock()
    client.upload_workout.return_value = {"workoutId": 42, "workoutName": "Swim"}
    client.get_workout_by_id.return_value = pool_template()
    workouts.configure(client)
    workout_builders.configure(client)
    app = FastMCP("swimming")
    workouts.register_tools(app)
    workout_builders.register_tools(app)
    result = await invoke(app, "create_swim_workout", {"name": "Swim", "steps": pool_template()["workoutSegments"][0]["workoutSteps"], "css_seconds_per_100m": 120})
    assert result["workout_id"] == 42
    assert client.upload_workout.call_args.args[0]["poolLength"] == 25
    result = await invoke(app, "validate_workout_structure", {"workout_id": 42, "expected_distance_m": 1000})
    assert result["valid"]
    assert result["totals"]["total_distance_m"] == 1000
    client.get_workout_by_id.side_effect = RuntimeError("API unavailable")
    result = await invoke(app, "validate_workout_structure", {"workout_id": 42})
    assert not result["valid"]
    assert "API unavailable" in result["error"]
