"""
Training and performance functions for Garmin Connect MCP Server
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
    """Register all training-related tools with the MCP server app"""

    @app.tool()
    async def get_progress_summary_between_dates(
        start_date: str, end_date: str, metric: str
    ) -> str:
        """Get progress summary for a metric between dates

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            metric: Metric to get progress for (e.g., "elevationGain", "duration", "distance", "movingDuration")
        """
        try:
            summary = garmin_client.get_progress_summary_between_dates(
                start_date, end_date, metric
            )
            if not summary:
                return f"No progress summary found for {metric} between {start_date} and {end_date}."

            # Curate to essential fields only
            curated = {
                "metric": metric,
                "start_date": start_date,
                "end_date": end_date,
            }

            # Add metric-specific fields with proper units
            if metric == "distance":
                curated["total_distance_meters"] = summary.get('totalDistance')
                curated["avg_distance_meters"] = summary.get('avgDistance')
            elif metric == "duration":
                curated["total_duration_seconds"] = summary.get('totalDuration')
                curated["avg_duration_seconds"] = summary.get('avgDuration')
            elif metric == "elevationGain":
                curated["total_elevation_meters"] = summary.get('totalElevationGain')
                curated["avg_elevation_meters"] = summary.get('avgElevationGain')
            elif metric == "movingDuration":
                curated["total_moving_seconds"] = summary.get('totalMovingDuration')
                curated["avg_moving_seconds"] = summary.get('avgMovingDuration')

            # Add common training metrics
            curated["aerobic_effect"] = summary.get('aerobicEffect')
            curated["anaerobic_effect"] = summary.get('anaerobicEffect')
            curated["training_load"] = summary.get('trainingLoad')
            curated["activity_count"] = summary.get('numberOfActivities')

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving progress summary: {str(e)}"

    @app.tool()
    async def get_hill_score(start_date: str, end_date: str) -> str:
        """Get hill score data between dates

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            hill_score_data = garmin_client.get_hill_score(start_date, end_date)
            if not hill_score_data:
                return f"No hill score data found between {start_date} and {end_date}."

            # Curate to essential fields only
            curated = {
                "start_date": start_date,
                "end_date": end_date,
                "hill_score": hill_score_data.get('hillScore'),
                "current_hill_score": hill_score_data.get('currentHillScore'),

                # Recent performance
                "recent_elevation_gain_meters": hill_score_data.get('recentElevationGain'),
                "recent_ascent_time_seconds": hill_score_data.get('recentAscentTime'),
                "recent_climb_count": hill_score_data.get('recentClimbCount'),

                # Historical averages
                "avg_elevation_gain_meters": hill_score_data.get('avgElevationGain'),
                "avg_ascent_time_seconds": hill_score_data.get('avgAscentTime'),

                # Performance indicators
                "improvement_percent": hill_score_data.get('improvementPercent'),
                "qualifier": hill_score_data.get('hillScoreQualifier'),
            }

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving hill score data: {str(e)}"

    @app.tool()
    async def get_endurance_score(start_date: str, end_date: str) -> str:
        """Get endurance score data between dates

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            endurance_data = garmin_client.get_endurance_score(start_date, end_date)
            if not endurance_data:
                return f"No endurance score data found between {start_date} and {end_date}."

            # Curate to essential fields only
            curated = {
                "start_date": start_date,
                "end_date": end_date,
                "endurance_score": endurance_data.get('enduranceScore'),
                "current_endurance_score": endurance_data.get('currentEnduranceScore'),

                # Recent performance
                "recent_duration_seconds": endurance_data.get('recentDuration'),
                "recent_distance_meters": endurance_data.get('recentDistance'),
                "recent_activity_count": endurance_data.get('recentActivityCount'),

                # Historical averages
                "avg_duration_seconds": endurance_data.get('avgDuration'),
                "avg_distance_meters": endurance_data.get('avgDistance'),

                # Performance indicators
                "improvement_percent": endurance_data.get('improvementPercent'),
                "qualifier": endurance_data.get('enduranceScoreQualifier'),
            }

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving endurance score data: {str(e)}"

    @app.tool()
    async def get_training_effect(activity_id: int) -> str:
        """Get training effect data for a specific activity

        Args:
            activity_id: ID of the activity to retrieve training effect for
        """
        try:
            effect = garmin_client.get_training_effect(activity_id)
            if not effect:
                return f"No training effect data found for activity with ID {activity_id}."

            # Curate to essential fields only
            curated = {
                "aerobic_effect": effect.get('aerobicTrainingEffect'),
                "aerobic_effect_label": effect.get('aerobicTrainingEffectLabel'),
                "anaerobic_effect": effect.get('anaerobicTrainingEffect'),
                "anaerobic_effect_label": effect.get('anaerobicTrainingEffectLabel'),

                # Recovery metrics
                "recovery_time_hours": round(effect.get('recoveryTime', 0) / 60, 1) if effect.get('recoveryTime') else None,

                # Training load
                "training_load": effect.get('activityTrainingLoad'),
                "load_ratio": effect.get('trainingLoadRatio'),

                # Performance condition
                "performance_condition": effect.get('performanceCondition'),
                "performance_condition_label": effect.get('performanceConditionLabel'),
            }

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving training effect data: {str(e)}"

    @app.tool()
    async def get_max_metrics(date: str) -> str:
        """Get max metrics data (like VO2 Max and fitness age)

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            metrics = garmin_client.get_max_metrics(date)
            if not metrics:
                return f"No max metrics data found for {date}."

            # Curate to essential fields only
            curated = {
                "date": date,

                # VO2 Max metrics
                "vo2_max": metrics.get('vo2MaxValue'),
                "vo2_max_precision": metrics.get('vo2MaxPrecisionIndex'),

                # Fitness age
                "fitness_age_years": metrics.get('fitnessAge'),
                "chronological_age_years": metrics.get('chronologicalAge'),
                "fitness_age_description": metrics.get('fitnessAgeDescription'),

                # Lactate threshold
                "lactate_threshold_heart_rate_bpm": metrics.get('lactateThresholdHeartRate'),
                "lactate_threshold_speed_mps": metrics.get('lactateThresholdSpeed'),
                "lactate_threshold_pace_seconds_per_km": metrics.get('lactateThresholdPace'),

                # Other max metrics
                "max_heart_rate_bpm": metrics.get('maxHeartRate'),
                "max_avg_power_watts": metrics.get('functionalThresholdPower'),
            }

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving max metrics data: {str(e)}"

    @app.tool()
    async def get_hrv_data(date: str) -> str:
        """Get Heart Rate Variability (HRV) data

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            hrv_data = garmin_client.get_hrv_data(date)
            if not hrv_data:
                return f"No HRV data found for {date}."

            # Curate to essential fields only
            curated = {
                "date": hrv_data.get('calendarDate') or date,

                # Current HRV values
                "last_night_avg_hrv_ms": hrv_data.get('lastNightAvg'),
                "last_night_5min_high_hrv_ms": hrv_data.get('lastNight5MinHigh'),

                # Baseline and trends
                "weekly_avg_hrv_ms": hrv_data.get('weeklyAvg'),
                "baseline_hrv_ms": hrv_data.get('baseline'),

                # Status and feedback
                "status": hrv_data.get('status'),
                "feedback": hrv_data.get('feedbackPhrase'),
                "status_value": hrv_data.get('statusValue'),

                # Historical markers
                "last_7_days_avg_hrv_ms": hrv_data.get('lastSevenDaysAvg'),
                "balanced_low_hrv_ms": hrv_data.get('balancedLow'),
                "balanced_high_hrv_ms": hrv_data.get('balancedHigh'),
            }

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving HRV data: {str(e)}"

    @app.tool()
    async def get_fitnessage_data(date: str) -> str:
        """Get fitness age data

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            fitness_age = garmin_client.get_fitnessage_data(date)
            if not fitness_age:
                return f"No fitness age data found for {date}."

            # Curate to essential fields only
            curated = {
                "date": date,
                "fitness_age_years": fitness_age.get('fitnessAge'),
                "chronological_age_years": fitness_age.get('chronologicalAge'),
                "age_difference_years": fitness_age.get('ageDifference'),

                # Contributing metrics
                "vo2_max": fitness_age.get('vo2Max'),
                "resting_heart_rate_bpm": fitness_age.get('restingHeartRate'),
                "bmi": fitness_age.get('bmi'),
                "body_fat_percent": fitness_age.get('bodyFatPercent'),

                # Metadata
                "fitness_age_description": fitness_age.get('fitnessAgeDescription'),
                "measurement_timestamp": fitness_age.get('measurementDate'),
            }

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving fitness age data: {str(e)}"

    @app.tool()
    async def get_training_status(date: str) -> str:
        """Get training status with curated metrics

        Returns comprehensive training status including load, VO2 max, recovery,
        and training readiness indicators.

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            status = garmin_client.get_training_status(date)
            if not status:
                return f"No training status data found for {date}."

            # Extract from nested structure
            recent_status = status.get('mostRecentTrainingStatus', {})
            latest_data = recent_status.get('latestTrainingStatusData', {})

            # Get first device data (usually the primary device)
            device_data = {}
            for device_id, data in latest_data.items():
                device_data = data
                break

            acwr_data = device_data.get('acuteTrainingLoadDTO', {})

            # VO2 Max data
            vo2_data = status.get('mostRecentVO2Max', {}).get('generic', {})

            # Training load balance
            load_balance = status.get('mostRecentTrainingLoadBalance', {})
            load_map = load_balance.get('metricsTrainingLoadBalanceDTOMap', {})
            load_data = {}
            for device_id, data in load_map.items():
                load_data = data
                break

            # Curate to essential fields only - remove userIds
            curated = {
                "date": device_data.get('calendarDate', date),

                # Training status
                "training_status": device_data.get('trainingStatus'),
                "training_status_feedback": device_data.get('trainingStatusFeedbackPhrase'),
                "sport": device_data.get('sport'),
                "fitness_trend": device_data.get('fitnessTrend'),

                # Acute Chronic Workload Ratio
                "acute_load": acwr_data.get('dailyTrainingLoadAcute'),
                "chronic_load": acwr_data.get('dailyTrainingLoadChronic'),
                "load_ratio": acwr_data.get('dailyAcuteChronicWorkloadRatio'),
                "acwr_status": acwr_data.get('acwrStatus'),
                "acwr_percent": acwr_data.get('acwrPercent'),
                "optimal_chronic_load_min": acwr_data.get('minTrainingLoadChronic'),
                "optimal_chronic_load_max": acwr_data.get('maxTrainingLoadChronic'),

                # VO2 Max
                "vo2_max": vo2_data.get('vo2MaxValue'),
                "vo2_max_precise": vo2_data.get('vo2MaxPreciseValue'),

                # Monthly training load
                "monthly_load_aerobic_low": load_data.get('monthlyLoadAerobicLow'),
                "monthly_load_aerobic_high": load_data.get('monthlyLoadAerobicHigh'),
                "monthly_load_anaerobic": load_data.get('monthlyLoadAnaerobic'),
                "training_balance_feedback": load_data.get('trainingBalanceFeedbackPhrase'),
            }

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving training status data: {str(e)}"

    @app.tool()
    async def get_lactate_threshold(date: str) -> str:
        """Get lactate threshold data

        Returns lactate threshold information, which is the exercise intensity at
        which lactate starts to accumulate in the blood. This is a key metric for
        endurance training.

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            threshold = garmin_client.get_lactate_threshold(date)
            if not threshold:
                return f"No lactate threshold data found for {date}"

            # Curate the lactate threshold data
            curated = {
                "date": date,
                "lactate_threshold_bpm": threshold.get('lactateThresholdHeartRate'),
                "lactate_threshold_speed_mps": threshold.get('lactateThresholdSpeed'),
                "lactate_threshold_pace_seconds_per_km": threshold.get('lactateThresholdPace'),
                "running_lactate_threshold_bpm": threshold.get('runningLactateThresholdHeartRate'),
                "cycling_lactate_threshold_bpm": threshold.get('cyclingLactateThresholdHeartRate'),
                "cycling_lactate_threshold_watts": threshold.get('cyclingLactateThresholdPower'),
                "auto_detected": threshold.get('autoDetected'),
                "measurement_timestamp": threshold.get('measurementDate'),
                "sport": threshold.get('sport'),
            }

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving lactate threshold data: {str(e)}"

    @app.tool()
    async def request_reload(date: str) -> str:
        """Request reload of epoch data

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            result = garmin_client.request_reload(date)
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error requesting data reload: {str(e)}"

    return app
