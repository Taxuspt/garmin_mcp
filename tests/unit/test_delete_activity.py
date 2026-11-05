"""Unit tests for delete_activity preview helper."""

from garmin_mcp.activity_management import _activity_preview


class _FakeClient:
    def __init__(self, activity=None, activities=None, error=None):
        self._activity = activity
        self._activities = activities
        self._error = error

    def get_activity(self, activity_id):
        if self._error:
            raise self._error
        return self._activity

    def get_activities(self, start, limit):
        return self._activities


def test_preview_from_activity_details():
    client = _FakeClient(
        activity={
            "activityName": "Morning Run",
            "activityTypeDTO": {"typeKey": "running"},
            "summaryDTO": {
                "startTimeLocal": "2024-01-15 07:00:00",
                "distance": 5000.0,
                "duration": 1800.0,
            },
        }
    )
    preview = _activity_preview(client, 99)
    assert preview["activity_id"] == 99
    assert preview["name"] == "Morning Run"
    assert preview["type"] == "running"
    assert preview["distance_meters"] == 5000.0


def test_preview_falls_back_to_activity_list_on_details_error():
    client = _FakeClient(
        error=Exception("403 Forbidden"),
        activities=[
            {
                "activityId": 99,
                "activityName": "Easy Ride",
                "activityType": {"typeKey": "cycling"},
                "startTimeLocal": "2024-01-14 16:00:00",
                "distance": 20000.0,
                "duration": 3600.0,
            }
        ],
    )
    preview = _activity_preview(client, 99)
    assert preview["name"] == "Easy Ride"
    assert preview["type"] == "cycling"
