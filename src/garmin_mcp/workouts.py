"""
Workout-related functions for Garmin Connect MCP Server
"""
import json
import datetime
from typing import Any, Dict, List, Optional, Union

# The garmin_client will be set by the main file
garmin_client = None


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


def _fix_hr_zone_step(step: dict) -> None:
    """Fix a common mistake where HR zone targets use targetValueOne instead of zoneNumber.

    When targetType is heart.rate.zone, Garmin expects zoneNumber (1-5).
    If targetValueOne is set to a small integer (1-5) and zoneNumber is missing,
    this is almost certainly a zone number, not an absolute HR value.
    """
    target_type = step.get('targetType', {})
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
    step_type = step.get('stepType', {})
    end_condition = step.get('endCondition', {})
    target_type = step.get('targetType', {})

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
    async def upload_workout(workout_data: dict) -> str:
        """Upload a workout from JSON data

        Creates a new workout in Garmin Connect from structured workout data.

        IMPORTANT: Step types must use Garmin's DTO format:
        - Use "ExecutableStepDTO" for regular steps (warmup, interval, cooldown, recovery)
        - Use "RepeatGroupDTO" for repeat/interval groups with numberOfIterations

        IMPORTANT: For heart rate zone targets, use "zoneNumber" (1-5), NOT targetValueOne/targetValueTwo.
        targetValueOne/targetValueTwo are only for absolute value ranges (e.g. pace in m/s, power in watts).

        IMPORTANT: Sport type IDs for workouts (different from activity API!):
        - 1 = running, 2 = cycling, 5 = strength_training, 6 = cardio, 11 = walking

        **Available Templates:**
        Instead of building workout JSON from scratch, you can use these MCP resources as starting points:
        - workout://templates/simple-run - Basic warmup/run/cooldown structure
        - workout://templates/interval-running - Interval training with repeat groups
        - workout://templates/tempo-run - Tempo run with heart rate zone targets
        - workout://templates/strength-circuit - Strength training with exercises, reps, rest
        - workout://reference/structure - Complete JSON structure reference with all fields

        Access these resources using your MCP client's resource reading capability, modify the template
        as needed, and pass the resulting JSON as the workout_data parameter.

        **Strength training workouts** require these additional fields on each exercise step:
        - "category": exercise category (e.g. "BENCH_PRESS", "PULL_UP", "CURL", "SHOULDER_PRESS",
          "ROW", "SQUAT", "DEADLIFT", "TRICEPS_EXTENSION", "PLANK", "LUNGE", "CARDIO")
        - "exerciseName": specific exercise (e.g. "BARBELL_BENCH_PRESS", "PULL_UP",
          "DUMBBELL_BICEPS_CURL", "DUMBBELL_SHOULDER_PRESS", "BENT_OVER_ROW_WITH_DUMBELL",
          "BODY_WEIGHT_DIP", "BARBELL_SQUAT", "BARBELL_DEADLIFT")
        - "weightValue" (optional): weight as number (e.g. 24.0)
        - "weightUnit" (optional): {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}
        Use endCondition reps (conditionTypeId: 10) for exercises, rest (stepTypeId: 5) between sets.

        Example strength exercise step:
        {
            "type": "ExecutableStepDTO",
            "stepOrder": 1,
            "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
            "endCondition": {"conditionTypeId": 10, "conditionTypeKey": "reps"},
            "endConditionValue": 10.0,
            "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
            "category": "BENCH_PRESS",
            "exerciseName": "BARBELL_BENCH_PRESS",
            "weightValue": 60.0,
            "weightUnit": {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}
        }

        Example running workout with HR zone target:
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

    return app
