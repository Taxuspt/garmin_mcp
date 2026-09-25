"""Training-load model: per-activity load and CTL/ATL/TSB (fitness/fatigue/form)."""

import math
from datetime import date, timedelta

CTL_DAYS, ATL_DAYS = 42, 7


def activity_load(act: dict, max_hr: int, rest_hr: int) -> float:
    """Garmin's EPOC-based training load, falling back to Banister TRIMP."""
    if act.get("training_load"):
        return float(act["training_load"])
    minutes = (act.get("duration_s") or 0) / 60
    hr = act.get("avg_hr")
    if not hr or max_hr <= rest_hr:
        # No HR (e.g. strength without strap): moderate flat rate per minute.
        return minutes * 0.8
    frac = max(0.0, min(1.0, (hr - rest_hr) / (max_hr - rest_hr)))
    return minutes * frac * 0.64 * math.exp(1.92 * frac)


def daily_loads(activities: list[dict], max_hr: int, rest_hr: int) -> dict[str, float]:
    loads: dict[str, float] = {}
    for act in activities:
        loads[act["date"]] = loads.get(act["date"], 0.0) + activity_load(act, max_hr, rest_hr)
    return loads


def pmc(loads: dict[str, float], start: date, end: date, ctl0: float = 0.0, atl0: float = 0.0) -> list[dict]:
    """Performance-management series. TSB is yesterday's CTL minus ATL."""
    out, ctl, atl = [], ctl0, atl0
    k_ctl, k_atl = 1 - math.exp(-1 / CTL_DAYS), 1 - math.exp(-1 / ATL_DAYS)
    d = start
    while d <= end:
        tsb = ctl - atl
        load = loads.get(d.isoformat(), 0.0)
        ctl += (load - ctl) * k_ctl
        atl += (load - atl) * k_atl
        out.append({"date": d.isoformat(), "load": round(load, 1), "ctl": round(ctl, 1), "atl": round(atl, 1), "tsb": round(tsb, 1)})
        d += timedelta(days=1)
    return out


def acwr(series: list[dict]) -> float | None:
    """Acute:chronic ratio from the latest PMC point (ATL/CTL)."""
    if not series or series[-1]["ctl"] < 5:
        return None
    return round(series[-1]["atl"] / series[-1]["ctl"], 2)
