"""
Workout-related functions for Garmin Connect MCP Server
"""
import copy
import json
import datetime
import unicodedata
import urllib.request
from typing import Any, Dict, List, Optional, Union

# The garmin_client will be set by the main file
garmin_client = None
_exercise_catalog_cache: dict | None = None
_exercise_translations_cache: dict[str, dict[str, str]] = {}

EXERCISE_CATALOG_URL = "https://connect.garmin.com/web-data/exercises/Exercises.json"
EXERCISE_TRANSLATIONS_URLS = {
    "en": "https://connect.garmin.com/web-translations/exercise_types/exercise_types.properties",
    "es": "https://connect.garmin.com/web-translations/exercise_types/exercise_types_es.properties",
}


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.lower())
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(stripped.replace("_", " ").split())


def _load_json_url(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def _load_text_url(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request) as response:
        return response.read().decode("utf-8")


def _get_exercise_catalog() -> dict:
    global _exercise_catalog_cache
    if _exercise_catalog_cache is None:
        _exercise_catalog_cache = _load_json_url(EXERCISE_CATALOG_URL)
    return _exercise_catalog_cache


def _parse_properties(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        mapping[key.strip()] = value.strip()
    return mapping


def _get_exercise_translations(lang: str) -> dict[str, str]:
    if lang not in _exercise_translations_cache:
        url = EXERCISE_TRANSLATIONS_URLS[lang]
        _exercise_translations_cache[lang] = _parse_properties(_load_text_url(url))
    return _exercise_translations_cache[lang]


def _exercise_translation_key(category: str, exercise_name: str) -> str:
    return f"{category}_{exercise_name}"


def _humanize_key(value: str) -> str:
    return value.replace("_", " ").title()


def _search_exercise_catalog(query: str, limit: int = 20) -> list[dict[str, Any]]:
    catalog = _get_exercise_catalog()
    translations_en = _get_exercise_translations("en")
    translations_es = _get_exercise_translations("es")
    normalized_query = _normalize_text(query)
    results: list[dict[str, Any]] = []

    for category, category_data in catalog.get("categories", {}).items():
        exercises = category_data.get("exercises", {})
        for exercise_name in exercises.keys():
            translation_key = _exercise_translation_key(category, exercise_name)
            display_name_en = translations_en.get(translation_key, _humanize_key(exercise_name))
            display_name_es = translations_es.get(translation_key)
            haystack = [
                _normalize_text(category),
                _normalize_text(exercise_name),
                _normalize_text(display_name_en),
            ]
            if display_name_es:
                haystack.append(_normalize_text(display_name_es))

            if not any(normalized_query in candidate for candidate in haystack):
                continue

            score = 0
            for candidate in haystack:
                if candidate == normalized_query:
                    score = max(score, 100)
                elif candidate.startswith(normalized_query):
                    score = max(score, 75)
                else:
                    score = max(score, 50)

            results.append(
                {
                    "category": category,
                    "exercise_name": exercise_name,
                    "display_name_en": display_name_en,
                    "display_name_es": display_name_es,
                    "score": score,
                }
            )

    results.sort(key=lambda item: (-item["score"], item["category"], item["exercise_name"]))
    return results[:limit]


def _fix_hr_zone_step(step: dict) -> None:
    """Fix a common mistake where HR zone targets use targetValueOne instead of zoneNumber.

    When targetType is heart.rate.zone, Garmin expects zoneNumber (1-5).
    If targetValueOne is set to a small integer (1-5) and zoneNumber is missing,
    this is almost certainly a zone number, not an absolute HR value.
    """
    target_type = step.get('targetType') or {}
    target_key = target_type.get('workoutTargetTypeKey', '')

    if target_key == 'heart.rate.zone' and 'zoneNumber' not in step:
        zone = step.get('targetValueOne')
        if zone is not None and 1 <= zone <= 5:
            step['zoneNumber'] = int(zone)
            step.pop('targetValueOne', None)
            step.pop('targetValueTwo', None)

    # Recurse into nested steps (RepeatGroupDTO)
    for nested in step.get('workoutSteps', []):
        _fix_hr_zone_step(nested)


def _fix_hr_zone_steps(workout_data: dict) -> None:
    """Walk all workout steps and fix HR zone target mistakes."""
    for segment in workout_data.get('workoutSegments', []):
        for step in segment.get('workoutSteps', []):
            _fix_hr_zone_step(step)


def _validate_strength_step(step: dict, errors: list[str], path: str) -> None:
    """Validate Garmin strength steps and recurse through nested steps."""
    step_type = step.get("type")
    target_type = step.get("targetType") or {}
    exercise_name = step.get("exerciseName")
    category = step.get("category")
    end_condition = step.get("endCondition") or {}

    if step_type == "ExecutableStepDTO":
        if exercise_name and not category:
            errors.append(f"{path}: exerciseName requires category for strength workouts")
        if category and not exercise_name:
            errors.append(f"{path}: category requires exerciseName for strength workouts")
        if (exercise_name or category) and end_condition.get("conditionTypeKey") == "distance":
            errors.append(f"{path}: strength exercise steps must use reps, not distance")
        if target_type and target_type.get("workoutTargetTypeKey") == "no.target":
            # no.target is valid; avoid carrying extra validation assumptions here
            pass

    for index, nested in enumerate(step.get("workoutSteps", []), start=1):
        _validate_strength_step(nested, errors, f"{path}.workoutSteps[{index}]")


def _validate_strength_workout(workout_data: dict) -> None:
    """Guard against malformed strength workout uploads that Garmin accepts poorly."""
    sport_type = workout_data.get("sportType") or {}
    if sport_type.get("sportTypeKey") != "strength_training":
        return

    errors: list[str] = []
    for segment_index, segment in enumerate(workout_data.get("workoutSegments", []), start=1):
        for step_index, step in enumerate(segment.get("workoutSteps", []), start=1):
            _validate_strength_step(step, errors, f"segment[{segment_index}].workoutSteps[{step_index}]")

    if errors:
        raise ValueError("; ".join(errors))


def _collect_strength_steps(step: dict, steps: list[dict[str, Any]]) -> None:
    if step.get("type") == "ExecutableStepDTO" and (step.get("exerciseName") or step.get("category")):
        entry = {
            "order": step.get("stepOrder"),
            "category": step.get("category"),
            "exercise_name": step.get("exerciseName"),
            "description": step.get("description"),
            "end_condition": (step.get("endCondition") or {}).get("conditionTypeKey"),
            "end_condition_value": step.get("endConditionValue"),
        }
        steps.append({k: v for k, v in entry.items() if v is not None})

    for nested in step.get("workoutSteps", []):
        _collect_strength_steps(nested, steps)


def _summarize_workout_payload(workout_data: dict) -> dict[str, Any]:
    sport_type = workout_data.get("sportType") or {}
    segments = workout_data.get("workoutSegments", [])
    strength_steps: list[dict[str, Any]] = []
    top_level_steps = 0

    for segment in segments:
        steps = segment.get("workoutSteps", [])
        top_level_steps += len(steps)
        for step in steps:
            _collect_strength_steps(step, strength_steps)

    return {
        "name": workout_data.get("workoutName"),
        "sport": sport_type.get("sportTypeKey"),
        "segment_count": len(segments),
        "top_level_step_count": top_level_steps,
        "strength_step_count": len(strength_steps),
        "strength_steps": strength_steps,
    }


def _normalize_weekday(value: str) -> int:
    normalized = _normalize_text(value)
    weekdays = {
        "monday": 0,
        "mon": 0,
        "lunes": 0,
        "tuesday": 1,
        "tue": 1,
        "martes": 1,
        "wednesday": 2,
        "wed": 2,
        "miercoles": 2,
        "miércoles": 2,
        "thursday": 3,
        "thu": 3,
        "jueves": 3,
        "friday": 4,
        "fri": 4,
        "viernes": 4,
        "saturday": 5,
        "sat": 5,
        "sabado": 5,
        "sábado": 5,
        "sunday": 6,
        "sun": 6,
        "domingo": 6,
    }
    if normalized not in weekdays:
        raise ValueError(f"Unsupported weekday: {value}")
    return weekdays[normalized]


def _generate_recurring_dates(
    start_date: str,
    end_date: str,
    weekdays: list[str],
    interval_weeks: int = 1,
    exclude_dates: list[str] | None = None,
) -> list[str]:
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    if interval_weeks < 1:
        raise ValueError("interval_weeks must be at least 1")

    weekday_values = {_normalize_weekday(day) for day in weekdays}
    excluded = set(exclude_dates or [])
    anchor = start - datetime.timedelta(days=start.weekday())
    scheduled: list[str] = []
    current = start

    while current <= end:
        current_week_anchor = current - datetime.timedelta(days=current.weekday())
        week_offset = (current_week_anchor - anchor).days // 7
        if (
            current.weekday() in weekday_values
            and week_offset % interval_weeks == 0
            and current.isoformat() not in excluded
        ):
            scheduled.append(current.isoformat())
        current += datetime.timedelta(days=1)

    return scheduled


def _curate_workout_summary(workout: dict) -> dict:
    """Extract essential workout metadata for list views"""
    sport_type = workout.get('sportType', {})

    summary = {
        "id": workout.get('workoutId'),
        "name": workout.get('workoutName'),
        "sport": sport_type.get('sportTypeKey'),
        "provider": workout.get('workoutProvider'),
        "created_date": workout.get('createdDate'),
        "updated_date": workout.get('updatedDate'),
    }

    # Add optional fields if present
    if workout.get('description'):
        summary['description'] = workout.get('description')

    if workout.get('estimatedDuration'):
        summary['estimated_duration_seconds'] = workout.get('estimatedDuration')

    if workout.get('estimatedDistance'):
        summary['estimated_distance_meters'] = workout.get('estimatedDistance')

    # Remove None values
    return {k: v for k, v in summary.items() if v is not None}


def _curate_workout_step(step: dict) -> dict:
    """Extract essential workout step information"""
    step_type = step.get('stepType') or {}
    end_condition = step.get('endCondition') or {}
    target_type = step.get('targetType') or {}
    weight_unit = step.get('weightUnit') or {}

    curated = {
        "order": step.get('stepOrder'),
        "type": step_type.get('stepTypeKey'),  # warmup, interval, cooldown, rest, recover
    }

    # Description
    if step.get('description'):
        curated['description'] = step.get('description')

    # End condition (duration/distance/lap press)
    if end_condition.get('conditionTypeKey'):
        curated['end_condition'] = end_condition.get('conditionTypeKey')
    if step.get('endConditionValue'):
        # Value meaning depends on condition type (seconds for time, meters for distance)
        curated['end_condition_value'] = step.get('endConditionValue')

    # Target (heart rate, pace, power, etc.)
    target_key = target_type.get('workoutTargetTypeKey')
    if target_key and target_key != 'no.target':
        curated['target_type'] = target_key
        if step.get('targetValueOne'):
            curated['target_value_low'] = step.get('targetValueOne')
        if step.get('targetValueTwo'):
            curated['target_value_high'] = step.get('targetValueTwo')
        if step.get('zoneNumber'):
            curated['target_zone'] = step.get('zoneNumber')

    # Repeat info for repeat steps
    if step.get('type') == 'RepeatGroupDTO':
        curated['repeat_count'] = step.get('numberOfIterations')
        nested_steps = step.get('workoutSteps', [])
        if nested_steps:
            curated['steps'] = [_curate_workout_step(nested) for nested in nested_steps]
            curated['step_count'] = len(nested_steps)

    # Strength-specific fields
    if step.get('category'):
        curated['category'] = step.get('category')
    if step.get('exerciseName'):
        curated['exercise_name'] = step.get('exerciseName')
    if step.get('weightValue') is not None:
        curated['weight_value'] = step.get('weightValue')
    if weight_unit.get('unitKey'):
        curated['weight_unit'] = weight_unit.get('unitKey')

    return {k: v for k, v in curated.items() if v is not None}


def _curate_workout_segment(segment: dict) -> dict:
    """Extract essential segment information including workout steps"""
    sport_type = segment.get('sportType', {})

    curated = {
        "order": segment.get('segmentOrder'),
        "sport": sport_type.get('sportTypeKey'),
    }

    # Estimated metrics
    if segment.get('estimatedDurationInSecs'):
        curated['estimated_duration_seconds'] = segment.get('estimatedDurationInSecs')
    if segment.get('estimatedDistanceInMeters'):
        curated['estimated_distance_meters'] = segment.get('estimatedDistanceInMeters')

    # Workout steps - the actual content of the segment
    steps = segment.get('workoutSteps', [])
    if steps:
        curated['steps'] = [_curate_workout_step(s) for s in steps]
        curated['step_count'] = len(steps)

    return {k: v for k, v in curated.items() if v is not None}


def _curate_workout_details(workout: dict) -> dict:
    """Extract detailed workout information with segments

    Handles both regular workouts (from get_workout_by_id) and training plan workouts
    (from fbt-adaptive endpoint) which use slightly different field names.
    """
    sport_type = workout.get('sportType') or {}

    details = {
        "id": workout.get('workoutId'),
        "uuid": workout.get('workoutUuid'),
        "name": workout.get('workoutName'),
        "sport": sport_type.get('sportTypeKey') if sport_type else None,
        "provider": workout.get('workoutProvider'),
        "created_date": workout.get('createdDate'),
        "updated_date": workout.get('updatedDate'),
    }

    # Optional fields
    if workout.get('description'):
        details['description'] = workout.get('description')

    # Handle both field name variants (regular vs training plan workouts)
    duration = workout.get('estimatedDuration') or workout.get('estimatedDurationInSecs')
    if duration:
        details['estimated_duration_seconds'] = duration

    distance = workout.get('estimatedDistance') or workout.get('estimatedDistanceInMeters')
    if distance:
        details['estimated_distance_meters'] = distance

    if workout.get('avgTrainingSpeed'):
        details['avg_training_speed_mps'] = workout.get('avgTrainingSpeed')

    # Training plan specific fields
    if workout.get('workoutPhrase'):
        details['workout_type'] = workout.get('workoutPhrase')

    if workout.get('trainingEffectLabel'):
        details['training_effect_label'] = workout.get('trainingEffectLabel')

    if workout.get('estimatedTrainingEffect'):
        details['estimated_training_effect'] = workout.get('estimatedTrainingEffect')

    # Curate segments with workout steps
    segments = workout.get('workoutSegments', [])
    if segments:
        details['segments'] = [_curate_workout_segment(seg) for seg in segments]
        details['segment_count'] = len(segments)

    # Remove None values
    return {k: v for k, v in details.items() if v is not None}


def _curate_scheduled_workout(scheduled: dict) -> dict:
    """Extract essential scheduled workout information from GraphQL response"""
    # GraphQL response has workout data at top level (not nested)
    # Completed is determined by presence of associatedActivityId
    is_completed = scheduled.get('associatedActivityId') is not None

    summary = {
        "scheduled_workout_id": scheduled.get('scheduledWorkoutId'),
        "date": scheduled.get('scheduleDate'),
        "workout_uuid": scheduled.get('workoutUuid'),
        "workout_id": scheduled.get('workoutId'),
        "name": scheduled.get('workoutName'),
        "sport": scheduled.get('workoutType'),
        "completed": is_completed,
    }

    # Training plan info
    if scheduled.get('tpPlanName'):
        summary['training_plan'] = scheduled.get('tpPlanName')

    # Workout type description (e.g., "AEROBIC_LOW_SHORTAGE_BASE", "ANAEROBIC_SPEED", "LONG_WORKOUT")
    # This describes the intent/type of the workout from Garmin Coach
    if scheduled.get('workoutPhrase'):
        summary['workout_type'] = scheduled.get('workoutPhrase')

    # Rest day and race day flags
    if scheduled.get('isRestDay'):
        summary['is_rest_day'] = True
    if scheduled.get('race'):
        summary['is_race_day'] = True

    # Optional fields
    if scheduled.get('estimatedDurationInSecs'):
        summary['estimated_duration_seconds'] = scheduled.get('estimatedDurationInSecs')

    if scheduled.get('estimatedDistanceInMeters'):
        summary['estimated_distance_meters'] = scheduled.get('estimatedDistanceInMeters')

    # If completed, include the activity ID
    if is_completed:
        summary['activity_id'] = scheduled.get('associatedActivityId')

    # Remove None values
    return {k: v for k, v in summary.items() if v is not None}


def register_tools(app):
    """Register all workout-related tools with the MCP server app"""

    @app.tool()
    async def get_workouts() -> str:
        """Get all workouts with curated summary list

        Returns a count and list of workout summaries with essential metadata only.
        For detailed workout information including segments, use get_workout_by_id.
        """
        try:
            workouts = garmin_client.get_workouts()
            if not workouts:
                return "No workouts found."

            # Curate the workout list
            curated = {
                "count": len(workouts),
                "workouts": [_curate_workout_summary(w) for w in workouts]
            }

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving workouts: {str(e)}"

    @app.tool()
    async def get_workout_by_id(workout_id: Union[int, str]) -> str:
        """Get detailed information for a specific workout

        Returns workout details including segments and step structure.

        Accepts either:
        - Numeric workout ID (from get_workouts or get_scheduled_workouts)
        - Workout UUID (from get_training_plan_workouts for Garmin Coach workouts)

        Args:
            workout_id: Workout ID (numeric) or UUID (for training plan workouts)
        """
        try:
            workout_id_str = str(workout_id)
            # Detect if this is a UUID (contains dashes) or numeric ID
            is_uuid = '-' in workout_id_str

            if is_uuid:
                # Training plan / Garmin Coach workout - use fbt-adaptive endpoint
                url = f"workout-service/fbt-adaptive/{workout_id_str}"
                response = garmin_client.garth.get("connectapi", url)
                if response.status_code != 200:
                    return f"No workout found with UUID {workout_id_str}. HTTP {response.status_code}"
                workout = response.json()
            else:
                # Regular workout - use standard endpoint
                workout = garmin_client.get_workout_by_id(int(workout_id_str))

            if not workout:
                return f"No workout found with ID {workout_id_str}."

            # Return curated details with segments
            curated = _curate_workout_details(workout)
            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving workout: {str(e)}"

    @app.tool()
    async def download_workout(workout_id: int) -> str:
        """Download a workout as a FIT file

        Downloads the workout in FIT format. The binary data cannot be returned
        directly through the MCP interface, but this confirms the workout is available.

        Args:
            workout_id: ID of the workout to download
        """
        try:
            workout_data = garmin_client.download_workout(workout_id)
            if not workout_data:
                return f"No workout data found for workout with ID {workout_id}."

            # Return information about the download
            data_size = len(workout_data) if isinstance(workout_data, (bytes, bytearray)) else 0
            return json.dumps({
                "workout_id": workout_id,
                "format": "FIT",
                "size_bytes": data_size,
                "message": "Workout data is available in FIT format. Use Garmin Connect API to save to file."
            }, indent=2)
        except Exception as e:
            return f"Error downloading workout: {str(e)}"

    @app.tool()
    async def search_exercise_catalog(query: str, limit: int = 20) -> str:
        """Search Garmin's exercise catalog by human query.

        Supports English keys and Garmin's public Spanish exercise translations.

        Args:
            query: Search string such as "sentadilla" or "pull up"
            limit: Maximum number of results to return
        """
        try:
            matches = _search_exercise_catalog(query, limit=limit)
            return json.dumps(
                {
                    "query": query,
                    "count": len(matches),
                    "matches": matches,
                },
                ensure_ascii=False,
                indent=2,
            )
        except Exception as e:
            return f"Error searching exercise catalog: {str(e)}"

    @app.tool()
    async def validate_workout_payload(workout_data: dict) -> str:
        """Dry-run validation for a workout payload without uploading it.

        Useful for confirming a Garmin strength workout is fully defined before upload.

        Args:
            workout_data: Dictionary containing workout structure to validate
        """
        try:
            normalized = copy.deepcopy(workout_data)
            _fix_hr_zone_steps(normalized)
            _validate_strength_workout(normalized)
            summary = _summarize_workout_payload(normalized)
            return json.dumps(
                {
                    "valid": True,
                    "errors": [],
                    "summary": summary,
                    "normalized_workout_data": normalized,
                },
                ensure_ascii=False,
                indent=2,
            )
        except Exception as e:
            summary = _summarize_workout_payload(workout_data)
            return json.dumps(
                {
                    "valid": False,
                    "errors": [str(e)],
                    "summary": summary,
                },
                ensure_ascii=False,
                indent=2,
            )

    @app.tool()
    async def upload_workout(workout_data: dict) -> str:
        """Upload a workout from JSON data

        Creates a new workout in Garmin Connect from structured workout data.

        IMPORTANT: Step types must use Garmin's DTO format:
        - Use "ExecutableStepDTO" for regular steps (warmup, interval, cooldown, recovery)
        - Use "RepeatGroupDTO" for repeat/interval groups with numberOfIterations

        IMPORTANT: For heart rate zone targets, use "zoneNumber" (1-5), NOT targetValueOne/targetValueTwo.
        targetValueOne/targetValueTwo are only for absolute value ranges (e.g. pace in m/s, power in watts).

        **Available Templates:**
        Instead of building workout JSON from scratch, you can use these MCP resources as starting points:
        - workout://templates/simple-run - Basic warmup/run/cooldown structure
        - workout://templates/interval-running - Interval training with repeat groups
        - workout://templates/tempo-run - Tempo run with heart rate zone targets
        - workout://templates/strength-circuit - Strength training circuit structure
        - workout://reference/structure - Complete JSON structure reference with all fields

        Access these resources using your MCP client's resource reading capability, modify the template
        as needed, and pass the resulting JSON as the workout_data parameter.

        Example workout structure with HR zone target:
        {
            "workoutName": "My Workout",
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSegments": [{
                "segmentOrder": 1,
                "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
                "workoutSteps": [{
                    "type": "ExecutableStepDTO",
                    "stepOrder": 1,
                    "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": 1200.0,
                    "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
                    "zoneNumber": 3
                }]
            }]
        }

        Args:
            workout_data: Dictionary containing workout structure (name, sport type, segments, etc.)
        """
        try:
            # Fix common mistake: HR zone targets using targetValueOne instead of zoneNumber
            _fix_hr_zone_steps(workout_data)
            _validate_strength_workout(workout_data)

            # Pass dict directly - library handles conversion
            result = garmin_client.upload_workout(workout_data)

            # Curate the response
            if isinstance(result, dict):
                curated = {
                    "status": "success",
                    "workout_id": result.get('workoutId'),
                    "name": result.get('workoutName'),
                    "message": "Workout uploaded successfully"
                }
                # Remove None values
                curated = {k: v for k, v in curated.items() if v is not None}
                return json.dumps(curated, indent=2)

            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error uploading workout: {str(e)}"

    @app.tool()
    async def delete_workout(workout_id: int) -> str:
        """Delete a workout from Garmin Connect

        Permanently removes a workout from your Garmin Connect workout library.

        Args:
            workout_id: ID of the workout to delete (get IDs from get_workouts)
        """
        try:
            url = f"{garmin_client.garmin_workouts}/workout/{workout_id}"
            response = garmin_client.garth.delete("connectapi", url, api=True)

            if response.status_code == 204 or response.status_code == 200:
                return json.dumps({
                    "status": "success",
                    "workout_id": workout_id,
                    "message": f"Workout {workout_id} deleted successfully"
                }, indent=2)
            else:
                return json.dumps({
                    "status": "failed",
                    "workout_id": workout_id,
                    "http_status": response.status_code,
                    "message": f"Failed to delete workout: HTTP {response.status_code}"
                }, indent=2)
        except Exception as e:
            return f"Error deleting workout: {str(e)}"

    @app.tool()
    async def get_scheduled_workouts(start_date: str, end_date: str) -> str:
        """Get scheduled workouts between two dates with curated summary list

        Returns workouts that have been scheduled on the Garmin Connect calendar,
        including their scheduled dates and completion status.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            # Query for scheduled workouts using GraphQL
            query = {
                "query": f'query{{workoutScheduleSummariesScalar(startDate:"{start_date}", endDate:"{end_date}")}}'
            }
            result = garmin_client.query_garmin_graphql(query)

            if not result or "data" not in result:
                return "No scheduled workouts found or error querying data."

            scheduled = result.get("data", {}).get("workoutScheduleSummariesScalar", [])

            if not scheduled:
                return f"No workouts scheduled between {start_date} and {end_date}."

            # Curate the scheduled workout list
            curated = {
                "count": len(scheduled),
                "date_range": {"start": start_date, "end": end_date},
                "scheduled_workouts": [_curate_scheduled_workout(s) for s in scheduled]
            }

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving scheduled workouts: {str(e)}"

    @app.tool()
    async def get_training_plan_workouts(calendar_date: str) -> str:
        """Get training plan workouts for the week containing the given date

        Returns workouts from your active training plan for the week containing
        the specified date. The API returns approximately 7 days of scheduled
        workouts anchored around the given date.

        Training plan workouts have workout_uuid (not workout_id). Use the
        workout_uuid with get_workout_by_id to get detailed step information.

        Args:
            calendar_date: Reference date in YYYY-MM-DD format (returns week's workouts)
        """
        try:
            # Query for training plan workouts using GraphQL
            query = {
                "query": f'query{{trainingPlanScalar(calendarDate:"{calendar_date}", lang:"en-US", firstDayOfWeek:"monday")}}'
            }
            result = garmin_client.query_garmin_graphql(query)

            if not result or "data" not in result:
                return "No training plan data found or error querying data."

            plan_data = result.get("data", {}).get("trainingPlanScalar", {})
            training_plans = plan_data.get("trainingPlanWorkoutScheduleDTOS", [])

            if not training_plans:
                return f"No training plan workouts scheduled for {calendar_date}."

            # Collect all workouts from all training plans
            all_workouts = []
            plan_names = []

            for plan in training_plans:
                plan_name = plan.get('planName')
                if plan_name and plan_name not in plan_names:
                    plan_names.append(plan_name)

                # workoutScheduleSummaries has same structure as scheduled workouts
                workout_summaries = plan.get('workoutScheduleSummaries', [])
                for workout in workout_summaries:
                    # Reuse the scheduled workout curation since structure is identical
                    all_workouts.append(_curate_scheduled_workout(workout))

            # Curate training plan data
            curated = {
                "date": calendar_date,
                "training_plans": plan_names if plan_names else None,
                "count": len(all_workouts),
                "workouts": all_workouts
            }

            # Remove None values from top level
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving training plan workouts: {str(e)}"

    @app.tool()
    async def schedule_workout(workout_id: int, calendar_date: str) -> str:
        """Schedule a workout to a specific calendar date

        This adds an existing workout from your Garmin workout library
        to your Garmin Connect calendar on the specified date.

        Args:
            workout_id: ID of the workout to schedule (get IDs from get_workouts)
            calendar_date: Date to schedule the workout in YYYY-MM-DD format
        """
        try:
            url = f"workout-service/schedule/{workout_id}"
            response = garmin_client.garth.post("connectapi", url, json={"date": calendar_date})

            if response.status_code == 200:
                return json.dumps({
                    "status": "success",
                    "workout_id": workout_id,
                    "scheduled_date": calendar_date,
                    "message": f"Successfully scheduled workout {workout_id} for {calendar_date}"
                }, indent=2)
            else:
                return json.dumps({
                    "status": "failed",
                    "workout_id": workout_id,
                    "scheduled_date": calendar_date,
                    "http_status": response.status_code,
                    "message": f"Failed to schedule workout: HTTP {response.status_code}"
                }, indent=2)
        except Exception as e:
            return f"Error scheduling workout: {str(e)}"

    @app.tool()
    async def unschedule_workout(scheduled_workout_id: int) -> str:
        """Remove a scheduled workout from the Garmin calendar.

        This only removes the calendar instance. It does not delete the workout
        template from your workout library.

        Args:
            scheduled_workout_id: Scheduled workout ID from get_scheduled_workouts
        """
        try:
            url = f"workout-service/schedule/{scheduled_workout_id}"
            response = garmin_client.garth.delete("connectapi", url, api=True)

            if response.status_code == 204 or response.status_code == 200:
                return json.dumps({
                    "status": "success",
                    "scheduled_workout_id": scheduled_workout_id,
                    "message": f"Successfully unscheduled workout {scheduled_workout_id}"
                }, indent=2)
            else:
                return json.dumps({
                    "status": "failed",
                    "scheduled_workout_id": scheduled_workout_id,
                    "http_status": response.status_code,
                    "message": f"Failed to unschedule workout: HTTP {response.status_code}"
                }, indent=2)
        except Exception as e:
            return f"Error unscheduling workout: {str(e)}"

    @app.tool()
    async def unschedule_workout_on_date(
        workout_id: int,
        calendar_date: str,
    ) -> str:
        """Unschedule a workout template occurrence for a specific date.

        This resolves the Garmin scheduled workout instance for the given date
        and then removes it from the calendar.

        Args:
            workout_id: Workout library ID
            calendar_date: Date in YYYY-MM-DD format
        """
        try:
            query = {
                "query": f'query{{workoutScheduleSummariesScalar(startDate:"{calendar_date}", endDate:"{calendar_date}")}}'
            }
            result = garmin_client.query_garmin_graphql(query)
            if not result or "data" not in result:
                return "No scheduled workouts found or error querying data."

            scheduled = result.get("data", {}).get("workoutScheduleSummariesScalar", [])
            match = next(
                (
                    item for item in scheduled
                    if item.get("workoutId") == workout_id and item.get("scheduleDate") == calendar_date
                ),
                None,
            )

            if not match:
                return json.dumps({
                    "status": "not_found",
                    "workout_id": workout_id,
                    "scheduled_date": calendar_date,
                    "message": "No scheduled workout matched the given workout_id and date"
                }, indent=2)

            scheduled_workout_id = match.get("scheduledWorkoutId")
            if not scheduled_workout_id:
                return json.dumps({
                    "status": "failed",
                    "workout_id": workout_id,
                    "scheduled_date": calendar_date,
                    "message": "Matched workout is missing scheduledWorkoutId"
                }, indent=2)

            url = f"workout-service/schedule/{scheduled_workout_id}"
            response = garmin_client.garth.delete("connectapi", url, api=True)

            if response.status_code == 204 or response.status_code == 200:
                return json.dumps({
                    "status": "success",
                    "workout_id": workout_id,
                    "scheduled_workout_id": scheduled_workout_id,
                    "scheduled_date": calendar_date,
                    "message": f"Successfully unscheduled workout {workout_id} on {calendar_date}"
                }, indent=2)

            return json.dumps({
                "status": "failed",
                "workout_id": workout_id,
                "scheduled_workout_id": scheduled_workout_id,
                "scheduled_date": calendar_date,
                "http_status": response.status_code,
                "message": f"Failed to unschedule workout: HTTP {response.status_code}"
            }, indent=2)
        except Exception as e:
            return f"Error unscheduling workout on date: {str(e)}"

    @app.tool()
    async def schedule_workout_recurring(
        workout_id: int,
        start_date: str,
        end_date: str,
        weekdays: list[str],
        interval_weeks: int = 1,
        exclude_dates: Optional[list[str]] = None,
        dry_run: bool = False,
    ) -> str:
        """Schedule a workout repeatedly across a date range.

        Generates matching dates between start_date and end_date for the given weekdays.
        When dry_run is true, returns the dates without scheduling them.

        Args:
            workout_id: Workout ID from get_workouts
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            weekdays: Weekdays such as ["monday", "thursday"] or ["lunes", "jueves"]
            interval_weeks: Schedule every N weeks (default 1)
            exclude_dates: Optional list of YYYY-MM-DD dates to skip
            dry_run: If true, do not call Garmin; just return the generated dates
        """
        try:
            dates = _generate_recurring_dates(
                start_date=start_date,
                end_date=end_date,
                weekdays=weekdays,
                interval_weeks=interval_weeks,
                exclude_dates=exclude_dates,
            )

            if dry_run:
                return json.dumps(
                    {
                        "status": "dry_run",
                        "workout_id": workout_id,
                        "count": len(dates),
                        "dates": dates,
                    },
                    indent=2,
                )

            scheduled = []
            for calendar_date in dates:
                url = f"workout-service/schedule/{workout_id}"
                response = garmin_client.garth.post(
                    "connectapi",
                    url,
                    json={"date": calendar_date},
                )
                scheduled.append(
                    {
                        "date": calendar_date,
                        "http_status": response.status_code,
                        "scheduled": response.status_code == 200,
                    }
                )

            failures = [entry for entry in scheduled if not entry["scheduled"]]
            status = "success" if not failures else "partial_failure"
            return json.dumps(
                {
                    "status": status,
                    "workout_id": workout_id,
                    "count": len(scheduled),
                    "scheduled_dates": scheduled,
                    "failed_count": len(failures),
                },
                indent=2,
            )
        except Exception as e:
            return f"Error scheduling recurring workout: {str(e)}"

    return app
