"""FastAPI dashboard for Garmin Connect + Coach data.

Reuses the same OAuth tokens the garmin MCP server authenticates with (see
garmin_mcp.token_utils), so it requires no separate login step as long as
`garmin-mcp-auth` has already been run once. Coach plan data (today's session,
weekly volume target) is read directly from the Coach Memory MCP's local
SQLite database -- read-only, no need to speak the MCP protocol for that.
"""
import datetime
import json
import sqlite3
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from garminconnect import Garmin

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from garmin_mcp import token_utils  # noqa: E402

app = FastAPI(title="Garmin Dashboard")

_client: Garmin | None = None

COACH_DB_PATH = Path.home() / ".local" / "share" / "coach" / "memory.db"

# Weekly km targets for the current 4-week Base block (set 19 sep 2026),
# re-bucketed into Monday-Sunday calendar weeks (21 sep 2026) -- summed from
# the actual planned distance of whichever sessions fall in each window,
# since the plan's own sessions are anchored Sun(long)/Tue/Thu and don't line
# up 1:1 with ISO calendar weeks.
BLOCK_WEEK_TARGETS = [
    ("2026-09-14", "2026-09-20", 8),
    ("2026-09-21", "2026-09-27", 19),
    ("2026-09-28", "2026-10-04", 21),
    ("2026-10-05", "2026-10-11", 18),
    ("2026-10-12", "2026-10-18", 9),
]

BIOMECHANICS_BOARD_PATH = Path.home() / ".local" / "share" / "coach" / "biomechanics_board.json"


def get_client() -> Garmin:
    """Lazily log in once and reuse the same Garmin client for every request."""
    global _client
    if _client is None:
        token_path = token_utils.get_token_path()
        client = Garmin(is_cn=False)
        try:
            client.login(token_path)
        except Exception as exc:
            raise HTTPException(
                502,
                f"Garmin login failed using tokens at {token_path}: {exc}. "
                "Run 'garmin-mcp-auth' to (re)authenticate.",
            ) from exc
        _client = client
    return _client


def get_coach_db() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{COACH_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _plan_for_date(date: str) -> dict | None:
    if not COACH_DB_PATH.exists():
        return None
    with get_coach_db() as conn:
        row = conn.execute(
            "SELECT id, planned_at, description, notes, status, activity_id "
            "FROM plan WHERE planned_at = ? ORDER BY id LIMIT 1",
            (date,),
        ).fetchone()
    return dict(row) if row else None


def _next_plan_after(date: str) -> dict | None:
    """Soonest pending plan strictly after the given date."""
    if not COACH_DB_PATH.exists():
        return None
    with get_coach_db() as conn:
        row = conn.execute(
            "SELECT id, planned_at, description, notes, status, activity_id "
            "FROM plan WHERE planned_at > ? AND status = 'pending' "
            "ORDER BY planned_at LIMIT 1",
            (date,),
        ).fetchone()
    return dict(row) if row else None


def _all_plans() -> list[dict]:
    if not COACH_DB_PATH.exists():
        return []
    with get_coach_db() as conn:
        rows = conn.execute(
            "SELECT id, planned_at, description, notes, status, activity_id "
            "FROM plan ORDER BY planned_at"
        ).fetchall()
    return [dict(r) for r in rows]


def _week_target(date: str) -> int | None:
    for start, end, km in BLOCK_WEEK_TARGETS:
        if start <= date <= end:
            return km
    return None


@app.get("/api/summary")
def api_summary(date: str | None = Query(default=None)):
    """Today's snapshot: body battery, resting HR, stress -- no steps/calories."""
    client = get_client()
    date = date or datetime.date.today().isoformat()
    try:
        stats = client.get_stats(date) or {}
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    return {
        "date": stats.get("calendarDate", date),
        "resting_hr": stats.get("restingHeartRate"),
        "avg_stress": stats.get("averageStressLevel"),
        "body_battery": stats.get("bodyBatteryMostRecentValue"),
        "body_battery_highest": stats.get("bodyBatteryHighestValue"),
        "body_battery_lowest": stats.get("bodyBatteryLowestValue"),
    }


@app.get("/api/recovery")
def api_recovery(days: int = Query(default=14, ge=1, le=28)):
    """Per-day recovery signals: body battery, resting HR, HRV, sleep."""
    client = get_client()
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days - 1)

    days_list = []
    current = start
    while current <= end:
        d = current.isoformat()

        try:
            stats = client.get_stats(d) or {}
        except Exception:
            stats = {}

        try:
            sleep = client.get_sleep_data(d) or {}
        except Exception:
            sleep = {}
        daily_sleep = sleep.get("dailySleepDTO") or {}
        overall_score = (daily_sleep.get("sleepScores") or {}).get("overall") or {}
        sleep_seconds = daily_sleep.get("sleepTimeSeconds")

        try:
            hrv = client.get_hrv_data(d) or {}
        except Exception:
            hrv = {}
        hrv_summary = hrv.get("hrvSummary") or {}

        days_list.append(
            {
                "date": d,
                "resting_hr": stats.get("restingHeartRate"),
                "body_battery": stats.get("bodyBatteryMostRecentValue"),
                "sleep_hours": round(sleep_seconds / 3600, 2) if sleep_seconds else None,
                "sleep_score": overall_score.get("value"),
                "hrv_avg": hrv_summary.get("lastNightAvg"),
                "hrv_status": hrv_summary.get("status"),
            }
        )
        current += datetime.timedelta(days=1)
    return {"days": days_list}


@app.get("/api/readiness")
def api_readiness():
    """Rule-based readiness verdict for today's planned session.

    Combines last night's sleep, HRV trend, resting HR trend and body
    battery. Transparent and explainable on purpose -- this is a coaching
    signal, not a black-box score, and never overrides real pain/injury.
    """
    client = get_client()
    today = datetime.date.today()
    today_str = today.isoformat()

    window_start = today - datetime.timedelta(days=8)
    days_list = []
    current = window_start
    while current <= today:
        d = current.isoformat()
        try:
            stats = client.get_stats(d) or {}
        except Exception:
            stats = {}
        try:
            sleep = client.get_sleep_data(d) or {}
        except Exception:
            sleep = {}
        daily_sleep = sleep.get("dailySleepDTO") or {}
        sleep_seconds = daily_sleep.get("sleepTimeSeconds")
        try:
            hrv = client.get_hrv_data(d) or {}
        except Exception:
            hrv = {}
        hrv_summary = hrv.get("hrvSummary") or {}

        days_list.append({
            "date": d,
            "resting_hr": stats.get("restingHeartRate"),
            "body_battery": stats.get("bodyBatteryMostRecentValue"),
            "sleep_hours": round(sleep_seconds / 3600, 2) if sleep_seconds else None,
            "hrv_avg": hrv_summary.get("lastNightAvg"),
            "hrv_status": hrv_summary.get("status"),
        })
        current += datetime.timedelta(days=1)

    reasons = []
    flags = 0

    last_night = days_list[-1] if days_list else {}

    sleep_hours = last_night.get("sleep_hours")
    if sleep_hours is not None:
        if sleep_hours < 6:
            flags += 1
            reasons.append(f"Sono curto: {sleep_hours}h esta noite (minimo recomendado 7h)")
        elif sleep_hours < 7:
            reasons.append(f"Sono abaixo do ideal: {sleep_hours}h (recomendado 7h+)")
    else:
        reasons.append("Ainda sem dados de sono da ultima noite")

    hrv_status = last_night.get("hrv_status")
    if hrv_status and hrv_status not in ("BALANCED", "NONE"):
        flags += 1
        reasons.append(f"HRV fora do equilibrio (status Garmin: {hrv_status})")

    hrv_valid = [d["hrv_avg"] for d in days_list if d.get("hrv_avg") is not None]
    if len(hrv_valid) >= 5:
        recent = hrv_valid[-3:]
        prior = hrv_valid[:-3]
        if prior:
            recent_avg = sum(recent) / len(recent)
            prior_avg = sum(prior) / len(prior)
            if recent_avg < prior_avg * 0.85:
                flags += 1
                reasons.append(
                    f"HRV em queda: media das ultimas noites {recent_avg:.0f}ms vs "
                    f"{prior_avg:.0f}ms do periodo anterior"
                )
    elif not hrv_valid:
        reasons.append("Sem dados de HRV suficientes para avaliar tendencia")

    rhr_valid = [d["resting_hr"] for d in days_list if d.get("resting_hr") is not None]
    if len(rhr_valid) >= 4:
        today_rhr = rhr_valid[-1]
        baseline = sum(rhr_valid[:-1]) / len(rhr_valid[:-1])
        if today_rhr - baseline >= 5:
            flags += 1
            reasons.append(
                f"FC repouso elevada: {today_rhr}bpm vs media recente {baseline:.0f}bpm "
                f"(+{today_rhr - baseline:.0f})"
            )

    body_battery = last_night.get("body_battery")
    if body_battery is not None and body_battery < 25:
        flags += 1
        reasons.append(f"Body Battery baixo: {body_battery}/100")

    if flags == 0:
        verdict, label = "apto", "Apto para o treino de hoje"
    elif flags == 1:
        verdict, label = "cautela", "Apto com cautela"
    else:
        verdict, label = "nao_apto", "Considera ajustar ou adiar"

    if not reasons:
        reasons.append("Sinais de recuperacao dentro do normal")

    plan = _plan_for_date(today_str)
    next_plan = _next_plan_after(today_str) if not plan else None

    return {
        "date": today_str,
        "verdict": verdict,
        "label": label,
        "flags": flags,
        "reasons": reasons,
        "today_plan": plan,
        "next_plan": next_plan,
        "snapshot": last_night,
    }


@app.get("/api/weekly_volume")
def api_weekly_volume():
    """This week's real running volume vs the block's target for that week."""
    client = get_client()
    today = datetime.date.today()
    week_start = today - datetime.timedelta(days=today.weekday())  # Monday
    week_end = week_start + datetime.timedelta(days=6)

    try:
        activities = client.get_activities_by_date(
            week_start.isoformat(), today.isoformat()
        ) or []
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc

    running = [
        a for a in activities
        if (a.get("activityType") or {}).get("typeKey") in ("running", "trail_running", "track_running")
    ]
    real_km = sum((a.get("distance") or 0) for a in running) / 1000

    target_km = _week_target(today.isoformat())

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "real_km": round(real_km, 1),
        "target_km": target_km,
        "sessions_count": len(running),
    }


@app.get("/api/race_projection")
def api_race_projection():
    """Riegel-formula race-time projections from the athlete's current 5km reference.

    Reference: Ricardo's stated 5km time (23min, ATHLETE.md, 19 sep 2026).
    Pure math projection, not a guarantee -- clearly labelled as an estimate.
    """
    ref_distance_km = 5.0
    ref_seconds = 23 * 60
    exponent = 1.06

    targets = [3, 5, 10, 15, 21.0975]
    projections = []
    for dist in targets:
        seconds = ref_seconds * (dist / ref_distance_km) ** exponent
        minutes = seconds / 60
        pace_min_per_km = minutes / dist
        projections.append({
            "distance_km": dist,
            "projected_minutes": round(minutes, 1),
            "pace_min_per_km": round(pace_min_per_km, 2),
        })

    return {
        "reference": {"distance_km": ref_distance_km, "time_minutes": ref_seconds / 60},
        "formula": "Riegel (T2 = T1 * (D2/D1)^1.06)",
        "projections": projections,
    }


# Custom zone boundaries (lower bound, inclusive), overriding Garmin's own
# Z2 ceiling (136) per Ricardo's request: Z2 runs up to 140bpm.
ZONE_FLOORS = {1: 90, 2: 120, 3: 140, 4: 153, 5: 165}


def _bucket_zone(hr: float) -> int:
    if hr < ZONE_FLOORS[2]:
        return 1
    if hr < ZONE_FLOORS[3]:
        return 2
    if hr < ZONE_FLOORS[4]:
        return 3
    if hr < ZONE_FLOORS[5]:
        return 4
    return 5


def _extract_hr_series(details: dict) -> list[tuple[float, float]]:
    """Return sorted (timestamp_ms, heart_rate) samples from activity detail metrics."""
    hr_idx = ts_idx = None
    for d in details.get("metricDescriptors") or []:
        if d.get("key") == "directHeartRate":
            hr_idx = d.get("metricsIndex")
        elif d.get("key") == "directTimestamp":
            ts_idx = d.get("metricsIndex")
    if hr_idx is None or ts_idx is None:
        return []

    series = []
    for row in details.get("activityDetailMetrics") or []:
        m = row.get("metrics") or []
        if len(m) <= max(hr_idx, ts_idx):
            continue
        hr, ts = m[hr_idx], m[ts_idx]
        if hr is not None and ts is not None:
            series.append((ts, hr))
    series.sort(key=lambda x: x[0])
    return series


def _activity_zone_seconds(client, activity_id) -> dict[int, float] | None:
    """Time-weighted zone-seconds for one activity, from raw per-second HR samples."""
    try:
        details = client.get_activity_details(activity_id)
    except Exception:
        return None
    series = _extract_hr_series(details)
    if len(series) < 2:
        return None

    zone_secs = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}
    for (ts_prev, hr_prev), (ts_cur, _hr_cur) in zip(series, series[1:]):
        dt = (ts_cur - ts_prev) / 1000.0
        if 0 < dt <= 30:  # skip pauses/gaps in recording
            zone_secs[_bucket_zone(hr_prev)] += dt
    return zone_secs


def _extract_hr_speed_series(details: dict) -> list[tuple[float, float, float]]:
    """Return sorted (timestamp_ms, heart_rate, speed_mps) samples."""
    hr_idx = ts_idx = speed_idx = None
    for d in details.get("metricDescriptors") or []:
        key = d.get("key")
        if key == "directHeartRate":
            hr_idx = d.get("metricsIndex")
        elif key == "directTimestamp":
            ts_idx = d.get("metricsIndex")
        elif key == "directSpeed":
            speed_idx = d.get("metricsIndex")
    if hr_idx is None or ts_idx is None:
        return []

    series = []
    need = max(hr_idx, ts_idx, speed_idx or 0)
    for row in details.get("activityDetailMetrics") or []:
        m = row.get("metrics") or []
        if len(m) <= need:
            continue
        hr, ts = m[hr_idx], m[ts_idx]
        speed = m[speed_idx] if speed_idx is not None else None
        if hr is not None and ts is not None:
            series.append((ts, hr, speed or 0.0))
    series.sort(key=lambda x: x[0])
    return series


def _activity_zone_km(client, activity_id) -> dict[int, float] | None:
    """Distance (km) covered in each HR zone for one activity.

    Integrates speed * dt over each interval, attributed to the zone the
    heart rate was in at the start of that interval -- so "km in Z2" means
    km actually covered while the heart rate was in Z2, not just time spent.
    """
    try:
        details = client.get_activity_details(activity_id)
    except Exception:
        return None
    series = _extract_hr_speed_series(details)
    if len(series) < 2:
        return None

    zone_km = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}
    for (ts_prev, hr_prev, speed_prev), (ts_cur, _hr_cur, _speed_cur) in zip(series, series[1:]):
        dt = (ts_cur - ts_prev) / 1000.0
        if 0 < dt <= 30:
            zone_km[_bucket_zone(hr_prev)] += (speed_prev * dt) / 1000.0
    return zone_km


@app.get("/api/zone_analysis")
def api_zone_analysis(count: int = Query(default=10, ge=1, le=20)):
    """Aggregate HR-zone time across the last N running activities.

    Recomputed from raw per-second HR samples (not Garmin's own zone buckets),
    using custom zone boundaries where Z2 extends up to 140bpm (Ricardo's
    request, 19 sep 2026) instead of Garmin's configured 136bpm ceiling.
    """
    client = get_client()
    try:
        candidates = client.get_activities(0, count * 4) or []
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc

    running = [
        a for a in candidates
        if (a.get("activityType") or {}).get("typeKey") in ("running", "trail_running", "track_running")
    ][:count]

    zone_secs = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}
    analyzed = []
    for a in running:
        activity_id = a.get("activityId")
        secs = _activity_zone_seconds(client, activity_id)
        if not secs or not sum(secs.values()):
            continue
        for num, s in secs.items():
            zone_secs[num] += s
        analyzed.append({
            "id": activity_id,
            "name": a.get("activityName"),
            "date": a.get("startTimeLocal"),
        })

    total = sum(zone_secs.values())
    zones_out = []
    for num in (1, 2, 3, 4, 5):
        secs = zone_secs[num]
        pct = round((secs / total) * 100) if total else 0
        zones_out.append({
            "zone": num,
            "minutes": round(secs / 60, 1),
            "pct": pct,
            "floor_bpm": ZONE_FLOORS[num],
        })

    return {
        "activities_analyzed": len(analyzed),
        "activities": analyzed,
        "total_minutes": round(total / 60, 1),
        "zones": zones_out,
        "z2_ceiling_bpm": ZONE_FLOORS[3] - 1,
    }


@app.get("/api/volume_trend")
def api_volume_trend(weeks: int = Query(default=6, ge=1, le=12)):
    """Weekly running km for the last N weeks (Monday-start), most recent week first."""
    client = get_client()
    today = datetime.date.today()
    this_week_start = today - datetime.timedelta(days=today.weekday())  # Monday
    range_start = this_week_start - datetime.timedelta(weeks=weeks - 1)

    try:
        activities = client.get_activities_by_date(
            range_start.isoformat(), today.isoformat()
        ) or []
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc

    running = [
        a for a in activities
        if (a.get("activityType") or {}).get("typeKey") in ("running", "trail_running", "track_running")
    ]

    buckets = {}
    week_start = range_start
    while week_start <= this_week_start:
        buckets[week_start.isoformat()] = {
            "km": 0.0, "sessions": 0,
            "zone_km": {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0},
        }
        week_start += datetime.timedelta(days=7)

    for a in running:
        try:
            d = datetime.date.fromisoformat(a["startTimeLocal"][:10])
        except (KeyError, ValueError):
            continue
        wk = d - datetime.timedelta(days=d.weekday())  # Monday
        key = wk.isoformat()
        if key not in buckets:
            continue
        buckets[key]["km"] += (a.get("distance") or 0) / 1000
        buckets[key]["sessions"] += 1

        zone_km = _activity_zone_km(client, a.get("activityId"))
        if zone_km:
            for num, km in zone_km.items():
                buckets[key]["zone_km"][num] += km

    weeks_out = []
    for k, v in sorted(buckets.items()):  # ascending: oldest -> current
        week_total = sum(v["zone_km"].values())
        weeks_out.append({
            "week_start": k,
            "km": round(v["km"], 1),
            "sessions": v["sessions"],
            "zones": [
                {
                    "zone": num,
                    "km": round(v["zone_km"][num], 2),
                    "pct": round((v["zone_km"][num] / week_total) * 100) if week_total else 0,
                }
                for num in (1, 2, 3, 4, 5)
            ],
        })
    return {"weeks": weeks_out, "z2_ceiling_bpm": ZONE_FLOORS[3] - 1}


@app.get("/api/vo2max_trend")
def api_vo2max_trend(days: int = Query(default=90, ge=7, le=180)):
    """Dense daily VO2max series (forward-filled), sport=running."""
    client = get_client()
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days - 1)

    metrics_url = getattr(client, "garmin_connect_metrics_url", None)
    by_date: dict[str, float] = {}
    if metrics_url:
        try:
            data = client.connectapi(f"{metrics_url}/{start.isoformat()}/{end.isoformat()}")
        except Exception:
            data = None

        def collect(item):
            if isinstance(item, list):
                for x in item:
                    collect(x)
                return
            if not isinstance(item, dict):
                return
            generic = item.get("generic") or {}
            val = generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxValue")
            cal_date = generic.get("calendarDate") or item.get("calendarDate")
            if val is not None and isinstance(cal_date, str):
                by_date.setdefault(cal_date, float(val))

        collect(data)

    series = []
    last = None
    current = start
    while current <= end:
        d = current.isoformat()
        if d in by_date:
            last = by_date[d]
        if last is not None:
            series.append({"date": d, "vo2_max": last})
        current += datetime.timedelta(days=1)

    first_val = series[0]["vo2_max"] if series else None
    last_val = series[-1]["vo2_max"] if series else None
    change = round(last_val - first_val, 1) if first_val is not None and last_val is not None else None

    return {"series": series, "first": first_val, "latest": last_val, "change": change}


@app.get("/api/fitness_freshness")
def api_fitness_freshness(days: int = Query(default=42, ge=7, le=90)):
    """Fitness (chronic load) / Fatigue (acute load) / Form (ratio), Garmin's ACWR model.

    Mirrors Garmin Connect's own "Fitness & Freshness": chronic load = longer-term
    training load ("fitness"), acute load = short-term training load ("fatigue"),
    their ratio = "form" (optimal band ~0.8-1.3 per Garmin's acwrStatus).
    """
    client = get_client()
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days - 1)

    series = []
    current = start
    while current <= end:
        d = current.isoformat()
        try:
            status = client.get_training_status(d) or {}
        except Exception:
            status = {}
        recent = status.get("mostRecentTrainingStatus") or {}
        latest = recent.get("latestTrainingStatusData") or {}
        device_data = {}
        for v in latest.values():
            if isinstance(v, dict) and v:
                device_data = v
                break
        acwr = device_data.get("acuteTrainingLoadDTO") or {}
        series.append({
            "date": d,
            "fitness": acwr.get("dailyTrainingLoadChronic"),
            "fatigue": acwr.get("dailyTrainingLoadAcute"),
            "ratio": acwr.get("dailyAcuteChronicWorkloadRatio"),
            "status": acwr.get("acwrStatus"),
        })
        current += datetime.timedelta(days=1)

    valid = [s for s in series if s["fitness"] is not None]
    latest_point = valid[-1] if valid else None

    return {"series": series, "latest": latest_point}


@app.get("/api/sleep_after_run")
def api_sleep_after_run(count: int = Query(default=10, ge=1, le=20)):
    """Sleep (hours/score) on the night following each of the last N runs."""
    client = get_client()
    try:
        candidates = client.get_activities(0, count * 4) or []
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc

    running = [
        a for a in candidates
        if (a.get("activityType") or {}).get("typeKey") in ("running", "trail_running", "track_running")
    ][:count]
    running.reverse()  # ascending: oldest -> most recent

    out = []
    for a in running:
        start_time = a.get("startTimeLocal")
        if not start_time:
            continue
        run_date = datetime.date.fromisoformat(start_time[:10])
        next_day = (run_date + datetime.timedelta(days=1)).isoformat()
        try:
            sleep = client.get_sleep_data(next_day) or {}
        except Exception:
            sleep = {}
        daily_sleep = sleep.get("dailySleepDTO") or {}
        sleep_seconds = daily_sleep.get("sleepTimeSeconds")
        overall = (daily_sleep.get("sleepScores") or {}).get("overall") or {}
        out.append({
            "run_date": run_date.isoformat(),
            "run_name": a.get("activityName"),
            "run_distance_km": round((a.get("distance") or 0) / 1000, 1),
            "next_night_date": next_day,
            "sleep_hours": round(sleep_seconds / 3600, 2) if sleep_seconds else None,
            "sleep_score": overall.get("value"),
        })
    return {"runs": out}


@app.get("/api/overtraining_risk")
def api_overtraining_risk(days: int = Query(default=21, ge=7, le=42)):
    """Multi-week overtraining assessment (HRV, RHR, body battery, sleep trends)."""
    client = get_client()
    today = datetime.date.today()
    start = today - datetime.timedelta(days=days - 1)

    daily = []
    current = start
    while current <= today:
        d = current.isoformat()
        try:
            stats = client.get_stats(d) or {}
        except Exception:
            stats = {}
        try:
            hrv = client.get_hrv_data(d) or {}
        except Exception:
            hrv = {}
        hrv_summary = hrv.get("hrvSummary") or {}
        try:
            sleep = client.get_sleep_data(d) or {}
        except Exception:
            sleep = {}
        daily_sleep = sleep.get("dailySleepDTO") or {}
        sleep_seconds = daily_sleep.get("sleepTimeSeconds")
        daily.append({
            "date": d,
            "resting_hr": stats.get("restingHeartRate"),
            "body_battery": stats.get("bodyBatteryMostRecentValue"),
            "hrv_avg": hrv_summary.get("lastNightAvg"),
            "hrv_status": hrv_summary.get("status"),
            "sleep_hours": round(sleep_seconds / 3600, 2) if sleep_seconds else None,
        })
        current += datetime.timedelta(days=1)

    reasons = []
    flags = 0

    hrv_valid = [d["hrv_avg"] for d in daily if d.get("hrv_avg") is not None]
    hrv_missing = sum(1 for d in daily if d.get("hrv_avg") is None)
    if any(d.get("hrv_status") in ("LOW", "UNBALANCED") for d in daily):
        flags += 1
        reasons.append("HRV saiu do equilibrio (status Garmin) em pelo menos um dia no periodo")
    if len(hrv_valid) >= 6:
        third = max(3, len(hrv_valid) // 3)
        recent_avg = sum(hrv_valid[-third:]) / third
        prior_avg = sum(hrv_valid[:-third]) / max(1, len(hrv_valid) - third)
        if recent_avg < prior_avg * 0.85:
            flags += 1
            reasons.append(f"HRV em tendencia de queda: {recent_avg:.0f}ms recente vs {prior_avg:.0f}ms antes")

    rhr_valid = [d["resting_hr"] for d in daily if d.get("resting_hr") is not None]
    if len(rhr_valid) >= 6:
        third = max(3, len(rhr_valid) // 3)
        recent_avg = sum(rhr_valid[-third:]) / third
        prior_avg = sum(rhr_valid[:-third]) / max(1, len(rhr_valid) - third)
        if recent_avg - prior_avg >= 3:
            flags += 1
            reasons.append(f"FC repouso em tendencia de subida: {recent_avg:.0f}bpm recente vs {prior_avg:.0f}bpm antes")

    sleep_valid = [d["sleep_hours"] for d in daily if d.get("sleep_hours") is not None]
    short_nights = sum(1 for s in sleep_valid if s < 6.5)
    if sleep_valid and short_nights / len(sleep_valid) >= 0.4:
        flags += 1
        reasons.append(f"{short_nights} de {len(sleep_valid)} noites com dados abaixo de 6.5h")

    if flags == 0:
        verdict, label = "baixo", "Sem sinais de overtraining"
    elif flags == 1:
        verdict, label = "moderado", "Um sinal de alerta — vale a pena monitorizar"
    else:
        verdict, label = "alto", "Varios sinais de alerta — considera reduzir carga"

    if not reasons:
        reasons.append("HRV, FC repouso e sono dentro do esperado no periodo analisado")

    data_gaps = []
    if hrv_missing > days * 0.3:
        data_gaps.append(f"HRV em falta em {hrv_missing} de {days} dias")
    sleep_missing = days - len(sleep_valid)
    if sleep_missing > days * 0.3:
        data_gaps.append(f"Sono em falta em {sleep_missing} de {days} dias")

    return {
        "period_days": days,
        "verdict": verdict,
        "label": label,
        "flags": flags,
        "reasons": reasons,
        "data_gaps": data_gaps,
    }


@app.get("/api/first10min_hr")
def api_first10min_hr(count: int = Query(default=10, ge=1, le=20)):
    """Average HR during the first 10 minutes of each of the last N runs.

    Checks whether Ricardo starts runs already above the Z2 ceiling (140bpm) --
    a classic "went out too fast" pattern.
    """
    client = get_client()
    try:
        candidates = client.get_activities(0, count * 4) or []
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc

    running = [
        a for a in candidates
        if (a.get("activityType") or {}).get("typeKey") in ("running", "trail_running", "track_running")
    ][:count]
    running.reverse()  # ascending: oldest -> most recent

    out = []
    for a in running:
        activity_id = a.get("activityId")
        try:
            details = client.get_activity_details(activity_id)
        except Exception:
            continue
        series = _extract_hr_series(details)
        if not series:
            continue
        t0 = series[0][0]
        window = [hr for ts, hr in series if ts - t0 <= 600_000]
        if not window:
            continue
        out.append({
            "id": activity_id,
            "name": a.get("activityName"),
            "date": a.get("startTimeLocal"),
            "avg_hr_first_10min": round(sum(window) / len(window), 1),
            "avg_hr_overall": a.get("averageHR"),
        })

    return {"runs": out, "z2_ceiling_bpm": ZONE_FLOORS[3] - 1}


@app.get("/api/running_dynamics")
def api_running_dynamics(count: int = Query(default=10, ge=1, le=20)):
    """Running-form metrics (cadence, stride, GCT, vertical oscillation/ratio) per run."""
    client = get_client()
    try:
        candidates = client.get_activities(0, count * 4) or []
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc

    running = [
        a for a in candidates
        if (a.get("activityType") or {}).get("typeKey") in ("running", "trail_running", "track_running")
    ][:count]
    running.reverse()  # ascending: oldest -> most recent

    out = []
    for a in running:
        activity_id = a.get("activityId")
        try:
            full = client.get_activity(activity_id) or {}
        except Exception:
            continue
        s = full.get("summaryDTO") or {}
        distance = s.get("distance")
        duration = s.get("duration")
        pace_min_per_km = (duration / 60) / (distance / 1000) if distance and duration else None
        out.append({
            "id": activity_id,
            "name": a.get("activityName"),
            "date": a.get("startTimeLocal"),
            "distance_km": round(distance / 1000, 2) if distance else None,
            "pace_min_per_km": round(pace_min_per_km, 2) if pace_min_per_km else None,
            "avg_hr": s.get("averageHR"),
            "avg_cadence_spm": s.get("averageRunCadence"),
            "max_cadence_spm": s.get("maxRunCadence"),
            "stride_length_cm": round(s["strideLength"], 1) if s.get("strideLength") else None,
            "ground_contact_time_ms": round(s["groundContactTime"], 1) if s.get("groundContactTime") else None,
            "vertical_oscillation_cm": round(s["verticalOscillation"], 2) if s.get("verticalOscillation") else None,
            "vertical_ratio_pct": round(s["verticalRatio"], 2) if s.get("verticalRatio") else None,
            "elevation_gain_m": round(s["elevationGain"]) if s.get("elevationGain") is not None else None,
        })

    return {"runs": out}


@app.get("/api/pace_trend")
def api_pace_trend(weeks: int = Query(default=8, ge=1, le=16)):
    """Weekly average pace and elevation gain -- tracks speed gains over time."""
    client = get_client()
    today = datetime.date.today()
    this_week_start = today - datetime.timedelta(days=today.weekday())  # Monday
    range_start = this_week_start - datetime.timedelta(weeks=weeks - 1)

    try:
        activities = client.get_activities_by_date(
            range_start.isoformat(), today.isoformat()
        ) or []
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc

    running = [
        a for a in activities
        if (a.get("activityType") or {}).get("typeKey") in ("running", "trail_running", "track_running")
    ]

    buckets = {}
    week_start = range_start
    while week_start <= this_week_start:
        buckets[week_start.isoformat()] = {
            "distance_m": 0.0, "duration_s": 0.0, "sessions": 0,
            "elevation_gain_m": 0.0, "best_pace": None,
        }
        week_start += datetime.timedelta(days=7)

    for a in running:
        try:
            d = datetime.date.fromisoformat(a["startTimeLocal"][:10])
        except (KeyError, ValueError):
            continue
        wk = d - datetime.timedelta(days=d.weekday())
        key = wk.isoformat()
        if key not in buckets:
            continue
        distance = a.get("distance") or 0
        duration = a.get("duration") or 0
        buckets[key]["distance_m"] += distance
        buckets[key]["duration_s"] += duration
        buckets[key]["sessions"] += 1
        buckets[key]["elevation_gain_m"] += a.get("elevationGain") or 0
        if distance and duration:
            pace = (duration / 60) / (distance / 1000)
            if buckets[key]["best_pace"] is None or pace < buckets[key]["best_pace"]:
                buckets[key]["best_pace"] = pace

    weeks_out = []
    for k, v in sorted(buckets.items()):  # ascending
        avg_pace = (v["duration_s"] / 60) / (v["distance_m"] / 1000) if v["distance_m"] else None
        km = v["distance_m"] / 1000
        weeks_out.append({
            "week_start": k,
            "km": round(km, 1),
            "sessions": v["sessions"],
            "avg_pace_min_per_km": round(avg_pace, 2) if avg_pace else None,
            "best_pace_min_per_km": round(v["best_pace"], 2) if v["best_pace"] else None,
            "elevation_gain_m": round(v["elevation_gain_m"]),
            "elevation_gain_per_km": round(v["elevation_gain_m"] / km, 1) if km else None,
        })
    return {"weeks": weeks_out}


@app.get("/api/weekly_closing")
def api_weekly_closing():
    """Closing report for the most recently fully-completed week (Monday-Sunday).

    Flags flat-only running (low elevation gain per km) so hill work doesn't get
    silently skipped, and compares pace/volume against the prior week.
    """
    client = get_client()
    today = datetime.date.today()
    this_week_start = today - datetime.timedelta(days=today.weekday())
    closed_start = this_week_start - datetime.timedelta(days=7)
    closed_end = this_week_start - datetime.timedelta(days=1)
    prior_start = closed_start - datetime.timedelta(days=7)
    prior_end = closed_start - datetime.timedelta(days=1)

    def week_stats(start, end):
        try:
            activities = client.get_activities_by_date(start.isoformat(), end.isoformat()) or []
        except Exception:
            activities = []
        running = [
            a for a in activities
            if (a.get("activityType") or {}).get("typeKey") in ("running", "trail_running", "track_running")
        ]
        distance_m = sum((a.get("distance") or 0) for a in running)
        duration_s = sum((a.get("duration") or 0) for a in running)
        elevation_m = sum((a.get("elevationGain") or 0) for a in running)
        avg_pace = (duration_s / 60) / (distance_m / 1000) if distance_m else None
        km = distance_m / 1000
        flat_runs = sum(
            1 for a in running
            if a.get("distance") and ((a.get("elevationGain") or 0) / (a["distance"] / 1000)) < 5
        )
        return {
            "sessions": len(running),
            "km": round(km, 1),
            "avg_pace_min_per_km": round(avg_pace, 2) if avg_pace else None,
            "elevation_gain_m": round(elevation_m),
            "elevation_gain_per_km": round(elevation_m / km, 1) if km else None,
            "flat_runs": flat_runs,
            "runs": [
                {
                    "name": a.get("activityName"),
                    "date": a.get("startTimeLocal"),
                    "distance_km": round((a.get("distance") or 0) / 1000, 2),
                    "elevation_gain_m": round(a.get("elevationGain") or 0),
                }
                for a in running
            ],
        }

    closed = week_stats(closed_start, closed_end)
    prior = week_stats(prior_start, prior_end)
    target_km = _week_target(closed_start.isoformat())

    pace_delta = None
    if closed["avg_pace_min_per_km"] and prior["avg_pace_min_per_km"]:
        pace_delta = round(closed["avg_pace_min_per_km"] - prior["avg_pace_min_per_km"], 2)

    flags = []
    if closed["sessions"] and closed["flat_runs"] == closed["sessions"]:
        flags.append(
            f"Todas as {closed['sessions']} corrida(s) desta semana foram em terreno essencialmente "
            "plano (<5m de ganho de elevação por km) -- nenhum trabalho de subida."
        )
    elif closed["sessions"] and closed["flat_runs"] / closed["sessions"] >= 0.5:
        flags.append(
            f"{closed['flat_runs']} de {closed['sessions']} corridas em terreno plano -- pouco "
            "trabalho de subida essa semana."
        )
    if pace_delta is not None:
        if pace_delta < -0.05:
            flags.append(f"Ritmo médio melhorou {abs(pace_delta)}min/km vs semana anterior.")
        elif pace_delta > 0.05:
            flags.append(f"Ritmo médio piorou {pace_delta}min/km vs semana anterior (pode ser volume/calor/fadiga, não é necessariamente ruim).")
    if not flags:
        flags.append("Sem sinais particulares esta semana.")

    return {
        "week_start": closed_start.isoformat(),
        "week_end": closed_end.isoformat(),
        "closed": closed,
        "prior": prior,
        "target_km": target_km,
        "pace_delta_min_per_km": pace_delta,
        "flags": flags,
    }


@app.get("/api/plan")
def api_plan():
    """Full training plan (all sessions, any status) from Coach Memory."""
    today = datetime.date.today().isoformat()
    plans = _all_plans()
    return {
        "today": today,
        "sessions": plans,
    }


@app.get("/api/biomechanics_notes")
def api_biomechanics_notes():
    """Weekly biomechanical assessment board, keyed by week_start (Monday).

    Source of truth is the human-readable ~/.local/share/coach/BIOMECANICA.md;
    this JSON mirror is what the dashboard reads to show notes when a week is
    clicked. Update both when adding a new assessment.
    """
    if not BIOMECHANICS_BOARD_PATH.exists():
        return {}
    try:
        with open(BIOMECHANICS_BOARD_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


@app.get("/api/activities")
def api_activities(limit: int = Query(default=10, ge=1, le=50)):
    client = get_client()
    try:
        activities = client.get_activities(0, limit) or []
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc

    result = []
    for a in activities:
        distance = a.get("distance")
        duration = a.get("duration")
        result.append(
            {
                "id": a.get("activityId"),
                "name": a.get("activityName"),
                "type": (a.get("activityType") or {}).get("typeKey"),
                "start_time": a.get("startTimeLocal"),
                "distance_km": round(distance / 1000, 2) if distance else None,
                "duration_min": round(duration / 60, 1) if duration else None,
                "calories": a.get("calories"),
                "avg_hr": a.get("averageHR"),
            }
        )
    return {"activities": result}


_INDEX_HTML = Path(__file__).resolve().parent / "static" / "index.html"


@app.get("/")
def index():
    return FileResponse(_INDEX_HTML)
