"""
Historical analytics tools for Garmin Connect data.

These tools turn daily Garmin records into compact intelligence: baselines,
anomalies, lagged correlations, and weekly review notes. They intentionally
bound date windows so MCP calls stay predictable and do not return raw history.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context

from garmin_mcp.client_resolver import get_client


garmin_client = None

MAX_DAYS = 180
DEFAULT_DAYS = 90
DEFAULT_BASELINE_WINDOW = 28
DEFAULT_REPORT_METRICS = "steps,sleep_score,stress_avg,overnight_hrv,training_readiness,recovery_pressure"
REPORT_AGGREGATIONS = {"avg", "sum", "min", "max", "latest"}
REPORT_GROUPS = {"date", "week", "month"}


def configure(client):
    """Configure the module with the Garmin client instance."""
    global garmin_client
    garmin_client = client


def register_tools(app):
    """Register historical analytics tools with the MCP server app."""

    @app.tool()
    async def get_health_baselines(
        ctx: Context,
        end_date: str | None = None,
        days: int = DEFAULT_DAYS,
        baseline_window: int = DEFAULT_BASELINE_WINDOW,
    ) -> str:
        """Compare current health metrics against personal rolling baselines.

        Args:
            end_date: Last date to include in YYYY-MM-DD format. Defaults to today.
            days: Number of days to fetch, capped at 180.
            baseline_window: Rolling baseline window in days, capped by fetched history.
        """
        try:
            rows = _daily_rows(get_client(ctx), end_date=end_date, days=days)
            if not rows:
                return "No health data found for the requested window"
            return json.dumps(
                _baseline_payload(rows, baseline_window=baseline_window),
                indent=2,
            )
        except Exception as e:
            return f"Error calculating health baselines: {str(e)}"

    @app.tool()
    async def get_wellness_anomalies(
        ctx: Context,
        end_date: str | None = None,
        days: int = DEFAULT_DAYS,
        baseline_window: int = DEFAULT_BASELINE_WINDOW,
        z_threshold: float = 1.5,
    ) -> str:
        """Find unusual days compared with the user's own recent baseline.

        Args:
            end_date: Last date to include in YYYY-MM-DD format. Defaults to today.
            days: Number of days to fetch, capped at 180.
            baseline_window: Days before each row used as its comparison baseline.
            z_threshold: Minimum absolute z-score to report.
        """
        try:
            rows = _daily_rows(get_client(ctx), end_date=end_date, days=days)
            anomalies = _anomalies(
                rows,
                baseline_window=baseline_window,
                z_threshold=z_threshold,
            )
            return json.dumps(
                {
                    "window": _window(rows),
                    "baseline_window_days": min(max(7, baseline_window), MAX_DAYS),
                    "z_threshold": z_threshold,
                    "count": len(anomalies),
                    "anomalies": anomalies,
                },
                indent=2,
            )
        except Exception as e:
            return f"Error detecting wellness anomalies: {str(e)}"

    @app.tool()
    async def get_lagged_health_correlations(
        ctx: Context,
        end_date: str | None = None,
        days: int = DEFAULT_DAYS,
        max_lag_days: int = 7,
    ) -> str:
        """Find delayed relationships between health and training metrics.

        A lag of 1 means the left metric on one day is compared with the right
        metric on the following day. This is useful for effects such as hard
        training today versus HRV, resting heart rate, or readiness tomorrow.

        Args:
            end_date: Last date to include in YYYY-MM-DD format. Defaults to today.
            days: Number of days to fetch, capped at 180.
            max_lag_days: Largest lag to test, capped at 14 days.
        """
        try:
            rows = _daily_rows(get_client(ctx), end_date=end_date, days=days)
            correlations = _lagged_correlations(rows, max_lag_days=max_lag_days)
            return json.dumps(
                {
                    "window": _window(rows),
                    "max_lag_days": min(max(1, max_lag_days), 14),
                    "minimum_pairs": 8,
                    "strongest": correlations[:12],
                },
                indent=2,
            )
        except Exception as e:
            return f"Error calculating lagged correlations: {str(e)}"

    @app.tool()
    async def get_weekly_health_review(ctx: Context, end_date: str | None = None) -> str:
        """Create a compact weekly review from Garmin history.

        The review compares the last 7 days against the prior 7 days, includes
        unusual days, and highlights the strongest delayed metric relationships.

        Args:
            end_date: Last date of the week in YYYY-MM-DD format. Defaults to today.
        """
        try:
            rows = _daily_rows(get_client(ctx), end_date=end_date, days=35)
            if len(rows) < 7:
                return "Not enough health data found for a weekly review"
            review = _weekly_review(rows)
            return json.dumps(review, indent=2)
        except Exception as e:
            return f"Error building weekly health review: {str(e)}"

    @app.tool()
    async def list_health_report_metrics(ctx: Context) -> str:
        """List metrics and options supported by custom health reports."""
        try:
            return json.dumps(
                {
                    "metrics": {
                        name: {
                            "label": metadata["label"],
                            "unit": metadata["unit"],
                            "direction": metadata["direction"],
                        }
                        for name, metadata in ANALYTIC_METRICS.items()
                    },
                    "groups": sorted(REPORT_GROUPS),
                    "aggregations": sorted(REPORT_AGGREGATIONS),
                    "saved_report_store": str(_report_store_path()),
                },
                indent=2,
            )
        except Exception as e:
            return f"Error listing report metrics: {str(e)}"

    @app.tool()
    async def run_custom_health_report(
        ctx: Context,
        end_date: str | None = None,
        days: int = DEFAULT_DAYS,
        metrics: str = DEFAULT_REPORT_METRICS,
        group_by: str = "week",
        aggregation: str = "avg",
        saved_report_name: str | None = None,
        include_daily_rows: bool = False,
    ) -> str:
        """Run a custom historical health report.

        Args:
            end_date: Last date to include in YYYY-MM-DD format. Defaults to today.
            days: Number of days to fetch, capped at 180.
            metrics: Comma-separated metric keys. Use list_health_report_metrics.
            group_by: One of date, week, or month.
            aggregation: One of avg, sum, min, max, or latest.
            saved_report_name: Optional saved report name to run instead of arguments.
            include_daily_rows: Include compact daily rows used for the report.
        """
        try:
            definition = _report_definition(
                saved_report_name=saved_report_name,
                metrics=metrics,
                group_by=group_by,
                aggregation=aggregation,
                days=days,
                end_date=end_date,
            )
            rows = _daily_rows(
                get_client(ctx),
                end_date=definition.get("end_date"),
                days=int(definition["days"]),
            )
            report = _custom_report(rows, definition, include_daily_rows=include_daily_rows)
            return json.dumps(report, indent=2)
        except Exception as e:
            return f"Error running custom health report: {str(e)}"

    @app.tool()
    async def save_custom_health_report(
        ctx: Context,
        name: str,
        metrics: str = DEFAULT_REPORT_METRICS,
        group_by: str = "week",
        aggregation: str = "avg",
        days: int = DEFAULT_DAYS,
        end_date: str | None = None,
        description: str | None = None,
    ) -> str:
        """Save a reusable custom health report definition locally.

        Args:
            name: Unique report name.
            metrics: Comma-separated metric keys. Use list_health_report_metrics.
            group_by: One of date, week, or month.
            aggregation: One of avg, sum, min, max, or latest.
            days: Number of days to fetch when the report is run.
            end_date: Optional fixed report end date in YYYY-MM-DD format.
            description: Optional human note for the report.
        """
        try:
            definition = _report_definition(
                saved_report_name=None,
                metrics=metrics,
                group_by=group_by,
                aggregation=aggregation,
                days=days,
                end_date=end_date,
            )
            definition["name"] = _clean_report_name(name)
            definition["description"] = description or ""
            reports = _load_saved_reports()
            reports[definition["name"]] = definition
            _write_saved_reports(reports)
            return json.dumps({"saved": True, "report": definition}, indent=2)
        except Exception as e:
            return f"Error saving custom health report: {str(e)}"

    @app.tool()
    async def list_saved_health_reports(ctx: Context) -> str:
        """List locally saved custom health report definitions."""
        try:
            reports = _load_saved_reports()
            return json.dumps(
                {
                    "store": str(_report_store_path()),
                    "count": len(reports),
                    "reports": sorted(reports.values(), key=lambda item: item["name"]),
                },
                indent=2,
            )
        except Exception as e:
            return f"Error listing saved health reports: {str(e)}"

    return app


def _daily_rows(client: Any, end_date: str | None, days: int) -> list[dict[str, Any]]:
    safe_days = min(max(1, days), MAX_DAYS)
    end = _parse_date(end_date) if end_date else dt.date.today()
    start = end - dt.timedelta(days=safe_days - 1)
    activities_by_date = _activity_totals(client, start, end)
    rows: list[dict[str, Any]] = []

    for offset in range(safe_days):
        current = start + dt.timedelta(days=offset)
        date_text = current.isoformat()
        stats = _safe_call(lambda: client.get_stats(date_text), default={})
        sleep = _safe_call(lambda: client.get_sleep_data(date_text), default={})
        readiness = _safe_call(lambda: client.get_training_readiness(date_text), default=[])
        activity = activities_by_date.get(date_text, {})
        rows.append(_normalize_day(date_text, stats, sleep, readiness, activity))

    return [row for row in rows if _has_signal(row)]


def _activity_totals(client: Any, start: dt.date, end: dt.date) -> dict[str, dict[str, float]]:
    activities = _safe_call(
        lambda: client.get_activities_by_date(start.isoformat(), end.isoformat(), ""),
        default=[],
    )
    output: dict[str, dict[str, float]] = {}
    if not isinstance(activities, list):
        return output
    for activity in activities:
        if not isinstance(activity, dict):
            continue
        raw_start = activity.get("startTimeLocal")
        if not isinstance(raw_start, str) or len(raw_start) < 10:
            continue
        key = raw_start[:10]
        bucket = output.setdefault(
            key,
            {
                "activity_count": 0.0,
                "activity_distance_meters": 0.0,
                "activity_duration_seconds": 0.0,
                "activity_calories": 0.0,
            },
        )
        bucket["activity_count"] += 1
        bucket["activity_distance_meters"] += _number(activity.get("distance")) or 0
        bucket["activity_duration_seconds"] += _number(activity.get("duration")) or 0
        bucket["activity_calories"] += _number(activity.get("calories")) or 0
    return output


def _normalize_day(
    date_text: str,
    stats: Any,
    sleep: Any,
    readiness: Any,
    activity: dict[str, float],
) -> dict[str, Any]:
    stats = stats if isinstance(stats, dict) else {}
    sleep = sleep if isinstance(sleep, dict) else {}
    daily_sleep = sleep.get("dailySleepDTO") if isinstance(sleep.get("dailySleepDTO"), dict) else {}
    readiness_entry = readiness[0] if isinstance(readiness, list) and readiness else {}
    sleep_score = _sleep_score(sleep, daily_sleep)
    readiness_score = _number(readiness_entry.get("score")) if isinstance(readiness_entry, dict) else None

    row = {
        "date": date_text,
        "steps": _number(stats.get("totalSteps")),
        "active_calories": _number(stats.get("activeKilocalories")),
        "distance_meters": _number(stats.get("totalDistanceMeters")),
        "resting_hr": _number(stats.get("restingHeartRate")),
        "stress_avg": _number(stats.get("averageStressLevel")),
        "stress_max": _number(stats.get("maxStressLevel")),
        "body_battery_high": _number(stats.get("bodyBatteryHighestValue")),
        "body_battery_low": _number(stats.get("bodyBatteryLowestValue")),
        "body_battery_charged": _number(stats.get("bodyBatteryChargedValue")),
        "body_battery_drained": _number(stats.get("bodyBatteryDrainedValue")),
        "sleep_score": sleep_score,
        "sleep_hours": _hours(daily_sleep.get("sleepTimeSeconds")),
        "deep_sleep_hours": _hours(daily_sleep.get("deepSleepSeconds")),
        "rem_sleep_hours": _hours(daily_sleep.get("remSleepSeconds")),
        "sleep_stress": _number(daily_sleep.get("avgSleepStress")),
        "overnight_hrv": _number(sleep.get("avgOvernightHrv")),
        "training_readiness": readiness_score,
        "activity_count": activity.get("activity_count"),
        "activity_distance_meters": activity.get("activity_distance_meters"),
        "activity_duration_hours": _hours(activity.get("activity_duration_seconds")),
        "activity_calories": activity.get("activity_calories"),
    }
    row["recovery_pressure"] = _recovery_pressure(row)
    return row


def _baseline_payload(rows: list[dict[str, Any]], baseline_window: int) -> dict[str, Any]:
    window = min(max(7, baseline_window), len(rows))
    baseline_rows = rows[-window:]
    prior_rows = rows[-(window * 2) : -window] if len(rows) >= window * 2 else []
    latest = rows[-1]
    metrics: dict[str, dict[str, Any]] = {}

    for field, metadata in ANALYTIC_METRICS.items():
        baseline_values = _values(baseline_rows, field)
        latest_value = _number(latest.get(field))
        prior_avg = _average(_values(prior_rows, field))
        baseline_avg = _average(baseline_values)
        baseline_std = _stddev(baseline_values)
        metrics[field] = {
            "label": metadata["label"],
            "unit": metadata["unit"],
            "latest": _round(latest_value),
            "baseline_avg": _round(baseline_avg),
            "prior_avg": _round(prior_avg),
            "delta_from_baseline": _round(latest_value - baseline_avg)
            if latest_value is not None and baseline_avg is not None
            else None,
            "z_score": _round((latest_value - baseline_avg) / baseline_std, 2)
            if latest_value is not None and baseline_std
            else None,
            "direction": metadata["direction"],
            "sample_days": len(baseline_values),
        }

    return {
        "window": _window(rows),
        "baseline_window_days": window,
        "latest_date": latest["date"],
        "metrics": metrics,
    }


def _anomalies(
    rows: list[dict[str, Any]], baseline_window: int, z_threshold: float
) -> list[dict[str, Any]]:
    window = min(max(7, baseline_window), MAX_DAYS)
    anomalies: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        history = rows[max(0, index - window) : index]
        if len(history) < 7:
            continue
        for field, metadata in ANALYTIC_METRICS.items():
            value = _number(row.get(field))
            values = _values(history, field)
            avg = _average(values)
            std = _stddev(values)
            if value is None or avg is None or not std:
                continue
            z_score = (value - avg) / std
            if abs(z_score) < z_threshold:
                continue
            anomalies.append(
                {
                    "date": row["date"],
                    "metric": field,
                    "label": metadata["label"],
                    "value": _round(value),
                    "baseline_avg": _round(avg),
                    "z_score": _round(z_score, 2),
                    "severity": _severity(abs(z_score)),
                    "interpretation": _interpret_anomaly(field, z_score),
                }
            )
    return sorted(anomalies, key=lambda item: abs(float(item["z_score"])), reverse=True)[:25]


def _lagged_correlations(rows: list[dict[str, Any]], max_lag_days: int) -> list[dict[str, Any]]:
    safe_lag = min(max(1, max_lag_days), 14)
    output: list[dict[str, Any]] = []
    fields = list(ANALYTIC_METRICS)
    for left in fields:
        for right in fields:
            if left == right:
                continue
            for lag in range(1, safe_lag + 1):
                pairs: list[tuple[float, float]] = []
                for index, row in enumerate(rows[:-lag]):
                    left_value = _number(row.get(left))
                    right_value = _number(rows[index + lag].get(right))
                    if left_value is not None and right_value is not None:
                        pairs.append((left_value, right_value))
                correlation = _pearson(pairs)
                if correlation is None:
                    continue
                output.append(
                    {
                        "left_metric": left,
                        "left_label": ANALYTIC_METRICS[left]["label"],
                        "right_metric": right,
                        "right_label": ANALYTIC_METRICS[right]["label"],
                        "lag_days": lag,
                        "correlation": correlation,
                        "pairs": len(pairs),
                        "plain_english": (
                            f"{ANALYTIC_METRICS[left]['label']} tends to move "
                            f"{'with' if correlation > 0 else 'opposite to'} "
                            f"{ANALYTIC_METRICS[right]['label']} {lag} day(s) later."
                        ),
                    }
                )
    return sorted(output, key=lambda item: abs(float(item["correlation"])), reverse=True)


def _weekly_review(rows: list[dict[str, Any]]) -> dict[str, Any]:
    current = rows[-7:]
    previous = rows[-14:-7] if len(rows) >= 14 else []
    baseline = _baseline_payload(rows, baseline_window=28)
    anomalies = _anomalies(rows[-35:], baseline_window=21, z_threshold=1.75)[:8]
    correlations = _lagged_correlations(rows[-35:], max_lag_days=5)[:6]
    comparisons: dict[str, dict[str, Any]] = {}
    notes: list[str] = []

    for field, metadata in ANALYTIC_METRICS.items():
        current_avg = _average(_values(current, field))
        previous_avg = _average(_values(previous, field))
        delta = current_avg - previous_avg if current_avg is not None and previous_avg is not None else None
        comparisons[field] = {
            "label": metadata["label"],
            "unit": metadata["unit"],
            "current_7d_avg": _round(current_avg),
            "previous_7d_avg": _round(previous_avg),
            "delta": _round(delta),
        }
        if delta is not None and abs(delta) > _material_delta(field):
            notes.append(_comparison_note(field, delta))

    return {
        "window": {"start": current[0]["date"], "end": current[-1]["date"], "days": len(current)},
        "summary_notes": notes[:8],
        "comparisons": comparisons,
        "latest_baselines": baseline["metrics"],
        "notable_anomalies": anomalies,
        "lagged_relationships": correlations,
    }


def _report_definition(
    saved_report_name: str | None,
    metrics: str,
    group_by: str,
    aggregation: str,
    days: int,
    end_date: str | None,
) -> dict[str, Any]:
    if saved_report_name:
        reports = _load_saved_reports()
        name = _clean_report_name(saved_report_name)
        if name not in reports:
            raise ValueError(f"Saved report not found: {name}")
        return reports[name]
    return {
        "name": "",
        "description": "",
        "metrics": _parse_metrics(metrics),
        "group_by": _parse_group(group_by),
        "aggregation": _parse_aggregation(aggregation),
        "days": min(max(1, days), MAX_DAYS),
        "end_date": end_date,
    }


def _custom_report(
    rows: list[dict[str, Any]], definition: dict[str, Any], include_daily_rows: bool
) -> dict[str, Any]:
    metrics = _parse_metrics(",".join(definition["metrics"]))
    group_by = _parse_group(str(definition["group_by"]))
    aggregation = _parse_aggregation(str(definition["aggregation"]))
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(_report_group_key(row["date"], group_by), []).append(row)

    report_rows: list[dict[str, Any]] = []
    for group, group_rows in sorted(groups.items()):
        output: dict[str, Any] = {"group": group, "days": len(group_rows)}
        for metric in metrics:
            output[metric] = _aggregate(_values(group_rows, metric), aggregation)
        report_rows.append(output)

    result: dict[str, Any] = {
        "definition": {
            "name": definition.get("name") or None,
            "description": definition.get("description") or "",
            "metrics": metrics,
            "group_by": group_by,
            "aggregation": aggregation,
            "days": int(definition["days"]),
            "end_date": definition.get("end_date"),
        },
        "window": _window(rows),
        "columns": ["group", "days", *metrics],
        "rows": report_rows,
        "chart_hint": {
            "x": "group",
            "series": [
                {
                    "metric": metric,
                    "label": ANALYTIC_METRICS[metric]["label"],
                    "unit": ANALYTIC_METRICS[metric]["unit"],
                }
                for metric in metrics
            ],
        },
    }
    if include_daily_rows:
        result["daily_rows"] = [
            {"date": row["date"], **{metric: row.get(metric) for metric in metrics}} for row in rows
        ]
    return result


def _report_group_key(date_text: str, group_by: str) -> str:
    current = _parse_date(date_text)
    if group_by == "month":
        return current.strftime("%Y-%m")
    if group_by == "week":
        year, week, _ = current.isocalendar()
        return f"{year}-W{week:02d}"
    return date_text


def _aggregate(values: list[float], aggregation: str) -> float | None:
    if not values:
        return None
    if aggregation == "sum":
        return _round(sum(values))
    if aggregation == "min":
        return _round(min(values))
    if aggregation == "max":
        return _round(max(values))
    if aggregation == "latest":
        return _round(values[-1])
    return _round(sum(values) / len(values))


def _parse_metrics(metrics: str) -> list[str]:
    parsed = [metric.strip() for metric in metrics.split(",") if metric.strip()]
    if not parsed:
        raise ValueError("At least one metric is required")
    invalid = [metric for metric in parsed if metric not in ANALYTIC_METRICS]
    if invalid:
        raise ValueError(f"Unsupported metrics: {', '.join(invalid)}")
    return list(dict.fromkeys(parsed))


def _parse_group(group_by: str) -> str:
    value = group_by.strip().lower()
    if value not in REPORT_GROUPS:
        raise ValueError(f"group_by must be one of: {', '.join(sorted(REPORT_GROUPS))}")
    return value


def _parse_aggregation(aggregation: str) -> str:
    value = aggregation.strip().lower()
    if value not in REPORT_AGGREGATIONS:
        raise ValueError(
            f"aggregation must be one of: {', '.join(sorted(REPORT_AGGREGATIONS))}"
        )
    return value


def _clean_report_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise ValueError("Report name is required")
    if len(cleaned) > 120:
        raise ValueError("Report name must be 120 characters or less")
    return cleaned


def _report_store_path() -> Path:
    configured = os.getenv("GARMIN_REPORTS_PATH")
    if configured:
        return Path(os.path.expanduser(configured))
    return Path.home() / ".garmin_mcp_reports.json"


def _load_saved_reports() -> dict[str, dict[str, Any]]:
    path = _report_store_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Saved report store is invalid: {path}")
    reports: dict[str, dict[str, Any]] = {}
    for name, definition in data.items():
        if not isinstance(name, str) or not isinstance(definition, dict):
            continue
        reports[name] = _validate_saved_definition(name, definition)
    return reports


def _write_saved_reports(reports: dict[str, dict[str, Any]]) -> None:
    path = _report_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reports, indent=2, sort_keys=True))


def _validate_saved_definition(name: str, definition: dict[str, Any]) -> dict[str, Any]:
    raw_metrics = definition.get("metrics") or []
    metric_text = raw_metrics if isinstance(raw_metrics, str) else ",".join(raw_metrics)
    validated = {
        "name": _clean_report_name(str(definition.get("name") or name)),
        "description": str(definition.get("description") or ""),
        "metrics": _parse_metrics(metric_text),
        "group_by": _parse_group(str(definition.get("group_by") or "week")),
        "aggregation": _parse_aggregation(str(definition.get("aggregation") or "avg")),
        "days": min(max(1, int(definition.get("days") or DEFAULT_DAYS)), MAX_DAYS),
        "end_date": definition.get("end_date"),
    }
    return validated


def _safe_call(fn: Callable[[], Any], default: Any) -> Any:
    try:
        result = fn()
        return default if result is None else result
    except Exception:
        return default


def _sleep_score(sleep: dict[str, Any], daily_sleep: dict[str, Any]) -> float | None:
    direct = _number(daily_sleep.get("sleepScoreTotal")) or _number(daily_sleep.get("sleepScore"))
    if direct is not None:
        return direct
    scores = daily_sleep.get("sleepScores")
    if isinstance(scores, dict):
        overall = scores.get("overall")
        if isinstance(overall, dict):
            return _number(overall.get("value"))
    return _number(sleep.get("sleepScore"))


def _recovery_pressure(row: dict[str, Any]) -> float | None:
    drivers = [
        _number(row.get("stress_avg")),
        ((_number(row.get("resting_hr")) or 45) - 45) * 1.5
        if row.get("resting_hr") is not None
        else None,
        (100 - (_number(row.get("body_battery_high")) or 100)) * 0.35
        if row.get("body_battery_high") is not None
        else None,
        (_number(row.get("activity_duration_hours")) or 0) * 4
        if row.get("activity_duration_hours") is not None
        else None,
        (45 - (_number(row.get("overnight_hrv")) or 45)) * 0.7
        if row.get("overnight_hrv") is not None
        else None,
    ]
    values = [float(value) for value in drivers if value is not None]
    if not values:
        return None
    return _round(max(0, min(100, sum(values) / len(values))))


def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 8:
        return None
    xs = [x for x, _ in pairs]
    ys = [y for _, y in pairs]
    x_avg = sum(xs) / len(xs)
    y_avg = sum(ys) / len(ys)
    numerator = sum((x - x_avg) * (y - y_avg) for x, y in pairs)
    x_den = math.sqrt(sum((x - x_avg) ** 2 for x in xs))
    y_den = math.sqrt(sum((y - y_avg) ** 2 for y in ys))
    if x_den == 0 or y_den == 0:
        return None
    return _round(numerator / (x_den * y_den), 3)


def _parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def _window(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"start": None, "end": None, "days": 0}
    return {"start": rows[0]["date"], "end": rows[-1]["date"], "days": len(rows)}


def _has_signal(row: dict[str, Any]) -> bool:
    return any(_number(row.get(field)) is not None for field in ANALYTIC_METRICS)


def _values(rows: list[dict[str, Any]], field: str) -> list[float]:
    return [value for value in (_number(row.get(field)) for row in rows) if value is not None]


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _stddev(values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    avg = sum(values) / len(values)
    variance = sum((value - avg) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _hours(seconds: Any) -> float | None:
    value = _number(seconds)
    return _round(value / 3600) if value is not None else None


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _severity(abs_z: float) -> str:
    if abs_z >= 3:
        return "high"
    if abs_z >= 2:
        return "medium"
    return "low"


def _interpret_anomaly(field: str, z_score: float) -> str:
    metadata = ANALYTIC_METRICS[field]
    if metadata["direction"] == "higher_is_better":
        return "better than normal" if z_score > 0 else "worse than normal"
    if metadata["direction"] == "lower_is_better":
        return "worse than normal" if z_score > 0 else "better than normal"
    return "higher than normal" if z_score > 0 else "lower than normal"


def _material_delta(field: str) -> float:
    return {
        "steps": 1000,
        "active_calories": 120,
        "resting_hr": 2,
        "stress_avg": 5,
        "body_battery_high": 8,
        "sleep_score": 6,
        "sleep_hours": 0.4,
        "overnight_hrv": 4,
        "training_readiness": 6,
        "activity_duration_hours": 0.3,
        "recovery_pressure": 5,
    }.get(field, 1)


def _comparison_note(field: str, delta: float) -> str:
    label = ANALYTIC_METRICS[field]["label"]
    direction = "up" if delta > 0 else "down"
    return f"{label} is {direction} by {_round(abs(delta))} versus the prior week."


ANALYTIC_METRICS = {
    "steps": {"label": "Steps", "unit": "steps", "direction": "higher_is_better"},
    "active_calories": {
        "label": "Active calories",
        "unit": "kcal",
        "direction": "neutral",
    },
    "resting_hr": {
        "label": "Resting heart rate",
        "unit": "bpm",
        "direction": "lower_is_better",
    },
    "stress_avg": {"label": "Average stress", "unit": "", "direction": "lower_is_better"},
    "body_battery_high": {
        "label": "Body Battery high",
        "unit": "",
        "direction": "higher_is_better",
    },
    "sleep_score": {"label": "Sleep score", "unit": "", "direction": "higher_is_better"},
    "sleep_hours": {"label": "Sleep duration", "unit": "h", "direction": "higher_is_better"},
    "overnight_hrv": {"label": "Overnight HRV", "unit": "ms", "direction": "higher_is_better"},
    "training_readiness": {
        "label": "Training readiness",
        "unit": "",
        "direction": "higher_is_better",
    },
    "activity_duration_hours": {
        "label": "Activity duration",
        "unit": "h",
        "direction": "neutral",
    },
    "recovery_pressure": {
        "label": "Recovery pressure",
        "unit": "",
        "direction": "lower_is_better",
    },
}
