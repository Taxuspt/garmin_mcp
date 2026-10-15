"""FastAPI dashboard for Garmin Connect + Coach data.

Reuses the same OAuth tokens the garmin MCP server authenticates with (see
garmin_mcp.token_utils), so it requires no separate login step as long as
`garmin-mcp-auth` has already been run once. Coach plan data (today's session,
weekly volume target) is read directly from the Coach Memory MCP's local
SQLite database -- read-only, no need to speak the MCP protocol for that.
"""
import datetime
import email.utils
import json
import logging
import math
import os
import random
import sqlite3
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

load_dotenv(REPO_ROOT / ".env")

from garmin_mcp import token_utils  # noqa: E402

app = FastAPI(title="Garmin Dashboard")

_client: Garmin | None = None
_client_lock = threading.Lock()

# Overridable via COACH_DATA_DIR so this can live outside ~/.local/share on
# machines (e.g. Windows) where that's not a natural place to keep app data.
COACH_DATA_DIR = Path(os.getenv("COACH_DATA_DIR") or (Path.home() / ".local" / "share" / "coach"))
COACH_DB_PATH = COACH_DATA_DIR / "memory.db"

BIOMECHANICS_BOARD_PATH = COACH_DATA_DIR / "biomechanics_board.json"

# Training-load model constants (Foster session-RPE / TrainingPeaks-style EWMA).
# Weekly km targets now come from the `mesocycle` table in Coach Memory instead
# of a hardcoded block (see _week_target_from_mesocycle).
CTL_DAYS = 42
ATL_DAYS = 7
LOAD_HISTORY_DAYS = 180  # fixed lookback; also what "Tudo" means in the UI toggle
SEED_DAYS = 7            # days averaged to seed CTL0/ATL0
RECENT_DAYS_ALWAYS_RECOMPUTE = 2  # today + yesterday: always refetched (late RPE entry)

# Zone -> approximate session-RPE (Borg CR10-ish), reusing the athlete's custom
# HR-zone floors (ZONE_FLOORS/_bucket_zone, defined below) already used for zone_analysis.
ZONE_RPE = {1: 2.0, 2: 3.5, 3: 5.5, 4: 7.5, 5: 9.0}
DEFAULT_RPE_NO_HR = 5.0  # activities with no HR at all (e.g. some strength sessions)

# Disk-persisted cache: iniciar_dashboard.bat starts a fresh uvicorn process every
# time the dashboard is opened, so an in-memory-only cache would be wiped on every
# launch. Persisting alongside the biomechanics board (same COACH_DATA_DIR) means
# the expensive per-activity RPE warm-up is paid once, not on every dashboard open.
TRAINING_LOAD_CACHE_PATH = COACH_DATA_DIR / "training_load_cache.json"
_activity_load_cache: dict[str, dict] = {}
_daily_load_cache: dict[str, dict] = {}
_load_cache_lock = threading.Lock()


def _load_training_load_cache_from_disk() -> None:
    if not TRAINING_LOAD_CACHE_PATH.exists():
        return
    try:
        with open(TRAINING_LOAD_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        _activity_load_cache.update(data.get("activity_load", {}))
        _daily_load_cache.update(data.get("daily_load", {}))
    except Exception:
        pass  # corrupt/missing cache -- endpoint will just recompute on demand


def _save_training_load_cache_to_disk() -> None:
    try:
        COACH_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with _load_cache_lock:
            data = {"activity_load": _activity_load_cache, "daily_load": _daily_load_cache}
        with open(TRAINING_LOAD_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass  # best-effort persistence -- next request just recomputes


_load_training_load_cache_from_disk()

logger = logging.getLogger("dashboard")

# Bounded retry with exponential backoff + jitter for transient Garmin failures
# (429 rate limit, 5xx, network). 4xx client errors and auth failures are not
# retried: repeating them never helps and hammering after a 429 makes Garmin
# throttle harder, hence the small attempt count and the delay cap.
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY_S = 0.5
RETRY_MAX_DELAY_S = 10.0
RETRY_JITTER = 0.2


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, GarminConnectTooManyRequestsError):
        return True
    if isinstance(exc, GarminConnectAuthenticationError):
        return False
    if isinstance(exc, GarminConnectConnectionError):
        # garminconnect tags 4xx as "API client error (4xx)"; everything else
        # under this type is a 5xx or a network-level failure.
        return "API client error" not in str(exc)
    return False


def _retry_after_seconds(exc: Exception) -> float | None:
    """Honor a Retry-After header if the underlying HTTP response carried one."""
    cause = exc
    while cause is not None:
        response = getattr(cause, "response", None)
        headers = getattr(response, "headers", None)
        if headers is not None:
            value = headers.get("Retry-After")
            if value:
                try:
                    return max(0.0, float(value))
                except ValueError:
                    try:
                        target = email.utils.parsedate_to_datetime(value)
                        return max(0.0, target.timestamp() - time.time())
                    except (TypeError, ValueError):
                        return None
        cause = cause.__cause__
    return None


def _backoff_delay(attempt: int, exc: Exception) -> float:
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        return min(retry_after, RETRY_MAX_DELAY_S)
    delay = min(RETRY_BASE_DELAY_S * 2 ** (attempt - 1), RETRY_MAX_DELAY_S)
    return max(0.0, delay + delay * RETRY_JITTER * (random.random() * 2 - 1))


def _call_with_retry(fn, *args, **kwargs):
    for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if attempt == RETRY_MAX_ATTEMPTS or not _is_retryable(exc):
                raise
            delay = _backoff_delay(attempt, exc)
            logger.warning(
                "Garmin %s falhou (%s); tentativa %d/%d em %.1fs",
                getattr(fn, "__name__", "call"), exc, attempt, RETRY_MAX_ATTEMPTS, delay,
            )
            time.sleep(delay)


class _RetryingGarmin:
    """Proxy that routes every Garmin client method through _call_with_retry."""

    def __init__(self, client: Garmin):
        self._client = client

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr

        def wrapper(*args, **kwargs):
            return _call_with_retry(attr, *args, **kwargs)

        wrapper.__name__ = name
        return wrapper


def get_client() -> Garmin:
    """Lazily log in once and reuse the same Garmin client for every request."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:  # re-check: another thread may have logged in already
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
                _client = _RetryingGarmin(client)
    return _client


# Per-day wellness cache (sleep, HRV, resting HR, body battery). readiness,
# recovery, overtraining_risk and sleep_after_run all read overlapping day
# windows, so without this every page open costs ~130 Garmin calls. Past days
# stop changing once the watch has synced, so they're persisted to disk; today
# and yesterday are always refetched (sleep/HRV land on the wake date only after
# sync). A day is cached only when every call succeeded, and -- unless it's old
# enough that a late sync is implausible -- only once sleep and RHR are present.
WELLNESS_CACHE_PATH = COACH_DATA_DIR / "wellness_cache.json"
WELLNESS_RECENT_DAYS_ALWAYS_REFETCH = 2
WELLNESS_SETTLED_DAYS = 7
WELLNESS_CACHE_MAX_AGE_DAYS = 120
# Bump when _fetch_wellness_day gains/changes fields: entries written by an
# older version are dropped on load and simply refetched.
WELLNESS_CACHE_VERSION = 4
_wellness_cache: dict[str, dict] = {}
_wellness_cache_lock = threading.Lock()

# Short-lived in-memory cache for days that aren't persisted yet (today,
# yesterday, not-yet-synced recent days). Several dashboard cards request the
# same recent days in parallel on every page open; a per-day lock makes them
# share one Garmin fetch, and the TTL keeps a quick reload from refetching.
# Short enough that a fresh watch sync shows up within a couple of minutes.
WELLNESS_RECENT_TTL_S = 120
_recent_wellness_cache: dict[str, tuple[float, dict]] = {}
_wellness_day_locks: dict[str, threading.Lock] = {}
_monotonic = time.monotonic


def _load_wellness_cache_from_disk() -> None:
    if not WELLNESS_CACHE_PATH.exists():
        return
    try:
        with open(WELLNESS_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        _wellness_cache.update({
            d: {k: v for k, v in entry.items() if k != "v"}
            for d, entry in data.items()
            if entry.get("v") == WELLNESS_CACHE_VERSION
        })
    except Exception:
        pass  # corrupt/missing cache -- days are just refetched


def _save_wellness_cache_to_disk() -> None:
    cutoff = (datetime.date.today() - datetime.timedelta(days=WELLNESS_CACHE_MAX_AGE_DAYS)).isoformat()
    try:
        COACH_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with _wellness_cache_lock:
            for d in [d for d in _wellness_cache if d < cutoff]:
                del _wellness_cache[d]
            data = {d: {**entry, "v": WELLNESS_CACHE_VERSION} for d, entry in _wellness_cache.items()}
        tmp = WELLNESS_CACHE_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, WELLNESS_CACHE_PATH)
    except Exception:
        pass  # best-effort persistence


_load_wellness_cache_from_disk()


def _fetch_wellness_day(client, d: str) -> dict:
    """Fetch and normalize one day. `errors` lists the sources that failed."""
    errors = []

    def fetch(name, fn):
        try:
            return fn(d) or {}
        except Exception:
            errors.append(name)
            return {}

    stats = fetch("stats", client.get_stats)
    sleep = fetch("sleep", client.get_sleep_data)
    hrv = fetch("hrv", client.get_hrv_data)

    daily_sleep = sleep.get("dailySleepDTO") or {}
    sleep_seconds = daily_sleep.get("sleepTimeSeconds")
    overall_score = (daily_sleep.get("sleepScores") or {}).get("overall") or {}
    hrv_summary = hrv.get("hrvSummary") or {}
    avg_stress = stats.get("averageStressLevel")
    if avg_stress is not None and avg_stress < 0:
        avg_stress = None  # Garmin uses negative levels for "not enough data"

    def sleep_field(key):
        # Only trust sleep sub-metrics when a night was actually recorded.
        return daily_sleep.get(key) if sleep_seconds else None

    def stage_minutes(key):
        seconds = sleep_field(key)
        return round(seconds / 60) if seconds is not None else None

    return {
        "date": d,
        "resting_hr": stats.get("restingHeartRate"),
        # Most recent value: end-of-day for past days, but for today it keeps
        # draining as the day goes on -- readiness uses the wake value instead.
        "body_battery": stats.get("bodyBatteryMostRecentValue"),
        "body_battery_wake": stats.get("bodyBatteryAtWakeTime"),
        "body_battery_charged": stats.get("bodyBatteryChargedValue"),
        "avg_stress": avg_stress,
        "sleep_hours": round(sleep_seconds / 3600, 2) if sleep_seconds else None,
        "sleep_score": overall_score.get("value"),
        "sleep_deep_min": stage_minutes("deepSleepSeconds"),
        "sleep_light_min": stage_minutes("lightSleepSeconds"),
        "sleep_rem_min": stage_minutes("remSleepSeconds"),
        "sleep_awake_min": stage_minutes("awakeSleepSeconds"),
        # Wrist-sensor trends only -- shown as context, never used for alerts.
        "respiration_avg": sleep_field("averageRespirationValue"),
        "spo2_avg": sleep_field("averageSpO2Value"),
        "spo2_lowest": sleep_field("lowestSpO2Value"),
        "sleep_stress": sleep_field("avgSleepStress"),
        "hrv_avg": hrv_summary.get("lastNightAvg"),
        "hrv_status": hrv_summary.get("status"),
        "errors": errors,
    }


def _is_wellness_day_cacheable(day: dict, today: datetime.date) -> bool:
    if day["errors"]:
        return False
    age = (today - datetime.date.fromisoformat(day["date"])).days
    if age < WELLNESS_RECENT_DAYS_ALWAYS_REFETCH:
        return False
    if age >= WELLNESS_SETTLED_DAYS:
        return True
    return day["sleep_hours"] is not None and day["resting_hr"] is not None


def _wellness_day(client, d: str) -> dict:
    with _wellness_cache_lock:
        cached = _wellness_cache.get(d)
        day_lock = _wellness_day_locks.setdefault(d, threading.Lock())
    if cached is not None:
        return dict(cached)

    with day_lock:  # concurrent requests for the same day wait for one fetch
        with _wellness_cache_lock:
            cached = _wellness_cache.get(d)
            recent = _recent_wellness_cache.get(d)
        if cached is not None:
            return dict(cached)
        if recent is not None and _monotonic() - recent[0] < WELLNESS_RECENT_TTL_S:
            return dict(recent[1])

        day = _fetch_wellness_day(client, d)
        with _wellness_cache_lock:
            if _is_wellness_day_cacheable(day, datetime.date.today()):
                _wellness_cache[d] = day
                _recent_wellness_cache.pop(d, None)
            elif not day["errors"]:  # failures are retried on the next request
                _recent_wellness_cache[d] = (_monotonic(), day)
        return dict(day)


def _wellness_days(client, start: datetime.date, end: datetime.date) -> list[dict]:
    """Normalized days start..end inclusive; persists any newly cached days."""
    with _wellness_cache_lock:
        before = len(_wellness_cache)
    days = []
    current = start
    while current <= end:
        days.append(_wellness_day(client, current.isoformat()))
        current += datetime.timedelta(days=1)
    with _wellness_cache_lock:
        grew = len(_wellness_cache) != before
    if grew:
        _save_wellness_cache_to_disk()
    return days


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


def _active_mesocycle_row(date: str) -> dict | None:
    """The mesocycle row (Coach Memory) whose date range contains `date`, if any."""
    if not COACH_DB_PATH.exists():
        return None
    with get_coach_db() as conn:
        row = conn.execute(
            "SELECT id, phase_name, start_date, end_date, status, weekly_km_csv, "
            "deload_week, target_race, target_race_date, notes FROM mesocycle "
            "WHERE status = 'active' AND start_date <= ? AND end_date >= ? "
            "ORDER BY start_date DESC LIMIT 1",
            (date, date),
        ).fetchone()
    return dict(row) if row else None


def _week_target_from_mesocycle(date: str) -> int | None:
    meso = _active_mesocycle_row(date)
    if not meso:
        return None
    start = datetime.date.fromisoformat(meso["start_date"])
    d = datetime.date.fromisoformat(date)
    week_idx = (d - start).days // 7
    weeks = [float(x) for x in meso["weekly_km_csv"].split(",")]
    if 0 <= week_idx < len(weeks):
        return round(weeks[week_idx])
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
    return {"days": _wellness_days(client, start, end)}


SHORT_SLEEP_HOURS = 6
HIGH_STRESS_LEVEL = 45     # Garmin 0-100 daily average; 26-50 is "low", 51+ "medium"
TRAINING_READINESS_LOW = 40
LOW_BODY_BATTERY_AT_WAKE = 25


def _garmin_training_readiness(client, date: str) -> dict | None:
    """Garmin's own Training Readiness for `date`, if the device provides it.

    Not every watch computes it (e.g. Forerunner 255 doesn't), in which case
    Garmin returns an empty list and this returns None -- the readiness verdict
    then just relies on our own signals.
    """
    try:
        entries = client.get_training_readiness(date) or []
    except Exception:
        return None
    if isinstance(entries, dict):
        entries = [entries]
    scored = [e for e in entries if isinstance(e, dict) and e.get("score") is not None]
    if not scored:
        return None
    latest = max(scored, key=lambda e: e.get("timestamp") or "")
    return {"score": latest["score"], "level": latest.get("level")}


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
    days_list = _wellness_days(client, window_start, today)

    reasons = []
    flags = 0

    last_night = days_list[-1] if days_list else {}
    last_night_errors = last_night.get("errors") or []

    sleep_hours = last_night.get("sleep_hours")
    sleep_pending = sleep_hours is None
    if not sleep_pending:
        if sleep_hours < SHORT_SLEEP_HOURS:
            flags += 1
            reasons.append(f"Sono curto: {sleep_hours}h esta noite (minimo recomendado 7h)")
        elif sleep_hours < 7:
            reasons.append(f"Sono abaixo do ideal: {sleep_hours}h (recomendado 7h+)")
    elif "sleep" in last_night_errors:
        reasons.append("Falha ao obter o sono do Garmin -- trata o sono como indisponivel, nao como noite sem dormir")
    else:
        reasons.append(
            "Sono da ultima noite ainda nao sincronizado (costuma aparecer apos acordar e sincronizar o relogio) "
            "-- ausencia de dados nao significa noite sem dormir"
        )

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

    # Judge readiness on the wake value: the live value drains through the day,
    # so an evening page load would otherwise flag a morning that was fine.
    body_battery_wake = last_night.get("body_battery_wake")
    charged = last_night.get("body_battery_charged")
    body_battery_info = None
    if body_battery_wake is not None:
        charged_txt = f" (+{charged} recarregado durante a noite)" if charged is not None else ""
        if body_battery_wake < LOW_BODY_BATTERY_AT_WAKE:
            flags += 1
            reasons.append(f"Body Battery baixo ao acordar: {body_battery_wake}/100{charged_txt}")
        else:
            body_battery_info = f"Body Battery ao acordar: {body_battery_wake}/100{charged_txt}"

    # Short sleep on its own is already a flag; after a high-stress day it's a
    # compounded recovery risk. Yesterday is the last complete day of stress.
    yesterday_stress = days_list[-2].get("avg_stress") if len(days_list) >= 2 else None
    if (
        sleep_hours is not None and sleep_hours < SHORT_SLEEP_HOURS
        and yesterday_stress is not None and yesterday_stress >= HIGH_STRESS_LEVEL
    ):
        flags += 1
        reasons.append(
            f"Sono curto depois de um dia de stress elevado (stress medio ontem {yesterday_stress}) "
            "-- combinacao ruim para treino intenso"
        )

    training_readiness = _garmin_training_readiness(client, today_str)
    if training_readiness is not None:
        score, level = training_readiness["score"], training_readiness["level"]
        if score < TRAINING_READINESS_LOW:
            flags += 1
            reasons.append(f"Training Readiness do Garmin baixo: {score}/100 ({level})")
        else:
            reasons.append(f"Training Readiness do Garmin: {score}/100 ({level})")

    # Without last night's sleep a clean slate is not evidence of readiness, so
    # don't call it "apto"; warning flags from other signals still stand.
    if flags == 0 and sleep_pending:
        verdict, label = "pendente", "Aguardando dados da noite"
    elif flags == 0:
        verdict, label = "apto", "Apto para o treino de hoje"
    elif flags == 1:
        verdict, label = "cautela", "Apto com cautela"
    else:
        verdict, label = "nao_apto", "Considera ajustar ou adiar"

    if not reasons:
        reasons.append("Sinais de recuperacao dentro do normal")
    if body_battery_info:
        reasons.append(body_battery_info)

    hrv_pending = last_night.get("hrv_avg") is None
    confidence = "alta" if not (sleep_pending or hrv_pending or last_night_errors) else "parcial"

    plan = _plan_for_date(today_str)
    next_plan = _next_plan_after(today_str) if not plan else None

    return {
        "date": today_str,
        "verdict": verdict,
        "label": label,
        "confidence": confidence,
        "flags": flags,
        "reasons": reasons,
        "training_readiness": training_readiness,
        "yesterday_stress": yesterday_stress,
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

    target_km = _week_target_from_mesocycle(today.isoformat())

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


def _estimate_session_rpe(avg_hr: float | None) -> tuple[float, str]:
    """Fallback RPE (0-10) when no manual rating exists. Returns (rpe, source_label)."""
    if avg_hr is None:
        return DEFAULT_RPE_NO_HR, "default_estimate"
    return ZONE_RPE[_bucket_zone(avg_hr)], "hr_zone_estimate"


def _activity_load(client, activity_summary: dict) -> dict:
    """Load contribution for one activity (Foster session-RPE: rpe * duration_min).

    `activity_summary` is one element from client.get_activities_by_date(...),
    which already has duration/averageHR/startTimeLocal for free. Only the
    manually-entered RPE (directWorkoutRpe) requires a per-activity API call,
    so results are cached by activity_id -- past activities are immutable
    once outside the RECENT_DAYS_ALWAYS_RECOMPUTE window.
    """
    activity_id = str(activity_summary.get("activityId"))
    start = activity_summary.get("startTimeLocal") or ""
    act_date = start[:10]
    is_recent = act_date >= (
        datetime.date.today() - datetime.timedelta(days=RECENT_DAYS_ALWAYS_RECOMPUTE)
    ).isoformat()

    with _load_cache_lock:
        cached = _activity_load_cache.get(activity_id)
    if cached is not None and not is_recent:
        return cached

    duration_min = (activity_summary.get("duration") or 0) / 60
    avg_hr = activity_summary.get("averageHR")
    manual_rpe_raw = None
    try:
        full = client.get_activity(activity_id) or {}
        manual_rpe_raw = (full.get("summaryDTO") or {}).get("directWorkoutRpe")
    except Exception:
        pass

    if manual_rpe_raw:  # 0 and None both mean "not rated"
        rpe, source = manual_rpe_raw / 10.0, "manual_rpe"
    else:
        rpe, source = _estimate_session_rpe(avg_hr)

    result = {"rpe": rpe, "duration_min": duration_min, "avg_hr": avg_hr, "date": act_date, "source": source}
    with _load_cache_lock:
        _activity_load_cache[activity_id] = result
    _save_training_load_cache_to_disk()
    return result


def _get_daily_load(client, d: str) -> dict:
    """daily_load(d) = sum(rpe_i * duration_min_i) over d's activities. Cached per-date."""
    is_recent = d >= (datetime.date.today() - datetime.timedelta(days=RECENT_DAYS_ALWAYS_RECOMPUTE)).isoformat()
    with _load_cache_lock:
        cached = _daily_load_cache.get(d)
    if cached is not None and not is_recent:
        return cached

    try:
        activities = client.get_activities_by_date(d, d) or []
    except Exception:
        activities = []

    total = 0.0
    ids = []
    for a in activities:
        info = _activity_load(client, a)
        total += info["rpe"] * info["duration_min"]
        ids.append(str(a.get("activityId")))

    result = {"daily_load": round(total, 1), "activity_ids": ids}
    with _load_cache_lock:
        _daily_load_cache[d] = result
    _save_training_load_cache_to_disk()
    return result


def _compute_ctl_atl_series(daily_loads: list[float]) -> list[dict]:
    """daily_loads: ascending, oldest->newest. Returns [{ctl, atl, tsb}, ...], same length.

    CTL[i] = CTL[i-1] + (load[i] - CTL[i-1]) * (1 - e^(-1/42))
    ATL[i] = ATL[i-1] + (load[i] - ATL[i-1]) * (1 - e^(-1/7))
    TSB[i] = CTL[i] - ATL[i]  (labelled PRT in the UI)
    Seed: CTL[0] = ATL[0] = mean(daily_load[0:7]) -- avoids a fake "building fitness
    from zero" ramp for an athlete who already has training history before the window.
    """
    if not daily_loads:
        return []
    seed = sum(daily_loads[:SEED_DAYS]) / min(SEED_DAYS, len(daily_loads))
    ctl_alpha = 1 - math.exp(-1 / CTL_DAYS)
    atl_alpha = 1 - math.exp(-1 / ATL_DAYS)
    ctl = atl = seed
    out = []
    for i, load in enumerate(daily_loads):
        if i == 0:
            ctl, atl = seed, seed
        else:
            ctl = ctl + (load - ctl) * ctl_alpha
            atl = atl + (load - atl) * atl_alpha
        out.append({"ctl": round(ctl, 1), "atl": round(atl, 1), "tsb": round(ctl - atl, 1)})
    return out


@app.on_event("startup")
def _warm_training_load_cache():
    """Pre-fill the training-load cache in the background so the first real
    page load after a fresh `uvicorn` start (iniciar_dashboard.bat) doesn't
    have to pay the full warm-up cost synchronously."""

    def _warm():
        try:
            client = get_client()
            end = datetime.date.today()
            for i in range(LOAD_HISTORY_DAYS):
                d = (end - datetime.timedelta(days=i)).isoformat()
                _get_daily_load(client, d)
        except Exception:
            pass  # best-effort; /api/training_load fills any gaps on demand

    threading.Thread(target=_warm, daemon=True).start()


@app.get("/api/training_load")
def api_training_load():
    """CTL/ATL/TSB ('Momento do atleta') via Foster's session-RPE method.

    Fixed LOAD_HISTORY_DAYS lookback returned in full; the UI's 28/42/90/Tudo
    toggle slices this client-side. Historical days are cached to disk (see
    TRAINING_LOAD_CACHE_PATH); only the last RECENT_DAYS_ALWAYS_RECOMPUTE days
    are ever refetched from Garmin.
    """
    client = get_client()
    end = datetime.date.today()
    start = end - datetime.timedelta(days=LOAD_HISTORY_DAYS - 1)

    dates = []
    daily_loads = []
    d = start
    while d <= end:
        ds = d.isoformat()
        info = _get_daily_load(client, ds)
        dates.append(ds)
        daily_loads.append(info["daily_load"])
        d += datetime.timedelta(days=1)

    ewma = _compute_ctl_atl_series(daily_loads)
    series = [{"date": dates[i], "daily_load": daily_loads[i], **ewma[i]} for i in range(len(dates))]

    latest = series[-1]
    vcc = round(latest["ctl"] - series[-8]["ctl"], 1) if len(series) > 7 else None

    return {
        "history_days": LOAD_HISTORY_DAYS,
        "series": series,
        "latest": {"date": latest["date"], "ctl": latest["ctl"], "atl": latest["atl"], "tsb": latest["tsb"], "vcc_7d": vcc},
    }


@app.get("/api/periodization")
def api_periodization():
    """Current mesocycle phase, week-by-week targets, and progress from Coach Memory."""
    today_str = datetime.date.today().isoformat()
    meso = _active_mesocycle_row(today_str)
    if not meso:
        return {"active": False, "mesocycle": None}

    start = datetime.date.fromisoformat(meso["start_date"])
    end = datetime.date.fromisoformat(meso["end_date"])
    today = datetime.date.today()
    weeks = [float(x) for x in meso["weekly_km_csv"].split(",")]
    week_idx = (today - start).days // 7
    deload_week = meso["deload_week"]

    weeks_out = [
        {
            "week_number": i + 1,
            "week_start": (start + datetime.timedelta(days=7 * i)).isoformat(),
            "target_km": w,
            "is_deload": deload_week == i + 1,
            "is_current": i == week_idx,
        }
        for i, w in enumerate(weeks)
    ]
    total_days = max(1, (end - start).days)
    progress_pct = round(min(100, max(0, (today - start).days / total_days * 100)))

    return {
        "active": True,
        "mesocycle": {
            "id": meso["id"],
            "phase_name": meso["phase_name"],
            "start_date": meso["start_date"],
            "end_date": meso["end_date"],
            "current_week": min(max(week_idx + 1, 1), len(weeks)),
            "total_weeks": len(weeks),
            "progress_pct": progress_pct,
            "target_race": meso["target_race"],
            "target_race_date": meso["target_race_date"],
            "weeks": weeks_out,
        },
    }


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
        night = _wellness_day(client, next_day)
        out.append({
            "run_date": run_date.isoformat(),
            "run_name": a.get("activityName"),
            "run_distance_km": round((a.get("distance") or 0) / 1000, 1),
            "next_night_date": next_day,
            "sleep_hours": night["sleep_hours"],
            "sleep_score": night["sleep_score"],
        })
    _save_wellness_cache_to_disk()
    return {"runs": out}


@app.get("/api/overtraining_risk")
def api_overtraining_risk(days: int = Query(default=21, ge=7, le=42)):
    """Multi-week overtraining assessment (HRV, RHR, body battery, sleep trends)."""
    client = get_client()
    today = datetime.date.today()
    start = today - datetime.timedelta(days=days - 1)

    daily = _wellness_days(client, start, today)

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
    target_km = _week_target_from_mesocycle(closed_start.isoformat())

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

    wellness = _wellness_days(client, prior_start, closed_end)
    recovery = _weekly_recovery_comparison(wellness[7:], wellness[:7])

    return {
        "week_start": closed_start.isoformat(),
        "week_end": closed_end.isoformat(),
        "closed": closed,
        "prior": prior,
        "target_km": target_km,
        "pace_delta_min_per_km": pace_delta,
        "flags": flags,
        "recovery": recovery,
    }


RECOVERY_METRICS = {
    # key: (wellness-day field, decimals -- None rounds to an int)
    "sleep_hours": ("sleep_hours", 1),
    "hrv_avg": ("hrv_avg", None),
    "resting_hr": ("resting_hr", None),
    "avg_stress": ("avg_stress", None),
    "body_battery_end": ("body_battery", None),
    "deep_sleep_min": ("sleep_deep_min", None),
    "rem_sleep_min": ("sleep_rem_min", None),
}
LOW_WEEKLY_SLEEP_HOURS = 6.5


def _recovery_averages(days: list[dict]) -> dict:
    out = {}
    for key, (field, decimals) in RECOVERY_METRICS.items():
        values = [d[field] for d in days if d.get(field) is not None]
        out[key] = round(sum(values) / len(values), decimals) if values else None
    out["days_with_sleep"] = sum(1 for d in days if d.get("sleep_hours") is not None)
    out["days_with_hrv"] = sum(1 for d in days if d.get("hrv_avg") is not None)
    return out


def _pct_change(current, previous):
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous * 100


def _weekly_recovery_comparison(week: list[dict], prior_week: list[dict]) -> dict:
    """Closed week's recovery averages vs the week before, plus bottlenecks.

    Thresholds follow the overtraining/readiness checks: sustained short sleep,
    elevated stress, and material swings versus the previous week. Sparse data
    is called out instead of being over-interpreted.
    """
    current = _recovery_averages(week)
    previous = _recovery_averages(prior_week)
    delta = {
        key: round(current[key] - previous[key], decimals)
        if current[key] is not None and previous[key] is not None else None
        for key, (_, decimals) in RECOVERY_METRICS.items()
    }

    bottlenecks = []
    if current["sleep_hours"] is not None and current["sleep_hours"] < LOW_WEEKLY_SLEEP_HOURS:
        bottlenecks.append(
            f"Sono medio de {current['sleep_hours']}h na semana, abaixo de {LOW_WEEKLY_SLEEP_HOURS}h "
            "-- a recuperacao pode ser o fator limitante"
        )
    sleep_pct = _pct_change(current["sleep_hours"], previous["sleep_hours"])
    if sleep_pct is not None and sleep_pct < -10:
        bottlenecks.append(f"Sono caiu {abs(sleep_pct):.0f}% vs semana anterior")
    hrv_pct = _pct_change(current["hrv_avg"], previous["hrv_avg"])
    if hrv_pct is not None and hrv_pct < -10:
        bottlenecks.append(f"HRV media caiu {abs(hrv_pct):.0f}% vs semana anterior")
    if delta["resting_hr"] is not None and delta["resting_hr"] >= 3:
        bottlenecks.append(f"FC repouso media subiu {delta['resting_hr']:.0f}bpm vs semana anterior")
    if current["avg_stress"] is not None and current["avg_stress"] >= HIGH_STRESS_LEVEL:
        bottlenecks.append(f"Stress medio elevado na semana ({current['avg_stress']:.0f})")
    stress_pct = _pct_change(current["avg_stress"], previous["avg_stress"])
    if stress_pct is not None and stress_pct > 20:
        bottlenecks.append(f"Stress subiu {stress_pct:.0f}% vs semana anterior")
    if current["days_with_hrv"] < 3:
        bottlenecks.append(f"HRV so em {current['days_with_hrv']} de 7 noites -- nao da pra tirar conclusoes de HRV")
    if current["days_with_sleep"] < 3:
        bottlenecks.append(f"Sono so em {current['days_with_sleep']} de 7 noites -- baixa confianca")
    if not bottlenecks:
        bottlenecks.append("Recuperacao estavel vs semana anterior")

    days_with_sleep = current["days_with_sleep"]
    confidence = "alta" if days_with_sleep >= 5 else ("media" if days_with_sleep >= 3 else "baixa")

    return {
        "current": current,
        "previous": previous,
        "delta": delta,
        "bottlenecks": bottlenecks,
        "confidence": confidence,
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


@app.get("/api/training_calendar")
def api_training_calendar(year: int = Query(default=None, ge=2000, le=2100)):
    """Year-at-a-glance training calendar (Strava-style): weekly sparkline,
    year totals (hours/km/PRs/activities) and one card per month with a
    daily-hours bar chart.
    """
    client = get_client()
    today = datetime.date.today()
    year = year or today.year
    year_start = datetime.date(year, 1, 1)
    year_end = datetime.date(year, 12, 31)
    range_end = min(year_end, today)

    activities = []
    if range_end >= year_start:
        try:
            activities = client.get_activities_by_date(
                year_start.isoformat(), range_end.isoformat()
            ) or []
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc

    daily_hours: dict[str, float] = {}
    total_duration_s = 0.0
    total_distance_m = 0.0
    for a in activities:
        start_time = a.get("startTimeLocal")
        if not start_time:
            continue
        try:
            d = datetime.date.fromisoformat(start_time[:10])
        except ValueError:
            continue
        if d.year != year:
            continue
        duration = a.get("duration") or 0
        daily_hours[d.isoformat()] = daily_hours.get(d.isoformat(), 0.0) + duration / 3600
        total_duration_s += duration
        total_distance_m += a.get("distance") or 0

    # Weekly (Monday-start) hours strip spanning the whole year, ~52-53 bars.
    first_monday = year_start - datetime.timedelta(days=year_start.weekday())
    weeks_out = []
    wk = first_monday
    while wk <= year_end:
        week_hours = sum(
            daily_hours.get((wk + datetime.timedelta(days=i)).isoformat(), 0.0)
            for i in range(7)
            if (wk + datetime.timedelta(days=i)).year == year
        )
        weeks_out.append({"week_start": wk.isoformat(), "hours": round(week_hours, 2)})
        wk += datetime.timedelta(days=7)

    # Personal records achieved during this year.
    try:
        records = client.get_personal_record() or []
    except Exception:
        records = []
    pr_count = 0
    for r in records:
        ts = r.get("activityStartDateTimeLocalFormatted") or r.get("actStartDateTimeInGMTFormatted")
        if ts and ts[:4] == str(year):
            pr_count += 1

    months_out = []
    for m in range(1, 13):
        month_start = datetime.date(year, m, 1)
        if month_start > today:
            months_out.append({"month": m, "hours": 0.0, "days": []})
            continue
        next_month = datetime.date(year + 1, 1, 1) if m == 12 else datetime.date(year, m + 1, 1)
        days_out = []
        month_hours = 0.0
        d = month_start
        while d < next_month:
            h = daily_hours.get(d.isoformat(), 0.0)
            month_hours += h
            days_out.append(round(h, 2))
            d += datetime.timedelta(days=1)
        months_out.append({"month": m, "hours": round(month_hours, 1), "days": days_out})

    return {
        "year": year,
        "weeks": weeks_out,
        "months": months_out,
        "totals": {
            "hours": round(total_duration_s / 3600, 1),
            "km": round(total_distance_m / 1000, 1),
            "personal_records": pr_count,
            "activities": len(activities),
        },
    }


_INDEX_HTML = Path(__file__).resolve().parent / "static" / "index.html"


@app.get("/")
def index():
    return FileResponse(_INDEX_HTML)
