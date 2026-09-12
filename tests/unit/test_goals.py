"""Unit tests for Connect UI goal parsing used by get_goals."""

from datetime import date

from garmin_mcp.challenges import (
    _as_goal_list,
    _classify_goal,
    _collect_goals,
    _curate_connect_ui_goal,
    _is_connect_ui_goal,
    _parse_goal_date,
)


CONNECT_UI_GOAL = {
    "id": 41039448,
    "name": "GCC 2026",
    "type": "distance_accumulation",
    "distanceInMeters": 160934.4,
    "startDate": "2026-09-01",
    "endDate": "2026-09-23",
    "activityType": "cycling",
    "period": "custom",
    "privacy": "private",
    "progress": {
        "percent": 8,
        "days": 11,
        "distanceInMeters": 14016.0,
    },
    "remaining": {
        "percent": 92,
        "days": 11,
        "distanceInMeters": 146918.4,
    },
    "overage": {"percent": 0, "days": 0, "distanceInMeters": 0.0},
    "active": True,
    "completed": False,
}


def test_parse_goal_date():
    assert _parse_goal_date("2026-09-01") == date(2026, 9, 1)
    assert _parse_goal_date("2026-09-01T00:00:00") == date(2026, 9, 1)
    assert _parse_goal_date("not-a-date") is None
    assert _parse_goal_date(None) is None


def test_as_goal_list_unwraps_legacy_and_modern_shapes():
    assert _as_goal_list([CONNECT_UI_GOAL]) == [CONNECT_UI_GOAL]
    assert _as_goal_list({"goals": [CONNECT_UI_GOAL]}) == [CONNECT_UI_GOAL]
    assert _as_goal_list(CONNECT_UI_GOAL) == [CONNECT_UI_GOAL]
    assert _as_goal_list(None) == []
    assert _as_goal_list(object()) == []


def test_classify_connect_ui_goal_by_dates_and_flags():
    today = date(2026, 9, 12)
    assert _classify_goal(CONNECT_UI_GOAL, today) == "active"
    future = {**CONNECT_UI_GOAL, "startDate": "2026-10-01", "endDate": "2026-10-31"}
    assert _classify_goal(future, today) == "future"
    past = {**CONNECT_UI_GOAL, "endDate": "2026-08-31", "active": False}
    assert _classify_goal(past, today) == "past"
    completed = {**CONNECT_UI_GOAL, "completed": True}
    assert _classify_goal(completed, today) == "past"


def test_curate_connect_ui_goal_flattens_progress():
    curated = _curate_connect_ui_goal(CONNECT_UI_GOAL)
    assert curated["name"] == "GCC 2026"
    assert curated["activity_type"] == "cycling"
    assert curated["target_distance_meters"] == 160934.4
    assert curated["progress_percent"] == 8
    assert curated["remaining_distance_meters"] == 146918.4
    assert curated["remaining_days"] == 11
    assert _is_connect_ui_goal(CONNECT_UI_GOAL)


class _FakeClient:
    def __init__(self, modern=None, legacy=None):
        self.garmin_connect_goals_url = "/goal-service/goal/goals"
        self._modern = modern
        self.legacy = legacy
        self.get_goals_calls = []

    def connectapi(self, url, params=None):
        return self._modern

    def get_goals(self, goal_type):
        self.get_goals_calls.append(goal_type)
        return self.legacy


def test_collect_goals_prefers_connect_ui_payload():
    client = _FakeClient(modern=[CONNECT_UI_GOAL], legacy=[])
    goals = _collect_goals(client, "active", date(2026, 9, 12))
    assert len(goals) == 1
    assert goals[0]["name"] == "GCC 2026"
    assert client.get_goals_calls == []


def test_collect_goals_falls_back_to_legacy_when_connect_ui_empty():
    legacy = {"goals": [{"goalType": "STEPS", "goalValue": 8000}]}
    client = _FakeClient(modern=[], legacy=legacy)
    goals = _collect_goals(client, "active", date(2026, 9, 12))
    assert goals == [{"goalType": "STEPS", "goalValue": 8000}]
    assert client.get_goals_calls == ["active"]
