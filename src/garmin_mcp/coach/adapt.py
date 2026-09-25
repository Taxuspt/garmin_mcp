"""Adapt the plan to how the athlete is actually doing.

Three levers, each deliberately conservative:

* **Day** – the morning readiness (Garmin Training Readiness, else HRV/sleep/
  form) shortens or swaps today's key session.
* **Week** – each Monday, last week's compliance, readiness trend and the
  acute:chronic load ratio nudge the volume scale of the coming weeks.
* **Pace** – the Garmin lactate-threshold pace moves the training VDOT, so
  target paces follow real fitness instead of the goal.
"""

import copy
from datetime import date, timedelta

from .plan import WEEKDAYS_LONG, monday_of
from .vdot import vdot_from_threshold_pace

SCALE_MIN, SCALE_MAX = 0.75, 1.10


def readiness_status(well: dict | None, tsb: float | None) -> dict:
    well = well or {}
    reasons = []
    if well.get("readiness") is not None:
        score = float(well["readiness"])
        reasons.append(f"Garmin Readiness {round(score)}")
    else:
        score = 70.0
        status = (well.get("hrv_status") or "").upper()
        if status == "LOW":
            score -= 25
            reasons.append("HRV niedrig")
        elif status in ("POOR", "UNBALANCED"):
            score -= 15
            reasons.append("HRV unausgeglichen")
        elif status == "BALANCED":
            reasons.append("HRV ausgeglichen")
        sleep = well.get("sleep_score")
        if sleep is not None:
            if sleep < 60:
                score -= 15
            elif sleep < 70:
                score -= 7
            elif sleep >= 80:
                score += 5
            reasons.append(f"Schlaf {sleep}")
    if tsb is not None:
        if tsb < -30:
            score = min(score, 55)
            reasons.append(f"Form {tsb:+.0f} (hohe Ermüdung)")
        elif tsb < -20:
            score -= 5
            reasons.append(f"Form {tsb:+.0f}")
    score = max(0, min(100, round(score)))
    level = "green" if score >= 60 else "yellow" if score >= 40 else "red"
    return {"score": score, "level": level, "reasons": reasons}


def _reduce(session: dict, factor: float, note: str) -> dict:
    s = copy.deepcopy(session)
    if s.get("distance_km"):
        s["distance_km"] = round(s["distance_km"] * factor, 1)
    s["duration_min"] = int(round(s["duration_min"] * factor))
    s["adjusted"] = note
    return s


def _easy_swap(session: dict, note: str, minutes: int = 35) -> dict:
    s = copy.deepcopy(session)
    s.update({
        "title": "Lockerer Regenerationslauf",
        "detail": f"{minutes}' ganz locker, Zone 1–2",
        "profile": "recovery",
        "key": False,
        "duration_min": minutes,
        "adjusted": note,
    })
    if s.get("distance_km"):
        s["distance_km"] = round(minutes / 6.0, 1)
    return s


def adapt_day(day: date, plan_sessions: dict[str, list[dict]], status: dict) -> dict[str, dict]:
    """Return overrides ``{date: {"sessions": [...], "note": str}}`` for today (and a moved-to day)."""
    iso = day.isoformat()
    today = plan_sessions.get(iso, [])
    if status["level"] == "green" or not any(s["sport"] != "sail" and not s.get("optional") for s in today):
        return {}
    overrides: dict[str, dict] = {}
    label = f"Readiness {status['score']}"
    new_today, note = [], ""
    for s in today:
        if s["sport"] == "sail":
            new_today.append(s)  # sailing has priority, readiness never moves it
            continue
        if status["level"] == "yellow":
            if s.get("key") and s["sport"] == "run":
                s2 = _reduce(s, 0.8, f"{label}: Hauptteil ~¼ kürzer, Tempo am langsamen Ende des Bereichs")
                note = "Gelbe Ampel – Schlüsseleinheit etwas entschärft."
                new_today.append(s2)
            elif s["sport"] in ("bike", "row"):
                new_today.append(_reduce(s, 0.8, f"{label}: etwas kürzer, streng Zone 1–2"))
            else:
                new_today.append(s)
            continue
        # red
        if s.get("key") and s["sport"] == "run":
            if s["profile"] == "long":
                new_today.append(_reduce(dict(s, profile="easy"), 0.6, f"{label}: nur 60 %, ohne Tempoanteile"))
                note = "Rote Ampel – langer Lauf gekürzt und ohne Tempo."
                continue
            moved_to = _find_move_target(day, plan_sessions)
            if moved_to:
                target_iso = moved_to.isoformat()
                moved = dict(copy.deepcopy(s), date=target_iso, id=s["id"].replace(iso, target_iso),
                             adjusted=f"Von {WEEKDAYS_LONG[day.weekday()]} verschoben ({label})")
                keep = [x for x in plan_sessions.get(target_iso, []) if x["sport"] != "run"]
                overrides[target_iso] = {"sessions": [moved] + keep, "note": f"Schlüsseleinheit von {WEEKDAYS_LONG[day.weekday()]} hierher verschoben."}
                note = f"Rote Ampel – Schlüsseleinheit auf {WEEKDAYS_LONG[moved_to.weekday()]} verschoben, heute nur locker."
            else:
                note = "Rote Ampel – Schlüsseleinheit entfällt diese Woche, heute nur locker."
            new_today.append(_easy_swap(s, f"{label}: statt {s['title']}"))
        elif s["sport"] == "strength":
            new_today.append(dict(copy.deepcopy(s), title="Mobility & Faszienrolle", detail="20' Mobilisation, kein schweres Heben",
                                  duration_min=20, adjusted=f"{label}: statt {s['title']}"))
        elif s["sport"] in ("bike", "row"):
            new_today.append(_reduce(s, 0.5, f"{label}: halbe Dauer, Zone 1"))
        else:
            new_today.append(_easy_swap(s, f"{label}: nur locker", 30))
    if not any(s.get("adjusted") for s in new_today):
        return overrides  # nothing on today's plan needed changing
    if not note:
        note = ("Gelbe Ampel – Nebeneinheiten etwas kürzer." if status["level"] == "yellow"
                else "Rote Ampel – heute nur locker bzw. Mobility.")
    overrides[iso] = {"sessions": new_today, "note": note, "readiness": status}
    return overrides


def _find_move_target(day: date, plan_sessions: dict[str, list[dict]]) -> date | None:
    """Next day this week with no key run on it or on either neighbour.

    ``day`` itself is ignored as a neighbour since its key session is the one
    being moved. Returns None when no slot keeps a rest day between key runs.
    """
    def has_key(d: date) -> bool:
        if d == day:
            return False
        return any(s.get("key") and s["sport"] == "run" for s in plan_sessions.get(d.isoformat(), []))

    def sailing(d: date) -> bool:
        return any(s["sport"] == "sail" for s in plan_sessions.get(d.isoformat(), []))

    sunday = monday_of(day) + timedelta(days=6)
    d = day + timedelta(days=1)
    while d < sunday:
        if not sailing(d) and not any(has_key(d + timedelta(days=k)) for k in (-1, 0, 1)):
            return d
        d += timedelta(days=1)
    return None


def weekly_scale(prev_scale: float, compliance: float | None, run_volume_ratio: float | None,
                 avg_readiness: float | None, acwr: float | None) -> tuple[float, str]:
    """New volume scale for the coming week plus a human-readable reason."""
    factor, reasons = 1.0, []
    if acwr is not None and acwr > 1.5:
        factor, reasons = 0.88, [f"Belastungsquote {acwr:.2f} zu hoch"]
    elif avg_readiness is not None and avg_readiness < 40:
        factor, reasons = 0.88, [f"Readiness im Schnitt {avg_readiness:.0f}"]
    elif compliance is not None and compliance < 0.6:
        factor, reasons = 0.92, [f"nur {compliance:.0%} der Schlüsseleinheiten geschafft"]
    elif (compliance is not None and compliance >= 0.9 and (run_volume_ratio or 0) >= 0.9
          and (avg_readiness is None or avg_readiness >= 55) and (acwr is None or acwr <= 1.3)):
        factor, reasons = 1.03, ["alles umgesetzt, gut erholt"]
    else:
        reasons = ["Plan bleibt wie er ist"]
    if prev_scale < 1.0 and factor >= 1.0 and not (acwr and acwr > 1.3):
        # Climb back towards the planned volume after a cut.
        factor = max(factor, 1.04)
        reasons.append("Rückkehr zum geplanten Umfang")
    new = max(SCALE_MIN, min(SCALE_MAX, prev_scale * factor))
    return round(new, 3), ", ".join(reasons)


def update_vdot(current: float, lt_pace: float | None, goal_vdot: float) -> tuple[float, str | None]:
    """Move training VDOT towards the lactate-threshold estimate (max ±0.8 per week)."""
    if not lt_pace:
        return current, None
    est = vdot_from_threshold_pace(lt_pace)
    if not 35 <= est <= 70:
        return current, None
    target = min(est, goal_vdot + 1.5)
    if target < current and current - target < 2:
        return current, None  # small dips are noise; only react to a clear drop
    step = max(-0.5, min(0.8, (target - current) * 0.5))
    if abs(step) < 0.1:
        return current, None
    new = round(current + step, 1)
    return new, f"VDOT {current:.1f} → {new:.1f} (Laktatschwelle {int(lt_pace) // 60}:{int(lt_pace) % 60:02d}/km)"
