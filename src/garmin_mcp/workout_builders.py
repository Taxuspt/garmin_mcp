"""
High-level workout builders for Garmin Connect MCP Server.

These tools construct the internal Garmin Connect JSON internally and delegate
to the existing upload_workout / schedule_workout endpoints.
"""
import json
from typing import Any, Dict, List, Optional

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


def resolve_exercise(ex_name: str, category: str | None = None) -> tuple[str | None, str | None]:
    """Resolve a human-readable exercise name against the FIT exercise catalog.

    Returns Garmin's ("CATEGORY", "EXERCISE_NAME") pair in uppercase when the
    name is found (e.g. "romanian deadlift" -> ("DEADLIFT", "ROMANIAN_DEADLIFT")),
    or (None, None) when it is not. Garmin Connect only links an exercise to its
    catalog entry (with animation, muscle groups etc.) when both fields match
    its enum; anything else is stored as a free-text step.

    Lookup order:
      1. inside the caller-supplied category, if any — this disambiguates names
         that occur in several categories (e.g. "lunge" is in both the lunge
         and the sandbag category)
      2. as a category name that doubles as an exercise name — "plank" means
         the plank category's plank, not the banded or suspension variant
      3. across all categories, in sorted order for determinism
    """
    from garmin_mcp.exercise_catalog_data import CATALOG

    key = (ex_name or "").strip().lower().replace(" ", "_").replace("-", "_")

    if category:
        cat = category.strip().lower()
        if cat in CATALOG and key in CATALOG[cat]:
            return cat.upper(), key.upper()

    if key in CATALOG and key in CATALOG[key]:
        return key.upper(), key.upper()

    for cat in sorted(CATALOG):
        if key in CATALOG[cat]:
            return cat.upper(), key.upper()

    if key in CATALOG:
        return key.upper(), key.upper()

    return None, None


def build_strength_json(
    name: str,
    exercises: List[Dict[str, Any]],
) -> dict:
    """Build the Garmin Connect JSON for a strength workout.

    Exercise names are resolved against the bundled FIT exercise catalog (see
    resolve_exercise). Recognised names are sent as Garmin's ("category",
    "exerciseName") enum pair, so Garmin Connect links the actual catalog
    exercise instead of storing a free-text step. Unrecognised names keep the
    previous behaviour: "exerciseName" is sent as free text (Garmin accepts it
    and stores an empty string), "category" is omitted — Garmin accepts an
    absent category but rejects anything outside its enum, including
    "UNASSIGNED" and "OTHER" — and the label survives the round trip in the
    step "description".

    A caller-supplied "category" first narrows the catalog lookup (for names
    that occur in several categories) and is otherwise validated, uppercased
    and passed through untouched. Valid values come from Garmin's published
    catalog: https://connect.garmin.com/web-data/exercises/Exercises.json

    Each exercise ends on reps by default; "duration_seconds" or
    "distance_meters" switch the end condition to time or distance, which is
    what planks, carries and ergometer intervals actually need. An optional
    "note" is appended to the step description.
    """
    steps: List[dict] = []
    step_order = 1

    for ex in exercises:
        ex_name = ex.get("name", "Exercise")
        sets = int(ex.get("sets", 1))
        rest_seconds = int(ex.get("rest_seconds", 60))

        category = ex.get("category")
        if category is not None:
            if not isinstance(category, str) or not category.strip():
                raise ValueError(
                    f"category for exercise {ex_name!r} must be a non-empty string"
                )
            category = category.strip()

        resolved_cat, resolved_name = resolve_exercise(ex_name, category)

        # End condition: reps by default; time and distance are what planks,
        # carries and ergometer intervals actually need.
        if ex.get("duration_seconds"):
            value = float(int(ex["duration_seconds"]))
            condition = {"conditionTypeId": 2, "conditionTypeKey": "time"}
            amount = f"{int(value)}s"
        elif ex.get("distance_meters"):
            value = float(int(ex["distance_meters"]))
            condition = {"conditionTypeId": 3, "conditionTypeKey": "distance"}
            amount = f"{int(value)}m"
        else:
            value = float(int(ex.get("reps", 1)))
            condition = {"conditionTypeId": 10, "conditionTypeKey": "reps"}
            amount = f"{int(value)} reps"

        description = f"{ex_name}: {sets} sets x {amount}"
        note = ex.get("note")
        if note:
            description = f"{description} - {note}"

        # Work step
        step = {
            "type": "ExecutableStepDTO",
            "stepOrder": step_order,
            "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
            "description": description,
            "endCondition": condition,
            "endConditionValue": value,
            "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
            "exerciseName": resolved_name if resolved_name else ex_name,
        }

        if resolved_cat is not None:
            step["category"] = resolved_cat
        elif category is not None:
            # Unresolved name with an explicit category: Garmin validates the
            # value against its own enum, so pass it through untouched rather
            # than guessing. An absent category is accepted for unknown names.
            step["category"] = category.upper()

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


# =============================================================================
# MCP TOOLS
# =============================================================================

def register_tools(app):
    """Register all high-level workout builder tools with the MCP server app"""

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

        Exercise names are resolved against the bundled FIT exercise catalog
        (51 categories, 1846 exercises — browse it with
        list_exercise_categories / list_exercises), so recognised exercises are
        linked to Garmin's catalog entry. Names outside the catalog become
        free-text steps whose label is kept in the step description, instead of
        failing the upload.

        Args:
            name: Workout name
            exercises: List of dicts. Keys per exercise:
                name           - catalog name (e.g. "romanian_deadlift") or free text
                sets           - number of sets
                rest_seconds   - rest after the step (default 60)
                category       - optional; disambiguates names that occur in
                                 several categories (e.g. "lunge"). For names
                                 outside the catalog it is passed through and must
                                 be one of Garmin's exercise categories — anything
                                 else, including "UNASSIGNED" and "OTHER", is
                                 rejected with 400 Invalid category. Full list:
                                 https://connect.garmin.com/web-data/exercises/Exercises.json
                note           - optional hint appended to the step description
              plus at most one end condition:
                reps             - repetitions (default, 1 if nothing is given)
                duration_seconds - time-based step (ergometer intervals, planks)
                distance_meters  - distance-based step (carries, sled)
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
