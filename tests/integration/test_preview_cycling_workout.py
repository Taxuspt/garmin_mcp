"""Preview-only extraction from #295, exercised through actual FastMCP calls."""

import copy
import json

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import workout_builders, workouts


@pytest.fixture
def preview_app(mock_garmin_client):
    workout_builders.configure(mock_garmin_client)
    return workout_builders.register_tools(FastMCP("Cycling preview"))


async def preview(app, steps, name="Cycling intervals"):
    response = await app.call_tool(
        "preview_cycling_workout", {"name": name, "steps": steps}
    )
    return json.loads(response[0][0].text)


def intervals():
    return [
        {"type": "warmup", "duration_seconds": 300, "hr_zone": "Z2"},
        {
            "type": "repeat",
            "repeats": 3,
            "steps": [
                {"duration_seconds": 60, "power_zone": 4},
                {
                    "type": "recovery",
                    "duration_seconds": 120,
                    "hr_min": 110,
                    "hr_max": 130,
                },
            ],
        },
        {"type": "cooldown", "distance_m": 1000},
    ]


@pytest.mark.asyncio
async def test_preview_builds_correct_sport_targets_and_repeat_summary(
    preview_app, mock_garmin_client
):
    result = await preview(preview_app, intervals())
    assert result["status"] == "success"
    assert result["write_performed"] is False
    payload = result["workout_data"]
    assert payload["sportType"] == {"sportTypeId": 2, "sportTypeKey": "cycling"}
    steps = payload["workoutSegments"][0]["workoutSteps"]
    assert [step["stepOrder"] for step in steps] == [1, 2, 3]
    assert steps[0]["zoneNumber"] == 2
    assert steps[0]["targetType"]["workoutTargetTypeId"] == 4
    repeat = steps[1]
    assert repeat["type"] == "RepeatGroupDTO"
    assert repeat["numberOfIterations"] == 3
    assert repeat["workoutSteps"][0]["targetType"] == {
        "workoutTargetTypeId": 2,
        "workoutTargetTypeKey": "power.zone",
    }
    assert repeat["workoutSteps"][0]["zoneNumber"] == 4
    hr_step = repeat["workoutSteps"][1]
    assert (hr_step["targetValueOne"], hr_step["targetValueTwo"]) == (110, 130)
    assert "targetValueOne" not in hr_step["targetType"]
    assert steps[2]["endCondition"] == {
        "conditionTypeId": 3,
        "conditionTypeKey": "distance",
    }
    assert result["summary"] == {
        "expanded_steps": 8,
        "timed_duration_seconds": 840,
        "distance_m": 1000,
    }
    assert mock_garmin_client.mock_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("zone", [1, 7, "4"])
async def test_named_power_zones_use_main_mapping(
    preview_app, mock_garmin_client, zone
):
    result = await preview(preview_app, [{"duration_seconds": 60, "power_zone": zone}])
    assert result["status"] == "success"
    step = result["workout_data"]["workoutSegments"][0]["workoutSteps"][0]
    assert step["zoneNumber"] == int(zone)
    assert step["targetType"]["workoutTargetTypeId"] == 2
    assert "targetValueOne" not in step
    assert mock_garmin_client.mock_calls == []


@pytest.mark.asyncio
async def test_explicit_upload_consumes_the_preview_payload(
    preview_app, mock_garmin_client
):
    result = await preview(preview_app, intervals())
    assert mock_garmin_client.mock_calls == []
    workouts.configure(mock_garmin_client)
    upload_app = workouts.register_tools(FastMCP("Explicit upload"))
    mock_garmin_client.upload_workout.return_value = {
        "workoutId": 123,
        "workoutName": "Cycling intervals",
    }
    response = await upload_app.call_tool(
        "upload_workout", {"workout_data": result["workout_data"]}
    )
    uploaded = json.loads(response[0][0].text)
    assert uploaded["status"] == "success"
    mock_garmin_client.upload_workout.assert_called_once()
    actual = mock_garmin_client.upload_workout.call_args.args[0]
    assert actual["sportType"]["sportTypeKey"] == "cycling"
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "step",
    [
        {"duration_seconds": 0},
        {"duration_seconds": -1},
        {"duration_seconds": True},
        {"duration_seconds": float("inf")},
        {"duration_seconds": float("nan")},
        {"duration_seconds": 60, "distance_m": 100},
        {},
        {"type": "unknown", "duration_seconds": 60},
        {"duration_seconds": 60, "power_zone": 0},
        {"duration_seconds": 60, "power_zone": 8},
        {"duration_seconds": 60, "power_zone": 2.5},
        {"duration_seconds": 60, "power_zone": True},
        {"duration_seconds": 60, "hr_zone": "Z6"},
        {"duration_seconds": 60, "hr_min": 130},
        {"duration_seconds": 60, "hr_min": 130, "hr_max": 120},
        {"duration_seconds": 60, "hr_min": 0, "hr_max": 120},
        {"duration_seconds": 60, "hr_min": 100, "hr_max": float("inf")},
        {"duration_seconds": 60, "hr_zone": "Z2", "power_zone": 2},
        {"duration_seconds": 60, "hr_zone": "Z2", "hr_min": 100, "hr_max": 120},
        {"duration_seconds": 60, "power_min": 150, "power_max": 200},
        {"duration_seconds": 60, "typo": 1},
        {"type": "repeat", "repeats": 1, "steps": [{"duration_seconds": 60}]},
        {"type": "repeat", "repeats": 2.5, "steps": [{"duration_seconds": 60}]},
        {"type": "repeat", "repeats": 2, "steps": []},
        {
            "type": "repeat",
            "repeats": 2,
            "steps": [{"duration_seconds": 60}],
            "duration_seconds": 20,
        },
    ],
)
async def test_invalid_steps_never_write(preview_app, mock_garmin_client, step):
    result = await preview(preview_app, [step])
    assert result["status"] == "failed"
    assert result["write_performed"] is False
    assert "message" in result
    assert mock_garmin_client.mock_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,steps",
    [
        ("", [{"duration_seconds": 60}]),
        ("  ", [{"duration_seconds": 60}]),
        ("Ride", []),
    ],
)
async def test_empty_inputs_are_rejected(preview_app, mock_garmin_client, name, steps):
    result = await preview(preview_app, steps, name)
    assert result["status"] == "failed"
    assert mock_garmin_client.mock_calls == []


@pytest.mark.asyncio
async def test_depth_and_expansion_limits(preview_app, mock_garmin_client):
    steps = [{"duration_seconds": 1}]
    for _ in range(5):
        steps = [{"type": "repeat", "repeats": 2, "steps": steps}]
    result = await preview(preview_app, steps)
    assert result["status"] == "failed"
    assert "depth" in result["message"]
    result = await preview(
        preview_app,
        [
            {
                "type": "repeat",
                "repeats": 50,
                "steps": [
                    {
                        "type": "repeat",
                        "repeats": 50,
                        "steps": [{"duration_seconds": 1}],
                    }
                ],
            }
        ],
    )
    assert result["status"] == "failed"
    assert "expanded" in result["message"]
    assert mock_garmin_client.mock_calls == []


@pytest.mark.asyncio
async def test_preview_is_deterministic_and_does_not_mutate_inputs(
    preview_app, mock_garmin_client
):
    source = intervals()
    before = copy.deepcopy(source)
    first = await preview(preview_app, source)
    second = await preview(preview_app, source)
    assert first == second
    assert source == before
    assert mock_garmin_client.mock_calls == []


@pytest.mark.asyncio
async def test_preview_effect_and_profile_contract(preview_app):
    from garmin_mcp import tool_annotations, tool_profiles

    names = {t.name for t in await preview_app.list_tools()}
    assert "preview_cycling_workout" in names
    assert "preview_cycling_workout" in tool_profiles.WORKOUTS_TOOLS
    assert "preview_cycling_workout" not in tool_profiles.MINIMAL_TOOLS
    effect = tool_annotations.annotations_for("preview_cycling_workout")
    assert effect.readOnlyHint is True
    assert effect.destructiveHint is False
    assert effect.openWorldHint is False


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [100, 101])
async def test_definition_count_boundary(preview_app, mock_garmin_client, limit):
    result = await preview(preview_app, [{"duration_seconds": 1} for _ in range(limit)])
    assert result["status"] == ("success" if limit == 100 else "failed")
    if limit == 100:
        assert result["summary"]["expanded_steps"] == 100
    assert mock_garmin_client.mock_calls == []


@pytest.mark.asyncio
async def test_exact_expansion_limit_stays_compact(preview_app, mock_garmin_client):
    result = await preview(
        preview_app,
        [
            {
                "type": "repeat",
                "repeats": 50,
                "steps": [
                    {
                        "type": "repeat",
                        "repeats": 20,
                        "steps": [{"duration_seconds": 1}],
                    }
                ],
            }
        ],
    )
    assert result["status"] == "success"
    assert result["summary"]["expanded_steps"] == 1000
    assert result["summary"]["timed_duration_seconds"] == 1000
    assert len(result["workout_data"]["workoutSegments"][0]["workoutSteps"]) == 1
    assert mock_garmin_client.mock_calls == []


@pytest.mark.asyncio
async def test_total_numeric_overflow_is_refused(preview_app, mock_garmin_client):
    result = await preview(
        preview_app,
        [{"type": "repeat", "repeats": 50, "steps": [{"duration_seconds": 1e308}]}],
    )
    assert result["status"] == "failed"
    assert result["write_performed"] is False
    assert mock_garmin_client.mock_calls == []


@pytest.mark.asyncio
async def test_exact_depth_limit(preview_app, mock_garmin_client):
    steps = [{"duration_seconds": 1}]
    for _ in range(3):
        steps = [{"type": "repeat", "repeats": 2, "steps": steps}]
    result = await preview(preview_app, steps)
    assert result["status"] == "success"
    assert result["summary"]["expanded_steps"] == 8
    assert mock_garmin_client.mock_calls == []


@pytest.mark.asyncio
async def test_preview_does_not_require_an_authenticated_client(monkeypatch):
    class NoClient:
        def __getattr__(self, name):
            raise AssertionError(f"Preview touched Garmin client attribute: {name}")

    monkeypatch.setattr(workout_builders, "garmin_client", NoClient())
    app = workout_builders.register_tools(FastMCP("Offline preview"))
    result = await preview(app, [{"duration_seconds": 60}])
    assert result["status"] == "success"
    assert result["workout_data"]["workoutSegments"][0]["workoutSteps"][0][
        "targetType"
    ] == {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
