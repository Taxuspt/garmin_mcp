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

    def get_workouts(self, start=0, limit=100):
        # Like Garmin: start is 1-based and 0 is treated as 1.
        offset = max(start, 1) - 1
        return self._workouts[offset:offset + limit]


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


def test_preview_fallback_searches_past_first_page():
    client = _FakeClient(
        error=Exception("403 Forbidden"),
        workouts=[
            {"workoutId": i, "workoutName": f"Workout {i}"}
            for i in range(1, 151)
        ],
    )
    preview = _workout_preview(client, 120)
    assert preview["name"] == "Workout 120"


def test_get_all_workouts_pages_a_large_library_without_gaps_or_duplicates():
    from garmin_mcp.workouts import _get_all_workouts

    workouts = [{"workoutId": i} for i in range(250)]
    client = _FakeClient(workouts=workouts)

    assert [w["workoutId"] for w in _get_all_workouts(client)] == list(range(250))
