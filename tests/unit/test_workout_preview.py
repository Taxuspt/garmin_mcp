"""Unit tests for delete_workout preview helper."""

from garmin_mcp.workouts import _workout_preview


class _FakeClient:
    def __init__(self, workout=None, workouts=None, error=None):
        self._workout = workout
        self._workouts = workouts
        self._error = error

    def get_workout_by_id(self, workout_id):
        if self._error:
            raise self._error
        return self._workout

    def get_workouts(self):
        return self._workouts


def test_preview_from_workout_details():
    client = _FakeClient(
        workout={
            "workoutId": 99,
            "workoutName": "Threshold Intervals",
            "sportType": {"sportTypeKey": "running"},
            "workoutProvider": "garmin",
            "updatedDate": "2024-01-15",
        }
    )
    preview = _workout_preview(client, 99)
    assert preview["workout_id"] == 99
    assert preview["name"] == "Threshold Intervals"
    assert preview["sport"] == "running"


def test_preview_falls_back_to_workout_list_on_details_error():
    client = _FakeClient(
        error=Exception("403 Forbidden"),
        workouts=[
            {
                "workoutId": 99,
                "workoutName": "Sweet Spot",
                "sportType": {"sportTypeKey": "cycling"},
            }
        ],
    )
    preview = _workout_preview(client, 99)
    assert preview["name"] == "Sweet Spot"
    assert preview["sport"] == "cycling"
