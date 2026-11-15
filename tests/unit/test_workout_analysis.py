from copy import deepcopy
from garmin_mcp.workout_analysis import analyze_workout


def swim():
    return {"sportType": {"sportTypeKey": "swimming"}, "workoutSegments": [{"workoutSteps": [
        {"stepOrder": 1, "endCondition": {"conditionTypeKey": "distance"}, "endConditionValue": 300},
        {"stepOrder": 2, "type": "RepeatGroupDTO", "numberOfIterations": 2, "workoutSteps": [
            {"stepOrder": 3, "endCondition": {"conditionTypeKey": "distance"}, "endConditionValue": 50},
            {"stepOrder": 4, "stepType": {"stepTypeKey": "rest"}, "endCondition": {"conditionTypeKey": "time"}, "endConditionValue": 25}
        ]}]}]}


def test_fixture_arithmetic():
    result = analyze_workout(swim(), 1000)
    assert result["totals"]["total_distance_m"] == 400
    assert result["totals"]["total_rest_s"] == 50
    assert not result["valid"]
    assert result["totals"]["computed_duration_s"] is None


def test_nested_and_lap():
    workout = swim()
    group = workout["workoutSegments"][0]["workoutSteps"][1]
    group["workoutSteps"] = [{"type": "RepeatGroupDTO", "stepOrder": 3, "numberOfIterations": 3,
                              "workoutSteps": deepcopy(group["workoutSteps"])}]
    for i, step in enumerate(group["workoutSteps"][0]["workoutSteps"], 4):
        step["stepOrder"] = i
    assert analyze_workout(workout)["totals"]["total_distance_m"] == 600
    group["workoutSteps"][0]["workoutSteps"][1]["endCondition"] = {"conditionTypeKey": "lap.button"}
    result = analyze_workout(workout)
    assert result["totals"]["open_ended_step_count"] == 6
    assert result["totals"]["total_rest_s"] == 0


def test_invalid_iterations_and_segment_pool():
    workout = swim()
    workout["workoutSegments"][0]["poolLength"] = 25
    workout["workoutSegments"][0]["workoutSteps"][1]["numberOfIterations"] = -1
    result = analyze_workout(workout)
    assert not result["valid"]
    assert {x["code"] for x in result["issues"]} >= {"INVALID_ITERATIONS", "SEGMENT_POOL_LENGTH"}
