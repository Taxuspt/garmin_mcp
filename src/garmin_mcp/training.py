"""
Training and performance functions for Garmin Connect MCP Server
"""

import json
import datetime
from typing import Any, Dict, List, Optional, Union

# The garmin_client will be set by the main file
garmin_client = None

# Cache for activity type mapping
_activity_type_cache: Optional[Dict[int, str]] = None


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client, _activity_type_cache
    garmin_client = client
    _activity_type_cache = None  # Reset cache when client changes


def _get_activity_type_mapping() -> Dict[int, str]:
    """Get or build a cached mapping of activity type IDs to names"""
    global _activity_type_cache
    if _activity_type_cache is not None:
        return _activity_type_cache

    try:
        activity_types = garmin_client.get_activity_types()
        _activity_type_cache = {
            at.get("typeId"): at.get("typeKey", "unknown")
            for at in activity_types
            if at.get("typeId") is not None
        }
    except Exception:
        _activity_type_cache = {}

    return _activity_type_cache


def _map_contributor(
    contributor: Dict[str, Any], activity_type_mapping: Dict[int, str]
) -> Dict[str, Any]:
    """Map a contributor dict to include human-readable activity type"""
    activity_type_id = contributor.get("activityTypeId")
    group = contributor.get("group")
    contribution = contributor.get("contribution")

    result: Dict[str, Any] = {
        "contribution_percent": round(contribution, 2) if contribution else None
    }

    if activity_type_id is not None:
        result["activity_type"] = activity_type_mapping.get(
            activity_type_id, f"unknown_{activity_type_id}"
        )
        result["activity_type_id"] = activity_type_id
    elif group is not None:
        # Group categories when activityTypeId is None
        # TODO: Find a proper mapping for these groups
        group_names = {
            0: "running (?)",
            1: "biking (?)",
            8: "Other Activities",
        }
        result["group"] = group_names.get(group, f"group_{group}")

    return result


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
            summary_data = garmin_client.get_progress_summary_between_dates(
                start_date, end_date, metric
            )

            if not summary_data:
                return f"No progress summary found for {metric} between {start_date} and {end_date}."

            # The API returns a list with one item containing stats by activity type
            if isinstance(summary_data, list) and len(summary_data) > 0:
                data = summary_data[0]
            else:
                return f"Unexpected response format from API"

            # Curate to essential fields only
            curated = {
                "metric": metric,
                "start_date": start_date,
                "end_date": end_date,
                "date": data.get("date"),
                "count_of_activities": data.get("countOfActivities"),
                "stats_by_activity_type": {},
            }

            # Parse stats by activity type
            stats = data.get("stats", {})
            for activity_type, activity_stats in stats.items():
                if metric in activity_stats:
                    metric_data = activity_stats[metric]
                    if metric_data and metric_data.get("count", 0) > 0:
                        curated["stats_by_activity_type"][activity_type] = {
                            "count": metric_data.get("count"),
                            "sum": metric_data.get("sum"),
                            "avg": metric_data.get("avg"),
                            "min": metric_data.get("min"),
                            "max": metric_data.get("max"),
                        }

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

            # Parse the period average and max score
            period_avg_score = hill_score_data.get("periodAvgScore", {})
            avg_score = (
                next(iter(period_avg_score.values())) if period_avg_score else None
            )

            # Get the most recent daily score (first in list)
            daily_scores = hill_score_data.get("hillScoreDTOList", [])
            latest_score = daily_scores[0] if daily_scores else {}

            # Curate to essential fields only
            curated = {
                "start_date": start_date,
                "end_date": end_date,
                "period_avg_score": avg_score,
                "max_score": hill_score_data.get("maxScore"),
                # Latest daily score
                "latest_date": latest_score.get("calendarDate"),
                "latest_overall_score": latest_score.get("overallScore"),
                "latest_strength_score": latest_score.get("strengthScore"),
                "latest_endurance_score": latest_score.get("enduranceScore"),
                "latest_classification_id": latest_score.get(
                    "hillScoreClassificationId"
                ),
                # Daily scores over the period
                "daily_scores": [
                    {
                        "date": score.get("calendarDate"),
                        "overall": score.get("overallScore"),
                        "strength": score.get("strengthScore"),
                        "endurance": score.get("enduranceScore"),
                    }
                    for score in daily_scores
                ],
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

            # Get activity type mapping for human-readable names
            activity_type_mapping = _get_activity_type_mapping()

            # Extract the current endurance score DTO
            score_dto = endurance_data.get("enduranceScoreDTO", {})

            # Map classification number to label
            classification_labels = {
                1: "recreational",
                2: "intermediate",
                3: "trained",
                4: "well_trained",
                5: "expert",
                6: "superior",
                7: "elite",
            }
            classification_id = score_dto.get("classification")
            classification_label = (
                classification_labels.get(
                    classification_id, f"level_{classification_id}"
                )
                if classification_id is not None
                else None
            )

            # Process contributors with activity type names
            raw_contributors = score_dto.get("contributors", [])
            contributors = (
                [_map_contributor(c, activity_type_mapping) for c in raw_contributors]
                if raw_contributors
                else None
            )

            # Process weekly breakdown from groupMap
            group_map = endurance_data.get("groupMap", {})
            weekly_breakdown = []
            for week_date, week_data in sorted(group_map.items()):
                week_contributors = [
                    _map_contributor(c, activity_type_mapping)
                    for c in week_data.get("enduranceContributorDTOList", [])
                ]
                weekly_breakdown.append(
                    {
                        "week_start": week_date,
                        "avg_score": week_data.get("groupAverage"),
                        "max_score": week_data.get("groupMax"),
                        "contributors": (
                            week_contributors if week_contributors else None
                        ),
                    }
                )

            # Curate to essential fields
            curated = {
                "start_date": start_date,
                "end_date": end_date,
                # Period summary
                "period_avg_score": endurance_data.get("avg"),
                "period_max_score": endurance_data.get("max"),
                # Current/latest endurance score
                "current_score": score_dto.get("overallScore"),
                "current_date": score_dto.get("calendarDate"),
                "classification": classification_label,
                "classification_id": classification_id,
                # Classification thresholds for context
                "thresholds": (
                    {
                        "intermediate": score_dto.get(
                            "classificationLowerLimitIntermediate"
                        ),
                        "trained": score_dto.get("classificationLowerLimitTrained"),
                        "well_trained": score_dto.get(
                            "classificationLowerLimitWellTrained"
                        ),
                        "expert": score_dto.get("classificationLowerLimitExpert"),
                        "superior": score_dto.get("classificationLowerLimitSuperior"),
                        "elite": score_dto.get("classificationLowerLimitElite"),
                    }
                    if score_dto.get("classificationLowerLimitTrained")
                    else None
                ),
                # Contributors breakdown with activity names
                "contributors": contributors,
                # Weekly breakdown
                "weekly_breakdown": weekly_breakdown if weekly_breakdown else None,
            }

            # Remove None values recursively
            def remove_none(obj):
                if isinstance(obj, dict):
                    return {k: remove_none(v) for k, v in obj.items() if v is not None}
                elif isinstance(obj, list):
                    return [remove_none(item) for item in obj]
                return obj

            curated = remove_none(curated)

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
            # Training effect data is available through get_activity
            # The garminconnect library doesn't have a separate get_training_effect method
            activity = garmin_client.get_activity(activity_id)
            if not activity:
                return f"No activity found with ID {activity_id}."

            # Extract training effect data from activity summary
            summary = activity.get("summaryDTO", {})

            # Curate to essential fields only
            curated = {
                "activity_id": activity_id,
                "training_effect": summary.get("trainingEffect"),
                "aerobic_effect": summary.get("trainingEffect"),
                "anaerobic_effect": summary.get("anaerobicTrainingEffect"),
                "training_effect_label": summary.get("trainingEffectLabel"),
                # Recovery metrics
                "recovery_time_hours": (
                    round(summary.get("recoveryTime", 0) / 60, 1)
                    if summary.get("recoveryTime")
                    else None
                ),
                # Training load
                "training_load": summary.get("activityTrainingLoad"),
                # Additional metrics that may be available
                "performance_condition": summary.get("performanceCondition"),
            }

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving training effect data: {str(e)}"

    @app.tool()
    async def get_hrv_data(date: str, return_timeseries: bool = False) -> str:
        """Get Heart Rate Variability (HRV) data

        Args:
            date: Date in YYYY-MM-DD format
            return_timeseries: If True, include detailed 5-minute HRV readings (can be large)
        """
        try:
            hrv_data = garmin_client.get_hrv_data(date)
            if not hrv_data:
                return f"No HRV data found for {date}."

            # Extract the summary from hrvSummary key
            summary = hrv_data.get("hrvSummary", {})
            baseline = summary.get("baseline", {})

            # Curate to essential fields only
            curated = {
                "date": summary.get("calendarDate") or date,
                # Current HRV values
                "last_night_avg_hrv_ms": summary.get("lastNightAvg"),
                "last_night_5min_high_hrv_ms": summary.get("lastNight5MinHigh"),
                # Weekly average
                "weekly_avg_hrv_ms": summary.get("weeklyAvg"),
                # Baseline thresholds
                "baseline_balanced_low_ms": baseline.get("balancedLow"),
                "baseline_balanced_upper_ms": baseline.get("balancedUpper"),
                "baseline_low_upper_ms": baseline.get("lowUpper"),
                # Status and feedback
                "status": summary.get("status"),
                "feedback": summary.get("feedbackPhrase"),
                # Sleep window timestamps
                "sleep_start": hrv_data.get("sleepStartTimestampLocal"),
                "sleep_end": hrv_data.get("sleepEndTimestampLocal"),
            }

            # Optionally include the detailed HRV readings timeseries
            if return_timeseries:
                readings = hrv_data.get("hrvReadings", [])
                curated["hrv_readings"] = [
                    {
                        "time": r.get("readingTimeLocal"),
                        "hrv_ms": r.get("hrvValue"),
                    }
                    for r in readings
                ]
                curated["readings_count"] = len(readings)

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving HRV data: {str(e)}"

    @app.tool()
    async def get_fitnessage_data(date: str, details: bool = False) -> str:
        """Get fitness age data

        Args:
            date: Date in YYYY-MM-DD format
            details: If True, include component breakdown (BMI, RHR, vigorous activity)
                     with targets and improvement suggestions
        """
        try:
            fitness_age = garmin_client.get_fitnessage_data(date)
            if not fitness_age:
                return f"No fitness age data found for {date}."

            # Calculate age difference
            chrono_age = fitness_age.get("chronologicalAge")
            fit_age = fitness_age.get("fitnessAge")
            age_diff = None
            if chrono_age is not None and fit_age is not None:
                age_diff = round(chrono_age - fit_age, 1)

            # Curate to essential fields
            curated = {
                "date": date,
                "fitness_age_years": round(fit_age, 1) if fit_age else None,
                "chronological_age_years": chrono_age,
                "age_difference_years": age_diff,
                "achievable_fitness_age_years": (
                    round(fitness_age.get("achievableFitnessAge"), 1)
                    if fitness_age.get("achievableFitnessAge")
                    else None
                ),
                "previous_fitness_age_years": (
                    round(fitness_age.get("previousFitnessAge"), 1)
                    if fitness_age.get("previousFitnessAge")
                    else None
                ),
                "last_updated": fitness_age.get("lastUpdated"),
            }

            # Optionally include component details
            if details:
                components = fitness_age.get("components", {})
                curated_components = {}

                for comp_name, comp_data in components.items():
                    if not isinstance(comp_data, dict):
                        continue

                    comp_info = {"value": comp_data.get("value")}

                    # Add target and improvement info if present
                    if comp_data.get("targetValue") is not None:
                        comp_info["target"] = comp_data.get("targetValue")
                    if comp_data.get("improvementValue") is not None:
                        comp_info["improvement_needed"] = comp_data.get("improvementValue")
                    if comp_data.get("potentialAge") is not None:
                        comp_info["potential_age_if_improved"] = round(
                            comp_data.get("potentialAge"), 1
                        )
                    if comp_data.get("priority") is not None:
                        comp_info["priority"] = comp_data.get("priority")
                    if comp_data.get("stale") is not None:
                        comp_info["stale"] = comp_data.get("stale")
                    if comp_data.get("lastMeasurementDate") is not None:
                        comp_info["last_measurement"] = comp_data.get("lastMeasurementDate")

                    curated_components[comp_name] = comp_info

                if curated_components:
                    curated["components"] = curated_components

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
            recent_status = status.get("mostRecentTrainingStatus", {})
            latest_data = recent_status.get("latestTrainingStatusData", {})

            # Get first device data (usually the primary device)
            device_data = {}
            for device_id, data in latest_data.items():
                device_data = data
                break

            acwr_data = device_data.get("acuteTrainingLoadDTO", {})

            # VO2 Max data
            vo2_data = status.get("mostRecentVO2Max", {}).get("generic", {})

            # Training load balance
            load_balance = status.get("mostRecentTrainingLoadBalance", {})
            load_map = load_balance.get("metricsTrainingLoadBalanceDTOMap", {})
            load_data = {}
            for device_id, data in load_map.items():
                load_data = data
                break

            # Curate to essential fields only - remove userIds
            curated = {
                "date": device_data.get("calendarDate", date),
                # Training status
                "training_status": device_data.get("trainingStatus"),
                "training_status_feedback": device_data.get(
                    "trainingStatusFeedbackPhrase"
                ),
                "sport": device_data.get("sport"),
                "fitness_trend": device_data.get("fitnessTrend"),
                # Acute Chronic Workload Ratio
                "acute_load": acwr_data.get("dailyTrainingLoadAcute"),
                "chronic_load": acwr_data.get("dailyTrainingLoadChronic"),
                "load_ratio": acwr_data.get("dailyAcuteChronicWorkloadRatio"),
                "acwr_status": acwr_data.get("acwrStatus"),
                "acwr_percent": acwr_data.get("acwrPercent"),
                "optimal_chronic_load_min": acwr_data.get("minTrainingLoadChronic"),
                "optimal_chronic_load_max": acwr_data.get("maxTrainingLoadChronic"),
                # VO2 Max
                "vo2_max": vo2_data.get("vo2MaxValue"),
                "vo2_max_precise": vo2_data.get("vo2MaxPreciseValue"),
                # Monthly training load
                "monthly_load_aerobic_low": load_data.get("monthlyLoadAerobicLow"),
                "monthly_load_aerobic_high": load_data.get("monthlyLoadAerobicHigh"),
                "monthly_load_anaerobic": load_data.get("monthlyLoadAnaerobic"),
                "training_balance_feedback": load_data.get(
                    "trainingBalanceFeedbackPhrase"
                ),
            }

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving training status data: {str(e)}"

    @app.tool()
    async def get_lactate_threshold(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> str:
        """Get lactate threshold data

        Returns lactate threshold information, which is the exercise intensity at
        which lactate starts to accumulate in the blood. This is a key metric for
        endurance training.

        Args:
            start_date: Start date in YYYY-MM-DD format (optional, omit for latest)
            end_date: End date in YYYY-MM-DD format (optional, omit for latest)
        """
        try:
            # Call API with appropriate parameters
            if start_date and end_date:
                threshold = garmin_client.get_lactate_threshold(
                    latest=False,
                    start_date=start_date,
                    end_date=end_date,
                )
            else:
                threshold = garmin_client.get_lactate_threshold(latest=True)

            if not threshold:
                if start_date and end_date:
                    return f"No lactate threshold data found between {start_date} and {end_date}"
                return "No lactate threshold data found"

            # Handle different response formats based on query type
            if start_date and end_date:
                # Historical range format: {speed: [...], heartRate: [...], power: [...]}
                curated = {
                    "start_date": start_date,
                    "end_date": end_date,
                }

                # Process speed history
                speed_history = threshold.get("speed", [])
                if speed_history:
                    curated["speed_history"] = [
                        {
                            "date": entry.get("from"),
                            "speed_mps": entry.get("value"),
                            "series": entry.get("series"),
                        }
                        for entry in speed_history
                    ]

                # Process heart rate history
                hr_history = threshold.get("heartRate", [])
                if hr_history:
                    curated["heart_rate_history"] = [
                        {
                            "date": entry.get("from"),
                            "heart_rate_bpm": entry.get("value"),
                            "series": entry.get("series"),
                        }
                        for entry in hr_history
                    ]

                # Process power history
                power_history = threshold.get("power", [])
                if power_history:
                    curated["power_history"] = [
                        {
                            "date": entry.get("from"),
                            "power_watts": entry.get("value"),
                            "series": entry.get("series"),
                        }
                        for entry in power_history
                    ]
            else:
                # Latest format: {speed_and_heart_rate: {...}, power: {...}}
                speed_hr = threshold.get("speed_and_heart_rate", {})
                power = threshold.get("power", {})

                curated = {
                    # Speed and heart rate data
                    "lactate_threshold_speed_mps": speed_hr.get("speed"),
                    "lactate_threshold_heart_rate_bpm": speed_hr.get("heartRate"),
                    "heart_rate_cycling_bpm": speed_hr.get("heartRateCycling"),
                    "speed_hr_date": speed_hr.get("calendarDate"),
                    # Power data
                    "functional_threshold_power_watts": power.get(
                        "functionalThresholdPower"
                    ),
                    "weight_kg": power.get("weight"),
                    "power_to_weight": power.get("powerToWeight"),
                    "sport": power.get("sport"),
                    "power_date": power.get("calendarDate"),
                    "is_stale": power.get("isStale"),
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

    @app.tool()
    async def get_training_load_trend(start_date: str, end_date: str) -> str:
        """Get the Performance Management Chart (CTL/ATL/TSB) over a date range.

        Returns Chronic Training Load (CTL, 42-day fitness), Acute Training Load (ATL, 7-day fatigue),
        Training Stress Balance (TSB = CTL - ATL, form/freshness), and Acute:Chronic Workload Ratio
        (ACWR) per day. Use this to assess whether the athlete is building fitness, peaking, or
        accumulating too much fatigue.

        Recommended range: 4-8 weeks. Maximum: 90 days.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        MAX_DAYS = 90
        try:
            start = datetime.date.fromisoformat(start_date)
            end = datetime.date.fromisoformat(end_date)
        except ValueError as e:
            return f"Invalid date format: {e}. Use YYYY-MM-DD."

        days = (end - start).days + 1
        if days > MAX_DAYS:
            return f"Date range too large ({days} days). Maximum is {MAX_DAYS} days."
        if days < 1:
            return "end_date must be on or after start_date."

        trend = []
        current = start
        while current <= end:
            date_str = current.isoformat()
            try:
                data = garmin_client.get_training_status(date_str)
                if data:
                    status_data = (
                        data.get("mostRecentTrainingStatus", {})
                        .get("latestTrainingStatusData", {})
                    )
                    atl_dto = status_data.get("acuteTrainingLoadDTO", {})
                    vo2_data = data.get("mostRecentVO2Max", {}).get("generic", {})
                    entry: Dict[str, Any] = {"date": date_str}
                    atl = atl_dto.get("dailyTrainingLoadAcute")
                    ctl = atl_dto.get("dailyTrainingLoadChronic")
                    acwr = atl_dto.get("dailyAcuteChronicWorkloadRatio")
                    if atl is not None:
                        entry["atl"] = round(atl, 1)
                    if ctl is not None:
                        entry["ctl"] = round(ctl, 1)
                    if atl is not None and ctl is not None:
                        entry["tsb"] = round(ctl - atl, 1)
                    if acwr is not None:
                        entry["acwr"] = round(acwr, 2)
                    acwr_status = atl_dto.get("acwrStatus")
                    if acwr_status:
                        entry["acwr_status"] = acwr_status
                    ts = status_data.get("trainingStatusDTO", {})
                    ts_label = ts.get("trainingStatusCyclingFeedbackPhrase") or ts.get("trainingStatusFeedbackPhrase")
                    if ts_label:
                        entry["training_status"] = ts_label
                    vo2 = vo2_data.get("vo2MaxValue")
                    if vo2 is not None:
                        entry["vo2_max"] = round(vo2, 1)
                    if len(entry) > 1:  # has more than just date
                        trend.append(entry)
            except Exception:
                pass  # skip days with no data
            current += datetime.timedelta(days=1)

        if not trend:
            return f"No training load data found between {start_date} and {end_date}."

        return json.dumps({
            "start_date": start_date,
            "end_date": end_date,
            "days_with_data": len(trend),
            "trend": trend,
        }, indent=2)

    @app.tool()
    async def get_hrv_trend(start_date: str, end_date: str) -> str:
        """Get HRV (Heart Rate Variability) trend over a date range.

        Returns daily HRV values and weekly rolling averages. Single-day HRV is too noisy
        to act on — use this tool to identify baseline shifts that signal accumulated fatigue
        or recovery. A drop of >10ms from the 7-day baseline warrants reducing training load.

        Recommended range: 7-21 days. Maximum: 30 days.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        MAX_DAYS = 30
        try:
            start = datetime.date.fromisoformat(start_date)
            end = datetime.date.fromisoformat(end_date)
        except ValueError as e:
            return f"Invalid date format: {e}. Use YYYY-MM-DD."

        days = (end - start).days + 1
        if days > MAX_DAYS:
            return f"Date range too large ({days} days). Maximum is {MAX_DAYS} days."
        if days < 1:
            return "end_date must be on or after start_date."

        trend = []
        current = start
        while current <= end:
            date_str = current.isoformat()
            try:
                data = garmin_client.get_hrv_data(date_str)
                if data:
                    hrv_summary = data.get("hrvSummary", {})
                    entry: Dict[str, Any] = {"date": date_str}
                    last_night = hrv_summary.get("lastNight")
                    weekly_avg = hrv_summary.get("weeklyAvg")
                    status = hrv_summary.get("status")
                    feedback = hrv_summary.get("feedbackPhrase")
                    high_hrv = hrv_summary.get("lastNight5MinHigh")
                    if last_night is not None:
                        entry["last_night_avg_hrv_ms"] = round(last_night, 1)
                    if weekly_avg is not None:
                        entry["weekly_avg_hrv_ms"] = round(weekly_avg, 1)
                    if high_hrv is not None:
                        entry["last_night_5min_high_hrv_ms"] = round(high_hrv, 1)
                    if status:
                        entry["status"] = status
                    if feedback:
                        entry["feedback"] = feedback
                    if len(entry) > 1:
                        trend.append(entry)
            except Exception:
                pass
            current += datetime.timedelta(days=1)

        if not trend:
            return f"No HRV data found between {start_date} and {end_date}."

        # Compute 7-day rolling average from the collected data
        hrv_values = [e.get("last_night_avg_hrv_ms") for e in trend if e.get("last_night_avg_hrv_ms") is not None]
        rolling_avg = None
        if hrv_values:
            rolling_avg = round(sum(hrv_values) / len(hrv_values), 1)

        return json.dumps({
            "start_date": start_date,
            "end_date": end_date,
            "days_with_data": len(trend),
            "period_avg_hrv_ms": rolling_avg,
            "trend": trend,
        }, indent=2)

    @app.tool()
    async def get_vo2max_trend(start_date: str, end_date: str) -> str:
        """Get VO2 max trend over a date range.

        Returns daily VO2 max estimates from Garmin's FirstBeat algorithm. Use this to track
        whether training is producing fitness gains over weeks or months. Flat or declining
        VO2 max over 4+ weeks suggests insufficient training stimulus or overreaching.

        Note: VO2 max estimates are smoothed and update gradually — daily changes of <0.5
        are within normal noise. Focus on the 4-6 week trend direction.

        Recommended range: 4-12 weeks. Maximum: 90 days.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        MAX_DAYS = 90
        try:
            start = datetime.date.fromisoformat(start_date)
            end = datetime.date.fromisoformat(end_date)
        except ValueError as e:
            return f"Invalid date format: {e}. Use YYYY-MM-DD."

        days = (end - start).days + 1
        if days > MAX_DAYS:
            return f"Date range too large ({days} days). Maximum is {MAX_DAYS} days."
        if days < 1:
            return "end_date must be on or after start_date."

        trend = []
        last_vo2 = None
        current = start
        while current <= end:
            date_str = current.isoformat()
            try:
                data = garmin_client.get_training_status(date_str)
                if data:
                    vo2_data = data.get("mostRecentVO2Max", {}).get("generic", {})
                    vo2 = vo2_data.get("vo2MaxValue")
                    if vo2 is not None:
                        vo2_rounded = round(vo2, 1)
                        if vo2_rounded != last_vo2:  # deduplicate unchanged values
                            trend.append({"date": date_str, "vo2_max": vo2_rounded})
                            last_vo2 = vo2_rounded
            except Exception:
                pass
            current += datetime.timedelta(days=1)

        if not trend:
            return f"No VO2 max data found between {start_date} and {end_date}."

        first_vo2 = trend[0]["vo2_max"] if trend else None
        latest_vo2 = trend[-1]["vo2_max"] if trend else None
        change = round(latest_vo2 - first_vo2, 1) if (first_vo2 and latest_vo2) else None

        return json.dumps({
            "start_date": start_date,
            "end_date": end_date,
            "data_points": len(trend),
            "first_vo2_max": first_vo2,
            "latest_vo2_max": latest_vo2,
            "change": change,
            "trend": trend,
        }, indent=2)

    @app.tool()
    async def get_respiration_trend(start_date: str, end_date: str) -> str:
        """Get overnight respiration rate trend over a date range.

        Elevated resting respiration rate (compared to personal baseline) is an early
        warning sign for overreaching, illness, or poor recovery. Use this alongside HRV
        trend for a complete recovery picture.

        Recommended range: 7-21 days. Maximum: 30 days.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        MAX_DAYS = 30
        try:
            start = datetime.date.fromisoformat(start_date)
            end = datetime.date.fromisoformat(end_date)
        except ValueError as e:
            return f"Invalid date format: {e}. Use YYYY-MM-DD."

        days = (end - start).days + 1
        if days > MAX_DAYS:
            return f"Date range too large ({days} days). Maximum is {MAX_DAYS} days."
        if days < 1:
            return "end_date must be on or after start_date."

        trend = []
        current = start
        while current <= end:
            date_str = current.isoformat()
            try:
                data = garmin_client.get_respiration_data(date_str)
                if data:
                    entry: Dict[str, Any] = {"date": date_str}
                    avg_waking = data.get("avgWakingRespirationValue")
                    avg_sleep = data.get("avgSleepRespirationValue")
                    high_sleep = data.get("highestRespirationValue")
                    low_sleep = data.get("lowestRespirationValue")
                    if avg_waking is not None:
                        entry["avg_waking_breaths_per_min"] = round(avg_waking, 1)
                    if avg_sleep is not None:
                        entry["avg_sleep_breaths_per_min"] = round(avg_sleep, 1)
                    if high_sleep is not None:
                        entry["highest_breaths_per_min"] = round(high_sleep, 1)
                    if low_sleep is not None:
                        entry["lowest_breaths_per_min"] = round(low_sleep, 1)
                    if len(entry) > 1:
                        trend.append(entry)
            except Exception:
                pass
            current += datetime.timedelta(days=1)

        if not trend:
            return f"No respiration data found between {start_date} and {end_date}."

        sleep_values = [e["avg_sleep_breaths_per_min"] for e in trend if "avg_sleep_breaths_per_min" in e]
        avg_sleep_overall = round(sum(sleep_values) / len(sleep_values), 1) if sleep_values else None

        return json.dumps({
            "start_date": start_date,
            "end_date": end_date,
            "days_with_data": len(trend),
            "period_avg_sleep_breaths_per_min": avg_sleep_overall,
            "trend": trend,
        }, indent=2)

    return app
