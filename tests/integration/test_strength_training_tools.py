"""Integration tests for structured strength activity MCP tools."""

import json
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import strength_training


CATALOG = (
    ("PULL_UP", "PULL_UP", "Pull-up"),
    ("PUSH_UP", "PUSH_UP", "Push-up"),
    ("SQUAT", "BARBELL_BACK_SQUAT", "Barbell Back Squat"),
)

PREVIOUS_SET = {
    "exercises": [
        {
            "category": "PULL_UP",
            "name": None,
            "probability": 100.0,
        }
    ],
    "duration": 30.0,
    "repetitionCount": 8,
    "weight": -1.0,
    "setType": "ACTIVE",
    "startTime": "2026-07-23T08:00:00.0",
    "wktStepIndex": None,
    "messageIndex": None,
}


@pytest.fixture
def strength_client(monkeypatch):
    client = Mock()
    client.set_activity_exercise_sets = Mock(return_value={"accepted": True})
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
    client.get_activity_exercise_sets = Mock(
        return_value={
            "activityId": 12345678901,
            "exerciseSets": [],
        }
    )
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
    strength_client.set_activity_exercise_sets.assert_not_called()
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
    strength_client.set_activity_exercise_sets.assert_not_called()


@pytest.mark.asyncio
async def test_confirmed_write_uses_exercise_sets_endpoint_and_verifies_readback(
    strength_app, strength_client
):
    written_payload = {}

    def capture_set(activity_id, payload):
        assert activity_id == 12345678901
        written_payload.update(payload)
        return {"accepted": True}

    strength_client.set_activity_exercise_sets.side_effect = capture_set

    def read_sets(activity_id):
        if not written_payload:
            return {"activityId": activity_id, "exerciseSets": []}
        return written_payload

    strength_client.get_activity_exercise_sets.side_effect = read_sets

    result = await strength_app.call_tool(
        "set_activity_strength_exercise_sets",
        {
            "activity_id": "12345678901",
            "sets": [
                {
                    "category": "PUSH_UP",
                    "exercise_name": "PUSH_UP",
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
    assert data["exercise_sets"][0]["weightKg"] == 5.0
    assert "weight" not in data["exercise_sets"][0]
    assert written_payload["activityId"] == 12345678901
    assert len(written_payload["exerciseSets"]) == 2
    assert written_payload["exerciseSets"][0]["weight"] == 5000.0
    assert written_payload["exerciseSets"][0]["startTime"] == (
        "2026-07-23T08:00:00.0"
    )
    assert written_payload["exerciseSets"][1]["startTime"] == (
        "2026-07-23T08:02:00.0"
    )
    assert strength_client.get_activity_exercise_sets.call_count == 2
    strength_client.get_activity_exercise_sets.assert_called_with(12345678901)


@pytest.mark.asyncio
async def test_stale_readback_after_update_is_retried_after_delay(
    strength_app, strength_client, monkeypatch
):
    written_payload = {}
    read_count = 0
    delays = []

    async def record_delay(seconds):
        delays.append(seconds)

    def capture_set(activity_id, payload):
        assert activity_id == 12345678901
        written_payload.update(payload)
        return {"accepted": True}

    def read_sets(activity_id):
        nonlocal read_count
        read_count += 1
        if read_count == 1:
            return {"activityId": activity_id, "exerciseSets": []}
        if read_count == 2:
            return {"activityId": activity_id, "exerciseSets": []}
        return written_payload

    monkeypatch.setattr(strength_training.asyncio, "sleep", record_delay)
    strength_client.set_activity_exercise_sets.side_effect = capture_set
    strength_client.get_activity_exercise_sets.side_effect = read_sets

    result = await strength_app.call_tool(
        "set_activity_strength_exercise_sets",
        {
            "activity_id": 12345678901,
            "sets": [{"exercise": "push up", "repetitions": 10}],
            "confirm": True,
        },
    )

    data = _data(result)
    assert data["success"] is True
    assert data["verified"] is True
    assert strength_client.get_activity_exercise_sets.call_count == 3
    strength_client.set_activity_exercise_sets.assert_called_once()
    assert delays == [strength_training._READBACK_RETRY_DELAY_SECONDS]


@pytest.mark.asyncio
async def test_update_accepts_null_as_an_empty_previous_set_list(
    strength_app, strength_client
):
    written_payload = {}
    read_count = 0

    def capture_set(activity_id, payload):
        assert activity_id == 12345678901
        written_payload.update(payload)
        return {"accepted": True}

    def read_sets(activity_id):
        nonlocal read_count
        read_count += 1
        if read_count == 1:
            return {"activityId": activity_id, "exerciseSets": None}
        return written_payload

    strength_client.set_activity_exercise_sets.side_effect = capture_set
    strength_client.get_activity_exercise_sets.side_effect = read_sets

    result = await strength_app.call_tool(
        "set_activity_strength_exercise_sets",
        {
            "activity_id": 12345678901,
            "sets": [{"exercise": "push up", "repetitions": 10}],
            "confirm": True,
        },
    )

    data = _data(result)
    assert data["success"] is True
    assert data["verified"] is True
    strength_client.set_activity_exercise_sets.assert_called_once()


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
    strength_client.set_activity_exercise_sets.assert_not_called()


@pytest.mark.asyncio
async def test_activity_with_missing_type_is_rejected(
    strength_app, strength_client
):
    strength_client.get_activity.return_value.pop("activityTypeDTO")

    result = await strength_app.call_tool(
        "set_activity_strength_exercise_sets",
        {
            "activity_id": "12345678901",
            "sets": [{"exercise": "push up"}],
            "confirm": True,
        },
    )

    data = _data(result)
    assert data["success"] is False
    assert data["activity_id"] == 12345678901
    assert "type 'unknown'" in data["error"]
    strength_client.set_activity_exercise_sets.assert_not_called()


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
    strength_client.set_activity_exercise_sets.assert_not_called()


@pytest.mark.asyncio
async def test_readback_mismatch_restores_previous_sets(
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
    assert data["rolled_back"] is True
    assert data["previous_sets"] == []
    assert "read-back verification failed" in data["error"]
    assert "set count differs" in data["verification_errors"][0]
    assert strength_client.set_activity_exercise_sets.call_count == 2


@pytest.mark.asyncio
async def test_update_aborts_when_previous_sets_cannot_be_saved(
    strength_app, strength_client
):
    strength_client.get_activity_exercise_sets.return_value = {}

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
    assert "replacement was not attempted" in data["error"]
    strength_client.set_activity_exercise_sets.assert_not_called()


@pytest.mark.asyncio
async def test_failed_update_returns_backup_when_rollback_is_disabled(
    strength_app, strength_client
):
    strength_client.get_activity_exercise_sets.side_effect = [
        {
            "activityId": 12345678901,
            "exerciseSets": [PREVIOUS_SET],
        },
        {
            "activityId": 12345678901,
            "exerciseSets": [],
        },
    ]

    result = await strength_app.call_tool(
        "set_activity_strength_exercise_sets",
        {
            "activity_id": 12345678901,
            "sets": [{"exercise": "push up", "repetitions": 10}],
            "confirm": True,
            "rollback_on_failure": False,
        },
    )

    data = _data(result)
    assert data["success"] is False
    assert data["rolled_back"] is False
    assert data["previous_sets"][0]["exercises"][0]["name"] is None
    assert data["previous_sets"][0]["weightKg"] is None
    strength_client.set_activity_exercise_sets.assert_called_once()


@pytest.mark.asyncio
async def test_update_reports_rollback_failure(
    strength_app, strength_client
):
    strength_client.get_activity_exercise_sets.side_effect = [
        {
            "activityId": 12345678901,
            "exerciseSets": [PREVIOUS_SET],
        },
        {
            "activityId": 12345678901,
            "exerciseSets": [],
        },
    ]
    strength_client.set_activity_exercise_sets.side_effect = [
        {"accepted": True},
        RuntimeError("restore failed"),
    ]

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
    assert data["rolled_back"] is False
    assert data["rollback_error"] == "restore failed"
    assert data["previous_sets"][0]["repetitionCount"] == 8


@pytest.mark.asyncio
async def test_readback_error_after_write_still_restores_previous_sets(
    strength_app, strength_client, monkeypatch
):
    delays = []

    async def record_delay(seconds):
        delays.append(seconds)

    monkeypatch.setattr(strength_training.asyncio, "sleep", record_delay)
    strength_client.get_activity_exercise_sets.side_effect = [
        {
            "activityId": 12345678901,
            "exerciseSets": [PREVIOUS_SET],
        },
        RuntimeError("read-back unavailable"),
        {
            "activityId": 12345678901,
            "exerciseSets": [PREVIOUS_SET],
        },
    ]

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
    assert data["rolled_back"] is True
    assert data["error"] == "read-back unavailable"
    assert data["rollback_exercise_sets"][0]["weightKg"] is None
    assert "weight" not in data["rollback_exercise_sets"][0]
    assert strength_client.set_activity_exercise_sets.call_count == 2
    assert strength_client.get_activity_exercise_sets.call_count == 3
    assert delays == []


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
@pytest.mark.parametrize(
    "set_start_time",
    [
        "10:30",
        "2026-07-23T10:30:00",
    ],
)
async def test_local_set_time_uses_requested_zone_with_gmt_activity_start(
    strength_app, set_start_time
):
    result = await strength_app.call_tool(
        "set_activity_strength_exercise_sets",
        {
            "activity_id": 12345678901,
            "sets": [
                {
                    "exercise": "push up",
                    "start_time": set_start_time,
                    "duration_seconds": 30,
                }
            ],
            "time_zone": "Europe/Warsaw",
            "dry_run": True,
        },
    )

    data = _data(result)
    assert data["success"] is True
    assert data["preview"]["exerciseSets"][0]["startTime"] == (
        "2026-07-23T08:30:00.0"
    )


@pytest.mark.asyncio
async def test_local_set_time_uses_activity_zone_when_no_override_is_given(
    strength_app, strength_client
):
    strength_client.get_activity.return_value["timeZoneUnitDTO"] = {
        "unitKey": "Europe/Warsaw"
    }

    result = await strength_app.call_tool(
        "set_activity_strength_exercise_sets",
        {
            "activity_id": 12345678901,
            "sets": [
                {
                    "exercise": "push up",
                    "start_time": "10:30",
                    "duration_seconds": 30,
                }
            ],
            "dry_run": True,
        },
    )

    data = _data(result)
    assert data["success"] is True
    assert data["preview"]["timeZone"] == "Europe/Warsaw"
    assert data["preview"]["exerciseSets"][0]["startTime"] == (
        "2026-07-23T08:30:00.0"
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
    strength_client.set_activity_exercise_sets.assert_not_called()


@pytest.mark.asyncio
async def test_bundled_catalog_failure_prevents_unverified_write(
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
            "sets": [
                {
                    "category": "PUSH_UP",
                    "exercise_name": "PUSH_UP",
                }
            ],
            "confirm": True,
        },
    )

    data = _data(result)
    assert data["success"] is False
    assert data["error"] == "catalog unavailable"
    strength_client.set_activity_exercise_sets.assert_not_called()


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
    strength_client.set_activity_exercise_sets.assert_not_called()


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

    def capture_set(activity_id, payload):
        assert activity_id == 987654321
        written_payload.update(payload)
        return {"accepted": True}

    strength_client.set_activity_exercise_sets.side_effect = capture_set
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
    assert data["exercise_sets"][0]["weightKg"] == 5.0
    assert "weight" not in data["exercise_sets"][0]
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
async def test_stale_readback_after_create_is_retried_after_delay(
    strength_app, strength_client, monkeypatch
):
    written_payload = {}
    read_count = 0
    delays = []

    async def record_delay(seconds):
        delays.append(seconds)

    def capture_set(activity_id, payload):
        assert activity_id == 987654321
        written_payload.update(payload)
        return {"accepted": True}

    def read_sets(_activity_id):
        nonlocal read_count
        read_count += 1
        if read_count == 1:
            return {"activityId": 987654321, "exerciseSets": []}
        return written_payload

    monkeypatch.setattr(strength_training.asyncio, "sleep", record_delay)
    strength_client.set_activity_exercise_sets.side_effect = capture_set
    strength_client.get_activity_exercise_sets.side_effect = read_sets

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
    assert data["success"] is True
    assert data["verified"] is True
    assert strength_client.get_activity_exercise_sets.call_count == 2
    strength_client.delete_activity.assert_not_called()
    assert delays == [strength_training._READBACK_RETRY_DELAY_SECONDS]


@pytest.mark.asyncio
async def test_create_accepts_zero_readback_for_omitted_repetition_counts(
    strength_app, strength_client
):
    written_payload = {}

    def capture_set(activity_id, payload):
        assert activity_id == 987654321
        written_payload.update(payload)
        return {"accepted": True}

    def readback_with_zero_repetitions(activity_id):
        return {
            "activityId": activity_id,
            "exerciseSets": [
                {
                    **exercise_set,
                    "repetitionCount": 0,
                }
                for exercise_set in written_payload["exerciseSets"]
            ],
        }

    strength_client.set_activity_exercise_sets.side_effect = capture_set
    strength_client.get_activity_exercise_sets.side_effect = (
        readback_with_zero_repetitions
    )

    result = await strength_app.call_tool(
        "create_strength_training_activity",
        {
            "activity_name": "Upper Body",
            "start_datetime": "2026-07-23T18:00:00",
            "time_zone": "Europe/Warsaw",
            "duration_minutes": 30,
            "sets": [
                {"exercise": "push up"},
                {"set_type": "REST"},
            ],
            "confirm": True,
        },
    )

    data = _data(result)
    assert data["success"] is True
    assert data["verified"] is True
    strength_client.delete_activity.assert_not_called()


@pytest.mark.asyncio
async def test_create_rolls_back_when_attaching_sets_fails(
    strength_app, strength_client
):
    strength_client.set_activity_exercise_sets.side_effect = RuntimeError(
        "write failed"
    )

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
    strength_client.set_activity_exercise_sets.side_effect = RuntimeError(
        "write failed"
    )
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
    strength_client.set_activity_exercise_sets.side_effect = RuntimeError(
        "write failed"
    )

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
    assert data["activity_may_exist"] is True
    assert data["manual_cleanup_may_be_required"] is True
    assert "Check Garmin Connect" in data["warning"]
    strength_client.set_activity_exercise_sets.assert_not_called()
    strength_client.delete_activity.assert_not_called()


@pytest.mark.asyncio
async def test_create_warns_when_request_raises_after_possible_submission(
    strength_app, strength_client
):
    strength_client.create_manual_activity.side_effect = TimeoutError(
        "create request timed out"
    )

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
    assert data["activity_id"] is None
    assert data["activity_may_exist"] is True
    assert data["manual_cleanup_may_be_required"] is True
    assert "Check Garmin Connect" in data["warning"]
    assert data["error"] == "create request timed out"
    strength_client.set_activity_exercise_sets.assert_not_called()
    strength_client.delete_activity.assert_not_called()


@pytest.mark.asyncio
async def test_create_accepts_timezone_aware_start_datetime(
    strength_app, strength_client
):
    result = await strength_app.call_tool(
        "create_strength_training_activity",
        {
            "activity_name": "Upper Body",
            "start_datetime": "2026-07-23T18:00:00+02:00",
            "time_zone": "Europe/Warsaw",
            "duration_minutes": 30,
            "sets": [{"exercise": "push up"}],
            "dry_run": True,
        },
    )

    data = _data(result)
    assert data["success"] is True
    assert data["preview"]["startTimeLocal"] == "2026-07-23T18:00:00.000"
    assert data["preview"]["exerciseSets"][0]["startTime"] == (
        "2026-07-23T16:00:00.0"
    )
    strength_client.create_manual_activity.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"activity_name": "  "}, "non-empty"),
        ({"duration_minutes": 0}, "at least"),
        ({"time_zone": "Mars/Olympus"}, "Unknown IANA"),
        ({"time_zone": ""}, "Unknown IANA"),
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
