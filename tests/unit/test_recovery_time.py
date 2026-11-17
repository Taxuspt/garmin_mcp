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
    # A drained clock leaves a minute or two on Garmin's counter, which rounds
    # to 0.0 h on its own -- the phrase is not needed to force that.
    assert _hours_from_recovery_minutes(1, "REACHED_ZERO") == 0.0
    # Regression for #491: Garmin keeps the stale phrase after it assigns new
    # recovery time, so a real value must win over "REACHED_ZERO".
    assert _hours_from_recovery_minutes(1222, "REACHED_ZERO") == 20.4
    assert _hours_from_recovery_minutes(None) is None
    assert _hours_from_recovery_minutes(True) is None


def test_hours_from_recovery_minutes_uses_phrase_only_without_value():
    assert _hours_from_recovery_minutes(None, "REACHED_ZERO") == 0.0
    assert _hours_from_recovery_minutes(None, "NO_CHANGE_SLEEP") is None


def test_recovery_state_bands():
    assert _recovery_state(0, None) == "recovered"
    assert _recovery_state(3, None) == "nearly_recovered"
    assert _recovery_state(12, None) == "recovering"
    assert _recovery_state(36, None) == "not_recovered"
    assert _recovery_state(None, "REACHED_ZERO") == "recovered"
    assert _recovery_state(None, None) == "unknown"
    # A live countdown decides the state even when the phrase still says zero.
    assert _recovery_state(12, "REACHED_ZERO") == "recovering"


# Snapshots taken on 2026-10-31, trimmed to the fields the curation reads.
POST_EXERCISE_RESET_SNAPSHOTS = [
    {
        "calendarDate": "2026-10-31",
        "timestampLocal": "2026-10-31T00:03:21",
        "inputContext": "UPDATE_REALTIME_VARIABLES",
        "recoveryTime": 464,
        "recoveryTimeFactorPercent": 87,
        "recoveryTimeChangePhrase": None,
    },
    {
        "calendarDate": "2026-10-31",
        "timestampLocal": "2026-10-31T08:26:51",
        "inputContext": "AFTER_WAKEUP_RESET",
        "recoveryTime": 1,
        "recoveryTimeFactorPercent": 99,
        "recoveryTimeChangePhrase": "NO_CHANGE_SLEEP",
    },
    {
        "calendarDate": "2026-10-31",
        "timestampLocal": "2026-10-31T11:58:14",
        "inputContext": "AFTER_POST_EXERCISE_RESET",
        "recoveryTime": 1,
        "recoveryTimeFactorPercent": 99,
        "recoveryTimeChangePhrase": "REACHED_ZERO",
    },
    {
        "calendarDate": "2026-10-31",
        "timestampLocal": "2026-10-31T17:35:03",
        "inputContext": "AFTER_POST_EXERCISE_RESET",
        "recoveryTime": 1222,
        "recoveryTimeFactorPercent": 67,
        "recoveryTimeChangePhrase": "REACHED_ZERO",
    },
]


def test_recovery_from_readiness_ignores_stale_reached_zero_after_workout():
    """#491: the 17:35 snapshot assigned 1222 min but kept the drained phrase."""
    curated = _recovery_from_readiness(POST_EXERCISE_RESET_SNAPSHOTS, "training_readiness")
    assert curated["remaining_hours"] == 20.4
    assert curated["remaining_hours_at_snapshot"] == 20.4
    assert curated["state"] == "recovering"
    assert curated["change_phrase"] == "REACHED_ZERO"
    assert curated["as_of"] == "2026-10-31T17:35:03"


def test_recovery_from_readiness_reports_drained_clock_as_recovered():
    drained = _recovery_from_readiness(POST_EXERCISE_RESET_SNAPSHOTS[2], "training_readiness")
    assert drained["remaining_hours"] == 0.0
    assert drained["state"] == "recovered"


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


def test_readiness_recovery_explicitly_labels_snapshot_time():
    curated = _recovery_from_readiness({
        "calendarDate": "2026-09-12",
        "timestampLocal": "2026-09-12T06:30:00",
        "recoveryTime": 1728,
    }, "training_readiness")
    assert curated["value_type"] == "snapshot"
    assert curated["remaining_hours_at_snapshot"] == 28.8
    assert curated["as_of"] == "2026-09-12T06:30:00"
    assert curated["remaining_hours"] == 28.8


def test_collect_does_not_label_current_activity_estimate_as_past_date():
    from unittest.mock import Mock

    now = datetime(2026, 10, 19, 18, tzinfo=timezone.utc)
    client = Mock()
    client.get_training_readiness.return_value = []
    client.get_morning_training_readiness.return_value = None
    client.get_activities.return_value = [{
        "activityId": 42,
        "beginTimestamp": datetime(2026, 10, 19, 12, tzinfo=timezone.utc).timestamp() * 1000,
        "duration": 3600,
        "recoveryTime": 1440,
    }]
    curated = _collect_recovery_time(client, "2026-09-12", now=now)
    assert curated["state"] == "unavailable"
    assert "only available for today" in curated["message"]
    assert "activity_id" not in curated
    client.get_activities.assert_not_called()


def test_collect_surfaces_errors_when_all_sources_fail():
    from unittest.mock import Mock
    import pytest

    client = Mock()
    client.get_training_readiness.side_effect = Exception("readiness unavailable")
    client.get_morning_training_readiness.side_effect = Exception("morning unavailable")
    client.get_activities.side_effect = Exception("activities unavailable")
    with pytest.raises(RuntimeError, match="readiness unavailable"):
        _collect_recovery_time(client, "2026-09-12", now=datetime(2026, 9, 12, 18, tzinfo=timezone.utc))


def test_collect_keeps_source_errors_when_another_source_succeeds():
    from unittest.mock import Mock

    client = Mock()
    client.get_training_readiness.side_effect = Exception("readiness unavailable")
    client.get_morning_training_readiness.return_value = {"recoveryTime": 120}
    curated = _collect_recovery_time(client, "2026-09-12")
    assert curated["remaining_hours"] == 2
    assert curated["source"] == "morning_training_readiness"
    assert curated["source_errors"] == [{"source": "get_training_readiness", "error": "readiness unavailable"}]


def test_collect_surfaces_activity_failure_after_empty_readiness():
    from unittest.mock import Mock
    import pytest

    client = Mock()
    client.get_training_readiness.return_value = []
    client.get_morning_training_readiness.return_value = None
    client.get_activities.side_effect = Exception("activity request failed")
    with pytest.raises(RuntimeError, match="activity request failed"):
        _collect_recovery_time(client, "2026-09-12", now=datetime(2026, 9, 12, 18, tzinfo=timezone.utc))
