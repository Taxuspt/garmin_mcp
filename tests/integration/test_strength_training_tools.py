"""Integration tests for structured strength activity MCP tools."""

import json
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import strength_training


CATALOG = (
    ("PULL_UP", "PULL_UP"),
    ("PUSH_UP", "PUSH_UP"),
    ("SQUAT", "BARBELL_BACK_SQUAT"),
)


@pytest.fixture
def strength_client(monkeypatch):
    client = Mock()
    client.garmin_connect_activity = "/activity-service/activity"
    client.client = Mock()
    client.client.put = Mock(return_value={"accepted": True})
    client.get_activity = Mock(
        return_value={
            "activityId": 12345678901,
            "activityTypeDTO": {"typeKey": "strength_training"},
            "summaryDTO": {
                "startTimeGMT": "2026-07-23T08:00:00.0",
                "startTimeLocal": "2026-07-23T10:00:00.0",
                "duration": 3600.0,
            },
        }
    )
    client.get_activity_exercise_sets = Mock(return_value={})
    client.create_manual_activity = Mock(return_value={"activityId": 987654321})
    client.delete_activity = Mock(return_value={})
    monkeypatch.setattr(
        strength_training, "_load_garmin_exercise_catalog", lambda: CATALOG
    )
    strength_training.configure(client)
    return client


@pytest.fixture
def strength_app(strength_client):
    app = FastMCP("Test Structured Strength")
    return strength_training.register_tools(app)


def _data(result):
    return json.loads(result[0][0].text)


@pytest.mark.asyncio
async def test_dry_run_resolves_and_expands_sets_without_writing(
    strength_app, strength_client
):
    result = await strength_app.call_tool(
        "set_activity_strength_exercise_sets",
        {
            "activity_id": "12345678901",
            "sets": [
                {
                    "exercise": "push up",
                    "sets": 3,
                    "repetitions": 10,
                    "duration_seconds": 35,
                    "rest_seconds": 85,
                }
            ],
            "dry_run": True,
        },
    )

    data = _data(result)
    assert data["success"] is True
    assert data["dry_run"] is True
    assert data["replacement_set_count"] == 3
    assert data["matches"][0]["category"] == "PUSH_UP"
    assert len(data["preview"]["exerciseSets"]) == 3
    strength_client.get_activity.assert_called_once_with(12345678901)
    strength_client.client.put.assert_not_called()
    strength_client.get_activity_exercise_sets.assert_not_called()


@pytest.mark.asyncio
async def test_write_requires_explicit_confirmation(
    strength_app, strength_client
):
    result = await strength_app.call_tool(
        "set_activity_strength_exercise_sets",
        {
            "activity_id": 12345678901,
            "sets": [{"exercise": "push up", "repetitions": 10}],
        },
    )

    data = _data(result)
    assert data["success"] is False
    assert "confirm=true is required" in data["error"]
    strength_client.get_activity.assert_not_called()
    strength_client.client.put.assert_not_called()


@pytest.mark.asyncio
async def test_confirmed_write_uses_exercise_sets_endpoint_and_verifies_readback(
    strength_app, strength_client
):
    written_payload = {}

    def capture_put(service, url, *, json, api):
        written_payload.update(json)
        assert service == "connectapi"
        assert url == "/activity-service/activity/12345678901/exerciseSets"
        assert api is True
        return {"accepted": True}

    strength_client.client.put.side_effect = capture_put
    strength_client.get_activity_exercise_sets.side_effect = (
        lambda activity_id: written_payload
    )

    result = await strength_app.call_tool(
        "set_activity_strength_exercise_sets",
        {
            "activity_id": "12345678901",
            "sets": [
                {
                    "category": "PUSH_UP",
                    "name": "PUSH_UP",
                    "sets": 2,
                    "repetitions": 12,
                    "weight_kg": 5,
                    "duration_seconds": 40,
                    "rest_seconds": 80,
                }
            ],
            "confirm": True,
        },
    )

    data = _data(result)
    assert data["success"] is True
    assert data["written"] is True
    assert data["verified"] is True
    assert written_payload["activityId"] == 12345678901
    assert len(written_payload["exerciseSets"]) == 2
    assert written_payload["exerciseSets"][0]["weight"] == 5000.0
    assert written_payload["exerciseSets"][0]["startTime"] == (
        "2026-07-23T08:00:00.0"
    )
    assert written_payload["exerciseSets"][1]["startTime"] == (
        "2026-07-23T08:02:00.0"
    )
    strength_client.get_activity_exercise_sets.assert_called_once_with(
        12345678901
    )


@pytest.mark.asyncio
async def test_non_strength_activity_is_rejected(
    strength_app, strength_client
):
    strength_client.get_activity.return_value["activityTypeDTO"] = {
        "typeKey": "running"
    }

    result = await strength_app.call_tool(
        "set_activity_strength_exercise_sets",
        {
            "activity_id": 12345678901,
            "sets": [{"exercise": "push up"}],
            "confirm": True,
        },
    )

    data = _data(result)
    assert data["success"] is False
    assert "not 'strength_training'" in data["error"]
    strength_client.client.put.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_activity_id_is_rejected_before_api_calls(
    strength_app, strength_client
):
    result = await strength_app.call_tool(
        "set_activity_strength_exercise_sets",
        {
            "activity_id": "not-an-id",
            "sets": [{"exercise": "push up"}],
            "dry_run": True,
        },
    )

    data = _data(result)
    assert data["success"] is False
    assert "positive integer" in data["error"]
    strength_client.get_activity.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_exercise_prevents_write(
    strength_app, strength_client
):
    result = await strength_app.call_tool(
        "set_activity_strength_exercise_sets",
        {
            "activity_id": 12345678901,
            "sets": [{"exercise": "definitely not a real exercise"}],
            "confirm": True,
        },
    )

    data = _data(result)
    assert data["success"] is False
    assert "No Garmin exercise" in data["error"]
    strength_client.client.put.assert_not_called()


@pytest.mark.asyncio
async def test_readback_mismatch_is_reported_as_written_but_unverified(
    strength_app, strength_client
):
    strength_client.get_activity_exercise_sets.return_value = {
        "activityId": 12345678901,
        "exerciseSets": [],
    }

    result = await strength_app.call_tool(
        "set_activity_strength_exercise_sets",
        {
            "activity_id": 12345678901,
            "sets": [{"exercise": "push up", "repetitions": 10}],
            "confirm": True,
        },
    )

    data = _data(result)
    assert data["success"] is False
    assert data["written"] is True
    assert data["verified"] is False
    assert "read-back verification failed" in data["error"]
    assert "set count differs" in data["verification_errors"][0]


@pytest.mark.asyncio
async def test_explicit_start_datetime_overrides_activity_timestamp(
    strength_app, strength_client
):
    result = await strength_app.call_tool(
        "set_activity_strength_exercise_sets",
        {
            "activity_id": 12345678901,
            "sets": [{"exercise": "push up"}],
            "activity_start_datetime": "2026-07-23T18:00:00",
            "time_zone": "Europe/Warsaw",
            "dry_run": True,
        },
    )

    data = _data(result)
    assert data["preview"]["exerciseSets"][0]["startTime"] == (
        "2026-07-23T16:00:00.0"
    )


@pytest.mark.asyncio
async def test_set_timeline_cannot_extend_past_activity_duration(
    strength_app, strength_client
):
    result = await strength_app.call_tool(
        "set_activity_strength_exercise_sets",
        {
            "activity_id": 12345678901,
            "sets": [
                {
                    "exercise": "push up",
                    "start_time": "09:59:50",
                    "duration_seconds": 30,
                }
            ],
            "dry_run": True,
        },
    )

    data = _data(result)
    assert data["success"] is False
    assert "after the activity" in data["error"]
    strength_client.client.put.assert_not_called()


@pytest.mark.asyncio
async def test_exact_identifiers_work_when_catalog_fetch_fails(
    strength_app, strength_client, monkeypatch
):
    def unavailable():
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(
        strength_training, "_load_garmin_exercise_catalog", unavailable
    )

    result = await strength_app.call_tool(
        "set_activity_strength_exercise_sets",
        {
            "activity_id": 12345678901,
            "sets": [{"category": "PUSH_UP", "name": "PUSH_UP"}],
            "dry_run": True,
        },
    )

    data = _data(result)
    assert data["success"] is True
    assert data["matches"][0]["match_type"] == "provided_unverified"


@pytest.mark.asyncio
async def test_create_strength_activity_dry_run_does_not_create_or_write(
    strength_app, strength_client
):
    result = await strength_app.call_tool(
        "create_strength_training_activity",
        {
            "activity_name": "Upper Body",
            "start_datetime": "2026-07-23T18:00:00",
            "time_zone": "Europe/Warsaw",
            "duration_minutes": 30,
            "sets": [
                {
                    "exercise": "push up",
                    "sets": 3,
                    "repetitions": 10,
                    "duration_seconds": 30,
                    "rest_seconds": 60,
                }
            ],
            "dry_run": True,
        },
    )

    data = _data(result)
    assert data["success"] is True
    assert data["dry_run"] is True
    assert data["set_count"] == 3
    assert data["preview"]["startTimeLocal"] == "2026-07-23T18:00:00.000"
    assert data["preview"]["exerciseSets"][0]["startTime"] == (
        "2026-07-23T16:00:00.0"
    )
    strength_client.create_manual_activity.assert_not_called()
    strength_client.client.put.assert_not_called()


@pytest.mark.asyncio
async def test_create_strength_activity_requires_confirmation(
    strength_app, strength_client
):
    result = await strength_app.call_tool(
        "create_strength_training_activity",
        {
            "activity_name": "Upper Body",
            "start_datetime": "2026-07-23T18:00:00",
            "time_zone": "Europe/Warsaw",
            "duration_minutes": 30,
            "sets": [{"exercise": "push up"}],
        },
    )

    data = _data(result)
    assert data["success"] is False
    assert "confirm=true is required" in data["error"]
    strength_client.create_manual_activity.assert_not_called()


@pytest.mark.asyncio
async def test_confirmed_create_attaches_sets_and_verifies(
    strength_app, strength_client
):
    written_payload = {}

    def capture_put(service, url, *, json, api):
        written_payload.update(json)
        assert service == "connectapi"
        assert url == "/activity-service/activity/987654321/exerciseSets"
        assert api is True
        return {"accepted": True}

    strength_client.client.put.side_effect = capture_put
    strength_client.get_activity_exercise_sets.side_effect = (
        lambda activity_id: written_payload
    )

    result = await strength_app.call_tool(
        "create_strength_training_activity",
        {
            "activity_name": "Upper Body",
            "start_datetime": "2026-07-23T18:00:00",
            "time_zone": "Europe/Warsaw",
            "duration_minutes": 30,
            "sets": [
                {
                    "exercise": "push up",
                    "sets": 2,
                    "repetitions": 12,
                    "weight_kg": 5,
                    "duration_seconds": 40,
                    "rest_seconds": 80,
                }
            ],
            "confirm": True,
        },
    )

    data = _data(result)
    assert data["success"] is True
    assert data["activity_created"] is True
    assert data["verified"] is True
    assert data["activity_id"] == 987654321
    strength_client.create_manual_activity.assert_called_once_with(
        start_datetime="2026-07-23T18:00:00.000",
        time_zone="Europe/Warsaw",
        type_key="strength_training",
        distance_km=0.0,
        duration_min=30.0,
        activity_name="Upper Body",
    )
    assert written_payload["activityId"] == 987654321
    assert len(written_payload["exerciseSets"]) == 2
    strength_client.delete_activity.assert_not_called()


@pytest.mark.asyncio
async def test_create_rolls_back_when_attaching_sets_fails(
    strength_app, strength_client
):
    strength_client.client.put.side_effect = RuntimeError("write failed")

    result = await strength_app.call_tool(
        "create_strength_training_activity",
        {
            "activity_name": "Upper Body",
            "start_datetime": "2026-07-23T18:00:00",
            "time_zone": "Europe/Warsaw",
            "duration_minutes": 30,
            "sets": [{"exercise": "push up"}],
            "confirm": True,
        },
    )

    data = _data(result)
    assert data["success"] is False
    assert data["activity_created"] is True
    assert data["rolled_back"] is True
    assert "write failed" in data["error"]
    strength_client.delete_activity.assert_called_once_with(987654321)


@pytest.mark.asyncio
async def test_create_reports_rollback_failure(
    strength_app, strength_client
):
    strength_client.client.put.side_effect = RuntimeError("write failed")
    strength_client.delete_activity.side_effect = RuntimeError("delete failed")

    result = await strength_app.call_tool(
        "create_strength_training_activity",
        {
            "activity_name": "Upper Body",
            "start_datetime": "2026-07-23T18:00:00",
            "time_zone": "Europe/Warsaw",
            "duration_minutes": 30,
            "sets": [{"exercise": "push up"}],
            "confirm": True,
        },
    )

    data = _data(result)
    assert data["success"] is False
    assert data["rolled_back"] is False
    assert data["rollback_error"] == "delete failed"


@pytest.mark.asyncio
async def test_create_can_leave_incomplete_activity_when_rollback_disabled(
    strength_app, strength_client
):
    strength_client.client.put.side_effect = RuntimeError("write failed")

    result = await strength_app.call_tool(
        "create_strength_training_activity",
        {
            "activity_name": "Upper Body",
            "start_datetime": "2026-07-23T18:00:00",
            "time_zone": "Europe/Warsaw",
            "duration_minutes": 30,
            "sets": [{"exercise": "push up"}],
            "confirm": True,
            "rollback_on_failure": False,
        },
    )

    data = _data(result)
    assert data["success"] is False
    assert data["rolled_back"] is False
    assert data["activity_id"] == 987654321
    strength_client.delete_activity.assert_not_called()


@pytest.mark.asyncio
async def test_create_rolls_back_when_readback_does_not_match(
    strength_app, strength_client
):
    strength_client.get_activity_exercise_sets.return_value = {
        "activityId": 987654321,
        "exerciseSets": [],
    }

    result = await strength_app.call_tool(
        "create_strength_training_activity",
        {
            "activity_name": "Upper Body",
            "start_datetime": "2026-07-23T18:00:00",
            "time_zone": "Europe/Warsaw",
            "duration_minutes": 30,
            "sets": [{"exercise": "push up"}],
            "confirm": True,
        },
    )

    data = _data(result)
    assert data["success"] is False
    assert data["rolled_back"] is True
    assert "Read-back verification failed" in data["error"]
    strength_client.delete_activity.assert_called_once_with(987654321)


@pytest.mark.asyncio
async def test_create_stops_when_garmin_response_has_no_activity_id(
    strength_app, strength_client
):
    strength_client.create_manual_activity.return_value = {"accepted": True}

    result = await strength_app.call_tool(
        "create_strength_training_activity",
        {
            "activity_name": "Upper Body",
            "start_datetime": "2026-07-23T18:00:00",
            "time_zone": "Europe/Warsaw",
            "duration_minutes": 30,
            "sets": [{"exercise": "push up"}],
            "confirm": True,
        },
    )

    data = _data(result)
    assert data["success"] is False
    assert "without an activityId" in data["error"]
    strength_client.client.put.assert_not_called()
    strength_client.delete_activity.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"activity_name": "  "}, "non-empty"),
        ({"duration_minutes": 0}, "at least"),
        ({"time_zone": "Mars/Olympus"}, "Unknown IANA"),
    ],
)
async def test_create_validates_activity_fields_before_mutation(
    strength_app, strength_client, overrides, message
):
    arguments = {
        "activity_name": "Upper Body",
        "start_datetime": "2026-07-23T18:00:00",
        "time_zone": "Europe/Warsaw",
        "duration_minutes": 30,
        "sets": [{"exercise": "push up"}],
        "confirm": True,
        **overrides,
    }

    result = await strength_app.call_tool(
        "create_strength_training_activity",
        arguments,
    )

    data = _data(result)
    assert data["success"] is False
    assert message in data["error"]
    strength_client.create_manual_activity.assert_not_called()
