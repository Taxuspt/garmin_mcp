"""Regression tests for safe training-device and readiness provenance."""

from garmin_mcp.training import _select_training_device, _tracker_acute_load


def test_device_lookup_failure_or_missing_primary_keeps_identity_unknown():
    latest = {"7": {"trainingStatus": "PRODUCTIVE", "primaryTrainingDevice": True}}
    assert _select_training_device(latest, None)[3] is None
    assert _select_training_device(latest, [{"deviceId": 7}])[3] is None


def test_zero_device_id_is_not_treated_as_missing_and_malformed_records_are_ignored():
    selected, reporting_id, _, is_primary = _select_training_device(
        {"0": {"deviceId": 0, "primaryTrainingDevice": True}},
        [None, "bad", {"deviceId": 0, "primaryDevice": True}],
    )
    assert selected["deviceId"] == 0
    assert reporting_id == "0"
    assert is_primary is True


def test_primary_training_device_is_deterministic_fallback():
    selected, reporting_id, _, is_primary = _select_training_device(
        {
            "200": {"deviceId": 200},
            "100": {"deviceId": 100, "primaryTrainingDevice": True},
        },
        [{"deviceId": 100, "displayName": "Primary"}, {"deviceId": 200}],
    )
    assert reporting_id == "100"
    assert selected["deviceId"] == 100
    assert is_primary is None


def test_missing_primary_tracker_prefers_primary_training_marker():
    _, reporting_id, _, is_primary = _select_training_device(
        {
            "200": {"deviceId": 200},
            "300": {"deviceId": 300, "primaryTrainingDevice": True},
        },
        [{"deviceId": 100, "primaryDevice": True}, {"deviceId": 200}, {"deviceId": 300}],
    )
    assert reporting_id == "300"
    assert is_primary is False


def test_readiness_requires_matching_tracker_and_date():
    payload = [
        {"deviceId": 200, "calendarDate": "2026-10-09", "acuteLoad": 999},
        {"deviceId": 100, "calendarDate": "2026-10-08", "acuteLoad": 888},
        {"deviceId": 100, "calendarDate": "2026-10-09", "acuteLoad": 123},
    ]
    assert _tracker_acute_load(payload, "2026-10-09", "100") == (123, None)
    assert _tracker_acute_load(payload[:2], "2026-10-09", "100")[0] is None
    assert _tracker_acute_load([{"calendarDate": "2026-10-09", "acuteLoad": 77}], "2026-10-09", "100")[0] is None


def test_readiness_rejects_undated_or_unidentified_tracker_rows():
    load, reason = _tracker_acute_load(
        [{"deviceId": 100, "acuteLoad": 77}], "2026-10-09", "100"
    )
    assert load is None
    assert reason == "readiness_device_or_date_mismatch"
