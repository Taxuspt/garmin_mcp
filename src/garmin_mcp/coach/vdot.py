"""Jack Daniels' VDOT model: race predictions and training paces."""

import math

HM_METERS = 21097.5


def _vo2(v_m_per_min: float) -> float:
    return -4.60 + 0.182258 * v_m_per_min + 0.000104 * v_m_per_min**2


def _pct_max(t_min: float) -> float:
    return (
        0.8
        + 0.1894393 * math.exp(-0.012778 * t_min)
        + 0.2989558 * math.exp(-0.1932605 * t_min)
    )


def vdot_from_race(distance_m: float, seconds: float) -> float:
    t = seconds / 60
    return _vo2(distance_m / t) / _pct_max(t)


def race_seconds(vdot: float, distance_m: float) -> float:
    """Predicted race time for ``distance_m`` at the given VDOT (bisection)."""
    lo, hi = 60.0, 60.0 * 600
    for _ in range(80):
        mid = (lo + hi) / 2
        if vdot_from_race(distance_m, mid) > vdot:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _speed_for_vo2(vo2: float) -> float:
    """Inverse of ``_vo2``: running speed in m/min for a given oxygen cost."""
    a, b, c = 0.000104, 0.182258, -4.60 - vo2
    return (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)


def pace_at_fraction(vdot: float, fraction: float) -> float:
    """Pace in seconds/km when running at ``fraction`` of VO2max."""
    return 60000 / _speed_for_vo2(vdot * fraction)


def vdot_from_threshold_pace(sec_per_km: float) -> float:
    """Threshold pace sits at ~88% of VO2max."""
    return _vo2(60000 / sec_per_km) / 0.88


def training_paces(vdot: float) -> dict:
    """Pace ranges (sec/km, fast..slow) for each training intensity."""
    hm = race_seconds(vdot, HM_METERS) / (HM_METERS / 1000)
    return {
        "recovery": (pace_at_fraction(vdot, 0.62), pace_at_fraction(vdot, 0.58)),
        "easy": (pace_at_fraction(vdot, 0.70), pace_at_fraction(vdot, 0.62)),
        "marathon": (pace_at_fraction(vdot, 0.81), pace_at_fraction(vdot, 0.78)),
        "hm": (hm - 3, hm + 3),
        "threshold": (pace_at_fraction(vdot, 0.89), pace_at_fraction(vdot, 0.86)),
        "interval": (pace_at_fraction(vdot, 0.99), pace_at_fraction(vdot, 0.95)),
    }


def fmt_pace(sec_per_km: float | None) -> str:
    if not sec_per_km:
        return "–"
    s = int(round(sec_per_km))
    return f"{s // 60}:{s % 60:02d}"


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
