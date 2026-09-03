"""Deterministic, preview-first cycling training plans and weekly adaptation."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import threading
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from mcp.server.fastmcp import Context

from garmin_mcp import write_confirmation
from garmin_mcp.result_models import (
    AdaptWeekResult,
    ApplyTrainingBlockResult,
    ApplyWeekAdaptationResult,
    TrainingBlockResult,
)
from garmin_mcp.workout_builders import build_cycling_json


POLICY_VERSION = "cycling-block-v1"
SUPPORTED_GOALS = {
    "base",
    "general_fitness",
    "gran_fondo",
    "road_race",
    "time_trial",
    "climb",
}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DEFAULT_DAYS = {
    1: ["saturday"],
    2: ["tuesday", "saturday"],
    3: ["tuesday", "thursday", "saturday"],
    4: ["tuesday", "thursday", "saturday", "sunday"],
    5: ["monday", "tuesday", "thursday", "saturday", "sunday"],
    6: ["monday", "tuesday", "wednesday", "thursday", "saturday", "sunday"],
    7: list(_WEEKDAYS),
}

garmin_client = None
_apply_lock = threading.RLock()


def configure(client) -> None:
    global garmin_client
    garmin_client = client


def _stable_id(prefix: str, value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}_{hashlib.sha256(encoded.encode()).hexdigest()[:16]}"


def _parse_days(days_per_week: int, available_weekdays: Optional[List[str]]) -> List[str]:
    if not 1 <= int(days_per_week) <= 7:
        raise ValueError("days_per_week must be between 1 and 7")
    if available_weekdays is None:
        return list(_DEFAULT_DAYS[int(days_per_week)])
    days = [str(day).strip().lower() for day in available_weekdays]
    if len(days) != int(days_per_week) or len(set(days)) != len(days):
        raise ValueError("available_weekdays must contain days_per_week unique values")
    invalid = sorted(set(days) - set(_WEEKDAYS))
    if invalid:
        raise ValueError(f"invalid weekday(s): {', '.join(invalid)}")
    return sorted(days, key=_WEEKDAYS.index)


def _monday_on_or_after(value: dt.date) -> dt.date:
    return value + dt.timedelta(days=(-value.weekday()) % 7)


def _phase(week_index: int, weeks: int) -> str:
    remaining = weeks - week_index
    if remaining <= 2:
        return "taper"
    if week_index % 4 == 0:
        return "recovery"
    if remaining <= 4:
        return "peak"
    if week_index <= max(2, round(weeks * 0.35)):
        return "base"
    return "build"


def _phase_multiplier(phase: str, load_week_number: int) -> float:
    if phase == "recovery":
        return 0.75
    if phase == "taper":
        return 0.60 if load_week_number % 2 else 0.50
    if phase == "peak":
        return 1.02
    return 1.0 + min(0.08 * max(0, load_week_number - 1), 0.32)


def _session_kind(
    day: str,
    phase: str,
    goal: str,
    index: int,
    total: int,
    *,
    has_saturday: bool,
) -> str:
    if day == "saturday" or (not has_saturday and index == total - 1):
        return "long_endurance"
    if phase == "taper" and index > 1:
        return "easy"
    if day in {"tuesday", "thursday"} and phase not in {"base", "recovery"}:
        return "intensity"
    if day == "tuesday" and phase == "base" and goal in {"time_trial", "climb"}:
        return "tempo"
    return "easy"


def _workout_for_session(name: str, kind: str, duration_min: int, goal: str) -> dict:
    duration_s = max(20, duration_min) * 60
    if kind == "intensity":
        zone = 5 if goal == "road_race" else 4
        repeats = 5 if goal == "road_race" else 3
        warmup_s = min(600, max(240, round(duration_s * 0.20)))
        cooldown_s = warmup_s
        repeat_budget_s = max(360, duration_s - warmup_s - cooldown_s)
        recovery_s = min(
            180 if goal == "road_race" else 240,
            max(60, round(repeat_budget_s / repeats / 3)),
        )
        work_s = max(60, round(repeat_budget_s / repeats - recovery_s))
        steps = [
            {"type": "warmup", "duration_seconds": warmup_s},
            {
                "type": "repeat",
                "repeats": repeats,
                "steps": [
                    {"type": "interval", "duration_seconds": work_s, "power_zone": zone},
                    {"type": "recovery", "duration_seconds": recovery_s, "power_zone": 1},
                ],
            },
            {"type": "cooldown", "duration_seconds": cooldown_s},
        ]
    elif kind == "tempo":
        steps = [
            {"type": "warmup", "duration_seconds": 600},
            {
                "type": "interval",
                "duration_seconds": max(1200, duration_s - 1200),
                "power_zone": 3,
            },
            {"type": "cooldown", "duration_seconds": 600},
        ]
    else:
        zone = 1 if kind == "recovery" else 2
        steps = [{"type": "interval", "duration_seconds": duration_s, "power_zone": zone}]
    return build_cycling_json(name, steps, f"{POLICY_VERSION}: {kind}")


def build_training_block(
    race_date: str,
    goal: str,
    sport: str,
    days_per_week: int,
    current_ctl: Optional[float] = None,
    available_weekdays: Optional[List[str]] = None,
    start_date: Optional[str] = None,
    zone_model_id: Optional[str] = None,
) -> dict:
    """Build a deterministic block without performing any Garmin writes."""
    goal_key = goal.strip().lower()
    if goal_key not in SUPPORTED_GOALS:
        raise ValueError(f"goal must be one of {', '.join(sorted(SUPPORTED_GOALS))}")
    if sport.strip().lower() != "cycling":
        raise ValueError("cycling is the only implemented sport in policy v1")
    race = dt.date.fromisoformat(race_date)
    start = dt.date.fromisoformat(start_date) if start_date else dt.date.today()
    first_monday = _monday_on_or_after(start)
    days_until_race = (race - first_monday).days
    weeks = (days_until_race + 6) // 7
    if not 4 <= weeks <= 52:
        raise ValueError("race_date must allow a block of 4 to 52 weeks")
    weekdays = _parse_days(days_per_week, available_weekdays)
    baseline_minutes = max(180, int(days_per_week) * 60)
    if current_ctl is not None and (
        isinstance(current_ctl, bool)
        or not math.isfinite(float(current_ctl))
        or float(current_ctl) < 0
    ):
        raise ValueError("current_ctl must be a finite non-negative number")
    baseline_load = round(float(current_ctl) * 7, 1) if current_ctl is not None else None

    seed = {
        "race_date": race_date,
        "goal": goal_key,
        "sport": "cycling",
        "days_per_week": days_per_week,
        "weekdays": weekdays,
        "current_ctl": current_ctl,
        "start": first_monday.isoformat(),
        "zone_model_id": zone_model_id,
        "policy_version": POLICY_VERSION,
    }
    plan_id = _stable_id("plan", seed)
    result_weeks = []
    # Recovery/taper weeks are deliberate reductions, not new load anchors.
    # Track the most recent actual load week so the next build week may resume
    # from that level while still respecting the 8% load-week progression cap.
    previous_load_minutes = baseline_minutes
    previous_target_load = baseline_load
    for week_index in range(1, weeks + 1):
        phase = _phase(week_index, weeks)
        multiplier = _phase_multiplier(phase, week_index)
        if phase == "recovery":
            proposed = round(previous_load_minutes * 0.75)
        else:
            proposed = round(baseline_minutes * multiplier)
        if phase not in {"recovery", "taper"}:
            proposed = min(proposed, round(previous_load_minutes * 1.08))
        weekly_minutes = max(90, proposed)
        if phase not in {"recovery", "taper"}:
            previous_load_minutes = weekly_minutes
        monday = first_monday + dt.timedelta(weeks=week_index - 1)

        long_minutes = max(45, round(weekly_minutes * 0.35))
        remaining = max(0, weekly_minutes - long_minutes)
        normal_minutes = max(20, round(remaining / max(1, len(weekdays) - 1)))
        if phase == "recovery" and previous_target_load is not None:
            target_load = round(previous_target_load * 0.75, 1)
        else:
            target_load = (
                round(baseline_load * weekly_minutes / baseline_minutes, 1)
                if baseline_load is not None
                else None
            )
        if (
            target_load is not None
            and previous_target_load is not None
            and phase not in {"recovery", "taper"}
        ):
            target_load = min(target_load, round(previous_target_load * 1.08, 1))
        if phase not in {"recovery", "taper"}:
            previous_target_load = target_load
        sessions = []
        intensity_count = 0
        for index, day in enumerate(weekdays):
            kind = _session_kind(
                day,
                phase,
                goal_key,
                index,
                len(weekdays),
                has_saturday="saturday" in weekdays,
            )
            if kind == "intensity":
                intensity_count += 1
                if intensity_count > 2:
                    kind = "easy"
            duration = long_minutes if kind == "long_endurance" else normal_minutes
            session_date = monday + dt.timedelta(days=_WEEKDAYS.index(day))
            session_name = f"W{week_index} {kind.replace('_', ' ').title()}"
            session = {
                "session_id": _stable_id(
                    "session", {"plan_id": plan_id, "date": session_date.isoformat(), "kind": kind}
                ),
                "date": session_date.isoformat(),
                "weekday": day,
                "kind": kind,
                "duration_min": duration,
                "locked": session_date == race,
                "workout": _workout_for_session(session_name, kind, duration, goal_key),
            }
            sessions.append(session)
        result_weeks.append({
            "week": week_index,
            "week_of": monday.isoformat(),
            "phase": phase,
            "target_volume_min": weekly_minutes,
            "target_load": target_load,
            "sessions": sessions,
        })

    return {
        "schema_version": 1,
        "policy_version": POLICY_VERSION,
        "plan_id": plan_id,
        "revision": 1,
        "status": "draft",
        "sport": "cycling",
        "goal": goal_key,
        "race_date": race.isoformat(),
        "start_date": first_monday.isoformat(),
        "days_per_week": int(days_per_week),
        "available_weekdays": weekdays,
        "zone_model_id": zone_model_id,
        "weeks": result_weeks,
    }


def build_week_adaptation(
    plan: dict,
    week_of: str,
    *,
    readiness: Optional[float],
    hrv_status: Optional[str],
    tsb: Optional[float],
    completion_rate: Optional[float],
    subjective_status: Optional[str] = None,
) -> dict:
    """Return an immutable proposed patch for one week."""
    week = next((item for item in plan.get("weeks", []) if item.get("week_of") == week_of), None)
    if week is None:
        raise ValueError(f"week {week_of} is not present in plan {plan.get('plan_id')}")
    subjective = (subjective_status or "normal").strip().lower()
    hrv_key = str(hrv_status or "").strip().lower()
    low_hrv = hrv_key in {"low", "unbalanced", "poor"}
    red = (
        subjective in {"ill", "injured"}
        or (readiness is not None and readiness < 40)
        or (low_hrv and tsb is not None and tsb < -10)
        or (completion_rate is not None and completion_rate < 0.5)
    )
    yellow = (
        not red
        and (
            subjective == "tired"
            or (readiness is not None and readiness < 60)
            or low_hrv
            or (tsb is not None and tsb < -10)
            or (completion_rate is not None and completion_rate < 0.7)
        )
    )
    positive_signals = sum(
        (
            readiness is not None and readiness >= 70,
            hrv_key in {"balanced", "good", "normal"},
            tsb is not None and tsb >= -10,
            completion_rate is not None and completion_rate >= 0.85,
            subjective_status is not None and subjective in {"good", "normal"},
        )
    )
    enough_objective_data = sum(
        value is not None for value in (readiness, hrv_status, tsb, completion_rate)
    ) >= 2
    if red:
        state = "red"
        multiplier = 0.65
    elif yellow:
        state = "yellow"
        multiplier = 0.80
    elif enough_objective_data and positive_signals >= 2:
        state = "green"
        multiplier = 1.05
    else:
        # Missing data is not a positive training signal. Keeping the week
        # unchanged prevents one isolated good metric from causing an increase.
        state = "insufficient_data"
        multiplier = 1.0

    patched = copy.deepcopy(week)
    high_kept = 0
    changes = []
    for session in patched.get("sessions", []):
        if session.get("locked"):
            continue
        old_kind = session["kind"]
        old_duration = int(session["duration_min"])
        if state == "red" and old_kind in {"intensity", "tempo"}:
            session["kind"] = "recovery"
        elif state == "yellow" and old_kind in {"intensity", "tempo"}:
            high_kept += 1
            if high_kept > 1:
                session["kind"] = "easy"
        session["duration_min"] = max(20, round(old_duration * multiplier))
        session["workout"] = _workout_for_session(
            session["workout"]["workoutName"],
            session["kind"],
            session["duration_min"],
            plan["goal"],
        )
        if session["kind"] != old_kind or session["duration_min"] != old_duration:
            changes.append({
                "session_id": session["session_id"],
                "from": {"kind": old_kind, "duration_min": old_duration},
                "to": {"kind": session["kind"], "duration_min": session["duration_min"]},
            })
    patched["target_volume_min"] = sum(
        int(item["duration_min"]) for item in patched.get("sessions", [])
    )
    evidence = {
        "readiness": readiness,
        "hrv_status": hrv_status,
        "tsb": tsb,
        "completion_rate": completion_rate,
        "subjective_status": subjective,
    }
    reasons: List[str] = []
    if subjective in {"ill", "injured", "tired"}:
        reasons.append(f"subjective_status={subjective}")
    if readiness is not None:
        reasons.append(f"readiness={readiness:g}")
    if hrv_status:
        reasons.append(f"hrv_status={hrv_status}")
    if tsb is not None:
        reasons.append(f"tsb={tsb:g}")
    if completion_rate is not None:
        reasons.append(f"completion_rate={completion_rate:.2f}")
    if state == "insufficient_data":
        reasons.append("fewer than two corroborating positive objective signals; volume held")
    adaptation_seed = {
        "plan_id": plan["plan_id"],
        "revision": plan.get("revision", 1),
        "week_of": week_of,
        "policy_version": POLICY_VERSION,
        "evidence": evidence,
    }
    return {
        "schema_version": 1,
        "adaptation_id": _stable_id("adapt", adaptation_seed),
        "plan_id": plan["plan_id"],
        "base_revision": plan.get("revision", 1),
        "week_of": week_of,
        "status": "pending",
        "readiness_state": state,
        "volume_multiplier": multiplier,
        "evidence": evidence,
        "reasons": reasons,
        "changes": changes,
        "patched_week": patched,
    }


def _payload_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _store_context():
    from garmin_mcp import physiology

    store = physiology.get_store(required=True)
    assert store is not None
    athlete_id = store.ensure_athlete("garmin", "local")
    return store, athlete_id


def persist_training_block(plan: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """Persist a deterministic draft, idempotently by its complete input/output."""

    store, athlete_id = _store_context()
    plan_data = copy.deepcopy(dict(plan))
    return store.save_plan(
        athlete_id=athlete_id,
        sport=str(plan_data["sport"]),
        plan=plan_data,
        input_hash=_payload_hash({"kind": "training_block", "plan": plan_data}),
        status="draft",
        revision=int(plan_data.get("revision", 1)),
        plan_id=str(plan_data["plan_id"]),
    )


def _plan_row(plan_id: Optional[str]) -> dict[str, Any]:
    store, athlete_id = _store_context()
    row = (
        store.get_plan(plan_id)
        if plan_id
        else store.get_latest_plan(sport="cycling", athlete_id=athlete_id)
    )
    if row is None:
        suffix = f" {plan_id!r}" if plan_id else ""
        raise KeyError(f"No stored cycling training plan{suffix}")
    return row


def _iter_sessions(plan: Mapping[str, Any], week_of: Optional[str] = None) -> Iterable[dict[str, Any]]:
    for week in plan.get("weeks", []):
        if week_of is not None and week.get("week_of") != week_of:
            continue
        for session in week.get("sessions", []):
            yield session


def _normalized_number(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 6)
    return value


def _step_signature(step: Mapping[str, Any]) -> tuple[Any, ...]:
    children = step.get("workoutSteps") or []
    step_type = step.get("stepType") or {}
    if children or step.get("type") == "RepeatGroupDTO":
        return (
            "repeat",
            int(step.get("numberOfIterations") or step.get("repeatValue") or 0),
            tuple(_step_signature(child) for child in children if isinstance(child, Mapping)),
        )
    end = step.get("endCondition") or {}
    target = step.get("targetType") or {}
    return (
        str(step_type.get("stepTypeKey") or ""),
        str(end.get("conditionTypeKey") or ""),
        _normalized_number(step.get("endConditionValue")),
        int(target.get("workoutTargetTypeId") or 0),
        str(target.get("workoutTargetTypeKey") or ""),
        _normalized_number(step.get("targetValueOne", target.get("targetValueOne"))),
        _normalized_number(step.get("targetValueTwo", target.get("targetValueTwo"))),
        step.get("zoneNumber", target.get("zoneNumber")),
    )


def _workout_signature(workout: Mapping[str, Any]) -> tuple[Any, ...]:
    segments = workout.get("workoutSegments") or []
    return tuple(
        _step_signature(step)
        for segment in segments
        if isinstance(segment, Mapping)
        for step in (segment.get("workoutSteps") or [])
        if isinstance(step, Mapping)
    )


def _extract_workout_id(result: Any) -> str:
    if not isinstance(result, Mapping):
        raise RuntimeError("Garmin upload returned no structured workout result")
    value = result.get("workoutId") or result.get("workout_id") or result.get("id")
    if value is None:
        raise RuntimeError("Garmin upload succeeded but returned no workout ID")
    return str(value)


def _extract_scheduled_id(result: Any) -> Optional[str]:
    if not isinstance(result, (Mapping, list, tuple)):
        json_loader = getattr(result, "json", None)
        if callable(json_loader):
            try:
                decoded = json_loader()
            except Exception:
                return None
            if isinstance(decoded, (Mapping, list, tuple)):
                return _extract_scheduled_id(decoded)
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
        if result.get("id") is not None and not any(
            key in result for key in ("workoutId", "workout_id")
        ):
            return str(result["id"])
        values = result.values()
    else:
        values = result
    for value in values:
        nested = _extract_scheduled_id(value)
        if nested is not None:
            return nested
    return None


def _lookup_scheduled_id(
    workout_id: str,
    scheduled_date: str,
    *,
    attempts: int = 12,
    retry_delay_s: float = 0.5,
) -> Optional[str]:
    """Poll the calendar-entry ID when Garmin's schedule response omits it."""
    query = {
        "query": (
            "query{workoutScheduleSummariesScalar("
            f'startDate:"{scheduled_date}", endDate:"{scheduled_date}")}}'
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
                    and str(entry.get("scheduleDate")) == str(scheduled_date)
                    and entry.get("scheduledWorkoutId") is not None
                ):
                    return str(entry["scheduledWorkoutId"])
        if attempt + 1 < max(1, attempts):
            time.sleep(max(0.0, retry_delay_s))
    return None


def _wait_scheduled_id_absent(
    scheduled_id: str,
    scheduled_date: str,
    *,
    attempts: int = 5,
    retry_delay_s: float = 0.5,
) -> bool:
    query = {
        "query": (
            "query{workoutScheduleSummariesScalar("
            f'startDate:"{scheduled_date}", endDate:"{scheduled_date}")}}'
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


def _plan_change_set(store, plan_row: Mapping[str, Any], sessions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    links = {
        item["local_workout_key"]: item
        for item in store.list_workout_links(plan_id=str(plan_row["id"]))
    }
    items = []
    for session in sessions:
        key = str(session["session_id"])
        existing = links.get(key)
        existing_state = str(existing.get("state")) if existing else None
        if existing_state in {"scheduled", "applied"}:
            action = "no_op"
        elif existing_state in {
            "creating",
            "uploaded",
            "indeterminate",
            "recovery_required",
        }:
            action = "reconcile_required"
        else:
            action = "create_and_schedule"
        items.append(
            {
                "session_id": key,
                "date": session["date"],
                "name": session["workout"]["workoutName"],
                "workout_signature": _workout_signature(session["workout"]),
                "action": action,
                "existing_link": existing,
            }
        )
    return {
        "total": len(items),
        "creates": sum(item["action"] == "create_and_schedule" for item in items),
        "no_ops": sum(item["action"] == "no_op" for item in items),
        "reconcile_required": sum(
            item["action"] == "reconcile_required" for item in items
        ),
        "items": items,
    }


def _rollback_created(store, created: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    outcomes: list[dict[str, Any]] = []
    recovery: list[str] = []
    for item in reversed(created):
        workout_id = item.get("workout_id")
        errors: list[str] = []
        if workout_id is None:
            if item.get("link_id"):
                store.update_workout_link_state(
                    link_id=item["link_id"], state="indeterminate"
                )
            message = (
                "Inspect Garmin for a possibly created workout named "
                f"{item.get('workout_name')!r} (session {item['session_id']}); "
                "the upload response was inconclusive."
            )
            outcomes.append(
                {
                    "workout_id": None,
                    "state": "indeterminate",
                    "errors": [message],
                }
            )
            recovery.append(message)
            continue
        scheduled_id = item.get("scheduled_id")
        safe_to_delete = not item.get("schedule_attempted")
        if scheduled_id is not None:
            unschedule_errors: list[str] = []
            scheduled_date = item.get("scheduled_date")
            for attempt in range(3):
                try:
                    garmin_client.unschedule_workout(scheduled_id)
                except Exception as exc:
                    unschedule_errors.append(str(exc))
                if scheduled_date and _wait_scheduled_id_absent(
                    str(scheduled_id), str(scheduled_date)
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
                    f"calendar entry {scheduled_id} has not disappeared from Garmin's read-back index{suffix}"
                )
        elif item.get("schedule_attempted"):
            errors.append(
                f"reconcile the calendar entry for workout {workout_id}; its schedule ID is not yet visible"
            )
        if safe_to_delete:
            try:
                garmin_client.delete_workout(workout_id)
            except Exception as exc:
                errors.append(f"delete workout {workout_id}: {exc}")
        else:
            errors.append(
                f"keep generated workout {workout_id} until its calendar entry is reconciled; deleting it now can leave an orphan schedule"
            )
        state = "recovery_required" if errors else "rolled_back"
        if item.get("link_id"):
            store.update_workout_link_state(link_id=item["link_id"], state=state)
        outcomes.append({"workout_id": workout_id, "state": state, "errors": errors})
        if errors:
            recovery.append(
                f"Reconcile generated workout {workout_id} (session {item['session_id']}); "
                + "; ".join(errors)
            )
    return outcomes, recovery


def _apply_sessions(
    store,
    plan_row: Mapping[str, Any],
    sessions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if garmin_client is None:
        raise RuntimeError("Garmin client is not configured")
    change_set = _plan_change_set(store, plan_row, sessions)
    unresolved = [
        item
        for item in change_set["items"]
        if item["action"] == "reconcile_required"
    ]
    if unresolved:
        return {
            "status": "blocked_recovery_required",
            "created": [],
            "recovery_checklist": [
                "Reconcile the indeterminate generated workout link for session "
                f"{item['session_id']} before applying this plan again."
                for item in unresolved
            ],
        }
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    try:
        for item, session in zip(change_set["items"], sessions):
            if item["action"] == "no_op":
                skipped.append(item)
                continue
            link = store.put_workout_link(
                plan_id=str(plan_row["id"]),
                local_workout_key=str(session["session_id"]),
                provider="garmin",
                scheduled_date=str(session["date"]),
                state="creating",
            )
            created_item = {
                "session_id": str(session["session_id"]),
                "workout_name": str(session["workout"]["workoutName"]),
                "workout_id": None,
                "link_id": link["id"],
                "scheduled_id": None,
                "scheduled_date": str(session["date"]),
                "schedule_attempted": False,
            }
            created.append(created_item)
            upload = garmin_client.upload_workout(copy.deepcopy(session["workout"]))
            workout_id = _extract_workout_id(upload)
            created_item["workout_id"] = workout_id
            store.update_workout_link_state(
                link_id=link["id"],
                state="uploaded",
                external_workout_id=workout_id,
                scheduled_date=str(session["date"]),
            )

            confirmed = garmin_client.get_workout_by_id(workout_id)
            if not isinstance(confirmed, Mapping) or _workout_signature(confirmed) != _workout_signature(
                session["workout"]
            ):
                raise RuntimeError(
                    f"Read-back validation failed for generated workout {workout_id}"
                )
            created_item["schedule_attempted"] = True
            schedule_result = garmin_client.schedule_workout(workout_id, str(session["date"]))
            scheduled_id = _extract_scheduled_id(schedule_result)
            if scheduled_id is None:
                scheduled_id = _lookup_scheduled_id(workout_id, str(session["date"]))
            if scheduled_id is None:
                raise RuntimeError(
                    f"Schedule outcome for workout {workout_id} could not be read back"
                )
            created_item["scheduled_id"] = scheduled_id
            store.update_workout_link_state(
                link_id=link["id"],
                state="scheduled",
                external_schedule_id=scheduled_id,
            )
    except Exception as exc:
        rollback, recovery = _rollback_created(store, created)
        return {
            "status": "failed_rolled_back" if not recovery else "failed_recovery_required",
            "error": f"{type(exc).__name__}: {exc}",
            "created_before_failure": created,
            "rollback": rollback,
            "recovery_checklist": recovery,
            "untouched_existing_objects": len(skipped),
        }

    return {
        "status": "applied" if created else "already_applied",
        "created": created,
        "idempotent_no_ops": skipped,
        "recovery_checklist": [],
    }


def apply_training_plan(plan_id: str, *, dry_run: bool = True) -> dict[str, Any]:
    store, _athlete_id = _store_context()
    row = _plan_row(plan_id)
    plan = row["plan_json"]
    sessions = list(_iter_sessions(plan))
    change_set = _plan_change_set(store, row, sessions)
    preview = {
        "status": "preview" if dry_run else "applying",
        "dry_run": bool(dry_run),
        "plan_id": row["logical_plan_id"],
        "plan_revision_id": row["id"],
        "revision": row["revision"],
        "change_set": change_set,
        "warnings": [
            "Only generated objects linked to this plan revision are eligible for compensation.",
            "Garmin writes are not retried automatically.",
        ],
    }
    if dry_run:
        return preview
    with _apply_lock:
        result = _apply_sessions(store, row, sessions)
    result.update({key: value for key, value in preview.items() if key not in {"status", "dry_run"}})
    result["dry_run"] = False
    store.add_audit(
        athlete_id=row["athlete_id"],
        action="apply",
        object_type="training_plan",
        object_id=str(row["id"]),
        details={"result": result["status"], "created": len(result.get("created", []))},
    )
    return result


def _latest_mapping(raw: Any) -> dict[str, Any]:
    if isinstance(raw, list):
        return next((item for item in reversed(raw) if isinstance(item, dict)), {})
    return raw if isinstance(raw, dict) else {}


def _readiness_score(raw: Any) -> Optional[float]:
    item = _latest_mapping(raw)
    value = item.get("score")
    return float(value) if isinstance(value, (int, float)) else None


def _hrv_status(raw: Any) -> Optional[str]:
    item = _latest_mapping(raw)
    summary = item.get("hrvSummary") or item
    value = summary.get("status") if isinstance(summary, Mapping) else None
    return str(value) if value is not None else None


def _training_tsb(raw: Any) -> Optional[float]:
    item = _latest_mapping(raw)
    recent = item.get("mostRecentTrainingStatus") or {}
    candidates = recent.get("latestTrainingStatusData") or {}
    if not isinstance(candidates, Mapping):
        return None
    chosen: Mapping[str, Any] = {}
    for value in candidates.values():
        if not isinstance(value, Mapping):
            continue
        if not chosen:
            chosen = value
        if value.get("primaryTrainingDevice"):
            chosen = value
            break
    load = chosen.get("acuteTrainingLoadDTO") or {}
    acute = load.get("dailyTrainingLoadAcute") if isinstance(load, Mapping) else None
    chronic = load.get("dailyTrainingLoadChronic") if isinstance(load, Mapping) else None
    if isinstance(acute, (int, float)) and isinstance(chronic, (int, float)):
        return round(float(chronic) - float(acute), 1)
    return None


def _activity_completion(plan: Mapping[str, Any], target_week_of: str) -> tuple[Optional[float], dict[str, Any]]:
    weeks = list(plan.get("weeks", []))
    index = next((i for i, week in enumerate(weeks) if week.get("week_of") == target_week_of), None)
    if index is None:
        raise ValueError(f"week {target_week_of} is not in the plan")
    if index == 0:
        return None, {"method": "no_previous_plan_week"}
    prior = weeks[index - 1]
    start = dt.date.fromisoformat(prior["week_of"])
    end = start + dt.timedelta(days=6)
    activities = garmin_client.get_activities_by_date(start.isoformat(), end.isoformat(), "cycling")
    if isinstance(activities, Mapping):
        activities = activities.get("activities") or activities.get("payload") or []
    activities = activities if isinstance(activities, list) else []
    planned = len(prior.get("sessions", []))
    completed = min(planned, len(activities))
    rate = completed / planned if planned else None
    return rate, {
        "method": "cycling_activity_count_over_planned_sessions",
        "week_of": prior["week_of"],
        "planned_sessions": planned,
        "cycling_activities": len(activities),
        "completed_sessions_capped": completed,
    }


def propose_week_adaptation(
    week_of: str,
    *,
    plan_id: Optional[str] = None,
    subjective_status: Optional[str] = None,
) -> dict[str, Any]:
    if garmin_client is None:
        raise RuntimeError("Garmin client is not configured")
    target = dt.date.fromisoformat(week_of)
    if target.weekday() != 0:
        raise ValueError("week_of must be a Monday")
    store, _athlete_id = _store_context()
    row = _plan_row(plan_id)
    plan = row["plan_json"]
    snapshot_date = min(dt.date.today(), target - dt.timedelta(days=1)).isoformat()
    errors: dict[str, str] = {}

    def fetch(name: str, call):
        try:
            return call()
        except Exception as exc:
            errors[name] = f"{type(exc).__name__}: {exc}"
            return None

    readiness_raw = fetch("readiness", lambda: garmin_client.get_training_readiness(snapshot_date))
    hrv_raw = fetch("hrv", lambda: garmin_client.get_hrv_data(snapshot_date))
    status_raw = fetch("training_status", lambda: garmin_client.get_training_status(snapshot_date))
    completion_result = fetch("completion", lambda: _activity_completion(plan, week_of))
    completion_rate, completion_detail = completion_result or (None, {"method": "unavailable"})
    proposal = build_week_adaptation(
        plan,
        week_of,
        readiness=_readiness_score(readiness_raw),
        hrv_status=_hrv_status(hrv_raw),
        tsb=_training_tsb(status_raw),
        completion_rate=completion_rate,
        subjective_status=subjective_status,
    )
    proposal["input_snapshot"] = {
        "as_of": snapshot_date,
        "completion": completion_detail,
        "errors": errors,
        **proposal["evidence"],
    }
    input_hash = _payload_hash(
        {
            "kind": "week_adaptation",
            "policy_version": POLICY_VERSION,
            "plan_revision_id": row["id"],
            "week_of": week_of,
            "input_snapshot": proposal["input_snapshot"],
        }
    )
    proposal["adaptation_id"] = f"adapt_{input_hash[:16]}"
    saved, created = store.save_adaptation(
        plan_id=str(row["id"]),
        week_of=week_of,
        revision=int(row["revision"]) + 1,
        patch=proposal,
        reasons=proposal["reasons"],
        input_hash=input_hash,
        status="pending",
        adaptation_id=proposal["adaptation_id"],
    )
    if not created:
        proposal = saved["patch_json"]
    proposal["stored"] = {"id": saved["id"], "created": created, "input_hash": input_hash}
    proposal["garmin_write_performed"] = False
    return proposal


def apply_week_adaptation_patch(adaptation_id: str, *, dry_run: bool = True) -> dict[str, Any]:
    store, _athlete_id = _store_context()
    adaptation = store.get_adaptation(adaptation_id)
    if adaptation is None:
        raise KeyError(f"Unknown adaptation: {adaptation_id}")
    if adaptation["status"] == "applied":
        return {
            "status": "already_applied",
            "dry_run": bool(dry_run),
            "adaptation_id": adaptation_id,
            "idempotent": True,
        }
    base_row = store.get_plan(str(adaptation["plan_id"]))
    if base_row is None:
        raise KeyError(f"Adaptation {adaptation_id} refers to a missing plan revision")
    proposal = adaptation["patch_json"]
    revised_plan = copy.deepcopy(base_row["plan_json"])
    replaced = False
    for index, week in enumerate(revised_plan.get("weeks", [])):
        if week.get("week_of") == adaptation["week_of"]:
            revised_plan["weeks"][index] = copy.deepcopy(proposal["patched_week"])
            replaced = True
            break
    if not replaced:
        raise ValueError(f"week {adaptation['week_of']} is no longer present in the base plan")
    revised_plan["revision"] = int(base_row["revision"]) + 1
    revised_plan["status"] = "adapted"
    revised_plan["plan_id"] = base_row["logical_plan_id"]
    revision_hash = _payload_hash(
        {"kind": "adapted_plan", "adaptation_id": adaptation_id, "plan": revised_plan}
    )
    all_target_sessions = list(_iter_sessions(revised_plan, adaptation["week_of"]))
    locked_session_ids = {
        str(session["session_id"])
        for session in all_target_sessions
        if session.get("locked")
    }
    target_sessions = [
        session
        for session in all_target_sessions
        if str(session["session_id"]) not in locked_session_ids
    ]
    week_links = [
        link
        for link in store.list_workout_links(plan_id=str(base_row["id"]))
        if link.get("scheduled_date")
        and dt.date.fromisoformat(str(link["scheduled_date"]))
        >= dt.date.fromisoformat(adaptation["week_of"])
        and dt.date.fromisoformat(str(link["scheduled_date"]))
        < dt.date.fromisoformat(adaptation["week_of"]) + dt.timedelta(days=7)
        and link.get("state") in {"scheduled", "applied"}
    ]
    locked_links = [
        link
        for link in week_links
        if str(link.get("local_workout_key")) in locked_session_ids
    ]
    old_links = [
        link
        for link in week_links
        if str(link.get("local_workout_key")) not in locked_session_ids
    ]
    preview = {
        "status": "preview",
        "dry_run": bool(dry_run),
        "adaptation_id": adaptation_id,
        "base_plan_revision_id": base_row["id"],
        "new_revision": revised_plan["revision"],
        "changes": proposal.get("changes", []),
        "existing_generated_workouts_to_supersede": old_links,
        "locked_sessions_preserved": [
            {
                "session_id": session["session_id"],
                "date": session["date"],
                "name": session["workout"]["workoutName"],
            }
            for session in all_target_sessions
            if str(session["session_id"]) in locked_session_ids
        ],
        "new_sessions": [
            {
                "session_id": item["session_id"],
                "date": item["date"],
                "name": item["workout"]["workoutName"],
                "workout_signature": _workout_signature(item["workout"]),
            }
            for item in target_sessions
        ],
        "garmin_write_performed": False,
    }
    if dry_run:
        return preview

    with _apply_lock:
        new_row, _created = store.save_plan(
            athlete_id=base_row["athlete_id"],
            sport=base_row["sport"],
            plan=revised_plan,
            input_hash=revision_hash,
            status="adapted",
            revision=int(revised_plan["revision"]),
            plan_id=base_row["logical_plan_id"],
        )
        preserved_locked_links = [
            store.put_workout_link(
                plan_id=str(new_row["id"]),
                local_workout_key=str(link["local_workout_key"]),
                provider=str(link["provider"]),
                state=str(link["state"]),
                external_workout_id=link.get("external_workout_id"),
                external_schedule_id=link.get("external_schedule_id"),
                scheduled_date=link.get("scheduled_date"),
            )
            for link in locked_links
        ]
        remote_result: dict[str, Any] = {
            "status": "not_needed",
            "created": [],
            "recovery_checklist": [],
        }
        if old_links:
            remote_result = _apply_sessions(store, new_row, target_sessions)
            if remote_result["status"] not in {"applied", "already_applied"}:
                store.update_adaptation_status(adaptation_id=adaptation_id, status="failed")
                preview.update(
                    {
                        "status": remote_result["status"],
                        "dry_run": False,
                        "garmin_write_performed": bool(remote_result.get("created_before_failure")),
                        "new_plan_revision_id": new_row["id"],
                        "remote_result": remote_result,
                    }
                )
                return preview

        recovery: list[str] = list(remote_result.get("recovery_checklist", []))
        superseded: list[dict[str, Any]] = []
        # Replacement workouts exist before old generated workouts are removed.
        # A cleanup failure therefore preserves a usable schedule and yields an
        # explicit checklist instead of losing the original week.
        for link in old_links:
            workout_id = link.get("external_workout_id")
            link_errors: list[str] = []
            scheduled_id = link.get("external_schedule_id")
            if scheduled_id is None and workout_id is not None:
                try:
                    scheduled_id = _lookup_scheduled_id(
                        str(workout_id), str(link["scheduled_date"])
                    )
                except Exception as exc:
                    link_errors.append(f"look up calendar entry: {exc}")
            if scheduled_id is not None:
                try:
                    garmin_client.unschedule_workout(scheduled_id)
                except Exception as exc:
                    link_errors.append(f"unschedule {scheduled_id}: {exc}")
            else:
                link_errors.append(
                    f"verify removal of the calendar entry for workout {workout_id}"
                )
            try:
                if workout_id is not None:
                    garmin_client.delete_workout(workout_id)
            except Exception as exc:
                link_errors.append(f"delete workout {workout_id}: {exc}")

            if link_errors:
                store.update_workout_link_state(link_id=link["id"], state="recovery_required")
                recovery.append(
                    f"Finish superseding generated workout {workout_id}: "
                    + "; ".join(link_errors)
                )
            else:
                store.update_workout_link_state(link_id=link["id"], state="superseded")
                superseded.append(
                    {
                        "link_id": link["id"],
                        "workout_id": workout_id,
                        "scheduled_id": scheduled_id,
                    }
                )

        store.update_adaptation_status(adaptation_id=adaptation_id, status="applied")
        preview.update(
            {
                "status": "applied" if not recovery else "applied_recovery_required",
                "dry_run": False,
                "garmin_write_performed": bool(old_links),
                "new_plan_revision_id": new_row["id"],
                "remote_result": remote_result,
                "superseded": superseded,
                "preserved_locked_links": preserved_locked_links,
                "recovery_checklist": recovery,
            }
        )
        return preview


def _tool_result(call, *args, **kwargs) -> dict[str, Any]:
    try:
        return call(*args, **kwargs)
    except (ValueError, RuntimeError, KeyError, OSError) as exc:
        return {"status": "error", "error": str(exc)}


def register_tools(app):
    """Register deterministic, preview-first cycling coach tools."""

    @app.tool(structured_output=True)
    async def plan_training_block(
        race_date: str,
        goal: str,
        sport: str = "cycling",
        current_ctl: Optional[float] = None,
        days_per_week: int = 4,
        available_weekdays: Optional[List[str]] = None,
        zone_model_id: Optional[str] = None,
        start_date: Optional[str] = None,
    ) -> TrainingBlockResult:
        """Create and persist a deterministic cycling block without writing Garmin."""

        plan = _tool_result(
            build_training_block,
            race_date=race_date,
            goal=goal,
            sport=sport,
            current_ctl=current_ctl,
            days_per_week=days_per_week,
            available_weekdays=available_weekdays,
            zone_model_id=zone_model_id,
            start_date=start_date,
        )
        if plan.get("status") == "error":
            return plan
        saved = _tool_result(persist_training_block, plan)
        if isinstance(saved, dict):
            return saved
        row, created = saved
        plan["storage"] = {
            "plan_revision_id": row["id"],
            "logical_plan_id": row["logical_plan_id"],
            "created": created,
        }
        return plan

    @app.tool(structured_output=True)
    async def apply_training_block(
        ctx: Context, plan_id: str, dry_run: bool = True
    ) -> ApplyTrainingBlockResult:
        """Preview or explicitly upload/schedule a stored plan with compensation."""
        preview = _tool_result(apply_training_plan, plan_id, dry_run=True)
        if dry_run or preview.get("status") == "error":
            return preview
        change_set = preview.get("change_set") or {}
        if not change_set.get("creates"):
            return _tool_result(
                apply_training_plan,
                preview.get("plan_revision_id", plan_id),
                dry_run=False,
            )
        confirmed, message = await write_confirmation.confirm_garmin_write(
            ctx,
            action="upload and schedule a stored cycling training block",
            summary={
                "plan_revision_id": preview["plan_revision_id"],
                "revision": preview["revision"],
                "workouts_to_create": change_set.get("creates", 0),
                "scheduled_workouts": [
                    {
                        "date": item["date"],
                        "name": item["name"],
                        "workout_signature": item["workout_signature"],
                    }
                    for item in change_set.get("items", [])
                    if item.get("action") == "create_and_schedule"
                ],
            },
        )
        if not confirmed:
            return write_confirmation.needs_confirmation_result(
                preview=preview, message=message
            )
        return _tool_result(
            apply_training_plan,
            preview["plan_revision_id"],
            dry_run=False,
        )

    @app.tool(structured_output=True)
    async def adapt_week(
        week_of: str,
        subjective_status: Optional[str] = None,
        plan_id: Optional[str] = None,
    ) -> AdaptWeekResult:
        """Create a pending weekly patch; this tool never writes to Garmin."""

        return _tool_result(
            propose_week_adaptation,
            week_of,
            plan_id=plan_id,
            subjective_status=subjective_status,
        )

    @app.tool(structured_output=True)
    async def apply_week_adaptation(
        ctx: Context, adaptation_id: str, dry_run: bool = True
    ) -> ApplyWeekAdaptationResult:
        """Preview or explicitly apply one pending patch and its Garmin delta."""
        preview = _tool_result(
            apply_week_adaptation_patch, adaptation_id, dry_run=True
        )
        if dry_run or preview.get("status") != "preview":
            return preview
        confirmed, message = await write_confirmation.confirm_garmin_write(
            ctx,
            action="apply one weekly adaptation and replace linked Garmin workouts",
            summary={
                "adaptation_id": adaptation_id,
                "base_plan_revision_id": preview["base_plan_revision_id"],
                "new_revision": preview["new_revision"],
                "changes": preview.get("changes", []),
                "replacement_sessions": preview.get("new_sessions", []),
                "workouts_to_supersede": len(
                    preview.get("existing_generated_workouts_to_supersede", [])
                ),
                "superseded_workouts": [
                    {
                        "workout_id": item.get("external_workout_id"),
                        "schedule_id": item.get("external_schedule_id"),
                        "date": item.get("scheduled_date"),
                    }
                    for item in preview.get(
                        "existing_generated_workouts_to_supersede", []
                    )
                ],
            },
        )
        if not confirmed:
            return write_confirmation.needs_confirmation_result(
                preview=preview, message=message
            )
        return _tool_result(
            apply_week_adaptation_patch, adaptation_id, dry_run=False
        )

    return app
