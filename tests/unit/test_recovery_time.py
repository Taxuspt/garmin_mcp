"""Unit tests for daily recovery-time remaining helpers."""

from datetime import datetime, timezone

from garmin_mcp.health_wellness import (
    _collect_recovery_time,
    _hours_from_recovery_minutes,
    _recovery_from_readiness,
    _recovery_state,
)


def test_hours_from_recovery_minutes_and_reached_zero():
    assert _hours_from_recovery_minutes(1728) == 28.8
    assert _hours_from_recovery_minutes(106, "REACHED_ZERO") == 0.0
    assert _hours_from_recovery_minutes(None) is None
    assert _hours_from_recovery_minutes(True) is None


def test_recovery_state_bands():
    assert _recovery_state(0, None) == "recovered"
    assert _recovery_state(3, None) == "nearly_recovered"
    assert _recovery_state(12, None) == "recovering"
    assert _recovery_state(36, None) == "not_recovered"
    assert _recovery_state(12, "REACHED_ZERO") == "recovered"


def test_recovery_from_readiness_uses_latest_snapshot():
    payload = [
        {
            "calendarDate": "2026-09-12",
            "timestampLocal": "2026-09-12T07:00:00",
            "recoveryTime": 1728,
            "score": 51,
            "level": "MODERATE",
            "recoveryTimeChangePhrase": "NO_CHANGE_SLEEP",
        },
        {
            "calendarDate": "2026-09-12",
            "timestampLocal": "2026-09-12T18:00:00",
            "recoveryTime": 106,
            "score": 72,
            "level": "PRODUCTIVE",
            "recoveryTimeChangePhrase": "NO_CHANGE_SLEEP",
        },
    ]
    curated = _recovery_from_readiness(payload, "training_readiness")
    assert curated["remaining_hours"] == 1.8
    assert curated["recovery_score"] == 72
    assert curated["state"] == "nearly_recovered"
    assert curated["source"] == "training_readiness"


class _FakeClient:
    def __init__(self, readiness=None, morning=None, activities=None):
        self._readiness = readiness
        self._morning = morning
        self._activities = activities
        self.readiness_calls = []

    def get_training_readiness(self, date):
        self.readiness_calls.append(date)
        return self._readiness

    def get_morning_training_readiness(self, date):
        return self._morning

    def get_activities(self, start, limit):
        return self._activities


def test_collect_prefers_training_readiness():
    client = _FakeClient(
        readiness={
            "recoveryTime": 600,
            "score": 80,
            "calendarDate": "2026-09-12",
            "timestampLocal": "2026-09-12T08:00:00",
        },
        activities=[{"recoveryTime": 9999}],
    )
    curated = _collect_recovery_time(client, "2026-09-12")
    assert curated["source"] == "training_readiness"
    assert curated["remaining_hours"] == 10.0
    assert curated["recovery_score"] == 80


def test_collect_falls_back_to_decayed_activity_recovery():
    now = datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)
    # Activity ended at 12:00 UTC with 8 hours (480 min) assigned -> 2 hours left.
    client = _FakeClient(
        readiness=[],
        morning=None,
        activities=[
            {
                "activityId": 42,
                "activityName": "Lunch run",
                "beginTimestamp": datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc).timestamp()
                * 1000,
                "duration": 3600,
                "recoveryTime": 480,
            }
        ],
    )
    curated = _collect_recovery_time(client, "2026-09-12", now=now)
    assert curated["source"] == "recent_activity"
    assert curated["remaining_hours"] == 2.0
    assert curated["activity_id"] == 42
    assert curated["state"] == "nearly_recovered"


def test_collect_unavailable_when_no_sources():
    client = _FakeClient(readiness=[], morning=None, activities=[])
    curated = _collect_recovery_time(client, "2026-09-12")
    assert curated["state"] == "unavailable"
    assert curated["remaining_hours"] is None
    assert "message" in curated
