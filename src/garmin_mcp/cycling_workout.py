"""Pure cycling workout preview, adapted from PR #295's JSON builder.

No client, upload, scheduling, inferred thresholds or absolute-watt mapping.
The returned payload can be explicitly passed to the existing upload_workout.
"""

import math

from garmin_mcp.workout_builders import _zone_number

_STEP_TYPES = {
    "warmup": (1, "warmup"),
    "cooldown": (2, "cooldown"),
    "interval": (3, "interval"),
    "work": (3, "interval"),
    "recovery": (4, "recovery"),
}
_EXECUTABLE_KEYS = {
    "type",
    "duration_seconds",
    "distance_m",
    "description",
    "hr_zone",
    "hr_min",
    "hr_max",
    "power_zone",
}
_REPEAT_KEYS = {"type", "repeats", "steps"}
MAX_DEFINITIONS = 100
MAX_EXPANDED_STEPS = 1000
MAX_DEPTH = 3
MAX_REPEATS = 50


def _positive_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a finite positive number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label} must be a finite positive number") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be a finite positive number")
    return number


def _target(source):
    """Keep main's named HR/power-zone IDs; defer absolute watt ranges."""
    hr_zone = source.get("hr_zone")
    power_zone = source.get("power_zone")
    has_range = source.get("hr_min") is not None or source.get("hr_max") is not None
    if sum([hr_zone is not None, power_zone is not None, has_range]) > 1:
        raise ValueError(
            "A step may use only one HR-zone, HR-range or power-zone target"
        )
    if hr_zone is not None:
        zone = _zone_number(str(hr_zone))
        return {
            "targetType": {
                "workoutTargetTypeId": 4,
                "workoutTargetTypeKey": "heart.rate.zone",
            },
            "zoneNumber": zone,
        }
    if has_range:
        low = _positive_number(source.get("hr_min"), "hr_min")
        high = _positive_number(source.get("hr_max"), "hr_max")
        if low >= high:
            raise ValueError("hr_min and hr_max must be an ordered pair")
        return {
            "targetType": {
                "workoutTargetTypeId": 4,
                "workoutTargetTypeKey": "heart.rate.zone",
            },
            "targetValueOne": low,
            "targetValueTwo": high,
        }
    if power_zone is not None:
        # Reject booleans and lossy truncation of fractional zone numbers.
        if isinstance(power_zone, bool) or not isinstance(power_zone, (int, str)):
            raise ValueError("power_zone must be an integer from 1 to 7")
        text = str(power_zone).strip()
        if len(text) != 1 or text not in "1234567":
            raise ValueError("power_zone must be an integer from 1 to 7")
        return {
            "targetType": {
                "workoutTargetTypeId": 2,
                "workoutTargetTypeKey": "power.zone",
            },
            "zoneNumber": int(text),
        }
    return {
        "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
    }


def build_cycling_preview(
    name: str, steps: list[dict], description: str | None = None
) -> dict:
    """Build, bound and summarize one preview without expanding repeat groups."""
    if not isinstance(name, str) or not name.strip() or len(name) > 256:
        raise ValueError("name must contain 1-256 characters")
    if description is not None and (
        not isinstance(description, str) or len(description) > 2048
    ):
        raise ValueError("description must be text of at most 2048 characters")
    definitions = 0

    def build(items, depth=0):
        nonlocal definitions
        if depth > MAX_DEPTH:
            raise ValueError("Repeat nesting depth exceeds preview limit")
        if not isinstance(items, list) or not items:
            raise ValueError("steps must be a non-empty list")
        if len(items) > MAX_DEFINITIONS:
            raise ValueError("Workout step definition count exceeds preview limit")
        result, expanded, seconds, meters = [], 0, 0.0, 0.0
        for order, source in enumerate(items, 1):
            definitions += 1
            if definitions > MAX_DEFINITIONS:
                raise ValueError("Workout step definition count exceeds preview limit")
            if not isinstance(source, dict):
                raise TypeError("Each step must be an object")
            kind = str(source.get("type", "interval")).strip().lower()
            if "power_min" in source or "power_max" in source:
                raise ValueError(
                    "Absolute watt ranges are not supported by this preview; use power_zone 1-7"
                )
            allowed = _REPEAT_KEYS if kind == "repeat" else _EXECUTABLE_KEYS
            if source.keys() - allowed:
                raise ValueError(
                    "Unsupported step fields: "
                    + ", ".join(sorted(source.keys() - allowed))
                )
            if kind == "repeat":
                repeats = source.get("repeats")
                if type(repeats) is not int or not 2 <= repeats <= MAX_REPEATS:
                    raise ValueError(
                        "repeat steps require an integer repeats from 2 to 50"
                    )
                children, count, duration, distance = build(
                    source.get("steps"), depth + 1
                )
                result.append(
                    {
                        "type": "RepeatGroupDTO",
                        "stepOrder": order,
                        "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat"},
                        "numberOfIterations": repeats,
                        "smartRepeat": False,
                        "endCondition": {
                            "conditionTypeId": 7,
                            "conditionTypeKey": "iterations",
                        },
                        "workoutSteps": children,
                    }
                )
                expanded += count * repeats
                seconds += duration * repeats
                meters += distance * repeats
            else:
                if kind not in _STEP_TYPES:
                    raise ValueError(
                        "Step type must be warmup, interval, work, recovery, cooldown or repeat"
                    )
                duration, distance = (
                    source.get("duration_seconds"),
                    source.get("distance_m"),
                )
                if (duration is None) == (distance is None):
                    raise ValueError(
                        "Each step requires exactly one of duration_seconds or distance_m"
                    )
                timed = duration is not None
                value = _positive_number(
                    duration if timed else distance,
                    "duration_seconds" if timed else "distance_m",
                )
                label = source.get("description", kind.title())
                if not isinstance(label, str) or len(label) > 512:
                    raise ValueError(
                        "Step description must be text of at most 512 characters"
                    )
                type_id, type_key = _STEP_TYPES[kind]
                result.append(
                    {
                        "type": "ExecutableStepDTO",
                        "stepOrder": order,
                        "stepType": {"stepTypeId": type_id, "stepTypeKey": type_key},
                        "description": label,
                        "endCondition": {
                            "conditionTypeId": 2 if timed else 3,
                            "conditionTypeKey": "time" if timed else "distance",
                        },
                        "endConditionValue": value,
                        **_target(source),
                    }
                )
                expanded += 1
                seconds += value if timed else 0
                meters += value if not timed else 0
            if expanded > MAX_EXPANDED_STEPS:
                raise ValueError(
                    "Workout expanded step count exceeds preview limit of 1000"
                )
            if not math.isfinite(seconds) or not math.isfinite(meters):
                raise ValueError(
                    "Workout duration/distance totals exceed finite numeric range"
                )
        return result, expanded, seconds, meters

    built, expanded, seconds, meters = build(steps)
    sport = {"sportTypeId": 2, "sportTypeKey": "cycling"}
    return {
        "status": "success",
        "write_performed": False,
        "workout_data": {
            "workoutName": name.strip(),
            "description": description
            if description is not None
            else f"Cycling workout: {name.strip()}",
            "sportType": dict(sport),
            "workoutSegments": [
                {"segmentOrder": 1, "sportType": dict(sport), "workoutSteps": built}
            ],
        },
        "summary": {
            "expanded_steps": expanded,
            "timed_duration_seconds": seconds,
            "distance_m": meters,
        },
    }
