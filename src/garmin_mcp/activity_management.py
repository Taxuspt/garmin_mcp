"""
Activity Management functions for Garmin Connect MCP Server
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


def _put_activity_update(activity_id: int, payload: Dict[str, Any]) -> Any:
    """Send a partial activity update via PUT to the activity-service endpoint.

    Garmin's activity endpoint accepts a partial ActivityDTO keyed by
    activityId, so callers only include the top-level fields they want to
    change. This mirrors how the library's set_activity_name works.
    """
    url = f"{garmin_client.garmin_connect_activity}/{activity_id}"
    body = {"activityId": activity_id, **payload}
    return garmin_client.client.put("connectapi", url, json=body, api=True)


def _update_activity_summary(activity_id: int, fields: Dict[str, Any]) -> Any:
    """Update fields nested in an activity's summaryDTO.

    Garmin merges a partial summaryDTO into the stored summary, so we send only
    the fields being changed and the other recorded metrics are preserved.

    We deliberately avoid a read-modify-write. The full summaryDTO returned by
    get_activity contains a complete GPS coordinate pair (startLatitude and
    startLongitude); PUTting both halves of a coordinate together is rejected by
    the endpoint with a 400. Each field is individually writable, so it is the
    coordinate pair specifically that trips validation, and a minimal update
    sidesteps it.
    """
    return _put_activity_update(activity_id, {"summaryDTO": fields})


def register_tools(app):
    """Register all activity management tools with the MCP server app"""

    @app.tool()
    async def get_activities_by_date(
        start_date: str,
        end_date: str,
        activity_type: str = "",
        page: int = 0,
        page_size: int = 100,
    ) -> str:
        """Get activities between specified dates with pagination support.

        For accounts with large activity histories, broad date ranges can return
        thousands of activities in a single response. Use page and page_size to
        retrieve activities in manageable chunks and avoid "result too large" errors.
        Activities are ordered newest-first.

        Pagination: when has_more is true the response includes next_page — pass
        that value as page on the next call to retrieve the following page. Repeat
        until has_more is false.

        Note: total_count for a date range is not available from the Garmin API
        without fetching all results. Use has_more / next_page to walk pages.

        Each activity includes an event_type field with values such as:
          - "race"          — explicitly tagged as a race by the user
          - "training"      — explicitly tagged as a training activity
          - "uncategorized" — no event type set; common for Peloton imports and
                              untagged outdoor runs. Distinct from "training":
                              filter for races with event_type == "race" rather
                              than excluding "training", since many non-race
                              activities appear as "uncategorized" not "training"
          - field omitted   — activity pre-dates event type support in the API

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            activity_type: Optional activity type filter (e.g., cycling, running, swimming)
            page: Zero-based page number (default 0)
            page_size: Number of activities per page, max 200 (default 100)
        """
        try:
            # Clamp page_size to [1, 200]
            page_size = min(max(1, page_size), 200)
            start = page * page_size

            # Call the Garmin API directly with explicit pagination params.
            # The library's get_activities_by_date auto-fetches ALL matching
            # activities in a loop (hardcoded limit=20 per request), which causes
            # "Tool result is too large" errors on accounts with large histories.
            # Calling connectapi directly lets us fetch exactly one page at a time.
            params: Dict[str, Any] = {
                "startDate": start_date,
                "endDate": end_date,
                "start": str(start),
                "limit": str(page_size),
            }
            if activity_type:
                params["activityType"] = activity_type

            activities = garmin_client.connectapi(
                garmin_client.garmin_connect_activities,
                params=params,
            )

            if not activities:
                return json.dumps({
                    "count": 0,
                    "page": page,
                    "page_size": page_size,
                    "has_more": False,
                    "date_range": {"start": start_date, "end": end_date},
                    "activities": [],
                }, indent=2)

            has_more = len(activities) == page_size
            curated: Dict[str, Any] = {
                "count": len(activities),
                "page": page,
                "page_size": page_size,
                "has_more": has_more,
                "date_range": {"start": start_date, "end": end_date},
                "activities": [],
            }
            if has_more:
                curated["next_page"] = page + 1

            for a in activities:
                activity = {
                    "id": a.get('activityId'),
                    "name": a.get('activityName'),
                    "type": a.get('activityType', {}).get('typeKey'),
                    "event_type": (a.get('eventType') or {}).get('typeKey'),
                    "start_time": a.get('startTimeLocal'),
                    "distance_meters": a.get('distance'),
                    "duration_seconds": a.get('duration'),
                    "calories": a.get('calories'),
                    "avg_hr_bpm": a.get('averageHR'),
                    "max_hr_bpm": a.get('maxHR'),
                    "steps": a.get('steps'),
                    "elevation_gain_meters": a.get('elevationGain'),
                    "elevation_loss_meters": a.get('elevationLoss'),
                }
                # Remove None values
                activity = {k: v for k, v in activity.items() if v is not None}
                curated["activities"].append(activity)

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving activities by date: {str(e)}"

    @app.tool()
    async def get_activities_fordate(date: str) -> str:
        """Get activities for a specific date

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            data = garmin_client.get_activities_fordate(date)
            if not data:
                return f"No activities found for {date}"

            # Extract just the activities, not the embedded HR data
            activities_data = data.get('ActivitiesForDay', {})
            payload = activities_data.get('payload', [])

            if not payload:
                return f"No activities found for {date}"

            curated = {
                "date": date,
                "count": len(payload),
                "activities": []
            }

            for a in payload:
                activity = {
                    "id": a.get('activityId'),
                    "name": a.get('activityName'),
                    "type": a.get('activityType', {}).get('typeKey'),
                    "event_type": (a.get('eventType') or {}).get('typeKey'),
                    "start_time": a.get('startTimeLocal'),
                    "distance_meters": a.get('distance'),
                    "duration_seconds": a.get('duration'),
                    "calories": a.get('calories'),
                    "avg_hr_bpm": a.get('averageHR'),
                    "steps": a.get('steps'),
                    "lap_count": a.get('lapCount'),
                    "moderate_intensity_minutes": a.get('moderateIntensityMinutes'),
                    "vigorous_intensity_minutes": a.get('vigorousIntensityMinutes'),
                }
                # Remove None values
                activity = {k: v for k, v in activity.items() if v is not None}
                curated["activities"].append(activity)

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving activities for date: {str(e)}"

    @app.tool()
    async def get_activity(activity_id: Union[int, str]) -> str:
        """Get detailed information for a single activity.

        Returns a comprehensive summary including timing, distance, heart rate,
        elevation, training effect, and an event_type field. Common event_type
        values: "race", "training", "uncategorized" (no event type set by the
        user). The field is omitted for very old activities that pre-date event
        type support in the Garmin API.

        Args:
            activity_id: ID of the activity to retrieve
        """
        try:
            activity_id = int(activity_id)
            activity = garmin_client.get_activity(activity_id)
            if not activity:
                return f"No activity found with ID {activity_id}"

            # Extract summary data
            summary = activity.get('summaryDTO', {})
            activity_type = activity.get('activityTypeDTO', {})
            metadata = activity.get('metadataDTO', {})

            curated = {
                "id": activity.get('activityId'),
                "name": activity.get('activityName'),
                "type": activity_type.get('typeKey'),
                "event_type": (activity.get('eventType') or {}).get('typeKey'),
                "parent_type": activity_type.get('parentTypeId'),

                # Timing
                "start_time_local": summary.get('startTimeLocal'),
                "start_time_gmt": summary.get('startTimeGMT'),
                "duration_seconds": summary.get('duration'),
                "moving_duration_seconds": summary.get('movingDuration'),
                "elapsed_duration_seconds": summary.get('elapsedDuration'),

                # Distance and speed
                "distance_meters": summary.get('distance'),
                "avg_speed_mps": summary.get('averageSpeed'),
                "max_speed_mps": summary.get('maxSpeed'),

                # Heart rate
                "avg_hr_bpm": summary.get('averageHR'),
                "max_hr_bpm": summary.get('maxHR'),
                "min_hr_bpm": summary.get('minHR'),

                # Calories
                "calories": summary.get('calories'),
                "bmr_calories": summary.get('bmrCalories'),

                # Running metrics
                "avg_cadence": summary.get('averageRunCadence'),
                "max_cadence": summary.get('maxRunCadence'),
                "avg_stride_length_cm": summary.get('strideLength'),
                "avg_ground_contact_time_ms": summary.get('groundContactTime'),
                "avg_vertical_oscillation_cm": summary.get('verticalOscillation'),
                "steps": summary.get('steps'),

                # Power
                "avg_power_watts": summary.get('averagePower'),
                "max_power_watts": summary.get('maxPower'),
                "normalized_power_watts": summary.get('normalizedPower'),

                # Training effect
                "training_effect": summary.get('trainingEffect'),
                "anaerobic_training_effect": summary.get('anaerobicTrainingEffect'),
                "training_effect_label": summary.get('trainingEffectLabel'),
                "training_load": summary.get('activityTrainingLoad'),

                # Intensity minutes
                "moderate_intensity_minutes": summary.get('moderateIntensityMinutes'),
                "vigorous_intensity_minutes": summary.get('vigorousIntensityMinutes'),

                # Elevation
                "elevation_gain_meters": summary.get('elevationGain'),
                "elevation_loss_meters": summary.get('elevationLoss'),
                "max_elevation_meters": summary.get('maxElevation'),
                "min_elevation_meters": summary.get('minElevation'),

                # Recovery
                "recovery_hr_bpm": summary.get('recoveryHeartRate'),
                "body_battery_impact": summary.get('differenceBodyBattery'),

                # Workout feedback
                "workout_feel": summary.get('directWorkoutFeel'),
                "workout_rpe": summary.get('directWorkoutRpe'),

                # Metadata
                "lap_count": metadata.get('lapCount'),
                "has_splits": metadata.get('hasSplits'),
                "device_manufacturer": metadata.get('manufacturer'),
            }

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving activity: {str(e)}"

    @app.tool()
    async def set_activity_name(activity_id: Union[int, str], activity_name: str) -> str:
        """Set or update the name of an activity.

        Args:
            activity_id: ID of the activity to update
            activity_name: New activity name
        """
        try:
            activity_id = int(activity_id)
            activity_name = activity_name.strip()
            if not activity_name:
                return "Activity name cannot be empty"

            garmin_client.set_activity_name(activity_id, activity_name)

            return json.dumps(
                {
                    "success": True,
                    "activity_id": activity_id,
                    "activity_name": activity_name,
                    "message": "Activity name successfully updated",
                },
                indent=2,
            )
        except Exception as e:
            return f"Error updating activity name: {str(e)}"

    @app.tool()
    async def set_activity_type(activity_id: Union[int, str], type_key: str) -> str:
        """Change the activity type (sport) of an activity.

        Useful for reclassifying a mislabelled activity, e.g. flipping a run
        logged as 'trail_running' to 'running', or a 'treadmill_running' walk to
        'treadmill_walking'. Call get_activity_types to see all valid type keys.

        Args:
            activity_id: ID of the activity to update
            type_key: Target activity type key (e.g. 'running', 'trail_running',
                'treadmill_running', 'cycling', 'lap_swimming')
        """
        try:
            activity_id = int(activity_id)
            type_key = type_key.strip()

            types = garmin_client.get_activity_types() or []
            match = next((t for t in types if t.get("typeKey") == type_key), None)
            if not match:
                valid = ", ".join(
                    sorted(t.get("typeKey") for t in types if t.get("typeKey"))
                )
                return f"Unknown activity type '{type_key}'. Valid type keys: {valid}"

            garmin_client.set_activity_type(
                activity_id,
                match["typeId"],
                match["typeKey"],
                match.get("parentTypeId"),
            )

            return json.dumps(
                {
                    "success": True,
                    "activity_id": activity_id,
                    "type_key": match["typeKey"],
                    "type_id": match["typeId"],
                    "message": "Activity type successfully updated",
                },
                indent=2,
            )
        except Exception as e:
            return f"Error updating activity type: {str(e)}"

    @app.tool()
    async def set_activity_description(
        activity_id: Union[int, str], description: str
    ) -> str:
        """Set or update the free-text description (notes) of an activity.

        This is the notes field shown on the activity page — useful for
        recording how a session felt, kit used, conditions, niggles, etc.
        Pass an empty string to clear an existing description.

        Args:
            activity_id: ID of the activity to update
            description: New description text (empty string clears it)
        """
        try:
            activity_id = int(activity_id)
            _put_activity_update(activity_id, {"description": description})

            return json.dumps(
                {
                    "success": True,
                    "activity_id": activity_id,
                    "description": description,
                    "message": "Activity description successfully updated",
                },
                indent=2,
            )
        except Exception as e:
            return f"Error updating activity description: {str(e)}"

    @app.tool()
    async def set_activity_event_type(
        activity_id: Union[int, str], event_type: str
    ) -> str:
        """Set the event type of an activity.

        Event type categorises the activity's purpose. Valid keys:
        race, recreation, specialEvent, training, transportation, touring,
        geocaching, fitness, uncategorized.

        Args:
            activity_id: ID of the activity to update
            event_type: Target event type key (e.g. 'race', 'training')
        """
        try:
            activity_id = int(activity_id)
            event_type = event_type.strip()

            event_types = (
                garmin_client.connectapi("/activity-service/activity/eventTypes") or []
            )
            match = next(
                (e for e in event_types if e.get("typeKey") == event_type), None
            )
            if not match:
                valid = ", ".join(e.get("typeKey") for e in event_types if e.get("typeKey"))
                return f"Unknown event type '{event_type}'. Valid event types: {valid}"

            _put_activity_update(
                activity_id,
                {
                    "eventTypeDTO": {
                        "typeId": match["typeId"],
                        "typeKey": match["typeKey"],
                        "sortOrder": match.get("sortOrder"),
                    }
                },
            )

            return json.dumps(
                {
                    "success": True,
                    "activity_id": activity_id,
                    "event_type": match["typeKey"],
                    "message": "Activity event type successfully updated",
                },
                indent=2,
            )
        except Exception as e:
            return f"Error updating activity event type: {str(e)}"

    @app.tool()
    async def set_perceived_effort(
        activity_id: Union[int, str], rpe: float
    ) -> str:
        """Set the perceived effort (RPE) for an activity.

        Mirrors Garmin Connect's 'Perceived Effort' rating on a 0-10 scale,
        where 0 clears the rating. Internally Garmin stores this multiplied by
        10 (so RPE 7 is stored as 70); this tool handles the conversion.

        Args:
            activity_id: ID of the activity to update
            rpe: Perceived effort from 0 to 10 (0 clears the rating)
        """
        try:
            activity_id = int(activity_id)
            rpe = float(rpe)
            if not 0 <= rpe <= 10:
                return "rpe must be between 0 and 10"

            _update_activity_summary(
                activity_id, {"directWorkoutRpe": int(round(rpe * 10))}
            )

            return json.dumps(
                {
                    "success": True,
                    "activity_id": activity_id,
                    "rpe": rpe,
                    "message": "Perceived effort successfully updated",
                },
                indent=2,
            )
        except Exception as e:
            return f"Error updating perceived effort: {str(e)}"

    @app.tool()
    async def set_activity_feel(activity_id: Union[int, str], feel: int) -> str:
        """Set how an activity felt ('How did you feel?').

        Mirrors Garmin Connect's 5-point feel rating, stored as one of:
          0   = very tired / poor
          25  = tired
          50  = normal
          75  = good
          100 = strong
        Higher is better.

        Args:
            activity_id: ID of the activity to update
            feel: One of 0, 25, 50, 75, 100
        """
        try:
            activity_id = int(activity_id)
            feel = int(feel)
            if feel not in (0, 25, 50, 75, 100):
                return "feel must be one of 0, 25, 50, 75, 100"

            _update_activity_summary(activity_id, {"directWorkoutFeel": feel})

            return json.dumps(
                {
                    "success": True,
                    "activity_id": activity_id,
                    "feel": feel,
                    "message": "Activity feel successfully updated",
                },
                indent=2,
            )
        except Exception as e:
            return f"Error updating activity feel: {str(e)}"

    @app.tool()
    async def get_activity_splits(activity_id: Union[int, str]) -> str:
        """Get splits for an activity

        Args:
            activity_id: ID of the activity to retrieve splits for
        """
        try:
            activity_id = int(activity_id)
            splits = garmin_client.get_activity_splits(activity_id)
            if not splits:
                return f"No splits found for activity with ID {activity_id}"

            # Curate the splits data
            laps = splits.get('lapDTOs', [])

            curated = {
                "activity_id": splits.get('activityId'),
                "lap_count": len(laps),
                "laps": []
            }

            for lap in laps:
                lap_data = {
                    "lap_number": lap.get('lapIndex'),
                    "start_time": lap.get('startTimeGMT'),
                    "distance_meters": lap.get('distance'),
                    "duration_seconds": lap.get('duration'),
                    "moving_duration_seconds": lap.get('movingDuration'),
                    "elapsed_duration_seconds": lap.get('elapsedDuration'),
                    "avg_speed_mps": lap.get('averageSpeed'),
                    "avg_moving_speed_mps": lap.get('averageMovingSpeed'),
                    "max_speed_mps": lap.get('maxSpeed'),
                    "avg_hr_bpm": lap.get('averageHR'),
                    "max_hr_bpm": lap.get('maxHR'),
                    "calories": lap.get('calories'),
                    "bmr_calories": lap.get('bmrCalories'),
                    "avg_cadence": lap.get('averageRunCadence'),
                    "avg_power_watts": lap.get('averagePower'),
                    "avg_swim_cadence": lap.get('averageSwimCadence'),
                    "active_length_count": lap.get('numberOfActiveLengths'),
                    "total_strokes": lap.get('totalNumberOfStrokes'),
                    "avg_strokes": lap.get('averageStrokes'),
                    "avg_swolf": lap.get('averageSWOLF'),
                    "avg_stroke_distance": lap.get('averageStrokeDistance'),
                    "intensity_type": lap.get('intensityType'),
                    "elevation_gain_meters": lap.get('elevationGain'),
                    "elevation_loss_meters": lap.get('elevationLoss'),
                    "workout_step_index": lap.get('wktStepIndex'),
                }

                length_dtos = lap.get('lengthDTOs', [])
                if length_dtos:
                    lap_data["lengths"] = []
                    for length in length_dtos:
                        length_data = {
                            "length_number": length.get('lengthIndex'),
                            "start_time": length.get('startTimeGMT'),
                            "distance_meters": length.get('distance'),
                            "duration_seconds": length.get('duration'),
                            "avg_speed_mps": length.get('averageSpeed'),
                            "max_speed_mps": length.get('maxSpeed'),
                            "calories": length.get('calories'),
                            "avg_hr_bpm": length.get('averageHR'),
                            "max_hr_bpm": length.get('maxHR'),
                            "total_strokes": length.get('totalNumberOfStrokes'),
                            "avg_swolf": length.get('averageSWOLF'),
                            "stroke": length.get('swimStroke'),
                        }
                        length_data = {
                            k: v for k, v in length_data.items() if v is not None
                        }
                        lap_data["lengths"].append(length_data)

                # Remove None values
                lap_data = {k: v for k, v in lap_data.items() if v is not None}
                curated["laps"].append(lap_data)

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving activity splits: {str(e)}"

    @app.tool()
    async def get_activity_typed_splits(activity_id: Union[int, str]) -> str:
        """Get typed splits for an activity

        Args:
            activity_id: ID of the activity to retrieve typed splits for
        """
        try:
            activity_id = int(activity_id)
            typed_splits = garmin_client.get_activity_typed_splits(activity_id)
            if not typed_splits:
                return f"No typed splits found for activity with ID {activity_id}"

            return json.dumps(typed_splits, indent=2)
        except Exception as e:
            return f"Error retrieving activity typed splits: {str(e)}"

    @app.tool()
    async def get_activity_split_summaries(activity_id: Union[int, str]) -> str:
        """Get split summaries for an activity

        Args:
            activity_id: ID of the activity to retrieve split summaries for
        """
        try:
            activity_id = int(activity_id)
            split_summaries = garmin_client.get_activity_split_summaries(activity_id)
            if not split_summaries:
                return f"No split summaries found for activity with ID {activity_id}"

            return json.dumps(split_summaries, indent=2)
        except Exception as e:
            return f"Error retrieving activity split summaries: {str(e)}"

    @app.tool()
    async def get_activity_weather(activity_id: Union[int, str]) -> str:
        """Get weather data for an activity

        Args:
            activity_id: ID of the activity to retrieve weather data for
        """
        try:
            activity_id = int(activity_id)
            weather = garmin_client.get_activity_weather(activity_id)
            if not weather:
                return f"No weather data found for activity with ID {activity_id}"

            # Curate weather data
            curated = {
                "activity_id": activity_id,
                "temperature_celsius": weather.get('temp'),
                "apparent_temperature_celsius": weather.get('apparentTemp'),
                "humidity_percent": weather.get('relativeHumidity'),
                "wind_speed_mps": weather.get('windSpeed'),
                "wind_direction_degrees": weather.get('windDirection'),
                "weather_type": weather.get('weatherTypeDTO', {}).get('weatherTypeName'),
                "weather_description": weather.get('weatherTypeDTO', {}).get('weatherTypeDesc'),
                "location": weather.get('issueLocation'),
                "issue_time": weather.get('issueDate'),
            }

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving activity weather data: {str(e)}"

    @app.tool()
    async def get_activity_hr_in_timezones(activity_id: Union[int, str]) -> str:
        """Get heart rate data in different time zones for an activity

        Args:
            activity_id: ID of the activity to retrieve heart rate time zone data for
        """
        try:
            activity_id = int(activity_id)
            hr_zones = garmin_client.get_activity_hr_in_timezones(activity_id)
            if not hr_zones:
                return f"No heart rate time zone data found for activity with ID {activity_id}"

            return json.dumps(hr_zones, indent=2)
        except Exception as e:
            return f"Error retrieving activity heart rate time zone data: {str(e)}"

    @app.tool()
    async def get_activity_power_in_timezones(activity_id: Union[int, str]) -> str:
        """Get power distribution across training zones for an activity.

        Returns time spent in each power zone with watt thresholds and duration.
        Requires a power meter. Zones are based on the athlete's FTP configured in Garmin Connect.

        Args:
            activity_id: ID of the activity to retrieve power zone data for
        """
        try:
            activity_id = int(activity_id)
            power_zones = garmin_client.get_activity_power_in_timezones(activity_id)
            if not power_zones:
                return f"No power zone data found for activity {activity_id}. Ensure the activity was recorded with a power meter."

            return json.dumps(power_zones, indent=2)
        except Exception as e:
            return f"Error retrieving activity power zone data: {str(e)}"

    @app.tool()
    async def get_activity_gear(activity_id: Union[int, str]) -> str:
        """Get gear data used for an activity

        Args:
            activity_id: ID of the activity to retrieve gear data for
        """
        try:
            activity_id = int(activity_id)
            gear = garmin_client.get_activity_gear(activity_id)
            if not gear:
                return f"No gear data found for activity with ID {activity_id}"

            return json.dumps(gear, indent=2)
        except Exception as e:
            return f"Error retrieving activity gear data: {str(e)}"

    @app.tool()
    async def get_activity_exercise_sets(activity_id: Union[int, str]) -> str:
        """Get exercise sets for strength training activities

        Args:
            activity_id: ID of the activity to retrieve exercise sets for
        """
        try:
            activity_id = int(activity_id)
            exercise_sets = garmin_client.get_activity_exercise_sets(activity_id)
            if not exercise_sets:
                return f"No exercise sets found for activity with ID {activity_id}"

            return json.dumps(exercise_sets, indent=2)
        except Exception as e:
            return f"Error retrieving activity exercise sets: {str(e)}"

    @app.tool()
    async def count_activities() -> str:
        """Get total count of activities in the user's Garmin account

        Returns the total number of activities recorded.
        """
        try:
            count = garmin_client.count_activities()
            if count is None:
                return "Unable to retrieve activity count"

            return json.dumps({
                "total_activities": count,
                "note": "Use get_activities() with pagination to retrieve activity details"
            }, indent=2)
        except Exception as e:
            return f"Error counting activities: {str(e)}"

    @app.tool()
    async def get_activities(start: int = 0, limit: int = 20) -> str:
        """Get activities with pagination support.

        Retrieves a paginated list of activities ordered newest-first. Use this
        for browsing through large activity lists when you do not need to filter
        by date range, or as a complement to get_activities_by_date.

        Each activity includes an event_type field. Common values: "race",
        "training", "uncategorized" (no event type set by the user — common for
        Peloton imports and untagged runs). Filter for races with
        event_type == "race" rather than excluding "training", as many non-race
        activities appear as "uncategorized" rather than "training".

        Args:
            start: Starting index (default 0)
            limit: Maximum number of activities to return (default 20, max 100)
        """
        try:
            # Cap limit at 100 for safety and performance
            limit = min(max(1, limit), 100)

            activities = garmin_client.get_activities(start, limit)
            if not activities:
                return f"No activities found at index {start}"

            # Curate the activity list
            curated = {
                "start": start,
                "limit": limit,
                "count": len(activities),
                "has_more": len(activities) == limit,
                "next_start": start + limit if len(activities) == limit else None,
                "activities": []
            }

            for a in activities:
                activity = {
                    "id": a.get('activityId'),
                    "name": a.get('activityName'),
                    "type": a.get('activityType', {}).get('typeKey'),
                    "event_type": (a.get('eventType') or {}).get('typeKey'),
                    "start_time": a.get('startTimeLocal'),
                    "distance_meters": a.get('distance'),
                    "duration_seconds": a.get('duration'),
                    "moving_duration_seconds": a.get('movingDuration'),
                    "calories": a.get('calories'),
                    "avg_hr_bpm": a.get('averageHR'),
                    "max_hr_bpm": a.get('maxHR'),
                    "steps": a.get('steps'),
                    "elevation_gain_meters": a.get('elevationGain'),
                    "elevation_loss_meters": a.get('elevationLoss'),
                    "owner_display_name": a.get('ownerDisplayName'),
                }
                # Remove None values
                activity = {k: v for k, v in activity.items() if v is not None}
                curated["activities"].append(activity)

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving activities: {str(e)}"

    @app.tool()
    async def get_activity_types() -> str:
        """Get all available activity types

        Returns a list of all activity types supported by Garmin Connect,
        useful for filtering activities by type.
        """
        try:
            activity_types = garmin_client.get_activity_types()
            if not activity_types:
                return "No activity types found"

            # Curate the activity types list
            curated = {
                "count": len(activity_types),
                "activity_types": []
            }

            for at in activity_types:
                activity_type = {
                    "type_id": at.get('typeId'),
                    "type_key": at.get('typeKey'),
                    "display_name": at.get('displayName'),
                    "parent_type_id": at.get('parentTypeId'),
                    "is_hidden": at.get('isHidden'),
                }
                # Remove None values
                activity_type = {k: v for k, v in activity_type.items() if v is not None}
                curated["activity_types"].append(activity_type)

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving activity types: {str(e)}"

    return app
