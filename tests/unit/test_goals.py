"""Unit tests for the Connect UI goal fetch used by get_goals."""

from garmin_mcp.challenges import (
    _as_goal_list,
    _collect_goals,
    _curate_connect_ui_goal,
    _is_connect_ui_goal,
)


# Response shape from /goal-service/goal/goals?status=active as observed on
# Garmin Connect's Goals page (cyberjunky/python-garminconnect#431).
CONNECT_UI_GOAL = {
    "id": 41139679,
    "name": "GCC 2026",
    "type": "distance_accumulation",
    "distanceInMeters": 160934.0,
    "durationInSeconds": None,
    "caloriesInKiloCalories": None,
    "numberOfActivities": None,
    "startDate": "2026-09-01",
    "activityType": "cycling",
    "period": "one_month",
    "privacy": "public",
    "progress": {
        "percent": 17,
        "days": 17,
        "distanceInMeters": 28089.0,
        "durationInSeconds": None,
        "caloriesInKiloCalories": None,
        "numberOfActivities": None,
    },
    "remaining": {
        "percent": 83,
        "days": 13,
        "distanceInMeters": 132845.0,
        "durationInSeconds": None,
        "caloriesInKiloCalories": None,
        "numberOfActivities": None,
    },
    "overage": {"percent": 0, "days": 0, "distanceInMeters": 0.0},
    "endDate": "2026-09-30",
    "active": True,
    "completed": False,
}


class _FakeClient:
    """Mimic goal-service as observed live.

    Goals are only returned when ``Sec-Fetch-Site: same-origin`` is sent and
    ``start`` is 1-based; ``start=0`` yields [] even when goals exist.
    """

    def __init__(self, goals_by_status=None, legacy=None, ignore_start=False):
        self.garmin_connect_goals_url = "/goal-service/goal/goals"
        self._goals = goals_by_status or {}
        self._ignore_start = ignore_start
        self.legacy = legacy
        self.get_goals_calls = []
        self.connectapi_calls = []

    def connectapi(self, url, params=None, headers=None):
        self.connectapi_calls.append((url, dict(params or {}), dict(headers or {})))
        params = params or {}
        if (headers or {}).get("Sec-Fetch-Site") != "same-origin":
            return []
        goals = self._goals.get(params.get("status"), [])
        if self._ignore_start:
            return goals
        start = int(params.get("start", 1))
        limit = int(params.get("limit", 30))
        if start < 1:
            return []
        return goals[start - 1 : start - 1 + limit]

    def get_goals(self, goal_type):
        self.get_goals_calls.append(goal_type)
        return self.legacy


def test_as_goal_list_unwraps_legacy_and_modern_shapes():
    assert _as_goal_list([CONNECT_UI_GOAL]) == [CONNECT_UI_GOAL]
    assert _as_goal_list({"goals": [CONNECT_UI_GOAL]}) == [CONNECT_UI_GOAL]
    assert _as_goal_list(CONNECT_UI_GOAL) == [CONNECT_UI_GOAL]
    assert _as_goal_list(None) == []
    assert _as_goal_list(object()) == []


def test_curate_connect_ui_goal_flattens_progress():
    curated = _curate_connect_ui_goal(CONNECT_UI_GOAL)
    assert curated["name"] == "GCC 2026"
    assert curated["activity_type"] == "cycling"
    assert curated["target_distance_meters"] == 160934.0
    assert curated["progress_percent"] == 17
    assert curated["progress_distance_meters"] == 28089.0
    assert curated["remaining_distance_meters"] == 132845.0
    assert curated["remaining_days"] == 13
    assert "target_duration_seconds" not in curated
    assert _is_connect_ui_goal(CONNECT_UI_GOAL)
    assert not _is_connect_ui_goal({"goalType": "STEPS", "goalValue": 8000})


def test_collect_goals_sends_header_and_one_based_start():
    client = _FakeClient({"active": [CONNECT_UI_GOAL]}, legacy=[])
    goals = _collect_goals(client, "active")

    assert [g["name"] for g in goals] == ["GCC 2026"]
    assert goals[0]["progress_percent"] == 17
    assert client.get_goals_calls == []
    url, params, headers = client.connectapi_calls[0]
    assert url == "/goal-service/goal/goals"
    assert params["status"] == "active"
    assert params["start"] == "1"
    assert headers == {"Sec-Fetch-Site": "same-origin"}
    assert "userId" not in params


def test_collect_goals_passes_status_through():
    past = {**CONNECT_UI_GOAL, "id": 1, "active": False, "completed": True}
    client = _FakeClient({"active": [CONNECT_UI_GOAL], "past": [past]}, legacy=[])
    goals = _collect_goals(client, "past")
    assert [g["id"] for g in goals] == [1]
    assert client.connectapi_calls[0][1]["status"] == "past"


def test_collect_goals_paginates_from_one():
    many = [{**CONNECT_UI_GOAL, "id": i} for i in range(150)]
    client = _FakeClient({"past": many}, legacy=[])
    goals = _collect_goals(client, "past")
    assert [g["id"] for g in goals] == list(range(150))
    assert [c[1]["start"] for c in client.connectapi_calls] == ["1", "101"]


def test_collect_goals_stops_if_server_ignores_start():
    full_page = [{**CONNECT_UI_GOAL, "id": i} for i in range(100)]
    client = _FakeClient({"active": full_page}, legacy=[], ignore_start=True)
    goals = _collect_goals(client, "active")
    assert len(goals) == 100
    assert len(client.connectapi_calls) == 2


def test_collect_goals_passes_legacy_wellness_goals_through():
    wellness = {"goalType": "STEPS", "goalValue": 8000}
    client = _FakeClient({"active": [wellness]}, legacy=[])
    assert _collect_goals(client, "active") == [wellness]


def test_collect_goals_falls_back_to_library_when_empty():
    legacy = {"goals": [{"goalType": "STEPS", "goalValue": 8000}]}
    client = _FakeClient({}, legacy=legacy)
    goals = _collect_goals(client, "active")
    assert goals == [{"goalType": "STEPS", "goalValue": 8000}]
    assert client.get_goals_calls == ["active"]


def test_collect_goals_falls_back_to_library_on_error():
    client = _FakeClient({}, legacy=[{"goalType": "STEPS", "goalValue": 8000}])

    def boom(*args, **kwargs):
        raise Exception("API Error 500")

    client.connectapi = boom
    assert _collect_goals(client, "active") == [{"goalType": "STEPS", "goalValue": 8000}]
    assert client.get_goals_calls == ["active"]
