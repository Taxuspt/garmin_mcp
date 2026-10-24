#!/usr/bin/env python3
"""Report what a Garmin Personal API key can actually reach.

Garmin documents the Personal tier as "Activity, Health, Women's Health" data,
but does not publish which service paths the gateway proxies. This server has
~150 tools built on the internal paths Garmin Connect's own web client uses, so
after switching to the Personal backend the real question is: which of them
still answer?

This script answers it without touching the MCP server. It reads the same key
the server reads (GARMIN_PERSONAL_API_KEY, or
~/.config/garmin/connect-personal-api-key), then calls one representative
method per area and prints the outcome. Nothing is written to Garmin: every
call is a GET.

Usage:
    python scripts/probe_personal_api.py
    python scripts/probe_personal_api.py --json probe.json
    python scripts/probe_personal_api.py --date 2026-10-20

Exit code is 0 when the key authenticates (even if some areas are refused),
1 when it does not.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

# Allow running from a checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from garmin_mcp import personal_api  # noqa: E402


def _status(exc: BaseException) -> str:
    """Pull the HTTP status out of whichever exception layer raised it."""
    text = str(exc)
    match = re.search(r"\b([45]\d\d)\b", text)
    if match:
        return f"HTTP {match.group(1)}"
    return type(exc).__name__


def _probe_methods(client, day: str, recent: str) -> list[tuple[str, str, callable]]:
    """One representative ``garminconnect`` call per tool area."""
    return [
        ("profile", "get_full_name", lambda: client.get_full_name()),
        ("profile", "get_user_profile", lambda: client.get_user_profile()),
        ("profile", "get_userprofile_settings", lambda: client.get_userprofile_settings()),
        ("activities", "get_activities", lambda: client.get_activities(0, 1)),
        ("activities", "get_last_activity", lambda: client.get_last_activity()),
        ("activities", "get_activity_types", lambda: client.get_activity_types()),
        ("health", "get_stats", lambda: client.get_stats(day)),
        ("health", "get_user_summary", lambda: client.get_user_summary(day)),
        ("health", "get_steps_data", lambda: client.get_steps_data(day)),
        ("health", "get_floors", lambda: client.get_floors(day)),
        ("health", "get_heart_rates", lambda: client.get_heart_rates(day)),
        ("health", "get_rhr_day", lambda: client.get_rhr_day(day)),
        ("health", "get_stress_data", lambda: client.get_stress_data(day)),
        ("health", "get_all_day_stress", lambda: client.get_all_day_stress(day)),
        ("health", "get_body_battery", lambda: client.get_body_battery(recent, day)),
        ("health", "get_body_battery_events", lambda: client.get_body_battery_events(day)),
        ("health", "get_respiration_data", lambda: client.get_respiration_data(day)),
        ("health", "get_spo2_data", lambda: client.get_spo2_data(day)),
        ("health", "get_hrv_data", lambda: client.get_hrv_data(day)),
        ("health", "get_hydration_data", lambda: client.get_hydration_data(day)),
        ("health", "get_intensity_minutes_data", lambda: client.get_intensity_minutes_data(day)),
        ("health", "get_all_day_events", lambda: client.get_all_day_events(day)),
        ("sleep", "get_sleep_data", lambda: client.get_sleep_data(day)),
        ("body", "get_body_composition", lambda: client.get_body_composition(recent, day)),
        ("body", "get_daily_weigh_ins", lambda: client.get_daily_weigh_ins(day)),
        ("body", "get_blood_pressure", lambda: client.get_blood_pressure(recent, day)),
        ("training", "get_training_status", lambda: client.get_training_status(day)),
        ("training", "get_training_readiness", lambda: client.get_training_readiness(day)),
        ("training", "get_morning_training_readiness",
         lambda: client.get_morning_training_readiness(day)),
        ("training", "get_max_metrics", lambda: client.get_max_metrics(day)),
        ("training", "get_endurance_score", lambda: client.get_endurance_score(recent, day)),
        ("training", "get_hill_score", lambda: client.get_hill_score(recent, day)),
        ("training", "get_race_predictions", lambda: client.get_race_predictions()),
        ("training", "get_fitnessage_data", lambda: client.get_fitnessage_data(day)),
        ("training", "get_lactate_threshold", lambda: client.get_lactate_threshold(latest=True)),
        ("devices", "get_devices", lambda: client.get_devices()),
        ("devices", "get_primary_training_device", lambda: client.get_primary_training_device()),
        ("devices", "get_device_last_used", lambda: client.get_device_last_used()),
        ("workouts", "get_workouts", lambda: client.get_workouts(0, 1)),
        ("workouts", "get_scheduled_workouts",
         lambda: client.get_scheduled_workouts(_year(day), _month(day))),
        ("workouts", "get_training_plans", lambda: client.get_training_plans()),
        ("challenges", "get_goals", lambda: client.get_goals()),
        ("challenges", "get_earned_badges", lambda: client.get_earned_badges()),
        ("challenges", "get_inprogress_virtual_challenges",
         lambda: client.get_inprogress_virtual_challenges(0, 5)),
        ("challenges", "get_adhoc_challenges", lambda: client.get_adhoc_challenges(0, 5)),
        ("challenges", "get_personal_record", lambda: client.get_personal_record()),
        ("women", "get_menstrual_data_for_date", lambda: client.get_menstrual_data_for_date(day)),
        ("nutrition", "get_nutrition_daily_food_log",
         lambda: client.get_nutrition_daily_food_log(day)),
        ("nutrition", "get_nutrition_daily_meals", lambda: client.get_nutrition_daily_meals(day)),
        ("nutrition", "get_nutrition_daily_settings",
         lambda: client.get_nutrition_daily_settings(day)),
        ("lifestyle", "get_lifestyle_logging_data",
         lambda: client.get_lifestyle_logging_data(day)),
    ]


def _probe_paths(client, day: str) -> list[tuple[str, str, callable]]:
    """Raw gateway paths for tools that read via ``connectapi`` directly."""
    display = client.display_name
    return [
        ("courses", "/course-service/course", lambda: client.connectapi("/course-service/course")),
        ("gear", "/gear-service/gear/v2/list",
         lambda: client.connectapi("/gear-service/gear/v2/list")),
        ("calendar", f"/calendar-service/year/{_year(day)}/month/{_month(day)}",
         lambda: client.connectapi(
             f"/calendar-service/year/{_year(day)}/month/{_month(day)}")),
        ("badges", "/badge-service/badge/earned",
         lambda: client.connectapi("/badge-service/badge/earned")),
        ("daily-summary-chart",
         f"/usersummary-service/chart/activities/{display}",
         lambda: client.connectapi(f"/usersummary-service/chart/activities/{display}")),
        ("download", "/download-service/files/activity/none",
         lambda: client.download("/download-service/files/activity/0")),
    ]


def _year(day: str) -> int:
    return int(day[0:4])


def _month(day: str) -> int:
    return int(day[5:7])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--json",
        type=str,
        default=None,
        help="Write the full report to this path as JSON",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="Calendar date used for per-day calls (default: yesterday)",
    )
    args = parser.parse_args()

    resolved = personal_api.resolve_api_key()
    if resolved is None:
        print("✗ No Personal API key found.")
        print(f"  Set GARMIN_PERSONAL_API_KEY or write it to {personal_api.resolve_api_key_file()}")
        print("  Create one at https://connect.garmin.com/app/settings/personalApiToken")
        return 1
    key, source = resolved

    try:
        client = personal_api.login_personal(key)
    except Exception as err:  # noqa: BLE001 - reported to the user
        print(f"✗ {personal_api.describe_login_error(err)}")
        return 1

    day = args.date
    recent = (date.fromisoformat(day) - timedelta(days=6)).isoformat()
    display = client.display_name or "(none)"

    print("Garmin Personal API probe")
    print(f"  key source : {source}")
    print(f"  endpoint   : {client.client.base_url}")
    print(f"  profile    : {display}")
    print(f"  date       : {day}\n")

    results = []
    probes = _probe_methods(client, day, recent) + _probe_paths(client, day)

    # ``download`` and other raw calls may raise before the token is reusable;
    # every probe is independent so one refusal never hides the others.
    for area, label, call in probes:
        entry = {"area": area, "call": label, "status": "ok", "detail": ""}
        try:
            result = call()
            if result is None:
                entry["status"] = "empty"
            else:
                entry["detail"] = f"{type(result).__name__}"
        except Exception as err:  # noqa: BLE001 - the point is to record it
            # Only the status and the exception class: third-party error
            # strings can contain the request URL, and this report is meant to
            # be shareable.
            entry["status"] = _status(err)
            entry["detail"] = type(err).__name__
        results.append(entry)

    width = max(len(entry["call"]) for entry in results)
    for entry in results:
        marker = "✓" if entry["status"] in ("ok", "empty") else "✗"
        detail = "" if entry["status"] == "ok" else f"  {entry['status']}"
        if entry["status"] == "ok" and entry["detail"]:
            detail = f"  ({entry['detail']})"
        print(f"  {marker} {entry['area']:<12} {entry['call']:<{width}}{detail}")

    ok = [entry for entry in results if entry["status"] in ("ok", "empty")]
    print(f"\n{len(ok)}/{len(results)} probes answered.")

    by_area: dict[str, list[bool]] = {}
    for entry in results:
        by_area.setdefault(entry["area"], []).append(entry["status"] in ("ok", "empty"))
    print("\nBy area:")
    for area in sorted(by_area):
        passed = sum(by_area[area])
        total = len(by_area[area])
        note = "" if passed == total else "  ← partial: those tools will fail"
        print(f"  {area:<12} {passed}/{total}{note}")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"date": day, "profile": display, "results": results}, indent=2),
            encoding="utf-8",
        )
        print(f"\nFull report written to {args.json}")

    print(
        "\nNote: a refused probe means Garmin does not expose that path on the Personal\n"
        "gateway, not that the tool is broken. Writes are refused everywhere by design."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
