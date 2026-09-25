"""Fetch and normalise the Garmin data the coach needs.

Everything returned here is a compact, JSON-friendly dict so the state file
stays small and the rest of the coach never touches raw Garmin payloads.
"""

import base64
import sys
from datetime import date

from garminconnect import Garmin

SPORTS = {
    "run": ("running", "trail_running", "treadmill_running", "track_running", "indoor_running", "virtual_run", "street_running"),
    "bike": ("cycling", "road_biking", "indoor_cycling", "virtual_ride", "mountain_biking", "gravel_cycling", "e_bike_fitness", "cyclocross"),
    "strength": ("strength_training", "hiit", "indoor_cardio", "fitness_equipment", "pilates", "yoga"),
    "row": ("indoor_rowing", "rowing"),
    "swim": ("lap_swimming", "open_water_swimming", "swimming"),
    "sail": ("sailing", "sailing_v2", "boating", "windsurfing", "kitesurfing", "wingfoiling"),
}


def sport_of(type_key: str | None) -> str:
    for sport, keys in SPORTS.items():
        if type_key in keys:
            return sport
    return "other"


def login(tokens: str) -> Garmin:
    """Log in from the token JSON (raw or base64) produced by ``garmin-mcp-auth``."""
    tokens = tokens.strip()
    if not tokens.startswith("{"):
        tokens = base64.b64decode(tokens).decode("utf-8")
    garmin = Garmin()
    garmin.login(tokens)
    return garmin


def dump_tokens(garmin: Garmin) -> str:
    """Current (possibly refreshed) tokens, to persist for the next run."""
    return garmin.client.dumps()


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as err:  # Garmin endpoints fail independently; keep going.
        print(f"[garmin] {getattr(fn, '__name__', fn)} failed: {str(err)[:200]}", file=sys.stderr)
        return None


def normalise_activity(raw: dict) -> dict:
    start = (raw.get("startTimeLocal") or "").replace("T", " ")
    type_key = (raw.get("activityType") or {}).get("typeKey")
    zones = [raw.get(f"hrTimeInZone_{i}") for i in range(1, 6)]
    act = {
        "id": str(raw.get("activityId")),
        "name": raw.get("activityName") or "",
        "type": type_key,
        "sport": sport_of(type_key),
        "start": start[:19],
        "date": start[:10],
        "duration_s": raw.get("movingDuration") or raw.get("duration") or 0,
        "elapsed_s": raw.get("duration") or 0,
        "distance_m": raw.get("distance") or 0,
        "avg_hr": raw.get("averageHR"),
        "max_hr": raw.get("maxHR"),
        "avg_speed": raw.get("averageSpeed"),
        "avg_power": raw.get("avgPower") or raw.get("averagePower"),
        "elevation_m": raw.get("elevationGain"),
        "calories": raw.get("calories"),
        "training_load": raw.get("activityTrainingLoad"),
        "te_aerobic": raw.get("aerobicTrainingEffect"),
        "te_anaerobic": raw.get("anaerobicTrainingEffect"),
        "cadence": raw.get("averageRunningCadenceInStepsPerMinute"),
        "hr_zones_s": zones if any(z for z in zones) else None,
    }
    return {k: v for k, v in act.items() if v is not None}


def fetch_activities(garmin: Garmin, start: date, end: date) -> list[dict]:
    raw = _safe(garmin.get_activities_by_date, start.isoformat(), end.isoformat()) or []
    return [normalise_activity(a) for a in raw if a.get("activityId")]


def _first(obj):
    if isinstance(obj, list):
        return obj[0] if obj else {}
    return obj or {}


def fetch_wellness(garmin: Garmin, day: date) -> dict:
    iso = day.isoformat()
    out: dict = {"date": iso}

    hrv = _safe(garmin.get_hrv_data, iso) or {}
    summary = hrv.get("hrvSummary") or {}
    baseline = summary.get("baseline") or {}
    out.update({
        "hrv": summary.get("lastNightAvg"),
        "hrv_week": summary.get("weeklyAvg"),
        "hrv_status": summary.get("status"),
        "hrv_low": baseline.get("balancedLow"),
        "hrv_high": baseline.get("balancedUpper"),
    })

    sleep = (_safe(garmin.get_sleep_data, iso) or {}).get("dailySleepDTO") or {}
    scores = sleep.get("sleepScores") or {}
    out["sleep_score"] = (scores.get("overall") or {}).get("value")
    out["sleep_h"] = round(sleep["sleepTimeSeconds"] / 3600, 1) if sleep.get("sleepTimeSeconds") else None

    readiness = _safe(garmin.get_training_readiness, iso)
    if isinstance(readiness, list):
        readiness = max(readiness, key=lambda r: r.get("timestamp") or "", default={})
    readiness = readiness or {}
    out["readiness"] = readiness.get("score")
    out["readiness_level"] = readiness.get("level")

    summary = _safe(garmin.get_user_summary, iso) or {}
    out["rhr"] = summary.get("restingHeartRate")
    out["body_battery"] = summary.get("bodyBatteryHighestValue")
    return {k: v for k, v in out.items() if v is not None}


def fetch_metrics(garmin: Garmin, day: date) -> dict:
    out: dict = {}
    lt = _safe(garmin.get_lactate_threshold, latest=True) or {}
    shr = lt.get("speed_and_heart_rate") or {}
    if shr.get("speed"):
        # Garmin reports seconds per metre (inverse pace), same as the
        # get_lactate_threshold tool; values above 1 are already m/s.
        raw = shr["speed"]
        mps = raw if raw > 1 else 1 / raw
        if 2.5 < mps < 7:
            out["lt_pace"] = round(1000 / mps)
    if shr.get("heartRate"):
        out["lt_hr"] = shr["heartRate"]
    maxm = _first(_safe(garmin.get_max_metrics, day.isoformat()))
    generic = (maxm or {}).get("generic") or {}
    if generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxValue"):
        out["vo2max"] = generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxValue")
    return out
