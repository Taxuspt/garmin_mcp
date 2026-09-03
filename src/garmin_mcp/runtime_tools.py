"""Intent-level runtime tools: authentication diagnostics and daily briefing."""

from __future__ import annotations

import os
import stat
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from garmin_mcp.runtime import GarminClientProvider, GarminGateway


class AuthCheckResult(BaseModel):
    """Structured, secret-free Garmin authentication diagnostic."""

    status: Literal["ready", "unverified", "error"]
    verify_requested: bool
    network_checked: bool
    authenticated: bool | None = None
    region: Literal["international", "china"]
    token_store: dict[str, Any]
    provider: dict[str, Any]
    gateway: dict[str, int]
    recommendation: str | None = None


class BriefingSection(BaseModel):
    """One independently fetched portion of a briefing."""

    status: Literal["ok", "stale", "error"]
    data: Any | None = None
    message: str | None = None


class BriefingResult(BaseModel):
    """Bounded, partial-failure-safe daily Garmin briefing."""

    date: str
    generated_at: str
    sections: dict[str, BriefingSection]
    request_budget: dict[str, int]
    cache: dict[str, int]
    warnings: list[str] = Field(default_factory=list)


_provider: GarminClientProvider | None = None
_gateway: GarminGateway | None = None
_token_path: str | None = None
_is_cn = False

# Eight logical reads; each can make up to the gateway's configured retry count.
_BRIEFING_ENDPOINT_CALLS = 8


def configure(
    provider: GarminClientProvider,
    gateway: GarminGateway,
    *,
    token_path: str,
    is_cn: bool,
) -> None:
    """Configure runtime services without initializing the Garmin client."""
    global _provider, _gateway, _token_path, _is_cn
    _provider = provider
    _gateway = gateway
    _token_path = token_path
    _is_cn = is_cn


def _services() -> tuple[GarminClientProvider, GarminGateway]:
    if _provider is None or _gateway is None:
        raise RuntimeError("Garmin runtime services have not been configured")
    return _provider, _gateway


def _token_store_status(path_value: str | None) -> dict[str, Any]:
    path = Path(path_value).expanduser() if path_value else None
    result: dict[str, Any] = {
        "path": str(path) if path else None,
        "exists": False,
        "readable": False,
        "secure_permissions": None,
    }
    if path is None:
        return result

    try:
        result["exists"] = path.exists()
        if not result["exists"]:
            return result
        result["kind"] = "directory" if path.is_dir() else "file"
        result["readable"] = os.access(path, os.R_OK)
        paths = [path]
        if path.is_dir():
            paths.extend(item for item in path.iterdir() if item.is_file())
        insecure = []
        for item in paths:
            mode = stat.S_IMODE(item.stat().st_mode)
            if mode & 0o077:
                insecure.append({"name": item.name, "mode": oct(mode)})
        result["secure_permissions"] = not insecure
        if insecure:
            result["insecure_entries"] = insecure
    except OSError as exc:
        result["inspection_error"] = str(exc)
    return result


def _nonempty(data: Any) -> bool:
    return data is not None and data != {} and data != []


def _fetch_section(
    fetch: Callable[[], Any], transform: Callable[[Any], Any], empty_message: str
) -> BriefingSection:
    try:
        raw = fetch()
        if not _nonempty(raw):
            return BriefingSection(status="stale", message=empty_message)
        transformed = transform(raw)
        if not _nonempty(transformed):
            return BriefingSection(status="stale", message=empty_message)
        return BriefingSection(status="ok", data=transformed)
    except Exception as exc:
        return BriefingSection(
            status="error",
            message=f"{type(exc).__name__}: {exc}",
        )


def _curate_readiness(raw: Any) -> Any:
    entries = raw if isinstance(raw, list) else [raw]
    curated = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        row = {
            "date": item.get("calendarDate"),
            "timestamp": item.get("timestampLocal"),
            "context": item.get("inputContext"),
            "score": item.get("score"),
            "level": item.get("level"),
            "feedback": item.get("feedbackShort"),
            "acute_load": item.get("acuteLoad"),
            "hrv_weekly_average": item.get("hrvWeeklyAverage"),
            "sleep_score": item.get("sleepScore"),
            "recovery_time_minutes": item.get("recoveryTime"),
        }
        curated.append({key: value for key, value in row.items() if value is not None})
    return curated


def _curate_sleep(raw: dict[str, Any]) -> dict[str, Any]:
    daily = raw.get("dailySleepDTO") or {}
    score = (daily.get("sleepScores") or {}).get("overall") or {}
    result = {
        "date": daily.get("calendarDate"),
        "sleep_seconds": daily.get("sleepTimeSeconds"),
        "deep_sleep_seconds": daily.get("deepSleepSeconds"),
        "rem_sleep_seconds": daily.get("remSleepSeconds"),
        "awake_sleep_seconds": daily.get("awakeSleepSeconds"),
        "sleep_score": score.get("value"),
        "sleep_score_quality": score.get("qualifierKey"),
        "resting_hr_bpm": daily.get("restingHeartRate"),
        "average_sleep_stress": daily.get("avgSleepStress"),
        "overnight_hrv_ms": raw.get("avgOvernightHrv"),
    }
    return {key: value for key, value in result.items() if value is not None}


def _curate_hrv(raw: dict[str, Any]) -> dict[str, Any]:
    summary = raw.get("hrvSummary") or {}
    baseline = summary.get("baseline") or {}
    result = {
        "date": summary.get("calendarDate"),
        "last_night_avg_ms": summary.get("lastNightAvg", summary.get("lastNight")),
        "last_night_5min_high_ms": summary.get("lastNight5MinHigh"),
        "weekly_avg_ms": summary.get("weeklyAvg"),
        "status": summary.get("status"),
        "feedback": summary.get("feedbackPhrase"),
        "baseline_balanced_low_ms": baseline.get("balancedLow"),
        "baseline_balanced_upper_ms": baseline.get("balancedUpper"),
    }
    return {key: value for key, value in result.items() if value is not None}


def _first_mapping_value(value: Any, *, prefer_primary: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    fallback: dict[str, Any] = {}
    for item in value.values():
        if not isinstance(item, dict):
            continue
        if not fallback:
            fallback = item
        if prefer_primary and item.get("primaryTrainingDevice"):
            return item
    return fallback


def _curate_training_status(raw: dict[str, Any]) -> dict[str, Any]:
    recent = raw.get("mostRecentTrainingStatus") or {}
    device = _first_mapping_value(
        recent.get("latestTrainingStatusData"), prefer_primary=True
    )
    load = device.get("acuteTrainingLoadDTO") or {}
    balance = raw.get("mostRecentTrainingLoadBalance") or {}
    balance_device = _first_mapping_value(
        balance.get("metricsTrainingLoadBalanceDTOMap"), prefer_primary=True
    )
    vo2 = raw.get("mostRecentVO2Max") or {}
    run_vo2 = vo2.get("generic") or {}
    cycling_vo2 = vo2.get("cycling") or {}
    acute = load.get("dailyTrainingLoadAcute")
    chronic = load.get("dailyTrainingLoadChronic")
    result = {
        "date": device.get("calendarDate"),
        "training_status": device.get("trainingStatus"),
        "feedback": device.get("trainingStatusFeedbackPhrase"),
        "fitness_trend": device.get("fitnessTrend"),
        "acute_load": acute,
        "chronic_load": chronic,
        "tsb": (
            round(chronic - acute, 1)
            if isinstance(acute, (int, float)) and isinstance(chronic, (int, float))
            else None
        ),
        "acwr": load.get("dailyAcuteChronicWorkloadRatio"),
        "acwr_status": load.get("acwrStatus"),
        "load_focus_feedback": balance_device.get("trainingBalanceFeedbackPhrase"),
        "running_vo2_max": run_vo2.get("vo2MaxValue"),
        "cycling_vo2_max": cycling_vo2.get("vo2MaxValue"),
    }
    return {key: value for key, value in result.items() if value is not None}


def _curate_activities(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("activities") or raw.get("payload") or []
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw[:5]:
        if not isinstance(item, dict):
            continue
        activity_type = item.get("activityType") or {}
        row = {
            "id": item.get("activityId"),
            "name": item.get("activityName"),
            "sport": activity_type.get("typeKey"),
            "start_time": item.get("startTimeLocal"),
            "duration_seconds": item.get("duration"),
            "distance_meters": item.get("distance"),
            "average_hr_bpm": item.get("averageHR"),
            "average_power_watts": item.get("avgPower"),
            "training_effect_aerobic": item.get("aerobicTrainingEffect"),
            "training_effect_anaerobic": item.get("anaerobicTrainingEffect"),
        }
        result.append({key: value for key, value in row.items() if value is not None})
    return result


_CHANGE_METRICS: tuple[tuple[str, str, float, str], ...] = (
    ("steps", "totalSteps", 20.0, "percent"),
    ("resting_hr_bpm", "restingHeartRate", 5.0, "absolute"),
    ("average_stress", "averageStressLevel", 10.0, "absolute"),
    ("body_battery", "bodyBatteryMostRecentValue", 15.0, "absolute"),
    ("sleep_seconds", "sleepingSeconds", 3600.0, "absolute"),
)


def _build_changes(
    current: Any,
    prior_7: Any,
    prior_28: Any,
    current_date: str,
    date_7: str,
    date_28: str,
) -> dict[str, Any]:
    current = current if isinstance(current, dict) else {}
    references = (("7_day", date_7, prior_7), ("28_day", date_28, prior_28))
    comparisons = []
    significant = []
    for window, reference_date, reference_raw in references:
        reference = reference_raw if isinstance(reference_raw, dict) else {}
        for label, key, threshold, mode in _CHANGE_METRICS:
            now_value = current.get(key)
            old_value = reference.get(key)
            if not isinstance(now_value, (int, float)) or not isinstance(
                old_value, (int, float)
            ):
                continue
            delta = now_value - old_value
            delta_pct = (
                round(delta / old_value * 100, 1) if old_value != 0 else None
            )
            is_significant = (
                abs(delta_pct) >= threshold
                if mode == "percent" and delta_pct is not None
                else abs(delta) >= threshold
            )
            row = {
                "window": window,
                "reference_date": reference_date,
                "metric": label,
                "current": now_value,
                "reference": old_value,
                "delta": round(delta, 1),
                "delta_percent": delta_pct,
                "significant": is_significant,
            }
            comparisons.append(row)
            if is_significant:
                significant.append(row)
    return {
        "current_date": current_date,
        "comparisons": comparisons,
        "significant": significant,
    }


def register_tools(app):
    """Register runtime-level tools."""

    @app.tool(structured_output=True)
    async def check_garmin_auth(verify: bool = False) -> AuthCheckResult:
        """Inspect Garmin authentication without exposing token contents.

        By default this is local-only and does not log in. Set ``verify`` to
        true to perform the lazy network login now.
        """
        provider, gateway = _services()
        token_status = _token_store_status(_token_path)
        authenticated: bool | None = None
        recommendation: str | None = None

        if verify:
            try:
                client = provider.get_client(force_retry=True)
                # Login establishes a session; this small authenticated read
                # confirms that an already initialized session remains valid.
                client.get_full_name()
                authenticated = True
            except Exception as exc:
                authenticated = False
                recommendation = str(exc)
        elif not token_status.get("exists"):
            recommendation = (
                "No token store was found. Run 'garmin-mcp-auth' before using "
                "Garmin tools."
            )
        elif token_status.get("secure_permissions") is False:
            recommendation = (
                "Token files are readable by other users; run 'garmin-mcp-auth' "
                "or restrict them to owner-only permissions."
            )

        provider_status = provider.snapshot()
        if authenticated is False:
            status: Literal["ready", "unverified", "error"] = "error"
        elif authenticated is True or provider_status["initialized"]:
            status: Literal["ready", "unverified", "error"] = "ready"
        elif provider_status["state"] == "error":
            status = "error"
        else:
            status = "unverified"

        return AuthCheckResult(
            status=status,
            verify_requested=verify,
            network_checked=verify,
            authenticated=authenticated,
            region="china" if _is_cn else "international",
            token_store=token_status,
            provider=provider_status,
            gateway=gateway.stats(),
            recommendation=recommendation,
        )

    @app.tool(structured_output=True)
    async def get_briefing(date: str) -> BriefingResult:
        """Return a compact daily training briefing in at most eight API reads.

        Readiness, sleep, HRV, load, recent activities, and 7/28-day snapshot
        changes fail independently. Cached reads do not consume a network call.
        """
        _provider_value, gateway = _services()
        try:
            briefing_date = Date.fromisoformat(date)
        except ValueError as exc:
            raise ValueError("date must use YYYY-MM-DD format") from exc

        date_7 = (briefing_date - timedelta(days=7)).isoformat()
        date_28 = (briefing_date - timedelta(days=28)).isoformat()
        before = gateway.stats()

        sections: dict[str, BriefingSection] = {}
        sections["readiness"] = _fetch_section(
            lambda: gateway.get_training_readiness(date),
            _curate_readiness,
            f"No training readiness data for {date}",
        )
        sections["sleep"] = _fetch_section(
            lambda: gateway.get_sleep_data(date),
            _curate_sleep,
            f"No sleep data for {date}",
        )
        sections["hrv"] = _fetch_section(
            lambda: gateway.get_hrv_data(date),
            _curate_hrv,
            f"No HRV data for {date}",
        )
        sections["training_state"] = _fetch_section(
            lambda: gateway.get_training_status(date),
            _curate_training_status,
            f"No training status data for {date}",
        )
        sections["recent_activities"] = _fetch_section(
            lambda: gateway.get_activities(0, 5),
            _curate_activities,
            "No recent activities were returned",
        )

        # Three fixed snapshot reads keep the briefing bounded. They show large
        # changes relative to the same athlete rather than guessing population
        # norms, and avoid the unbounded per-day loops in trend tools.
        current_stats: Any = None
        stats_7: Any = None
        stats_28: Any = None
        change_errors = []
        for label, stats_date in (
            ("current", date),
            ("7_day", date_7),
            ("28_day", date_28),
        ):
            try:
                value = gateway.get_stats(stats_date)
                if label == "current":
                    current_stats = value
                elif label == "7_day":
                    stats_7 = value
                else:
                    stats_28 = value
            except Exception as exc:
                change_errors.append(f"{label}: {type(exc).__name__}: {exc}")

        changes = _build_changes(current_stats, stats_7, stats_28, date, date_7, date_28)
        if changes["comparisons"]:
            sections["significant_changes"] = BriefingSection(
                status="ok",
                data=changes,
                message="; ".join(change_errors) if change_errors else None,
            )
        elif change_errors:
            sections["significant_changes"] = BriefingSection(
                status="error", message="; ".join(change_errors)
            )
        else:
            sections["significant_changes"] = BriefingSection(
                status="stale",
                data=changes,
                message="No comparable daily snapshots were returned",
            )

        after = gateway.stats()
        delta = {
            key: after.get(key, 0) - before.get(key, 0)
            for key in (
                "logical_calls",
                "remote_calls",
                "cache_hits",
                "cache_misses",
                "retries",
                "failures",
            )
        }
        warnings = [
            f"{name}: {section.message}"
            for name, section in sections.items()
            if section.status != "ok" and section.message
        ]
        return BriefingResult(
            date=date,
            generated_at=datetime.now(timezone.utc).isoformat(),
            sections=sections,
            request_budget={
                "logical_endpoint_calls_max": _BRIEFING_ENDPOINT_CALLS,
                "logical_endpoint_calls_used": delta["logical_calls"],
                "network_attempts_max": (
                    _BRIEFING_ENDPOINT_CALLS * gateway.max_read_attempts
                ),
                "network_attempts_used": delta["remote_calls"],
            },
            cache=delta,
            warnings=warnings,
        )

    return app
