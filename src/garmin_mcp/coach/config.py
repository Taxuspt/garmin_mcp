"""Athlete configuration (goal race, volume corridor, notification times)."""

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "coach" / "athlete.json"


@dataclass
class Config:
    name: str
    timezone: str
    race: str
    race_date: date
    race_distance_m: float
    target_seconds: float
    plan_start: date
    start_vdot: float
    max_hr: int
    rest_hr: int
    start_run_km: float
    peak_run_km: float
    start_hours: float
    peak_hours: float
    long_run_cap_km: float
    morning_brief_hour: int
    weekly_review_weekday: int
    weekly_review_hour: int
    sailing: list[dict] = field(default_factory=list)


def load_config(path: str | Path | None = None) -> Config:
    raw = json.loads(Path(path or DEFAULT_CONFIG_PATH).read_text(encoding="utf-8"))
    goal, fit, vol = raw["goal"], raw["fitness"], raw["volume"]
    notif = raw.get("notifications") or {}
    return Config(
        name=raw.get("name", "Athlet"),
        timezone=raw.get("timezone", "Europe/Berlin"),
        race=goal["race"],
        race_date=date.fromisoformat(goal["race_date"]),
        race_distance_m=float(goal.get("distance_m", 21097.5)),
        target_seconds=float(goal["target_seconds"]),
        plan_start=date.fromisoformat(raw["plan_start"]),
        start_vdot=float(fit["start_vdot"]),
        max_hr=int(fit.get("max_hr", 190)),
        rest_hr=int(fit.get("rest_hr", 50)),
        start_run_km=float(vol["start_run_km"]),
        peak_run_km=float(vol["peak_run_km"]),
        start_hours=float(vol["start_hours"]),
        peak_hours=float(vol["peak_hours"]),
        long_run_cap_km=float(vol.get("long_run_cap_km", 24)),
        morning_brief_hour=int(notif.get("morning_brief_hour", 6)),
        weekly_review_weekday=int(notif.get("weekly_review_weekday", 6)),
        weekly_review_hour=int(notif.get("weekly_review_hour", 19)),
        sailing=[_sailing_block(b) for b in raw.get("sailing") or []],
    )


def _sailing_block(raw: dict) -> dict:
    """A sailing block: travel/training days, optionally with regatta days inside."""
    start, end = date.fromisoformat(raw["start"]), date.fromisoformat(raw["end"])
    reg_from = date.fromisoformat(raw["regatta_from"]) if raw.get("regatta_from") else None
    reg_to = date.fromisoformat(raw["regatta_to"]) if raw.get("regatta_to") else (end if reg_from else None)
    return {"title": raw["title"], "start": start, "end": end, "regatta_from": reg_from,
            "regatta_to": reg_to, "tentative": bool(raw.get("tentative"))}


def sailing_day(cfg: "Config", day: date) -> dict | None:
    """``{"block": ..., "regatta": bool}`` when ``day`` is a sailing day."""
    for block in cfg.sailing:
        if block["start"] <= day <= block["end"]:
            regatta = bool(block["regatta_from"] and block["regatta_from"] <= day <= block["regatta_to"])
            return {"block": block, "regatta": regatta}
    return None
