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


def _as_snapshot_list(payload: Any) -> List[Dict[str, Any]]:
    """Normalize training-readiness payloads to a list of dicts."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def _recovery_state(remaining_hours: Optional[float], phrase: Any) -> str:
    """Map remaining hours / Garmin change phrase to a coarse state."""
    if phrase == "REACHED_ZERO" or remaining_hours == 0:
        return "recovered"
    if remaining_hours is None:
        return "unknown"
    if remaining_hours <= 6:
        return "nearly_recovered"
    if remaining_hours <= 24:
        return "recovering"
    return "not_recovered"


def _hours_from_recovery_minutes(minutes: Any, phrase: Any = None) -> Optional[float]:
    """Convert Garmin recovery minutes to hours.

    When ``recoveryTimeChangePhrase`` is ``REACHED_ZERO``, Garmin keeps the last
    assigned minute value even though the clock has drained to zero.
    """
    if phrase == "REACHED_ZERO":
        return 0.0
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
        return None
    if minutes < 0:
        return None
    return round(float(minutes) / 60.0, 1)


def _recovery_from_readiness(payload: Any, source: str) -> Optional[Dict[str, Any]]:
    """Build a recovery-time payload from training-readiness snapshots."""
    snapshots = _as_snapshot_list(payload)
    if not snapshots:
        return None
    latest = max(
        snapshots,
        key=lambda item: str(item.get("timestampLocal") or item.get("timestamp") or ""),
    )
    phrase = latest.get("recoveryTimeChangePhrase")
    hours = _hours_from_recovery_minutes(latest.get("recoveryTime"), phrase)
    if hours is None:
        return None
    score = latest.get("score")
    if score is None:
        score = latest.get("readinessScore")
    curated = {
        "remaining_hours": hours,
        "recovery_score": score,
        "state": _recovery_state(hours, phrase),
        "source": source,
        "date": latest.get("calendarDate"),
        "timestamp": latest.get("timestampLocal") or latest.get("timestamp"),
        "change_phrase": phrase,
        "level": latest.get("level") or latest.get("readinessLevel"),
    }
    return {key: value for key, value in curated.items() if value is not None}


def _activity_end_utc(activity: Dict[str, Any]) -> Optional[datetime.datetime]:
    """Estimate activity end time from list-search fields."""
    begin = activity.get("beginTimestamp")
    duration = activity.get("duration") or activity.get("elapsedDuration") or 0
    try:
        duration_s = float(duration)
    except (TypeError, ValueError):
        duration_s = 0.0
    if isinstance(begin, (int, float)) and not isinstance(begin, bool) and begin > 0:
        start = datetime.datetime.fromtimestamp(begin / 1000.0, tz=datetime.timezone.utc)
        return start + datetime.timedelta(seconds=duration_s)
    return None


def _recovery_from_recent_activities(
    client: Any, now: datetime.datetime
) -> Optional[Dict[str, Any]]:
    """Decay activity-assigned recoveryTime when Training Readiness is absent."""
    get_activities = getattr(client, "get_activities", None)
    if not callable(get_activities):
        return None
    try:
        items = get_activities(0, 20)
    except Exception:
        return None
    if not isinstance(items, list):
        return None

    best: Optional[Dict[str, Any]] = None
    best_end: Optional[datetime.datetime] = None
    for item in items:
        if not isinstance(item, dict):
            continue
        recovery = item.get("recoveryTime")
        if isinstance(recovery, bool) or not isinstance(recovery, (int, float)):
            continue
        end = _activity_end_utc(item)
        if end is None:
            continue
        elapsed_min = max(0.0, (now - end).total_seconds() / 60.0)
        remaining_hours = round(max(0.0, float(recovery) - elapsed_min) / 60.0, 1)
        if best_end is None or end > best_end:
            best_end = end
            best = {
                "remaining_hours": remaining_hours,
                "recovery_score": None,
                "state": _recovery_state(remaining_hours, None),
                "source": "recent_activity",
                "activity_id": item.get("activityId"),
                "activity_name": item.get("activityName"),
            }
    if best is None:
        return None
    return {key: value for key, value in best.items() if value is not None}


def _safe_call(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception:
        return None


def _collect_recovery_time(
    client: Any, date_str: str, now: Optional[datetime.datetime] = None
) -> Dict[str, Any]:
    """Resolve daily recovery-time remaining from readiness, then activities."""
    readiness = _safe_call(getattr(client, "get_training_readiness", None), date_str)
    curated = _recovery_from_readiness(readiness, "training_readiness")
    if curated:
        curated.setdefault("date", date_str)
        return curated

    morning = _safe_call(
        getattr(client, "get_morning_training_readiness", None), date_str
    )
    curated = _recovery_from_readiness(morning, "morning_training_readiness")
    if curated:
        curated.setdefault("date", date_str)
        return curated

    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    curated = _recovery_from_recent_activities(client, now)
    if curated:
        curated.setdefault("date", date_str)
        return curated

    return {
        "remaining_hours": None,
        "recovery_score": None,
        "state": "unavailable",
        "date": date_str,
        "message": (
            "No recovery time remaining found for this date. Training Readiness "
            "snapshots were empty and recent activities did not include recoveryTime. "
            "Some devices (for example Forerunner 255) show recovery on-device without "
            "publishing a Connect Training Readiness feed."
        ),
    }


def _extract_sleep_summary(sleep_data: Dict[str, Any]) -> Dict[str, Any]:
    """Curate a single night's raw Garmin sleep payload down to essential metrics.

    Shared by get_sleep_summary (single night) and get_sleep_summary_range
    (multi-night) so both endpoints stay in sync.
    """
    summary: Dict[str, Any] = {}

    # Extract data from dailySleepDTO if available
    daily_sleep = sleep_data.get('dailySleepDTO', {})
    if daily_sleep:
        # Sleep duration and timing
        summary['sleep_seconds'] = daily_sleep.get('sleepTimeSeconds')
        summary['nap_seconds'] = daily_sleep.get('napTimeSeconds')
        summary['sleep_start'] = daily_sleep.get('sleepStartTimestampGMT')
        summary['sleep_end'] = daily_sleep.get('sleepEndTimestampGMT')

        # Sleep score and quality. Guard each level with `or {}`: a sleep record
        # can exist while `sleepScores` (or its `overall` block) is an explicit
        # null, which would otherwise raise "'NoneType' object has no attribute 'get'".
        sleep_scores = daily_sleep.get('sleepScores') or {}
        overall_score = sleep_scores.get('overall') or {}
        summary['sleep_score'] = overall_score.get('value')
        summary['sleep_score_qualifier'] = overall_score.get('qualifierKey')

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

    # Calculate sleep phase percentages if total sleep time is available.
    # The phase keys above are written unconditionally, so a `.get(key, 0)`
    # default never fires: Garmin can report a total duration while leaving the
    # breakdown as an explicit null, which raised "unsupported operand type(s)
    # for /: 'NoneType' and 'int'". Skip unmeasured phases instead of defaulting
    # them to 0, so an absent breakdown is not reported as 0%.
    total_sleep = summary.get('sleep_seconds')
    if total_sleep and total_sleep > 0:
        for phase_key, percent_key in (
            ('deep_sleep_seconds', 'deep_sleep_percent'),
            ('light_sleep_seconds', 'light_sleep_percent'),
            ('rem_sleep_seconds', 'rem_sleep_percent'),
        ):
            phase_seconds = summary.get(phase_key)
            if phase_seconds is not None:
                summary[percent_key] = round((phase_seconds / total_sleep) * 100, 1)

    # Convert sleep duration to hours for convenience
    if total_sleep:
        summary['sleep_hours'] = round(total_sleep / 3600, 2)

    # Remove None values
    return {k: v for k, v in summary.items() if v is not None}


def _iter_date_chunks(
    start: datetime.date, end: datetime.date, max_days: int
) -> List[tuple]:
    """Split [start, end] into consecutive inclusive windows of at most max_days."""
    chunks = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(end, chunk_start + datetime.timedelta(days=max_days - 1))
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + datetime.timedelta(days=1)
    return chunks


def _fetch_daily_calories(
    client: Any, start: datetime.date, end: datetime.date
) -> Dict[str, Dict[str, Any]]:
    """Fetch per-day total calories from Garmin, chunked under its 28-day cap.

    Returns a dict keyed by ISO date string; a date absent from the result had
    no data in Garmin (future date, or before the account existed).
    """
    STATS_CHUNK_DAYS = 28
    by_date: Dict[str, Dict[str, Any]] = {}
    for chunk_start, chunk_end in _iter_date_chunks(start, end, STATS_CHUNK_DAYS):
        url = f"/usersummary-service/stats/daily/{chunk_start.isoformat()}/{chunk_end.isoformat()}"
        resp = client.connectapi(url, params={"statsType": "CALORIES"})
        for entry in (resp or {}).get("values") or []:
            date_str = entry.get("calendarDate")
            if date_str:
                by_date[date_str] = entry.get("values") or {}
    return by_date


def _fetch_daily_intake(
    client: Any, start: datetime.date, end: datetime.date
) -> Dict[str, Dict[str, Any]]:
    """Fetch per-day logged calories and item count from Garmin's nutrition range endpoint.

    Returns a dict keyed by ISO date string; a date with item_count 0 had no
    logged food.
    """
    resp = client.connectapi(
        "/nutrition-service/food/logs/range",
        params={"startDate": start.isoformat(), "endDate": end.isoformat()},
    )
    by_date: Dict[str, Dict[str, Any]] = {}
    for day in (resp or {}).get("dailyNutritionSummaries") or []:
        date_str = day.get("mealDate")
        if not date_str:
            continue
        content = day.get("dailyNutritionContent") or {}
        item_count = sum(
            len(meal.get("loggedFoods") or [])
            for meal in (day.get("mealDetails") or [])
        )
        by_date[date_str] = {"calories": content.get("calories"), "item_count": item_count}
    return by_date


def _linear_fit(xs: List[float], ys: List[float]) -> Optional[Dict[str, float]]:
    """Ordinary least-squares fit of ys against xs. Returns slope, intercept,
    and (when there are more than 2 points) the standard error of the slope.

    Used to derive a body-composition trend from all qualifying readings in
    a window instead of just the earliest and latest -- the latter is fully
    determined by two individual measurements and inherits all their noise.
    """
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x

    se_slope = None
    if n > 2:
        sse = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
        dof = n - 2
        if dof > 0:
            se_slope = (sse / dof / sxx) ** 0.5

    return {"slope": slope, "intercept": intercept, "se_slope": se_slope, "n": n}


def _body_comp_gate_failure(xs: List[float]) -> Optional[str]:
    """Check whether body-composition readings are well-conditioned enough to
    fit a trend from, beyond just having 2+ of them.

    Two failure modes, either of which makes a fitted trend unreliable even
    though the arithmetic still "works":
    - The readings don't span enough time for a real trend to be
      distinguishable from ordinary day-to-day water-weight noise.
    - Most of the fit's statistical leverage sits in one tight cluster of
      readings taken within ~48h of each other. A close, low-noise pair
      inside that cluster can make the fit's residual (and therefore its
      reported uncertainty) look small, while the slope is still mostly an
      extrapolation from that cluster to wherever the other reading(s) are --
      exactly the failure mode a naive `n > 2` check misses.
    """
    n = len(xs)
    MIN_SPAN_DAYS = 21
    span = max(xs) - min(xs)
    if span < MIN_SPAN_DAYS:
        return (
            f"readings span only {span} days; need at least {MIN_SPAN_DAYS} to "
            f"separate a real trend from water-weight noise"
        )

    mean_x = sum(xs) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        return "all readings fall on the same day"

    leverages = [1 / n + (x - mean_x) ** 2 / sxx for x in xs]
    total_leverage = sum(leverages)  # == 2 for a 2-parameter fit

    # Cluster readings whose day-offsets sit within 2 days of a neighbor.
    order = sorted(range(n), key=lambda i: xs[i])
    clusters: List[List[int]] = []
    for idx in order:
        if clusters and xs[idx] - xs[clusters[-1][-1]] <= 2:
            clusters[-1].append(idx)
        else:
            clusters.append([idx])

    max_cluster_leverage = max(sum(leverages[i] for i in c) for c in clusters)
    if max_cluster_leverage > total_leverage / 2:
        return (
            f"a single cluster of readings within 48h of each other accounts for "
            f"{max_cluster_leverage / total_leverage:.0%} of the fit's statistical "
            f"leverage -- the trend across the full window is not reliably "
            f"distinguishable from noise in that cluster alone"
        )
    return None


def register_tools(app):
    """Register all health and wellness tools with the MCP server app"""

    @app.tool()
    async def get_stats(date: str) -> str:
        """Get daily activity stats with curated essential metrics

        Returns a summary of daily health and activity data including steps,
        calories, heart rate, stress, body battery, and sleep metrics.

        Note: bmr_calories is Garmin's bmrKilocalories field -- per Garmin's
        own documentation this is RMR (BMR plus sedentary-to-light movement),
        not true basal metabolic rate. For the current date, all calorie and
        duration fields are a partial-day accumulator (elapsed hours so far),
        not a full-day total.

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
    async def get_stats_range(start_date: str, end_date: str) -> str:
        """Get lightweight per-day calorie and step totals for a date range.

        Returns total/active/resting calories and steps for each day between
        start_date and end_date, inclusive, instead of the full get_stats
        payload per day. Use this for multi-day analysis (e.g. comparing
        measured intake/expenditure against Garmin's calorie model) instead
        of calling get_stats once per day.

        resting_calories is Garmin's bmrKilocalories field -- per Garmin's
        own documentation this is RMR (BMR plus sedentary-to-light movement),
        not true basal metabolic rate.

        is_partial is true for the current date, whose accumulator only
        covers elapsed hours so far -- treating it as a full day will
        overstate a mean. Days with no device data (e.g. before the account
        existed, or future dates within the range) return has_data: false
        and null values rather than zeros, so a missing day never silently
        enters an average as zero.

        Maximum range: 28 days per call (Garmin's own limit for this endpoint).

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        MAX_DAYS = 28
        try:
            start = datetime.date.fromisoformat(start_date)
            end = datetime.date.fromisoformat(end_date)
        except ValueError as e:
            return f"Invalid date format: {e}. Use YYYY-MM-DD."

        days_requested = (end - start).days + 1
        if days_requested < 1:
            return "end_date must be on or after start_date."
        if days_requested > MAX_DAYS:
            return f"Date range too large ({days_requested} days). Maximum is {MAX_DAYS} days."

        url = f"/usersummary-service/stats/daily/{start_date}/{end_date}"
        try:
            calories_resp = garmin_client.connectapi(url, params={"statsType": "CALORIES"})
            steps_resp = garmin_client.connectapi(url, params={"statsType": "STEPS"})
        except Exception as e:
            return f"Error retrieving stats range: {str(e)}"

        calories_by_date = {
            entry.get("calendarDate"): entry.get("values") or {}
            for entry in (calories_resp or {}).get("values") or []
        }
        steps_by_date = {
            entry.get("calendarDate"): entry.get("values") or {}
            for entry in (steps_resp or {}).get("values") or []
        }

        today = datetime.date.today().isoformat()
        daily = []
        current = start
        while current <= end:
            date_str = current.isoformat()
            cal = calories_by_date.get(date_str)
            steps = steps_by_date.get(date_str)
            daily.append({
                "date": date_str,
                "total_calories": (cal or {}).get("totalCalories"),
                "active_calories": (cal or {}).get("activeCalories"),
                "resting_calories": (cal or {}).get("restingCalories"),
                "steps": (steps or {}).get("totalSteps"),
                "has_data": cal is not None or steps is not None,
                "is_partial": date_str == today,
            })
            current += datetime.timedelta(days=1)

        if not any(d["has_data"] for d in daily):
            return f"No stats found between {start_date} and {end_date}."

        return json.dumps({
            "start_date": start_date,
            "end_date": end_date,
            "days": daily,
        }, indent=2)

    @app.tool()
    async def get_energy_balance(start_date: str, end_date: str) -> str:
        """Estimate a composition-derived TDEE from logged intake, Garmin's
        claimed expenditure, and body-composition change over a date range.

        Combines three signals Garmin exposes separately -- mean logged intake
        (nutrition log), mean claimed expenditure (Garmin's total_calories from
        daily stats), and a body-composition trend (weight and body-fat % from
        smart-scale readings) -- into composition_derived_tdee, an estimate
        independent of Garmin's calorie model:
        composition_derived_tdee = mean_intake + implied_deficit, where
        implied_deficit comes from the fitted rate of fat and fat-free mass
        change, using the standard energy-density approximations
        7700 kcal/kg fat and 1800 kcal/kg fat-free mass. These are body
        composition literature estimates, not Garmin-provided or measured
        values -- treat the result as a rough estimate, not a lab measurement.
        difference_kcal_per_day (Garmin's claim minus this estimate) has at
        least two explanations -- Garmin overestimating expenditure, or
        under-logged intake, which is common -- and this tool cannot
        distinguish between them.

        The body-composition trend is fit by least-squares regression across
        every qualifying scale reading in range, not just the first and last --
        an endpoint-to-endpoint difference is fully determined by two
        individual measurements and inherits all of their noise (a 0.5%
        body-fat wobble, well within normal scale repeatability, can swing a
        two-point estimate by over a hundred kcal/day). weight_change_kg,
        fat_mass_change_kg, and lean_mass_change_kg in body_composition are
        all fitted values (the fitted line's value at the last reading's day
        offset minus its value at the first), not raw differences between the
        observed_first_*/observed_last_* fields, which are the actual
        readings for reference/sanity-checking, not what the change figures
        are computed from -- fitted_first_weight_kg/fitted_last_weight_kg are
        also included so fitted_last_weight_kg - fitted_first_weight_kg can be
        checked against weight_change_kg directly.

        implied_deficit_kcal_per_day is the slope of a single regression: for
        each reading, compute -(fat_kg * 7700 + lean_kg * 1800) where
        lean_kg = weight_kg - fat_kg, then fit that value against day offset.
        This is mathematically identical to combining separately-fit fat and
        lean slopes, but its standard error -- implied_deficit_uncertainty_kcal_per_day,
        returned whenever more than 2 readings are used -- is that single
        fit's own residual standard error, which is well-defined and
        reproducible from the raw readings alone (fat_kg and lean_kg are both
        derived from the same weight/body-fat pair per reading, so treating
        their separately-fit slopes' errors as independent and combining them
        after the fact, instead of fitting the combined series directly,
        would not be reproducible from outside without also knowing that
        assumption). Treat a deficit within roughly one standard error of
        zero as indistinguishable from no change.

        Below a minimum span, or when most of the fit's statistical leverage
        sits in one tight cluster of readings taken within about 48 hours of
        each other, the fitted trend is not reliable even with 3+ readings --
        a close, low-noise pair inside that cluster makes the fit look
        precise while the slope is still mostly an extrapolation from that
        cluster to wherever the other reading(s) fall. When this gate fails,
        body_composition is still returned (it's just the fitted line, always
        checkable against the observed readings) but derived is omitted and
        gate_note explains which condition failed. Prefer windows of 3+ weeks
        with several readings spread across them, not clustered at one end.

        Short windows are also dominated by water/glycogen shifts (which
        carry little caloric weight, unlike true lean tissue), which biases
        the fat-free-mass term; see assumptions.note.

        Exclusions applied automatically, matching get_nutrition_summary_between_dates
        and get_stats_range:
        - Intake: days with no logged food (item_count 0) are excluded from
          the mean, not counted as zero. Days with a low but nonzero
          item_count remain included (excluding them requires judgment this
          tool doesn't make unilaterally) but are called out in
          low_item_count_dates so low-confidence days are visible rather than
          silently blended into the mean. A window with many such days (or
          many item_count-0 days) biases composition_derived_tdee downward --
          check days_excluded_unlogged and low_item_count_dates before
          comparing two windows against each other.
        - Expenditure: days with no device data, and the current (partial)
          day, are excluded from the mean.
        - Body composition: only scale readings with both weight and body-fat
          percentage are used (skips weight-only manual entries).

        Needs at least 2 qualifying body-composition readings to derive a
        trend, and to pass the span/leverage gate above to derive a TDEE from
        it. With 0 or 1 readings, returns the intake/expenditure means and
        whatever body composition data exists, but omits the derived TDEE.

        Maximum range: 61 days per call.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        MAX_DAYS = 61
        FAT_KCAL_PER_KG = 7700
        LEAN_MASS_KCAL_PER_KG = 1800
        LOW_ITEM_COUNT_THRESHOLD = 3
        try:
            start = datetime.date.fromisoformat(start_date)
            end = datetime.date.fromisoformat(end_date)
        except ValueError as e:
            return f"Invalid date format: {e}. Use YYYY-MM-DD."

        days_requested = (end - start).days + 1
        if days_requested < 1:
            return "end_date must be on or after start_date."
        if days_requested > MAX_DAYS:
            return f"Date range too large ({days_requested} days). Maximum is {MAX_DAYS} days."

        try:
            intake_by_date = _fetch_daily_intake(garmin_client, start, end)
            expenditure_by_date = _fetch_daily_calories(garmin_client, start, end)
            body_comp = garmin_client.get_body_composition(start_date, end_date)
        except Exception as e:
            return f"Error retrieving energy balance data: {str(e)}"

        today = datetime.date.today().isoformat()

        logged_days = [
            (date_str, v["calories"]) for date_str, v in intake_by_date.items()
            if v.get("item_count", 0) > 0 and v.get("calories") is not None
        ]
        intake_days_included = len(logged_days)
        mean_intake = (
            sum(c for _, c in logged_days) / intake_days_included if intake_days_included else None
        )
        low_item_count_dates = sorted(
            date_str for date_str, v in intake_by_date.items()
            if 0 < v.get("item_count", 0) < LOW_ITEM_COUNT_THRESHOLD
        )

        expenditure_values = [
            v["totalCalories"] for date_str, v in expenditure_by_date.items()
            if date_str != today and v.get("totalCalories") is not None
        ]
        expenditure_days_included = len(expenditure_values)
        mean_expenditure = (
            sum(expenditure_values) / expenditure_days_included if expenditure_days_included else None
        )

        # Only readings with both weight and body fat % are usable.
        readings = []
        for entry in (body_comp or {}).get("dateWeightList") or []:
            date_str = entry.get("calendarDate")
            weight_g = entry.get("weight")
            body_fat_pct = entry.get("bodyFat")
            if date_str and weight_g is not None and body_fat_pct is not None:
                readings.append((date_str, weight_g, body_fat_pct))
        readings.sort(key=lambda r: r[0])

        result: Dict[str, Any] = {
            "start_date": start_date,
            "end_date": end_date,
            "days_requested": days_requested,
            "intake": {
                "mean_calories_per_day": round(mean_intake, 1) if mean_intake is not None else None,
                "days_included": intake_days_included,
                "days_excluded_unlogged": days_requested - intake_days_included,
                "low_item_count_dates": low_item_count_dates,
            },
            "expenditure_garmin": {
                "mean_total_calories_per_day": round(mean_expenditure, 1) if mean_expenditure is not None else None,
                "days_included": expenditure_days_included,
                "days_excluded": days_requested - expenditure_days_included,
            },
        }

        if len(readings) >= 2:
            first_date = readings[0][0]
            last_date = readings[-1][0]
            day_zero = datetime.date.fromisoformat(first_date)
            xs = [(datetime.date.fromisoformat(d) - day_zero).days for d, _, _ in readings]
            observed_weight_kg = [w / 1000 for _, w, _ in readings]
            observed_body_fat_pct = [bf for _, _, bf in readings]
            fat_kg_series = [w * bf / 100 for w, bf in zip(observed_weight_kg, observed_body_fat_pct)]
            span_days = xs[-1] - xs[0]

            weight_fit = _linear_fit(xs, observed_weight_kg)
            fat_fit = _linear_fit(xs, fat_kg_series)

            result["body_composition"] = {
                "readings_used": len(readings),
                "first_reading_date": first_date,
                "last_reading_date": last_date,
                "days_between_readings": span_days,
                "observed_first_weight_kg": round(observed_weight_kg[0], 2),
                "observed_last_weight_kg": round(observed_weight_kg[-1], 2),
                "observed_first_body_fat_percent": observed_body_fat_pct[0],
                "observed_last_body_fat_percent": observed_body_fat_pct[-1],
            }

            gate_failure = _body_comp_gate_failure(xs)

            if weight_fit and fat_fit and span_days >= 1:
                fat_change_kg = fat_fit["slope"] * span_days
                weight_change_kg = weight_fit["slope"] * span_days
                lean_change_kg = weight_change_kg - fat_change_kg
                result["body_composition"].update({
                    "fitted_first_weight_kg": round(weight_fit["intercept"] + weight_fit["slope"] * xs[0], 2),
                    "fitted_last_weight_kg": round(weight_fit["intercept"] + weight_fit["slope"] * xs[-1], 2),
                    "weight_change_kg": round(weight_change_kg, 2),
                    "fat_mass_change_kg": round(fat_change_kg, 3),
                    "lean_mass_change_kg": round(lean_change_kg, 3),
                })

                if gate_failure:
                    result["gate_note"] = f"Derived TDEE suppressed: {gate_failure}"
                elif mean_intake is not None:
                    # implied_deficit is the slope of a single regression of the
                    # per-reading implied-deficit value against day offset --
                    # not two slopes combined after the fact -- so its standard
                    # error is that one fit's own residual SE, not an assumed-
                    # independent combination of two correlated fits' errors
                    # (fat_kg and lean_kg are both derived from the same
                    # weight/body-fat pair per reading, so they are not
                    # independent).
                    deficit_series = [
                        -(fat_kg * FAT_KCAL_PER_KG + (w - fat_kg) * LEAN_MASS_KCAL_PER_KG)
                        for w, fat_kg in zip(observed_weight_kg, fat_kg_series)
                    ]
                    deficit_fit = _linear_fit(xs, deficit_series)
                    implied_deficit = deficit_fit["slope"]
                    measured_tdee = mean_intake + implied_deficit

                    derived: Dict[str, Any] = {
                        "implied_deficit_kcal_per_day": round(implied_deficit, 1),
                        "implied_deficit_uncertainty_kcal_per_day": (
                            round(deficit_fit["se_slope"], 1) if deficit_fit["se_slope"] is not None else None
                        ),
                        "composition_derived_tdee_kcal_per_day": round(measured_tdee, 1),
                        "garmin_tdee_kcal_per_day": (
                            round(mean_expenditure, 1) if mean_expenditure is not None else None
                        ),
                        "difference_kcal_per_day": (
                            round(mean_expenditure - measured_tdee, 1)
                            if mean_expenditure is not None else None
                        ),
                    }
                    if deficit_fit["se_slope"] is None:
                        derived["uncertainty_note"] = (
                            "Only 2 readings -- no residual variance to estimate uncertainty from. "
                            "More readings in range would let this be quantified."
                        )

                    derived["note"] = (
                        "composition_derived_tdee is a model estimate, not ground truth. "
                        "difference_kcal_per_day is not attributable to Garmin's model or to "
                        "under-logged intake without independent validation of either. A window "
                        "with a high days_excluded_unlogged count biases this estimate downward, "
                        "since under-logged days pull down the intake mean it's built from."
                    )
                    result["derived"] = derived
                    result["assumptions"] = {
                        "fat_kcal_per_kg": FAT_KCAL_PER_KG,
                        "lean_mass_kcal_per_kg": LEAN_MASS_KCAL_PER_KG,
                        "note": "Standard energy-density approximations from body-composition "
                                "literature, not Garmin-provided or measured constants. Fat-free "
                                "mass change over short windows is often water/glycogen rather than "
                                "structural tissue, which carries little caloric weight -- this "
                                "biases lean_mass_kcal_per_kg high for short windows.",
                    }
                else:
                    result["derived_note"] = (
                        "Body composition trend fit but no qualifying intake days to derive a TDEE from."
                    )
            else:
                result["derived_note"] = (
                    "Body composition readings found but a trend could not be fit "
                    "(readings span zero days)."
                )
        elif readings:
            result["body_composition_note"] = (
                f"Only 1 qualifying body-composition reading (weight + body fat %) in range, "
                f"on {readings[0][0]}. Need at least 2 to fit a trend."
            )
        else:
            result["body_composition_note"] = (
                "No body-composition readings with both weight and body fat % in range."
            )

        if intake_days_included == 0 and expenditure_days_included == 0 and not readings:
            return f"No usable data found between {start_date} and {end_date}."

        return json.dumps(result, indent=2)

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

                for event in (day.get('bodyBatteryActivityEvent') or []):
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

            summary = _extract_sleep_summary(sleep_data)

            return json.dumps(summary, indent=2)
        except Exception as e:
            return f"Error retrieving sleep summary: {str(e)}"

    @app.tool()
    async def get_sleep_summary_range(start_date: str, end_date: str) -> str:
        """Get lightweight sleep summaries for every night in a date range.

        Returns the same curated metrics as get_sleep_summary (sleep score, duration,
        sleep stages, HRV, resting HR, etc.) for each night between start_date and
        end_date, inclusive. Use this instead of calling get_sleep_summary once per
        night when analyzing sleep trends over weeks or months.

        Note: Garmin Connect does not expose a native range endpoint for sleep, so
        this makes one request per night internally. Recommended range: up to a
        few weeks for quick checks. Maximum: 90 nights per call.

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

        nights = []
        current = start
        while current <= end:
            date_str = current.isoformat()
            try:
                sleep_data = garmin_client.get_sleep_data(date_str)
                if sleep_data:
                    entry = {"date": date_str}
                    entry.update(_extract_sleep_summary(sleep_data))
                    if len(entry) > 1:  # has more than just date
                        nights.append(entry)
            except Exception:
                pass  # skip nights with no data / transient errors
            current += datetime.timedelta(days=1)

        if not nights:
            return f"No sleep data found between {start_date} and {end_date}."

        return json.dumps({
            "start_date": start_date,
            "end_date": end_date,
            "nights_requested": days,
            "nights_returned": len(nights),
            "nights": nights,
        }, indent=2)

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
    async def get_lifestyle_logging_data(date: str) -> str:
        """Get lifestyle logging data for a specific date

        Returns lifestyle logging data which allows users to track behaviors
        and their impact on health metrics.

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            data = garmin_client.get_lifestyle_logging_data(date)
            if not data:
                return f"No lifestyle logging data found for {date}"

            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error retrieving lifestyle logging data: {str(e)}"

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
                "recovery_time_hours": round(readiness.get('recoveryTime', 0) / 60, 1) if readiness.get('recoveryTime') is not None else None,
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

    @app.tool()
    async def get_recovery_time_remaining(date: str = "") -> str:
        """Get remaining recovery time in hours for a day.

        This is the Firstbeat recovery clock shown on-device, distinct from
        Training Readiness score (missing on some devices such as Forerunner 255)
        and from Training Status ACWR. When Training Readiness snapshots exist
        they are the current remaining value. Otherwise the tool decays
        recoveryTime assigned on recent activities.

        Args:
            date: Date in YYYY-MM-DD format. Defaults to today.
        """
        try:
            date = (date or "").strip() or datetime.date.today().isoformat()
            datetime.datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return f"Invalid date {date!r}. Use YYYY-MM-DD."

        try:
            curated = _collect_recovery_time(garmin_client, date)
            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving recovery time remaining: {str(e)}"

    return app
