"""
High-level workout builders for Garmin Connect MCP Server.

These tools construct the internal Garmin Connect JSON internally and delegate
to the existing upload_workout / schedule_workout endpoints.
"""
import json
import datetime
import hashlib
import time
from collections.abc import Mapping, Sequence
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context

from garmin_mcp import write_confirmation
from garmin_mcp.result_models import CyclingWorkoutResult

# The garmin_client will be set by the main file
garmin_client = None


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


# =============================================================================
# JSON BUILDERS
# =============================================================================

HR_ZONE_MAP = {
    "Z1": 1,
    "Z2": 2,
    "Z3": 3,
    "Z4": 4,
    "Z5": 5,
}

_CYCLING_STEP_TYPES = {
    "warmup": (1, "warmup"),
    "cooldown": (2, "cooldown"),
    "interval": (3, "interval"),
    "work": (3, "interval"),
    "recovery": (4, "recovery"),
}


def _zone_number(zone: str) -> int:
    """Resolve a human-friendly zone string like 'Z3' to Garmin's zoneNumber."""
    zone_upper = zone.strip().upper()
    if zone_upper in HR_ZONE_MAP:
        return HR_ZONE_MAP[zone_upper]
    # Fallback: if user passed a digit directly
    try:
        z = int(zone_upper)
        if 1 <= z <= 5:
            return z
    except ValueError:
        pass
    raise ValueError(f"Invalid hr_zone '{zone}'. Use Z1-Z5 or 1-5.")


def _hr_target(
    hr_zone: str,
    hr_min: Optional[int],
    hr_max: Optional[int],
) -> tuple:
    """Resolve HR target fields and a short description suffix.

    Returns (target_extra_fields, description_suffix). If hr_min/hr_max are
    both given, builds a custom bpm-range target (targetValueOne/targetValueTwo)
    instead of a named Garmin zone. A custom range and a named zone are mutually
    exclusive -- if a range is given, hr_zone is ignored.
    """
    if hr_min is not None or hr_max is not None:
        if hr_min is None or hr_max is None:
            raise ValueError("hr_min and hr_max must both be provided together.")
        if hr_min >= hr_max:
            raise ValueError(f"hr_min ({hr_min}) must be less than hr_max ({hr_max}).")
        return (
            {"targetValueOne": float(hr_min), "targetValueTwo": float(hr_max)},
            f"{hr_min}-{hr_max}bpm",
        )
    zone = _zone_number(hr_zone)
    return ({"zoneNumber": zone}, f"Z{zone}")


def build_run_json(
    name: str,
    run_seconds: int,
    warmup_min: int,
    cooldown_min: int,
    hr_zone: str = "Z3",
    hr_min: Optional[int] = None,
    hr_max: Optional[int] = None,
) -> dict:
    """Build the Garmin Connect JSON for a continuous run workout.

    Targets a named heart-rate zone (hr_zone) by default. Pass hr_min and
    hr_max together to target an exact custom bpm range instead (e.g. a
    136-148 bpm range that doesn't line up with any single Garmin zone).
    """
    hr_target_fields, hr_desc = _hr_target(hr_zone, hr_min, hr_max)
    run_display = (
        f"{run_seconds // 60}m" if run_seconds % 60 == 0 else f"{run_seconds}s"
    )
    return {
        "workoutName": name,
        "description": (
            f"{warmup_min}m warmup + {run_display} run {hr_desc} + {cooldown_min}m cooldown"
        ),
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": [
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 1,
                    "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
                    "description": f"Warmup {warmup_min} min",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": float(warmup_min * 60),
                    "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                },
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 2,
                    "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                    "description": f"Run {run_seconds}s {hr_desc}",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": float(run_seconds),
                    "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
                    **hr_target_fields,
                },
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 3,
                    "stepType": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
                    "description": f"Cooldown {cooldown_min} min",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": float(cooldown_min * 60),
                    "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                },
            ],
        }],
    }


def build_walk_run_json(
    name: str,
    run_seconds: int,
    walk_seconds: int,
    repeats: int,
    warmup_min: int,
    cooldown_min: int,
    hr_zone: str = "Z3",
) -> dict:
    """Build the Garmin Connect JSON for a walk/run interval workout.

    Parameters match create_walk_run_workout exactly.
    """
    zone = _zone_number(hr_zone)
    return {
        "workoutName": name,
        "description": (
            f"{warmup_min}m warmup + {repeats}x({run_seconds}s run / {walk_seconds}s walk) Z{zone} + "
            f"{cooldown_min}m cooldown"
        ),
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": [
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 1,
                    "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
                    "description": f"Warmup {warmup_min} min",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": float(warmup_min * 60),
                    "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                },
                {
                    "type": "RepeatGroupDTO",
                    "stepOrder": 2,
                    "numberOfIterations": repeats,
                    "workoutSteps": [
                        {
                            "type": "ExecutableStepDTO",
                            "stepOrder": 1,
                            "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                            "description": f"Run {run_seconds}s Z{zone}",
                            "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                            "endConditionValue": float(run_seconds),
                            "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
                            "zoneNumber": zone,
                        },
                        {
                            "type": "ExecutableStepDTO",
                            "stepOrder": 2,
                            "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery"},
                            "description": f"Walk {walk_seconds}s Z{zone}",
                            "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                            "endConditionValue": float(walk_seconds),
                            "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
                            "zoneNumber": zone,
                        },
                    ],
                },
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 3,
                    "stepType": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
                    "description": f"Cooldown {cooldown_min} min",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": float(cooldown_min * 60),
                    "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                },
            ],
        }],
    }


def build_z2_walk_json(
    name: str,
    duration_min: int,
    hr_min: int,
    hr_max: int,
) -> dict:
    """Build the Garmin Connect JSON for a steady Z2 walking workout with absolute HR range."""
    return {
        "workoutName": name,
        "description": f"Walk {duration_min} min at Z2 ({hr_min}-{hr_max} bpm)",
        "sportType": {"sportTypeId": 12, "sportTypeKey": "walking"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 12, "sportTypeKey": "walking"},
            "workoutSteps": [
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 1,
                    "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
                    "description": "Warmup 5 min",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": 300.0,
                    "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                },
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 2,
                    "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                    "description": f"Walk {duration_min} min Z2",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": float(duration_min * 60),
                    "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
                    "zoneNumber": 2,
                },
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 3,
                    "stepType": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
                    "description": "Cooldown 5 min",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": 300.0,
                    "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                },
            ],
        }],
    }


def build_strength_json(
    name: str,
    exercises: List[Dict[str, Any]],
) -> dict:
    """Build the Garmin Connect JSON for a strength workout.

    Each exercise becomes a reps-based work step. When "sets" > 1 the exercise is
    emitted as a RepeatGroupDTO iterated that many times (work + rest per set), so
    Garmin shows the real set count — previously "sets" only ever reached the
    description text and every exercise displayed as 1 set. The name is preserved
    in the step "description", which is what survives the round trip. It is also
    sent as "exerciseName", but Garmin only retains that when it matches one of
    its own exercise keys (e.g. "FARMERS_CARRY"); any other value is accepted and
    then stored as an empty string.

    "category" is optional and only emitted when the caller supplies one, uppercased
    and otherwise passed through untouched. Garmin validates it against its own enum
    and rejects anything outside it, including "UNASSIGNED" and "OTHER"; omitting the
    key is accepted. Valid values come from Garmin's published catalog:
    https://connect.garmin.com/web-data/exercises/Exercises.json
    """
    steps: List[dict] = []
    step_order = 1

    for ex in exercises:
        ex_name = ex.get("name", "Exercise")
        sets = int(ex.get("sets", 1))
        reps = int(ex.get("reps", 1))
        rest_seconds = int(ex.get("rest_seconds", 60))

        # Work step
        step = {
            "type": "ExecutableStepDTO",
            "stepOrder": step_order,
            "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
            "description": f"{ex_name}: {sets} sets x {reps} reps",
            "endCondition": {"conditionTypeId": 10, "conditionTypeKey": "reps"},
            "endConditionValue": float(reps),
            "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
            "exerciseName": ex_name,
        }

        # Only set when the caller asked for it: Garmin rejects values outside its own
        # enum, and an absent category is accepted, so an unknown one stays absent
        # rather than being guessed into a wrong record.
        category = ex.get("category")
        if category is not None:
            if not isinstance(category, str) or not category.strip():
                raise ValueError(
                    f"category for exercise {ex_name!r} must be a non-empty string"
                )
            step["category"] = category.strip().upper()

        if sets > 1:
            # N sets = a repeat group iterated N times (work + rest per iteration).
            # The explicit stepTypeId 6 / endCondition "iterations" shape matches
            # what _sanitize_repeat_group enforces on the upload path: the Garmin
            # API silently corrupts a RepeatGroupDTO without a valid iterations
            # endCondition.
            group_steps = [{**step, "stepOrder": 1}]
            if rest_seconds > 0:
                group_steps.append({
                    "type": "ExecutableStepDTO",
                    "stepOrder": 2,
                    "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery"},
                    "description": f"Rest {rest_seconds}s",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": float(rest_seconds),
                    "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                })
            steps.append({
                "type": "RepeatGroupDTO",
                "stepOrder": step_order,
                "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat"},
                "numberOfIterations": sets,
                "smartRepeat": False,
                "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations"},
                "workoutSteps": group_steps,
            })
            step_order += 1
            # The in-group rest after the final set already separates this exercise
            # from the next one — no extra inter-exercise rest step.
            continue

        steps.append(step)
        step_order += 1

        # Rest step (skip after last exercise)
        if rest_seconds > 0 and ex != exercises[-1]:
            steps.append({
                "type": "ExecutableStepDTO",
                "stepOrder": step_order,
                "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery"},
                "description": f"Rest {rest_seconds}s",
                "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                "endConditionValue": float(rest_seconds),
                "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
            })
            step_order += 1

    return {
        "workoutName": name,
        "description": f"Strength: {len(exercises)} exercises",
        "sportType": {"sportTypeId": 5, "sportTypeKey": "strength_training"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 5, "sportTypeKey": "strength_training"},
            "workoutSteps": steps,
        }],
    }


def _cycling_target(step: Dict[str, Any]) -> Dict[str, Any]:
    """Return Garmin target fields for one high-level cycling step.

    Exactly one target family may be present.  Garmin target type ID 2 carries
    both named FTP zones and absolute (3-second) watt ranges; ID 6 is pace and
    must never be used for power.
    """
    families = 0
    target: Dict[str, Any] = {}

    hr_zone = step.get("hr_zone")
    hr_min = step.get("hr_min")
    hr_max = step.get("hr_max")
    power_zone = step.get("power_zone")
    power_min = step.get("power_min")
    power_max = step.get("power_max")

    if hr_zone is not None:
        families += 1
        target = {
            "targetType": {
                "workoutTargetTypeId": 4,
                "workoutTargetTypeKey": "heart.rate.zone",
            },
            "zoneNumber": _zone_number(str(hr_zone)),
        }
    if hr_min is not None or hr_max is not None:
        families += 1
        if hr_min is None or hr_max is None or int(hr_min) >= int(hr_max):
            raise ValueError("hr_min and hr_max must be an ordered pair")
        target = {
            "targetType": {
                "workoutTargetTypeId": 4,
                "workoutTargetTypeKey": "heart.rate.zone",
            },
            "targetValueOne": float(hr_min),
            "targetValueTwo": float(hr_max),
        }
    if power_zone is not None:
        families += 1
        zone = int(power_zone)
        if not 1 <= zone <= 7:
            raise ValueError("power_zone must be between 1 and 7")
        target = {
            "targetType": {
                "workoutTargetTypeId": 2,
                "workoutTargetTypeKey": "power.zone",
            },
            "zoneNumber": zone,
        }
    if power_min is not None or power_max is not None:
        families += 1
        if power_min is None or power_max is None:
            raise ValueError("power_min and power_max must be provided together")
        low, high = float(power_min), float(power_max)
        if low < 0 or low >= high:
            raise ValueError("power_min and power_max must be a non-negative ordered pair")
        target = {
            "targetType": {
                "workoutTargetTypeId": 2,
                "workoutTargetTypeKey": "power.zone",
            },
            "targetValueOne": low,
            "targetValueTwo": high,
        }

    if families > 1:
        raise ValueError("a cycling step may use only one HR or power target family")
    if not target:
        target = {
            "targetType": {
                "workoutTargetTypeId": 1,
                "workoutTargetTypeKey": "no.target",
            }
        }
    return target


def _build_cycling_steps(steps: List[Dict[str, Any]]) -> List[dict]:
    if not steps:
        raise ValueError("steps must contain at least one workout step")

    result: List[dict] = []
    for order, source in enumerate(steps, start=1):
        kind = str(source.get("type", "interval")).strip().lower()
        if kind == "repeat":
            repeats = int(source.get("repeats", 0))
            if repeats < 2:
                raise ValueError("repeat steps require repeats >= 2")
            nested = source.get("steps")
            if not isinstance(nested, list) or not nested:
                raise ValueError("repeat steps require a non-empty steps list")
            result.append({
                "type": "RepeatGroupDTO",
                "stepOrder": order,
                "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat"},
                "numberOfIterations": repeats,
                "smartRepeat": False,
                "endCondition": {
                    "conditionTypeId": 7,
                    "conditionTypeKey": "iterations",
                },
                "workoutSteps": _build_cycling_steps(nested),
            })
            continue

        if kind not in _CYCLING_STEP_TYPES:
            raise ValueError(
                f"invalid cycling step type {kind!r}; use warmup, interval, "
                "recovery, cooldown, or repeat"
            )
        duration = source.get("duration_seconds")
        distance = source.get("distance_m")
        if (duration is None) == (distance is None):
            raise ValueError(
                "each executable cycling step requires exactly one of "
                "duration_seconds or distance_m"
            )
        condition_id, condition_key, value = (
            (2, "time", float(duration))
            if duration is not None
            else (3, "distance", float(distance))
        )
        if value <= 0:
            raise ValueError("step duration or distance must be positive")
        step_type_id, step_type_key = _CYCLING_STEP_TYPES[kind]
        built = {
            "type": "ExecutableStepDTO",
            "stepOrder": order,
            "stepType": {
                "stepTypeId": step_type_id,
                "stepTypeKey": step_type_key,
            },
            "description": str(source.get("description") or kind.title()),
            "endCondition": {
                "conditionTypeId": condition_id,
                "conditionTypeKey": condition_key,
            },
            "endConditionValue": value,
        }
        built.update(_cycling_target(source))
        result.append(built)
    return result


def build_cycling_json(
    name: str,
    steps: List[Dict[str, Any]],
    description: Optional[str] = None,
) -> dict:
    """Build a validated, high-level Garmin cycling workout payload."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    workout_steps = _build_cycling_steps(steps)
    return {
        "workoutName": name.strip(),
        "description": description or f"Cycling workout: {name.strip()}",
        "sportType": {"sportTypeId": 2, "sportTypeKey": "cycling"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 2, "sportTypeKey": "cycling"},
            "workoutSteps": workout_steps,
        }],
    }


def _validate_schedule_date(value: Optional[str]) -> None:
    if value is None:
        return
    try:
        datetime.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("schedule_date must use YYYY-MM-DD") from exc


def _cycling_step_signature(step: Dict[str, Any]) -> tuple:
    children = step.get("workoutSteps") or []
    step_type = step.get("stepType") or {}
    if children or step.get("type") == "RepeatGroupDTO":
        return (
            "repeat",
            int(step.get("numberOfIterations") or step.get("repeatValue") or 0),
            tuple(_cycling_step_signature(child) for child in children),
        )
    end = step.get("endCondition") or {}
    target = step.get("targetType") or {}

    def number(value):
        return round(float(value), 6) if isinstance(value, (int, float)) else value

    return (
        step_type.get("stepTypeKey"),
        end.get("conditionTypeKey"),
        number(step.get("endConditionValue")),
        target.get("workoutTargetTypeId"),
        target.get("workoutTargetTypeKey"),
        number(step.get("targetValueOne", target.get("targetValueOne"))),
        number(step.get("targetValueTwo", target.get("targetValueTwo"))),
        step.get("zoneNumber", target.get("zoneNumber")),
    )


def _cycling_workout_signature(payload: Dict[str, Any]) -> tuple:
    return tuple(
        _cycling_step_signature(step)
        for segment in payload.get("workoutSegments") or []
        for step in segment.get("workoutSteps") or []
    )


def _cycling_step_confirmation(step: Dict[str, Any]) -> Dict[str, Any]:
    """Return the complete human-relevant semantics for one Garmin step."""

    children = step.get("workoutSteps") or []
    if children or step.get("type") == "RepeatGroupDTO":
        return {
            "order": step.get("stepOrder"),
            "type": "repeat",
            "repeats": int(
                step.get("numberOfIterations") or step.get("repeatValue") or 0
            ),
            "steps": [_cycling_step_confirmation(child) for child in children],
        }
    step_type = step.get("stepType") or {}
    end = step.get("endCondition") or {}
    target = step.get("targetType") or {}
    target_summary: Dict[str, Any] = {
        "type": target.get("workoutTargetTypeKey"),
    }
    for key in ("zoneNumber", "targetValueOne", "targetValueTwo"):
        value = step.get(key, target.get(key))
        if value is not None:
            target_summary[key] = value
    return {
        "order": step.get("stepOrder"),
        "type": step_type.get("stepTypeKey"),
        "description": step.get("description"),
        "end": {
            "type": end.get("conditionTypeKey"),
            "value": step.get("endConditionValue"),
        },
        "target": target_summary,
    }


def _cycling_confirmation_steps(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        _cycling_step_confirmation(step)
        for segment in payload.get("workoutSegments") or []
        for step in segment.get("workoutSteps") or []
    ]


def _scheduled_workout_id(result: Any) -> Optional[str]:
    """Extract a calendar-entry ID from dicts or requests-like responses."""
    if not isinstance(result, (Mapping, Sequence)) or isinstance(
        result, (str, bytes, bytearray)
    ):
        json_loader = getattr(result, "json", None)
        if callable(json_loader):
            try:
                decoded = json_loader()
            except Exception:
                return None
            if isinstance(decoded, (Mapping, list, tuple)):
                return _scheduled_workout_id(decoded)
        return None
    if isinstance(result, Mapping):
        for key in (
            "workoutScheduleId",
            "scheduledWorkoutId",
            "scheduled_workout_id",
            "scheduleId",
        ):
            if result.get(key) is not None:
                return str(result[key])
        # Some schedule endpoints return only ``id``.  Do not mistake an upload
        # response containing workoutId for a calendar-entry ID.
        if result.get("id") is not None and not any(
            key in result for key in ("workoutId", "workout_id")
        ):
            return str(result["id"])
        values = result.values()
    else:
        values = result
    for value in values:
        nested = _scheduled_workout_id(value)
        if nested is not None:
            return nested
    return None


def _lookup_cycling_schedule_id(
    workout_id: Any,
    schedule_date: str,
    *,
    attempts: int = 12,
    retry_delay_s: float = 0.5,
) -> Optional[str]:
    """Poll Garmin's eventually-consistent calendar index for the entry ID."""
    query = {
        "query": (
            "query{workoutScheduleSummariesScalar("
            f'startDate:"{schedule_date}", endDate:"{schedule_date}")}}'
        )
    }
    for attempt in range(max(1, attempts)):
        try:
            result = garmin_client.query_garmin_graphql(query) or {}
        except Exception:
            result = {}
        if isinstance(result, Mapping):
            data = result.get("data")
            entries = (
                data.get("workoutScheduleSummariesScalar") or []
                if isinstance(data, Mapping)
                else []
            )
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                if (
                    str(entry.get("workoutId")) == str(workout_id)
                    and str(entry.get("scheduleDate")) == schedule_date
                    and entry.get("scheduledWorkoutId") is not None
                ):
                    return str(entry["scheduledWorkoutId"])
        if attempt + 1 < max(1, attempts):
            time.sleep(max(0.0, retry_delay_s))
    return None


def _wait_cycling_schedule_absent(
    scheduled_id: str,
    schedule_date: str,
    *,
    attempts: int = 5,
    retry_delay_s: float = 0.5,
) -> bool:
    """Confirm Garmin's calendar index no longer contains an exact entry."""
    query = {
        "query": (
            "query{workoutScheduleSummariesScalar("
            f'startDate:"{schedule_date}", endDate:"{schedule_date}")}}'
        )
    }
    for attempt in range(max(1, attempts)):
        getter = getattr(garmin_client, "get_scheduled_workout_by_id", None)
        if callable(getter):
            try:
                getter(scheduled_id)
            except Exception as exc:
                message = str(exc).lower()
                if "404" in message or "no workout found for workout schedule" in message:
                    return True
        try:
            result = garmin_client.query_garmin_graphql(query) or {}
        except Exception:
            result = {}
        if isinstance(result, Mapping):
            data = result.get("data")
            entries = (
                data.get("workoutScheduleSummariesScalar") or []
                if isinstance(data, Mapping)
                else []
            )
            if not any(
                isinstance(entry, Mapping)
                and str(entry.get("scheduledWorkoutId")) == str(scheduled_id)
                for entry in entries
            ):
                return True
        if attempt + 1 < max(1, attempts):
            time.sleep(max(0.0, retry_delay_s))
    return False


def _compensate_cycling_upload(
    workout_id: Any,
    *,
    scheduled_id: Optional[str] = None,
    schedule_date: Optional[str] = None,
    schedule_attempted: bool = False,
) -> Dict[str, Any]:
    errors: List[str] = []
    safe_to_delete = not schedule_attempted
    if scheduled_id is not None:
        unschedule_errors: List[str] = []
        for attempt in range(3):
            try:
                # DELETE of this exact calendar-entry ID is idempotent. Garmin
                # can expose the entry in GraphQL before the delete path is
                # ready, so an exact retry is safe and required for rollback.
                garmin_client.unschedule_workout(scheduled_id)
            except Exception as exc:
                unschedule_errors.append(str(exc))
            if schedule_date is not None and _wait_cycling_schedule_absent(
                scheduled_id, schedule_date
            ):
                safe_to_delete = True
                break
            if attempt < 2:
                time.sleep(1.0)
        if not safe_to_delete:
            suffix = (
                f"; last errors: {' | '.join(unschedule_errors[-2:])}"
                if unschedule_errors
                else ""
            )
            errors.append(
                f"Calendar entry {scheduled_id} has not disappeared from Garmin's read-back index{suffix}"
            )
    elif schedule_attempted:
        errors.append(
            f"Reconcile the calendar entry for workout {workout_id}; its schedule ID is not yet visible"
        )
    if safe_to_delete:
        try:
            garmin_client.delete_workout(workout_id)
        except Exception as exc:
            errors.append(f"Delete generated Garmin workout {workout_id}: {exc}")
    else:
        errors.append(
            f"Keep generated workout {workout_id} until its calendar entry is reconciled; deleting it now can leave an orphan schedule"
        )
    return {
        "status": "recovery_required" if errors else "rolled_back",
        "workout_id": workout_id,
        **({"scheduled_id": scheduled_id} if scheduled_id is not None else {}),
        **({"recovery_checklist": errors} if errors else {}),
    }


def _upload_cycling_payload(
    payload: dict,
    *,
    dry_run: bool,
    schedule_date: Optional[str],
) -> dict:
    """Preview or upload a cycling workout, with optional read-back/schedule."""
    _validate_schedule_date(schedule_date)
    if dry_run:
        return {
            "status": "preview",
            "dry_run": True,
            "would_schedule": schedule_date,
            "workout": payload,
        }

    try:
        uploaded = garmin_client.upload_workout(payload)
    except Exception as exc:
        return {
            "status": "indeterminate_recovery_required",
            "dry_run": False,
            "error": f"{type(exc).__name__}: {exc}",
            "recovery_checklist": [
                f"Search Garmin for a newly created workout named {payload['workoutName']!r} before retrying."
            ],
        }
    if not isinstance(uploaded, dict):
        return {
            "status": "indeterminate_recovery_required",
            "dry_run": False,
            "response": uploaded,
            "warning": "Upload response had no workout ID, so its remote outcome is unknown.",
            "recovery_checklist": [
                f"Search Garmin for a newly created workout named {payload['workoutName']!r} before retrying."
            ],
        }
    workout_id = uploaded.get("workoutId")
    result: Dict[str, Any] = {
        "status": "uploaded",
        "dry_run": False,
        "workout_id": workout_id,
        "name": uploaded.get("workoutName") or payload["workoutName"],
    }

    if workout_id is None:
        return {
            **result,
            "status": "indeterminate_recovery_required",
            "warning": "Upload response did not contain workoutId; do not retry until Garmin is reconciled.",
            "recovery_checklist": [
                f"Search Garmin for a newly created workout named {payload['workoutName']!r} before retrying."
            ],
        }

    getter = getattr(garmin_client, "get_workout_by_id", None)
    if not callable(getter):
        rollback = _compensate_cycling_upload(workout_id)
        return {
            **result,
            "status": (
                "failed_rolled_back"
                if rollback["status"] == "rolled_back"
                else "failed_recovery_required"
            ),
            "error": "Garmin client cannot read a workout back for validation.",
            "rollback": rollback,
        }
    try:
        confirmed = getter(int(workout_id))
    except Exception as exc:
        rollback = _compensate_cycling_upload(workout_id)
        return {
            **result,
            "status": (
                "failed_rolled_back"
                if rollback["status"] == "rolled_back"
                else "failed_recovery_required"
            ),
            "error": f"Workout read-back failed: {exc}",
            "rollback": rollback,
        }
    if not isinstance(confirmed, dict) or _cycling_workout_signature(
        confirmed
    ) != _cycling_workout_signature(payload):
        rollback = _compensate_cycling_upload(workout_id)
        return {
            **result,
            "status": (
                "failed_rolled_back"
                if rollback["status"] == "rolled_back"
                else "failed_recovery_required"
            ),
            "error": "Garmin workout read-back did not preserve steps, targets, or repeats.",
            "rollback": rollback,
        }
    result["read_back"] = confirmed
    result["read_back_validated"] = True

    if schedule_date is not None:
        scheduled_id: Optional[str] = None
        try:
            schedule_response = garmin_client.schedule_workout(
                int(workout_id), schedule_date
            )
            scheduled_id = _scheduled_workout_id(schedule_response)
            if scheduled_id is None:
                scheduled_id = _lookup_cycling_schedule_id(workout_id, schedule_date)
            if scheduled_id is None:
                raise RuntimeError("Garmin schedule response could not be read back")
            result["schedule"] = {
                "date": schedule_date,
                "status": "scheduled",
                "scheduled_workout_id": scheduled_id,
                "response": schedule_response,
                "verification": "calendar entry read back",
            }
        except Exception as exc:
            rollback = _compensate_cycling_upload(
                workout_id,
                scheduled_id=scheduled_id,
                schedule_date=schedule_date,
                schedule_attempted=True,
            )
            return {
                **result,
                "status": (
                    "failed_rolled_back"
                    if rollback["status"] == "rolled_back"
                    else "failed_recovery_required"
                ),
                "error": f"Workout scheduling failed: {exc}",
                "rollback": rollback,
            }
    return {key: value for key, value in result.items() if value is not None}


async def _confirmed_cycling_upload(
    ctx: Context,
    payload: dict[str, Any],
    *,
    dry_run: bool,
    schedule_date: Optional[str],
) -> dict[str, Any]:
    preview = _upload_cycling_payload(
        payload, dry_run=True, schedule_date=schedule_date
    )
    if dry_run:
        return preview
    payload_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    confirmed, message = await write_confirmation.confirm_garmin_write(
        ctx,
        action="create and optionally schedule a cycling workout",
        summary={
            "workout_name": payload["workoutName"],
            "description": payload.get("description"),
            "schedule_date": schedule_date,
            "steps": _cycling_confirmation_steps(payload),
            "payload_sha256": payload_hash,
        },
    )
    if not confirmed:
        return write_confirmation.needs_confirmation_result(
            preview=preview, message=message
        )
    return _upload_cycling_payload(
        payload, dry_run=False, schedule_date=schedule_date
    )


# =============================================================================
# MCP TOOLS
# =============================================================================

def register_tools(app):
    """Register all high-level workout builder tools with the MCP server app"""

    @app.tool(structured_output=True)
    async def create_cycling_workout(
        ctx: Context,
        name: str,
        steps: List[Dict[str, Any]],
        description: Optional[str] = None,
        schedule_date: Optional[str] = None,
        dry_run: bool = True,
    ) -> CyclingWorkoutResult:
        """Preview or create a structured cycling workout.

        Steps support warmup, interval/work, recovery, cooldown, and nested
        repeat groups. Executable steps require exactly one of duration_seconds
        or distance_m and may target one of hr_zone, hr_min/hr_max, power_zone,
        or power_min/power_max. New high-level write tools preview by default.
        """
        try:
            payload = build_cycling_json(name, steps, description)
            return await _confirmed_cycling_upload(
                ctx, payload, dry_run=dry_run, schedule_date=schedule_date
            )
        except Exception as exc:
            return {"status": "error", "error": str(exc), "dry_run": dry_run}

    @app.tool(structured_output=True)
    async def create_hr_target_ride(
        ctx: Context,
        name: str,
        duration_min: int,
        hr_min: Optional[int] = None,
        hr_max: Optional[int] = None,
        hr_zone: Optional[str] = None,
        warmup_min: int = 10,
        cooldown_min: int = 10,
        schedule_date: Optional[str] = None,
        dry_run: bool = True,
    ) -> CyclingWorkoutResult:
        """Preview or create a steady cycling ride with an HR target."""
        try:
            if duration_min <= 0 or warmup_min < 0 or cooldown_min < 0:
                raise ValueError("durations must be positive (warmup/cooldown may be zero)")
            target: Dict[str, Any]
            if hr_zone is not None:
                if hr_min is not None or hr_max is not None:
                    raise ValueError("use hr_zone or hr_min/hr_max, not both")
                target = {"hr_zone": hr_zone}
            else:
                if hr_min is None or hr_max is None:
                    raise ValueError("provide hr_zone or both hr_min and hr_max")
                target = {"hr_min": hr_min, "hr_max": hr_max}
            steps: List[Dict[str, Any]] = []
            if warmup_min:
                steps.append({"type": "warmup", "duration_seconds": warmup_min * 60})
            steps.append({
                "type": "interval",
                "duration_seconds": duration_min * 60,
                "description": "Steady HR target",
                **target,
            })
            if cooldown_min:
                steps.append({"type": "cooldown", "duration_seconds": cooldown_min * 60})
            payload = build_cycling_json(name, steps)
            return await _confirmed_cycling_upload(
                ctx, payload, dry_run=dry_run, schedule_date=schedule_date
            )
        except Exception as exc:
            return {"status": "error", "error": str(exc), "dry_run": dry_run}

    @app.tool(structured_output=True)
    async def create_interval_workout(
        ctx: Context,
        name: str,
        work_seconds: int,
        recovery_seconds: int,
        repeats: int,
        target_type: str = "power",
        target_min: Optional[float] = None,
        target_max: Optional[float] = None,
        target_zone: Optional[int] = None,
        warmup_min: int = 10,
        cooldown_min: int = 10,
        schedule_date: Optional[str] = None,
        dry_run: bool = True,
    ) -> CyclingWorkoutResult:
        """Preview or create a cycling interval workout.

        target_type is power, heart_rate, power_zone, or hr_zone. Range targets
        require target_min/target_max; zone targets require target_zone.
        """
        try:
            if work_seconds <= 0 or recovery_seconds <= 0 or repeats < 2:
                raise ValueError("positive work/recovery durations and repeats >= 2 are required")
            target_key = target_type.strip().lower()
            if target_key == "power":
                target = {"power_min": target_min, "power_max": target_max}
            elif target_key in {"heart_rate", "hr"}:
                target = {"hr_min": target_min, "hr_max": target_max}
            elif target_key == "power_zone":
                target = {"power_zone": target_zone}
            elif target_key == "hr_zone":
                target = {"hr_zone": target_zone}
            else:
                raise ValueError(
                    "target_type must be power, heart_rate, power_zone, or hr_zone"
                )
            steps: List[Dict[str, Any]] = []
            if warmup_min:
                steps.append({"type": "warmup", "duration_seconds": warmup_min * 60})
            steps.append({
                "type": "repeat",
                "repeats": repeats,
                "steps": [
                    {
                        "type": "interval",
                        "duration_seconds": work_seconds,
                        "description": "Work interval",
                        **target,
                    },
                    {
                        "type": "recovery",
                        "duration_seconds": recovery_seconds,
                        "description": "Recovery",
                    },
                ],
            })
            if cooldown_min:
                steps.append({"type": "cooldown", "duration_seconds": cooldown_min * 60})
            payload = build_cycling_json(name, steps)
            return await _confirmed_cycling_upload(
                ctx, payload, dry_run=dry_run, schedule_date=schedule_date
            )
        except Exception as exc:
            return {"status": "error", "error": str(exc), "dry_run": dry_run}

    @app.tool()
    async def create_walk_run_workout(
        name: str,
        run_seconds: int,
        walk_seconds: int,
        repeats: int,
        warmup_min: int,
        cooldown_min: int,
        hr_zone: str = "Z3",
    ) -> str:
        """Create a walk/run interval workout and upload it to Garmin Connect.

        Builds the internal Garmin JSON automatically and returns the new workout ID.

        Args:
            name: Workout name (e.g. "W3 Mié 2:2")
            run_seconds: Duration of each run interval in seconds
            walk_seconds: Duration of each walk/recovery interval in seconds
            repeats: Number of run/walk repetitions
            warmup_min: Warmup duration in minutes
            cooldown_min: Cooldown duration in minutes
            hr_zone: Target heart-rate zone (Z1-Z5, default Z3)
        """
        try:
            workout_json = build_walk_run_json(
                name=name,
                run_seconds=run_seconds,
                walk_seconds=walk_seconds,
                repeats=repeats,
                warmup_min=warmup_min,
                cooldown_min=cooldown_min,
                hr_zone=hr_zone,
            )
            result = garmin_client.upload_workout(workout_json)

            if isinstance(result, dict):
                curated = {
                    "status": "success",
                    "workout_id": result.get("workoutId"),
                    "name": result.get("workoutName"),
                    "message": "Workout uploaded successfully",
                }
                curated = {k: v for k, v in curated.items() if v is not None}
                return json.dumps(curated, indent=2)
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error creating walk/run workout: {str(e)}"

    @app.tool()
    async def create_run_workout(
        name: str,
        run_seconds: int,
        warmup_min: int,
        cooldown_min: int,
        hr_zone: str = "Z3",
        hr_min: Optional[int] = None,
        hr_max: Optional[int] = None,
    ) -> str:
        """Create a continuous run workout and upload it to Garmin Connect.

        Builds a single uninterrupted run interval with warmup and cooldown walks.

        Targets a named Garmin heart-rate zone by default. Named zones (Z1-Z5)
        don't line up with every real training target -- e.g. a 136-148 bpm
        Zone 2 goal straddles Garmin's Z2 (118-137) and Z3 (138-157). Pass
        hr_min and hr_max together to target that exact bpm range instead;
        the watch will then show "in range" only for the range you actually
        want, not a whole zone that over- or under-shoots it.

        Args:
            name: Workout name (e.g. "Step 8 - 30min continuous")
            run_seconds: Duration of the run in seconds
            warmup_min: Warmup walk duration in minutes
            cooldown_min: Cooldown walk duration in minutes
            hr_zone: Target heart-rate zone (Z1-Z5, default Z3). Ignored if hr_min/hr_max are given.
            hr_min: Optional custom target heart rate range, minimum bpm (must be given with hr_max)
            hr_max: Optional custom target heart rate range, maximum bpm (must be given with hr_min)
        """
        try:
            workout_json = build_run_json(
                name=name,
                run_seconds=run_seconds,
                warmup_min=warmup_min,
                cooldown_min=cooldown_min,
                hr_zone=hr_zone,
                hr_min=hr_min,
                hr_max=hr_max,
            )
            result = garmin_client.upload_workout(workout_json)

            if isinstance(result, dict):
                curated = {
                    "status": "success",
                    "workout_id": result.get("workoutId"),
                    "name": result.get("workoutName"),
                    "message": "Workout uploaded successfully",
                }
                curated = {k: v for k, v in curated.items() if v is not None}
                return json.dumps(curated, indent=2)
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error creating run workout: {str(e)}"

    @app.tool()
    async def create_z2_walk_workout(
        name: str,
        duration_min: int,
        hr_min: int,
        hr_max: int,
    ) -> str:
        """Create a steady Z2 walking workout and upload it to Garmin Connect.

        Args:
            name: Workout name
            duration_min: Main walking block duration in minutes
            hr_min: Minimum heart rate in bpm (used for description; target is Z2)
            hr_max: Maximum heart rate in bpm (used for description; target is Z2)
        """
        try:
            workout_json = build_z2_walk_json(
                name=name,
                duration_min=duration_min,
                hr_min=hr_min,
                hr_max=hr_max,
            )
            result = garmin_client.upload_workout(workout_json)

            if isinstance(result, dict):
                curated = {
                    "status": "success",
                    "workout_id": result.get("workoutId"),
                    "name": result.get("workoutName"),
                    "message": "Workout uploaded successfully",
                }
                curated = {k: v for k, v in curated.items() if v is not None}
                return json.dumps(curated, indent=2)
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error creating Z2 walk workout: {str(e)}"

    @app.tool()
    async def create_strength_workout(
        name: str,
        exercises: List[Dict[str, Any]],
    ) -> str:
        """Create a strength workout and upload it to Garmin Connect.

        Each exercise becomes a reps-based step; when sets > 1 the exercise is
        emitted as a repeat group of that many iterations (work + rest per set),
        so Garmin shows the real set count. The name is kept in the step
        description; it is also sent as exerciseName, which Garmin only retains when
        it matches one of its own exercise keys (e.g. "FARMERS_CARRY").

        Args:
            name: Workout name
            exercises: List of dicts with keys: name, sets, reps, rest_seconds and an
                optional category. Category is omitted from the payload when not
                given; Garmin accepts that. When given it must be one of Garmin's
                exercise categories (e.g. SQUAT, DEADLIFT, PUSH_UP, CARRY, SLED) —
                anything else, including "UNASSIGNED" and "OTHER", is rejected with
                400 Invalid category. Full list:
                https://connect.garmin.com/web-data/exercises/Exercises.json
        """
        try:
            workout_json = build_strength_json(name=name, exercises=exercises)
            result = garmin_client.upload_workout(workout_json)

            if isinstance(result, dict):
                curated = {
                    "status": "success",
                    "workout_id": result.get("workoutId"),
                    "name": result.get("workoutName"),
                    "message": "Workout uploaded successfully",
                }
                curated = {k: v for k, v in curated.items() if v is not None}
                return json.dumps(curated, indent=2)
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error creating strength workout: {str(e)}"

    @app.tool()
    async def schedule_week(week: List[Dict[str, Any]]) -> str:
        """Schedule a list of workouts for the week in a single call.

        Idempotent: if a workout is already scheduled for that date, it is
        reported as already scheduled and the POST is skipped (avoids
        duplicating calendar entries).

        Args:
            week: List of dicts with keys: date (YYYY-MM-DD), workout_id (int)
        """
        # Imported here (not at module top) to avoid any import-time ordering
        # surprises between sibling modules. Both modules share the same
        # garmin_client instance via configure() in __main__.
        from garmin_mcp.workouts import _is_already_scheduled

        try:
            results = []
            for item in week:
                calendar_date = item["date"]
                workout_id = int(item["workout_id"])

                if _is_already_scheduled(workout_id, calendar_date):
                    results.append({
                        "date": calendar_date,
                        "workout_id": workout_id,
                        "status": "already_scheduled",
                        "idempotent": True,
                    })
                    continue

                # garminconnect 0.3.2 dropped the .garth attribute; use .client.
                url = f"workout-service/schedule/{workout_id}"
                response = garmin_client.client.post(
                    "connectapi", url, json={"date": calendar_date}
                )
                if response.status_code == 200:
                    results.append({
                        "date": calendar_date,
                        "workout_id": workout_id,
                        "status": "scheduled",
                    })
                else:
                    results.append({
                        "date": calendar_date,
                        "workout_id": workout_id,
                        "status": "failed",
                        "http_status": response.status_code,
                    })
            return json.dumps({
                "status": "complete",
                "scheduled": results,
            }, indent=2)
        except Exception as e:
            return f"Error scheduling week: {str(e)}"

    return app
