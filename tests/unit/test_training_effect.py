"""Unit tests for training-effect helpers used by get_training_effect."""

from garminconnect import GarminConnectConnectionError

from garmin_mcp.training import (
    _activity_id_matches,
    _as_activity_list,
    _curate_training_effect,
    _is_forbidden_error,
    _lookup_activity_in_list,
    _minutes_to_hours,
)


class _FakeClient:
    def __init__(self, payload=None, error=None):
        self.garmin_connect_activities = "/activitylist-service/activities/search/activities"
        self._payload = payload
        self._error = error
        self.calls = []

    def connectapi(self, url, params=None):
        self.calls.append((url, params))
        if self._error:
            raise self._error
        return self._payload


def test_is_forbidden_error_detects_403_and_forbidden():
    assert _is_forbidden_error(Exception("HTTP error: API Error 403 - HTTP 403 Forbidden"))
    assert _is_forbidden_error(GarminConnectConnectionError("Forbidden"))
    assert not _is_forbidden_error(Exception("API Error"))
    assert not _is_forbidden_error(Exception("timeout"))


def test_activity_id_matches_int_and_str():
    assert _activity_id_matches({"activityId": 123}, 123)
    assert _activity_id_matches({"activityId": "123"}, 123)
    assert not _activity_id_matches({"activityId": 999}, 123)
    assert not _activity_id_matches({}, 123)


def test_as_activity_list_accepts_list_and_wrapped_payloads():
    item = {"activityId": 1}
    assert _as_activity_list([item]) == [item]
    assert _as_activity_list({"activityList": [item]}) == [item]
    assert _as_activity_list({"activities": [item]}) == [item]
    assert _as_activity_list(None) == []
    assert _as_activity_list(object()) == []


def test_curate_training_effect_from_details_summary():
    curated = _curate_training_effect(
        {
            "summaryDTO": {
                "trainingEffect": 3.5,
                "anaerobicTrainingEffect": 2.0,
                "trainingEffectLabel": "Highly Improving",
                "activityTrainingLoad": 150,
                "recoveryTime": 720,
                "performanceCondition": 95,
            }
        },
        123,
        "activity_details",
    )
    assert curated["activity_id"] == 123
    assert curated["source"] == "activity_details"
    assert curated["training_effect"] == 3.5
    assert curated["aerobic_effect"] == 3.5
    assert curated["anaerobic_effect"] == 2.0
    assert curated["training_effect_label"] == "Highly Improving"
    assert curated["recovery_time_hours"] == 12.0
    assert curated["training_load"] == 150
    assert curated["performance_condition"] == 95


def test_curate_training_effect_from_list_item():
    curated = _curate_training_effect(
        {
            "activityId": 456,
            "aerobicTrainingEffect": 2.4,
            "anaerobicTrainingEffect": 0.3,
            "aerobicTrainingEffectMessage": "IMPROVING_AEROBIC_BASE_8",
            "activityTrainingLoad": 87.0,
        },
        456,
        "activity_list",
    )
    assert curated == {
        "activity_id": 456,
        "source": "activity_list",
        "training_effect": 2.4,
        "aerobic_effect": 2.4,
        "anaerobic_effect": 0.3,
        "training_effect_label": "IMPROVING_AEROBIC_BASE_8",
        "training_load": 87.0,
    }


def test_minutes_to_hours_rejects_invalid_values():
    assert _minutes_to_hours(60) == 1.0
    assert _minutes_to_hours(0) is None
    assert _minutes_to_hours(None) is None
    assert _minutes_to_hours(True) is None


def test_lookup_activity_in_list_prefers_activity_ids_filter():
    item = {"activityId": 99, "aerobicTrainingEffect": 3.1}
    client = _FakeClient(payload=[item])

    found = _lookup_activity_in_list(client, 99)

    assert found is item
    assert client.calls[0][1]["activityIds"] == "99"


def test_lookup_activity_in_list_scans_recent_page_when_filter_misses():
    wanted = {"activityId": "77", "aerobicTrainingEffect": 1.2}

    class ScanningClient:
        garmin_connect_activities = "/activitylist-service/activities/search/activities"

        def connectapi(self, url, params=None):
            if params and params.get("activityIds"):
                return []
            return [wanted, {"activityId": 1}]

    found = _lookup_activity_in_list(ScanningClient(), 77)
    assert found is wanted


def test_lookup_activity_in_list_returns_none_when_missing():
    client = _FakeClient(payload=[{"activityId": 1}])
    assert _lookup_activity_in_list(client, 99) is None
