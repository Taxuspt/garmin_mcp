"""Pool workout construction with explicit, account-verified Garmin DTO IDs."""
import math
from copy import deepcopy
from garminconnect.workout import SwimmingWorkout

METER = {"unitId": 1, "unitKey": "meter", "factor": 100}
SPORT = {"sportTypeId": 4, "sportTypeKey": "swimming"}
STROKES = {0: None, 2: "backstroke", 3: "breaststroke", 5: "fly", 6: "free"}
EQUIPMENT = {0: None, 1: "fins", 2: "kickboard", 3: "paddles", 4: "pull_buoy", 5: "snorkel"}


def build_swim_json(name, steps, pool_length_m=25, css_seconds_per_100m=None):
    """Validate with SwimmingWorkout while overriding its incorrect sport default.

    CSS is an absolute pace, not an offset from the device's configured CSS.
    """
    for value in (pool_length_m, css_seconds_per_100m):
        if value is not None and (isinstance(value, bool) or not math.isfinite(value) or value <= 0):
            raise ValueError("Pool length and CSS pace must be finite and positive")
    if not name.strip() or not steps:
        raise ValueError("Name and steps are required")
    def clone(items):
        result = []
        for item in items:
            step = deepcopy(item)
            if "workoutSteps" in step:
                step["workoutSteps"] = clone(step["workoutSteps"])
            result.append(step)
        return result
    steps = clone(steps)
    def assign(items, order=1):
        for step in items:
            step["stepOrder"] = order
            order += 1
            if step.get("type") == "RepeatGroupDTO":
                order = assign(step.get("workoutSteps", []), order)
            else:
                step.setdefault("type", "ExecutableStepDTO")
                if css_seconds_per_100m is not None and (step.get("endCondition") or {}).get("conditionTypeId") == 3:
                    step["targetType"] = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"}
                    step["targetValueOne"] = step["targetValueTwo"] = 100 / css_seconds_per_100m
                else:
                    step.setdefault("targetType", {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"})
        return order
    assign(steps)
    workout = SwimmingWorkout(workoutName=name, sportType=SPORT, estimatedDurationInSecs=0,
        poolLength=pool_length_m, poolLengthUnit=METER,
        workoutSegments=[{"segmentOrder": 1, "sportType": SPORT, "workoutSteps": steps}]).to_dict()
    from garmin_mcp.workouts import _normalize_workout_steps, _validate_end_condition_steps, _validate_target_type_steps
    from garmin_mcp.workout_analysis import analyze_workout
    _normalize_workout_steps(workout)
    _validate_end_condition_steps(workout)
    _validate_target_type_steps(workout)
    result = analyze_workout(workout)
    if not result["valid"]:
        raise ValueError(str(result["issues"]))
    return workout


def distance_step(distance, kind="interval", stroke=6):
    return {"type": "ExecutableStepDTO", "stepType": {"stepTypeId": {"warmup": 1, "interval": 3, "cooldown": 2}[kind], "stepTypeKey": kind},
            "endCondition": {"conditionTypeId": 3, "conditionTypeKey": "distance"}, "endConditionValue": distance,
            "preferredEndConditionUnit": METER, "strokeType": {"strokeTypeId": stroke, "strokeTypeKey": STROKES[stroke], "displayOrder": stroke}}


def repeat(n, steps):
    return {"type": "RepeatGroupDTO", "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat"},
            "numberOfIterations": n, "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations"},
            "endConditionValue": n, "workoutSteps": steps}


def pool_template():
    rest = {"type": "ExecutableStepDTO", "stepType": {"stepTypeId": 5, "stepTypeKey": "rest"},
            "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"}, "endConditionValue": 20}
    drill = distance_step(50)
    drill["description"] = "50 m drill"
    drill["equipmentType"] = {"equipmentTypeId": 2, "displayOrder": 2}
    return build_swim_json("Pool 1000 m", [distance_step(200, "warmup"),
        repeat(2, [drill, distance_step(50), rest]), repeat(4, [distance_step(100), rest]),
        distance_step(200, "cooldown")])
