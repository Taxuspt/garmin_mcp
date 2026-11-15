"""Pure workout accounting. Garmin estimates are deliberately not used."""
import math


def block(value):
    return value if isinstance(value, dict) else {}


def analyze_workout(workout, expected_distance_m=None):
    totals = dict(total_distance_m=0, total_rest_s=0, total_timed_duration_s=0,
                  open_ended_step_count=0)
    issues = []
    duration = 0
    duration_complete = True
    distance_complete = True

    def issue(code, path, message, severity="warning"):
        issues.append(dict(code=code, path=path, message=message, severity=severity))

    def number(value, path):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            issue("INVALID_VALUE", path, "Expected a finite non-negative number", "error")
            return None
        return value

    def walk(steps, path, multiplier, orders, sport):
        nonlocal duration, duration_complete, distance_complete
        last = None
        for i, step in enumerate(steps):
            p = f"{path}[{i}]"
            if not isinstance(step, dict):
                issue("INVALID_STEP", p, "Expected an object", "error")
                duration_complete = distance_complete = False
                continue
            orders.append(step.get("stepOrder"))
            if step.get("type") == "RepeatGroupDTO":
                n = step.get("numberOfIterations", step.get("endConditionValue"))
                if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or n < 1 or int(n) != n:
                    issue("INVALID_ITERATIONS", p, "Expected a positive integer", "error")
                    duration_complete = distance_complete = False
                    continue
                if step.get("endConditionValue") not in (None, n):
                    issue("ITERATION_MISMATCH", p, "Iteration counts disagree", "error")
                nested = step.get("workoutSteps")
                if not isinstance(nested, list) or not nested:
                    issue("EMPTY_REPEAT", p, "Repeat needs child steps", "error")
                    duration_complete = distance_complete = False
                    continue
                last = walk(nested, p + ".workoutSteps", multiplier * int(n), orders, sport)
                continue
            last = step
            condition = block(step.get("endCondition"))
            key = condition.get("conditionTypeKey") or {1: "lap.button", 2: "time", 3: "distance"}.get(condition.get("conditionTypeId"))
            rest = block(step.get("stepType")).get("stepTypeId") == 5 or block(step.get("stepType")).get("stepTypeKey") == "rest"
            if key in ("time", "distance"):
                value = number(step.get("endConditionValue"), p + ".endConditionValue")
                if value is None:
                    duration_complete = distance_complete = False
                    continue
                if key == "time":
                    totals["total_timed_duration_s"] += value * multiplier
                    duration += value * multiplier
                    if rest:
                        totals["total_rest_s"] += value * multiplier
                    else:
                        distance_complete = False
                else:
                    totals["total_distance_m"] += value * multiplier
                    a, b = step.get("targetValueOne"), step.get("targetValueTwo")
                    target = block(step.get("targetType"))
                    if target.get("workoutTargetTypeId") == 6 and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 for v in (a, b)):
                        duration += value * multiplier / ((a + b) / 2)
                    else:
                        duration_complete = False
            else:
                totals["open_ended_step_count"] += multiplier
                duration_complete = False
                if not rest:
                    distance_complete = False
            if sport == "swimming" and key == "reps":
                issue("SPORT_CONDITION", p, "Pool swim uses distance, time or lap.button")
        return last

    segments = workout.get("workoutSegments") or []
    if not isinstance(segments, list) or not segments:
        issue("EMPTY_WORKOUT", "workoutSegments", "Expected non-empty segments", "error")
        segments = []
        duration_complete = distance_complete = False
    for i, segment in enumerate(segments):
        path = f"workoutSegments[{i}]"
        if "poolLength" in segment:
            issue("SEGMENT_POOL_LENGTH", path + ".poolLength", "Garmin discards segment pool length; set it on the workout")
        orders = []
        steps = segment.get("workoutSteps") or []
        if not isinstance(steps, list) or not steps:
            issue("EMPTY_SEGMENT", path, "Expected non-empty steps", "error")
            duration_complete = distance_complete = False
            continue
        sport = (segment.get("sportType") or workout.get("sportType") or {}).get("sportTypeKey")
        last = walk(steps, path + ".workoutSteps", 1, orders, sport)
        if orders != list(range(1, len(orders) + 1)):
            issue("STEP_ORDER", path, "Expected unique contiguous preorder stepOrder starting at 1", "error")
        if last and (last.get("stepType") or {}).get("stepTypeKey") == "rest":
            issue("TRAILING_REST", path, "Segment ends with rest")
    if expected_distance_m is not None and not math.isclose(totals["total_distance_m"], expected_distance_m, abs_tol=0.01):
        issue("DISTANCE_MISMATCH", "workoutSegments", f"Expected {expected_distance_m} m; calculated {totals['total_distance_m']} m", "error")
    totals.update(distance_complete=distance_complete,
                  computed_duration_s=duration if duration_complete else None,
                  duration_method="timed steps plus distance / mean pace-target speed; null if any step is unknown")
    return dict(valid=not any(x["severity"] == "error" for x in issues), totals=totals, issues=issues)
