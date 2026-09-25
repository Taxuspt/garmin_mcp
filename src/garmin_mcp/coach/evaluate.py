"""Rate a finished activity against the planned session and write feedback."""

from datetime import date

from .plan import WEEKDAYS_LONG
from .vdot import fmt_duration, fmt_pace

SPORT_LABELS = {"run": "Lauf", "bike": "Rad", "strength": "Kraft", "row": "Rudern", "swim": "Schwimmen", "sail": "Segeln", "other": "Training"}

# Expected intensity per session profile:
# (min share of time in zones 1–2, min share in zones 4–5, aerobic TE range)
PROFILES = {
    "recovery": {"easy_share": 0.85, "hard_share": None, "te": (1.0, 2.5)},
    "easy": {"easy_share": 0.75, "hard_share": None, "te": (1.8, 3.2)},
    "long": {"easy_share": 0.65, "hard_share": None, "te": (2.5, 4.0)},
    "hills": {"easy_share": 0.55, "hard_share": None, "te": (2.3, 3.8)},
    "steady": {"easy_share": 0.40, "hard_share": None, "te": (2.8, 3.9)},
    "threshold": {"easy_share": None, "hard_share": 0.20, "te": (3.0, 4.3)},
    "vo2": {"easy_share": None, "hard_share": 0.22, "te": (3.3, 4.8)},
    "hm": {"easy_share": None, "hard_share": 0.25, "te": (3.2, 4.6)},
    "race": {"easy_share": None, "hard_share": None, "te": (3.0, 5.0)},
    "z2": {"easy_share": 0.70, "hard_share": None, "te": (1.5, 3.3)},
}


def rating(score: int) -> tuple[str, str]:
    if score >= 85:
        return "Volltreffer", "white_check_mark"
    if score >= 70:
        return "Gut umgesetzt", "+1"
    if score >= 50:
        return "Okay – mit Abweichungen", "warning"
    return "Deutlich neben dem Plan", "x"


def _pace(act: dict) -> float | None:
    if act.get("distance_m") and act.get("duration_s"):
        return act["duration_s"] / (act["distance_m"] / 1000)
    return None


def match_session(act: dict, planned: list[dict], taken: set[str]) -> dict | None:
    """Same-day planned session of the same sport family that is still open."""
    family = {"row": {"row", "bike"}, "bike": {"bike", "row"}}.get(act["sport"], {act["sport"]})
    candidates = [s for s in planned if s["sport"] in family and s["id"] not in taken]
    if not candidates:
        return None
    # Closest duration wins when a day holds two sessions of the same family.
    minutes = act.get("duration_s", 0) / 60
    return min(candidates, key=lambda s: abs(s["duration_min"] - minutes))


def _volume_ratio(act: dict, session: dict) -> float | None:
    if session.get("distance_km") and act.get("distance_m"):
        return act["distance_m"] / 1000 / session["distance_km"]
    if session.get("duration_min") and act.get("duration_s"):
        return act["duration_s"] / 60 / session["duration_min"]
    return None


def _volume_score(r: float | None) -> float:
    if r is None:
        return 70
    if 0.9 <= r <= 1.12:
        return 100
    gap = 0.9 - r if r < 0.9 else r - 1.12
    return max(0.0, 100 - gap * 220)


def evaluate(act: dict, session: dict | None, lt_hr: int | None = None) -> dict:
    sport = SPORT_LABELS.get(act["sport"], "Training")
    pace = _pace(act) if act["sport"] == "run" else None
    zones = act.get("hr_zones_s")
    total_z = sum(z or 0 for z in zones) if zones else 0
    easy_share = (zones[0] + zones[1]) / total_z if total_z else None
    mid_share = zones[2] / total_z if total_z else None
    hard_share = (zones[3] + zones[4]) / total_z if total_z else None

    facts = []
    if act.get("distance_m"):
        facts.append(f"{act['distance_m'] / 1000:.1f} km".replace(".", ","))
    facts.append(fmt_duration(act.get("duration_s")))
    if pace:
        facts.append(f"Ø {fmt_pace(pace)} /km")
    elif act.get("avg_speed") and act["sport"] == "bike":
        facts.append(f"Ø {act['avg_speed'] * 3.6:.1f} km/h".replace(".", ","))
    if act.get("avg_power"):
        facts.append(f"Ø {round(act['avg_power'])} W")
    if act.get("avg_hr"):
        facts.append(f"Ø HF {round(act['avg_hr'])}")

    lines = [f"**Ist:** {' · '.join(facts)}"]
    if total_z:
        lines.append(f"**Zonen:** Z1–2 {easy_share:.0%} · Z3 {mid_share:.0%} · Z4–5 {hard_share:.0%}")
    te_bits = []
    if act.get("te_aerobic") is not None:
        te_bits.append(f"aerob {act['te_aerobic']:.1f}")
    if act.get("te_anaerobic") is not None:
        te_bits.append(f"anaerob {act['te_anaerobic']:.1f}")
    if act.get("training_load"):
        te_bits.append(f"Load {round(act['training_load'])}")
    if te_bits:
        lines.append(f"**Training Effect:** {' · '.join(te_bits)}")

    tips: list[str] = []
    if session is None:
        score = None
        headline = f"{sport}: ungeplante Einheit"
        tips.append("War nicht im Plan – die Belastung fließt in Fitness/Ermüdung ein und wird bei der Wochenanpassung berücksichtigt.")
        if act["sport"] == "run" and hard_share and hard_share > 0.2:
            tips.append("Recht intensiv für eine Zusatzeinheit – achte auf die nächste Schlüsseleinheit.")
        return {"activity_id": act["id"], "session_id": None, "score": None, "rating": "Ungeplant",
                "tag": "information_source", "headline": headline, "lines": lines, "tips": tips}

    if session["sport"] == "sail" or act["sport"] == "sail":
        tips.append("Segeln hat Priorität – die Belastung zählt in Fitness/Ermüdung. Morgen früh entscheidet die Readiness, wie viel Laufen drin ist.")
        return {"activity_id": act["id"], "session_id": session["id"], "score": None, "rating": "Segeln",
                "tag": "sailboat", "headline": f"Segeln erfasst: {fmt_duration(act.get('duration_s'))}", "lines": lines, "tips": tips}

    lines.insert(0, f"**Plan:** {session['title']} – {session['detail']}"
                 + (f" · {session['distance_km']:.1f} km".replace(".", ",") if session.get("distance_km") else f" · {session['duration_min']}'")
                 + (f" · Ziel {session['pace_label']}" if session.get("pace_label") and session["profile"] not in ("hills",) else ""))

    ratio = _volume_ratio(act, session)
    vol = _volume_score(ratio)
    if ratio is not None and ratio < 0.9:
        tips.append(f"Umfang bei {ratio:.0%} des Plans – kein Nachholen, die Woche läuft normal weiter.")
    elif ratio is not None and ratio > 1.15:
        tips.append(f"Umfang bei {ratio:.0%} des Plans – mehr ist nicht automatisch besser, Erholung sichern.")

    profile = PROFILES.get(session["profile"])
    penalties = []
    if profile and session["sport"] != "strength":
        if profile["easy_share"] is not None and easy_share is not None and easy_share < profile["easy_share"]:
            penalties.append(min(45, (profile["easy_share"] - easy_share) * 150))
            tips.append(f"Nur {easy_share:.0%} in Z1–2 (Ziel ≥ {profile['easy_share']:.0%}) – locker heißt wirklich locker, sonst fehlt die Frische für die Schlüsseleinheiten.")
        if profile["hard_share"] is not None and hard_share is not None and hard_share < profile["hard_share"]:
            penalties.append(min(40, (profile["hard_share"] - hard_share) * 150))
            tips.append(f"Nur {hard_share:.0%} in Z4–5 – die Belastungen waren zu weich oder zu kurz, um den gewünschten Reiz zu setzen.")
        te = act.get("te_aerobic")
        lo, hi = profile["te"]
        if te is not None and te < lo:
            penalties.append(min(30, (lo - te) * 25))
            tips.append(f"Aerober Effekt {te:.1f} unter dem Zielbereich {lo:.1f}–{hi:.1f}.")
        elif te is not None and te > hi + 0.2:
            penalties.append(min(30, (te - hi) * 25))
            tips.append(f"Aerober Effekt {te:.1f} über dem Zielbereich {lo:.1f}–{hi:.1f} – härter als geplant.")
        if pace and session["profile"] in ("easy", "recovery", "long") and session.get("pace_range"):
            fast = session["pace_range"][0] - (15 if session["profile"] == "long" else 5)
            if pace < fast:
                penalties.append(min(25, (fast - pace) * 1.5))
                tips.append(f"Ø-Tempo {fmt_pace(pace)} schneller als der Lockerbereich ({session['pace_label']}).")
        if (easy_share is None and act.get("avg_hr") and lt_hr and session["profile"] in ("easy", "recovery", "long", "z2")
                and act["avg_hr"] > 0.88 * lt_hr):
            penalties.append(20)
            tips.append(f"Ø-Puls {round(act['avg_hr'])} liegt nah an der Schwelle ({lt_hr}) – lockerer bleiben.")
    intensity = max(0.0, 100 - sum(penalties))
    score = int(round(0.4 * vol + 0.6 * intensity))
    label, tag = rating(score)
    if not tips:
        tips.append("Genau so geplant umgesetzt – weiter so.")
    weekday = WEEKDAYS_LONG[date.fromisoformat(act["date"]).weekday()]
    return {
        "activity_id": act["id"],
        "session_id": session["id"],
        "score": score,
        "rating": label,
        "tag": tag,
        "headline": f"{sport} am {weekday}: {score}/100 – {label}",
        "volume_ratio": round(ratio, 2) if ratio is not None else None,
        "lines": lines,
        "tips": tips,
    }
