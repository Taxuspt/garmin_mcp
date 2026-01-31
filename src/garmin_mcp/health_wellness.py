"""
Health & Wellness Data functions for Garmin Connect MCP Server
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


def register_tools(app):
    """Register all health and wellness tools with the MCP server app"""

    @app.tool()
    async def get_stats(date: str) -> str:
        """Get daily activity stats with curated essential metrics

        Returns a summary of daily health and activity data including steps,
        calories, heart rate, stress, body battery, and sleep metrics.

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            stats = garmin_client.get_stats(date)
            if not stats:
                return f"No stats found for {date}"

            # Curate to essential fields only
            summary = {
                "date": stats.get('calendarDate'),

                # Activity
                "total_steps": stats.get('totalSteps'),
                "daily_step_goal": stats.get('dailyStepGoal'),
                "distance_meters": stats.get('totalDistanceMeters'),
                "floors_ascended": round(stats.get('floorsAscended', 0), 1) if stats.get('floorsAscended') else None,
                "floors_descended": round(stats.get('floorsDescended', 0), 1) if stats.get('floorsDescended') else None,

                # Calories
                "total_calories": stats.get('totalKilocalories'),
                "active_calories": stats.get('activeKilocalories'),
                "bmr_calories": stats.get('bmrKilocalories'),

                # Activity duration
                "highly_active_seconds": stats.get('highlyActiveSeconds'),
                "active_seconds": stats.get('activeSeconds'),
                "sedentary_seconds": stats.get('sedentarySeconds'),
                "sleeping_seconds": stats.get('sleepingSeconds'),

                # Intensity minutes
                "moderate_intensity_minutes": stats.get('moderateIntensityMinutes'),
                "vigorous_intensity_minutes": stats.get('vigorousIntensityMinutes'),
                "intensity_minutes_goal": stats.get('intensityMinutesGoal'),

                # Heart rate
                "min_heart_rate_bpm": stats.get('minHeartRate'),
                "max_heart_rate_bpm": stats.get('maxHeartRate'),
                "resting_heart_rate_bpm": stats.get('restingHeartRate'),
                "last_7_days_avg_resting_hr": stats.get('lastSevenDaysAvgRestingHeartRate'),

                # Stress
                "avg_stress_level": stats.get('averageStressLevel'),
                "max_stress_level": stats.get('maxStressLevel'),
                "stress_qualifier": stats.get('stressQualifier'),

                # Body Battery
                "body_battery_charged": stats.get('bodyBatteryChargedValue'),
                "body_battery_drained": stats.get('bodyBatteryDrainedValue'),
                "body_battery_highest": stats.get('bodyBatteryHighestValue'),
                "body_battery_lowest": stats.get('bodyBatteryLowestValue'),
                "body_battery_current": stats.get('bodyBatteryMostRecentValue'),

                # SpO2
                "avg_spo2_percent": stats.get('averageSpo2'),
                "lowest_spo2_percent": stats.get('lowestSpo2'),

                # Respiration
                "avg_waking_respiration": stats.get('avgWakingRespirationValue'),
                "highest_respiration": stats.get('highestRespirationValue'),
                "lowest_respiration": stats.get('lowestRespirationValue'),
            }

            # Remove None values for cleaner output
            summary = {k: v for k, v in summary.items() if v is not None}

            return json.dumps(summary, indent=2)
        except Exception as e:
            return f"Error retrieving stats: {str(e)}"

    @app.tool()
    async def get_user_summary(date: str) -> str:
        """Get user summary data (compatible with garminconnect-ha)

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            summary = garmin_client.get_user_summary(date)
            if not summary:
                return f"No user summary found for {date}"

            return json.dumps(summary, indent=2)
        except Exception as e:
            return f"Error retrieving user summary: {str(e)}"

    @app.tool()
    async def get_body_composition(start_date: str, end_date: str = None) -> str:
        """Get body composition data for a single date or date range

        Args:
            start_date: Date in YYYY-MM-DD format or start date if end_date provided
            end_date: Optional end date in YYYY-MM-DD format for date range
        """
        try:
            if end_date:
                composition = garmin_client.get_body_composition(start_date, end_date)
                if not composition:
                    return f"No body composition data found between {start_date} and {end_date}"
            else:
                composition = garmin_client.get_body_composition(start_date)
                if not composition:
                    return f"No body composition data found for {start_date}"

            return json.dumps(composition, indent=2)
        except Exception as e:
            return f"Error retrieving body composition data: {str(e)}"

    @app.tool()
    async def get_stats_and_body(date: str) -> str:
        """Get stats and body composition data

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            data = garmin_client.get_stats_and_body(date)
            if not data:
                return f"No stats and body composition data found for {date}"

            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error retrieving stats and body composition data: {str(e)}"

    @app.tool()
    async def get_steps_data(date: str) -> str:
        """Get detailed steps data with 15-minute intervals

        Note: This returns full interval data (~14KB). For a compact summary,
        use get_stats() which includes total_steps.

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            steps_data = garmin_client.get_steps_data(date)
            if not steps_data:
                return f"No steps data found for {date}"

            return json.dumps(steps_data, indent=2)
        except Exception as e:
            return f"Error retrieving steps data: {str(e)}"

    @app.tool()
    async def get_daily_steps(start_date: str, end_date: str) -> str:
        """Get steps data for a date range

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            steps_data = garmin_client.get_daily_steps(start_date, end_date)
            if not steps_data:
                return f"No daily steps data found between {start_date} and {end_date}"

            return json.dumps(steps_data, indent=2)
        except Exception as e:
            return f"Error retrieving daily steps data: {str(e)}"

    @app.tool()
    async def get_training_readiness(date: str) -> str:
        """Get training readiness data with curated metrics

        Returns training readiness score and contributing factors.

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            readiness_list = garmin_client.get_training_readiness(date)
            if not readiness_list:
                return f"No training readiness data found for {date}"

            # Curate each readiness entry (usually 1-2 per day)
            curated = []
            for r in readiness_list:
                entry = {
                    "date": r.get('calendarDate'),
                    "timestamp": r.get('timestampLocal'),
                    "context": r.get('inputContext'),

                    # Overall readiness
                    "level": r.get('level'),
                    "score": r.get('score'),
                    "feedback": r.get('feedbackShort'),

                    # Contributing factors
                    "sleep_score": r.get('sleepScore'),
                    "sleep_factor_percent": r.get('sleepScoreFactorPercent'),
                    "sleep_factor_feedback": r.get('sleepScoreFactorFeedback'),

                    "recovery_time_hours": round(r.get('recoveryTime', 0) / 60, 1) if r.get('recoveryTime') else None,
                    "recovery_factor_percent": r.get('recoveryTimeFactorPercent'),
                    "recovery_factor_feedback": r.get('recoveryTimeFactorFeedback'),

                    "training_load_factor_percent": r.get('acwrFactorPercent'),
                    "training_load_feedback": r.get('acwrFactorFeedback'),
                    "acute_load": r.get('acuteLoad'),

                    "hrv_factor_percent": r.get('hrvFactorPercent'),
                    "hrv_factor_feedback": r.get('hrvFactorFeedback'),
                    "hrv_weekly_avg": r.get('hrvWeeklyAverage'),

                    "stress_history_factor_percent": r.get('stressHistoryFactorPercent'),
                    "stress_history_feedback": r.get('stressHistoryFactorFeedback'),

                    "sleep_history_factor_percent": r.get('sleepHistoryFactorPercent'),
                    "sleep_history_feedback": r.get('sleepHistoryFactorFeedback'),
                }
                # Remove None values
                entry = {k: v for k, v in entry.items() if v is not None}
                curated.append(entry)

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving training readiness data: {str(e)}"

    @app.tool()
    async def get_body_battery(start_date: str, end_date: str) -> str:
        """Get body battery data with events

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            battery_data = garmin_client.get_body_battery(start_date, end_date)
            if not battery_data:
                return f"No body battery data found between {start_date} and {end_date}"

            # Curate each day's data
            curated = []
            for day in battery_data:
                entry = {
                    "date": day.get('date'),
                    "charged": day.get('charged'),
                    "drained": day.get('drained'),

                    # Curate activity events
                    "events": []
                }

                for event in day.get('bodyBatteryActivityEvent', []):
                    entry["events"].append({
                        "type": event.get('eventType'),
                        "start_time": event.get('eventStartTimeGmt'),
                        "duration_minutes": round(event.get('durationInMilliseconds', 0) / 60000, 1),
                        "body_battery_impact": event.get('bodyBatteryImpact'),
                        "feedback": event.get('shortFeedback'),
                    })

                # Add dynamic feedback if present
                feedback = day.get('bodyBatteryDynamicFeedbackEvent', {})
                if feedback:
                    entry["current_feedback"] = feedback.get('feedbackShortType')
                    entry["body_battery_level"] = feedback.get('bodyBatteryLevel')

                curated.append(entry)

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving body battery data: {str(e)}"

    @app.tool()
    async def get_body_battery_events(date: str) -> str:
        """Get body battery events data

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            events = garmin_client.get_body_battery_events(date)
            if not events:
                return f"No body battery events found for {date}"

            return json.dumps(events, indent=2)
        except Exception as e:
            return f"Error retrieving body battery events: {str(e)}"

    @app.tool()
    async def get_blood_pressure(start_date: str, end_date: str) -> str:
        """Get blood pressure data

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            bp_data = garmin_client.get_blood_pressure(start_date, end_date)
            if not bp_data:
                return f"No blood pressure data found between {start_date} and {end_date}"

            return json.dumps(bp_data, indent=2)
        except Exception as e:
            return f"Error retrieving blood pressure data: {str(e)}"

    @app.tool()
    async def get_floors(date: str) -> str:
        """Get floors climbed data

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            floors_data = garmin_client.get_floors(date)
            if not floors_data:
                return f"No floors data found for {date}"

            return json.dumps(floors_data, indent=2)
        except Exception as e:
            return f"Error retrieving floors data: {str(e)}"

    @app.tool()
    async def get_rhr_day(date: str) -> str:
        """Get resting heart rate data

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            rhr_data = garmin_client.get_rhr_day(date)
            if not rhr_data:
                return f"No resting heart rate data found for {date}"

            return json.dumps(rhr_data, indent=2)
        except Exception as e:
            return f"Error retrieving resting heart rate data: {str(e)}"

    @app.tool()
    async def get_heart_rates(date: str) -> str:
        """Get full heart rate time-series data

        Note: This returns detailed 2-minute interval data (~25KB).
        For a compact summary, use get_heart_rates_summary().

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            hr_data = garmin_client.get_heart_rates(date)
            if not hr_data:
                return f"No heart rate data found for {date}"

            return json.dumps(hr_data, indent=2)
        except Exception as e:
            return f"Error retrieving heart rate data: {str(e)}"

    @app.tool()
    async def get_heart_rates_summary(date: str) -> str:
        """Get heart rate summary with essential metrics (lightweight version)

        Returns a compact summary (~500 bytes) instead of full time-series data (~25KB).
        Ideal for daily health checkups and LLM integrations.

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            hr_data = garmin_client.get_heart_rates(date)
            if not hr_data:
                return f"No heart rate data found for {date}"

            summary = {
                "date": hr_data.get('calendarDate'),
                "max_heart_rate_bpm": hr_data.get('maxHeartRate'),
                "min_heart_rate_bpm": hr_data.get('minHeartRate'),
                "resting_heart_rate_bpm": hr_data.get('restingHeartRate'),
                "last_7_days_avg_resting_hr": hr_data.get('lastSevenDaysAvgRestingHeartRate'),
            }

            # Calculate average from time-series if available
            hr_values = hr_data.get('heartRateValues', [])
            if hr_values:
                valid_values = [v[1] for v in hr_values if v[1] and v[1] > 0]
                if valid_values:
                    summary["avg_heart_rate_bpm"] = round(sum(valid_values) / len(valid_values), 1)
                    summary["data_points_count"] = len(valid_values)

            # Remove None values
            summary = {k: v for k, v in summary.items() if v is not None}

            return json.dumps(summary, indent=2)
        except Exception as e:
            return f"Error retrieving heart rate summary: {str(e)}"

    @app.tool()
    async def get_hydration_data(date: str) -> str:
        """Get hydration data

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            hydration_data = garmin_client.get_hydration_data(date)
            if not hydration_data:
                return f"No hydration data found for {date}"

            return json.dumps(hydration_data, indent=2)
        except Exception as e:
            return f"Error retrieving hydration data: {str(e)}"

    @app.tool()
    async def get_sleep_data(date: str) -> str:
        """Get full sleep data with all details

        Note: This returns detailed sleep data (~50KB).
        For a compact summary, use get_sleep_summary().

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            sleep_data = garmin_client.get_sleep_data(date)
            if not sleep_data:
                return f"No sleep data found for {date}"

            return json.dumps(sleep_data, indent=2)
        except Exception as e:
            return f"Error retrieving sleep data: {str(e)}"

    @app.tool()
    async def get_sleep_summary(date: str) -> str:
        """Get sleep summary with only essential metrics (lightweight version)

        This endpoint returns a compact summary of sleep data (~350 bytes) instead of
        the full granular data (~50KB). Ideal for daily health checkups and LLM integrations
        where the full time-series data would overwhelm the context window.

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            sleep_data = garmin_client.get_sleep_data(date)
            if not sleep_data:
                return f"No sleep summary found for {date}"

            # Extract only essential summary metrics
            summary = {}

            # Extract data from dailySleepDTO if available
            daily_sleep = sleep_data.get('dailySleepDTO', {})
            if daily_sleep:
                # Sleep duration and timing
                summary['sleep_seconds'] = daily_sleep.get('sleepTimeSeconds')
                summary['nap_seconds'] = daily_sleep.get('napTimeSeconds')
                summary['sleep_start'] = daily_sleep.get('sleepStartTimestampGMT')
                summary['sleep_end'] = daily_sleep.get('sleepEndTimestampGMT')

                # Sleep score and quality
                summary['sleep_score'] = daily_sleep.get('sleepScores', {}).get('overall', {}).get('value')
                summary['sleep_score_qualifier'] = daily_sleep.get('sleepScores', {}).get('overall', {}).get('qualifierKey')

                # Sleep phases (in seconds)
                summary['deep_sleep_seconds'] = daily_sleep.get('deepSleepSeconds')
                summary['light_sleep_seconds'] = daily_sleep.get('lightSleepSeconds')
                summary['rem_sleep_seconds'] = daily_sleep.get('remSleepSeconds')
                summary['awake_seconds'] = daily_sleep.get('awakeSleepSeconds')

                # Sleep disruptions
                summary['awake_count'] = daily_sleep.get('awakeCount')
                summary['restless_moments_count'] = daily_sleep.get('restlessMomentsCount')

                # Average physiological metrics
                summary['avg_sleep_stress'] = daily_sleep.get('avgSleepStress')
                summary['resting_heart_rate_bpm'] = daily_sleep.get('restingHeartRate')

            # Extract SpO2 summary if available
            spo2_summary = sleep_data.get('wellnessSpO2SleepSummaryDTO', {})
            if spo2_summary:
                summary['avg_spo2_percent'] = spo2_summary.get('averageSpo2')
                summary['lowest_spo2_percent'] = spo2_summary.get('lowestSpo2')

            # Add HRV data if available at top level
            if 'avgOvernightHrv' in sleep_data:
                summary['avg_overnight_hrv'] = sleep_data.get('avgOvernightHrv')

            # Calculate sleep phase percentages if total sleep time is available
            total_sleep = summary.get('sleep_seconds', 0)
            if total_sleep and total_sleep > 0:
                summary['deep_sleep_percent'] = round((summary.get('deep_sleep_seconds', 0) / total_sleep) * 100, 1)
                summary['light_sleep_percent'] = round((summary.get('light_sleep_seconds', 0) / total_sleep) * 100, 1)
                summary['rem_sleep_percent'] = round((summary.get('rem_sleep_seconds', 0) / total_sleep) * 100, 1)

            # Convert sleep duration to hours for convenience
            if total_sleep:
                summary['sleep_hours'] = round(total_sleep / 3600, 2)

            # Remove None values
            summary = {k: v for k, v in summary.items() if v is not None}

            return json.dumps(summary, indent=2)
        except Exception as e:
            return f"Error retrieving sleep summary: {str(e)}"

    @app.tool()
    async def get_stress_data(date: str) -> str:
        """Get full stress time-series data

        Note: This returns detailed interval data (~35KB) including body battery.
        For a compact summary, use get_stress_summary().

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            stress_data = garmin_client.get_stress_data(date)
            if not stress_data:
                return f"No stress data found for {date}"

            return json.dumps(stress_data, indent=2)
        except Exception as e:
            return f"Error retrieving stress data: {str(e)}"

    @app.tool()
    async def get_stress_summary(date: str) -> str:
        """Get stress summary with essential metrics (lightweight version)

        Returns a compact summary (~400 bytes) instead of full time-series data (~35KB).
        Ideal for daily health checkups and LLM integrations.

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            stress_data = garmin_client.get_stress_data(date)
            if not stress_data:
                return f"No stress data found for {date}"

            summary = {
                "date": stress_data.get('calendarDate'),
                "max_stress_level": stress_data.get('maxStressLevel'),
                "avg_stress_level": stress_data.get('avgStressLevel'),
            }

            # Calculate stress distribution from time-series if available
            stress_values = stress_data.get('stressValuesArray', [])
            if stress_values:
                # Filter valid stress readings (exclude -1 and -2 which are gaps/activity)
                valid_values = [v[1] for v in stress_values if v[1] and v[1] > 0]
                rest_values = [v for v in valid_values if v < 26]
                low_values = [v for v in valid_values if 26 <= v < 51]
                medium_values = [v for v in valid_values if 51 <= v < 76]
                high_values = [v for v in valid_values if v >= 76]

                total = len(valid_values) if valid_values else 1
                summary["rest_percent"] = round(len(rest_values) / total * 100, 1)
                summary["low_stress_percent"] = round(len(low_values) / total * 100, 1)
                summary["medium_stress_percent"] = round(len(medium_values) / total * 100, 1)
                summary["high_stress_percent"] = round(len(high_values) / total * 100, 1)
                summary["data_points_count"] = len(valid_values)

            # Remove None values
            summary = {k: v for k, v in summary.items() if v is not None}

            return json.dumps(summary, indent=2)
        except Exception as e:
            return f"Error retrieving stress summary: {str(e)}"

    @app.tool()
    async def get_respiration_data(date: str) -> str:
        """Get full respiration time-series data

        Note: This returns detailed interval data (~20KB).
        For a compact summary, use get_respiration_summary().

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            respiration_data = garmin_client.get_respiration_data(date)
            if not respiration_data:
                return f"No respiration data found for {date}"

            return json.dumps(respiration_data, indent=2)
        except Exception as e:
            return f"Error retrieving respiration data: {str(e)}"

    @app.tool()
    async def get_respiration_summary(date: str) -> str:
        """Get respiration summary with essential metrics (lightweight version)

        Returns a compact summary (~300 bytes) instead of full time-series data (~20KB).

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            resp_data = garmin_client.get_respiration_data(date)
            if not resp_data:
                return f"No respiration data found for {date}"

            summary = {
                "date": resp_data.get('calendarDate'),
                "lowest_breaths_per_min": resp_data.get('lowestRespirationValue'),
                "highest_breaths_per_min": resp_data.get('highestRespirationValue'),
                "avg_waking_breaths_per_min": resp_data.get('avgWakingRespirationValue'),
                "avg_sleep_breaths_per_min": resp_data.get('avgSleepRespirationValue'),
            }

            # Remove None values
            summary = {k: v for k, v in summary.items() if v is not None}

            return json.dumps(summary, indent=2)
        except Exception as e:
            return f"Error retrieving respiration summary: {str(e)}"

    @app.tool()
    async def get_spo2_data(date: str) -> str:
        """Get SpO2 (blood oxygen) data

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            spo2_data = garmin_client.get_spo2_data(date)
            if not spo2_data:
                return f"No SpO2 data found for {date}"

            # Curate the response
            summary = {
                "date": spo2_data.get('calendarDate'),
                "avg_spo2_percent": spo2_data.get('averageSpO2'),
                "lowest_spo2_percent": spo2_data.get('lowestSpO2'),
                "latest_spo2_percent": spo2_data.get('latestSpO2'),
                "latest_reading_time": spo2_data.get('latestSpO2TimestampLocal'),
                "last_7_days_avg_spo2": spo2_data.get('lastSevenDaysAvgSpO2'),
                "avg_sleep_spo2_percent": spo2_data.get('avgSleepSpO2'),
            }

            # Include hourly averages if available
            hourly = spo2_data.get('spO2HourlyAverages')
            if hourly:
                summary["hourly_averages"] = hourly

            # Remove None values
            summary = {k: v for k, v in summary.items() if v is not None}

            return json.dumps(summary, indent=2)
        except Exception as e:
            return f"Error retrieving SpO2 data: {str(e)}"

    @app.tool()
    async def get_all_day_stress(date: str) -> str:
        """Get all-day stress data

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            stress_data = garmin_client.get_all_day_stress(date)
            if not stress_data:
                return f"No all-day stress data found for {date}"

            return json.dumps(stress_data, indent=2)
        except Exception as e:
            return f"Error retrieving all-day stress data: {str(e)}"

    @app.tool()
    async def get_all_day_events(date: str) -> str:
        """Get daily wellness events data

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            events = garmin_client.get_all_day_events(date)
            if not events:
                return f"No daily wellness events found for {date}"

            return json.dumps(events, indent=2)
        except Exception as e:
            return f"Error retrieving daily wellness events: {str(e)}"

    @app.tool()
    async def get_weekly_steps(end_date: str, weeks: int = 4) -> str:
        """Get weekly step data aggregates

        Returns weekly step totals for the specified number of weeks ending at end_date.

        Args:
            end_date: End date in YYYY-MM-DD format
            weeks: Number of weeks to fetch (default 4, max 52)
        """
        try:
            weeks = min(weeks, 52)  # Cap at 52 weeks
            weekly_data = garmin_client.get_weekly_steps(end_date, weeks)
            if not weekly_data:
                return f"No weekly steps data found for {weeks} weeks ending {end_date}"

            # Curate the weekly steps data (API returns a list with nested 'values')
            curated_weeks = []
            for week in weekly_data:
                values = week.get("values", {})
                week_entry = {
                    "week_start": week.get("calendarDate"),
                    "total_steps": values.get("totalSteps"),
                    "average_steps": values.get("averageSteps"),
                    "total_distance_meters": values.get("totalDistance"),
                    "average_distance_meters": values.get("averageDistance"),
                    "days_with_data": values.get("wellnessDataDaysCount"),
                }
                # Remove None values
                week_entry = {k: v for k, v in week_entry.items() if v is not None}
                curated_weeks.append(week_entry)

            # Sort by date (most recent first)
            curated_weeks.sort(key=lambda x: x.get("week_start") or "", reverse=True)

            return json.dumps(
                {
                    "end_date": end_date,
                    "weeks_requested": weeks,
                    "weeks_returned": len(curated_weeks),
                    "weekly_data": curated_weeks,
                },
                indent=2,
            )
        except Exception as e:
            return f"Error retrieving weekly steps data: {str(e)}"

    @app.tool()
    async def get_weekly_stress(end_date: str, weeks: int = 4) -> str:
        """Get weekly stress data aggregates

        Returns weekly stress values for the specified number of weeks ending at end_date.

        Args:
            end_date: End date in YYYY-MM-DD format
            weeks: Number of weeks to fetch (default 4, max 52)
        """
        try:
            weeks = min(weeks, 52)  # Cap at 52 weeks
            weekly_data = garmin_client.get_weekly_stress(end_date, weeks)
            if not weekly_data:
                return f"No weekly stress data found for {weeks} weeks ending {end_date}"

            # Curate the weekly stress data (API returns a list)
            curated_weeks = []
            for week in weekly_data:
                week_entry = {
                    "week_start": week.get("calendarDate"),
                    "stress_value": week.get("value"),
                }
                # Remove None values
                week_entry = {k: v for k, v in week_entry.items() if v is not None}
                curated_weeks.append(week_entry)

            # Sort by date (most recent first)
            curated_weeks.sort(key=lambda x: x.get("week_start") or "", reverse=True)

            return json.dumps(
                {
                    "end_date": end_date,
                    "weeks_requested": weeks,
                    "weeks_returned": len(curated_weeks),
                    "weekly_data": curated_weeks,
                },
                indent=2,
            )
        except Exception as e:
            return f"Error retrieving weekly stress data: {str(e)}"

    @app.tool()
    async def get_weekly_intensity_minutes(end_date: str, weeks: int = 4) -> str:
        """Get weekly intensity minutes data aggregates

        Returns weekly intensity minutes (moderate and vigorous) for the specified
        number of weeks ending at end_date.

        Args:
            end_date: End date in YYYY-MM-DD format
            weeks: Number of weeks to fetch (default 4, max 52)
        """
        try:
            weeks = min(weeks, 52)  # Cap at 52 weeks

            # Calculate start_date from end_date and weeks
            end_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d")
            start_dt = end_dt - datetime.timedelta(days=(weeks * 7) - 1)
            start_date = start_dt.strftime("%Y-%m-%d")

            weekly_data = garmin_client.get_weekly_intensity_minutes(start_date, end_date)
            if not weekly_data:
                return f"No weekly intensity minutes data found for {weeks} weeks ending {end_date}"

            # Curate the weekly intensity data (API returns a list)
            curated_weeks = []
            for week in weekly_data:
                week_entry = {
                    "week_start": week.get("calendarDate"),
                    "weekly_goal": week.get("weeklyGoal"),
                    "moderate_minutes": week.get("moderateValue"),
                    "vigorous_minutes": week.get("vigorousValue"),
                }
                # Calculate total intensity minutes (vigorous counts double per WHO guidelines)
                moderate = week.get("moderateValue") or 0
                vigorous = week.get("vigorousValue") or 0
                week_entry["total_minutes"] = moderate + vigorous

                # Remove None values
                week_entry = {k: v for k, v in week_entry.items() if v is not None}
                curated_weeks.append(week_entry)

            # Sort by date (most recent first)
            curated_weeks.sort(key=lambda x: x.get("week_start") or "", reverse=True)

            return json.dumps(
                {
                    "end_date": end_date,
                    "weeks_requested": weeks,
                    "weeks_returned": len(curated_weeks),
                    "weekly_data": curated_weeks,
                },
                indent=2,
            )
        except Exception as e:
            return f"Error retrieving weekly intensity minutes data: {str(e)}"

    @app.tool()
    async def get_morning_training_readiness(date: str) -> str:
        """Get morning training readiness score

        Returns the morning training readiness assessment, which evaluates
        recovery status and readiness to train based on overnight metrics.

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            readiness = garmin_client.get_morning_training_readiness(date)
            if not readiness:
                return f"No morning training readiness data found for {date}"

            # Curate the morning training readiness data
            curated = {
                "date": date,
                "readiness_score": readiness.get('readinessScore'),
                "readiness_level": readiness.get('readinessLevel'),
                "recovery_time_hours": readiness.get('recoveryTime'),
                "hrv_status": readiness.get('hrvStatus'),
                "sleep_quality": readiness.get('sleepQuality'),
                "sleep_score": readiness.get('sleepScore'),
                "resting_heart_rate_bpm": readiness.get('restingHeartRate'),
                "hrv_baseline": readiness.get('hrvBaseline'),
                "hrv_last_night": readiness.get('hrvLastNight'),
                "body_battery_percent": readiness.get('bodyBattery'),
                "stress_level": readiness.get('stressLevel'),
                "training_load_balance": readiness.get('trainingLoadBalance'),
                "acute_load": readiness.get('acuteLoad'),
                "chronic_load": readiness.get('chronicLoad'),
            }

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving morning training readiness: {str(e)}"

    return app
