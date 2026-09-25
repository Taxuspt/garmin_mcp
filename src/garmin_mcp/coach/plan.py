"""Periodised half-marathon plan: phases, weekly volume and daily sessions.

The week is fixed around four run days (Tue quality, Thu quality, Sat easy,
Sun long), Monday off, cycling/rowing as aerobic filler and two strength
sessions. Volume ramps from the configured start to peak, every fourth week
is a deload, and the last two weeks taper into the race. ``scales`` lets the
adaptation layer shrink or grow individual weeks without rewriting the plan.
"""

from datetime import date, timedelta

from .config import Config, sailing_day
from .vdot import HM_METERS, fmt_pace, race_seconds, training_paces

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
WEEKDAYS_LONG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

PHASE_LABELS = {
    "reset": "Übergang",
    "base1": "Grundlage 1",
    "base2": "Grundlage 2",
    "build": "Aufbau",
    "specific": "HM-spezifisch",
    "taper": "Taper",
}

# Quality-session libraries, indexed by week within the phase (clamped).
# Each entry: (title, main-set description, intensity key for pace targets,
# evaluation profile).
TUE = {
    "base1": [
        ("Hügelsprints", "8×10 s bergauf maximal, dazwischen voll erholen", "easy", "hills"),
        ("Hügelsprints", "10×10 s bergauf maximal, volle Pause", "easy", "hills"),
        ("Bergläufe", "6×45 s zügig bergauf, Trab zurück", "easy", "hills"),
        ("Bergläufe", "8×45 s zügig bergauf, Trab zurück", "easy", "hills"),
        ("Fahrtspiel", "10×1' zügig / 1' locker", "interval", "vo2"),
        ("Fahrtspiel", "12×1' zügig / 1' locker", "interval", "vo2"),
    ],
    "base2": [
        ("Intervalle 800 m", "6×800 m @ I-Tempo, 2' Trabpause", "interval", "vo2"),
        ("Intervalle 800 m", "7×800 m @ I-Tempo, 2' Trabpause", "interval", "vo2"),
        ("Intervalle 1000 m", "5×1000 m @ I-Tempo, 2:30' Trabpause", "interval", "vo2"),
        ("Intervalle 800 m", "8×800 m @ I-Tempo, 2' Trabpause", "interval", "vo2"),
        ("Intervalle 1000 m", "6×1000 m @ I-Tempo, 2:30' Trabpause", "interval", "vo2"),
        ("Intervalle 1200 m", "5×1200 m @ I-Tempo, 3' Trabpause", "interval", "vo2"),
    ],
    "build": [
        ("VO2max 1000 m", "6×1000 m @ I-Tempo, 2:30' Trabpause", "interval", "vo2"),
        ("VO2max 1200 m", "5×1200 m @ I-Tempo, 3' Trabpause", "interval", "vo2"),
        ("VO2max 1600 m", "4×1600 m @ I-Tempo, 3' Trabpause", "interval", "vo2"),
        ("VO2max 1000 m", "7×1000 m @ I-Tempo, 2:30' Trabpause", "interval", "vo2"),
        ("VO2max 1200 m", "6×1200 m @ I-Tempo, 3' Trabpause", "interval", "vo2"),
        ("VO2max 2000 m", "3×2000 m @ I-Tempo, 4' Trabpause", "interval", "vo2"),
        ("VO2max 1600 m", "5×1600 m @ I-Tempo, 3' Trabpause", "interval", "vo2"),
    ],
    "specific": [
        ("HM-Tempo 3 km", "3×3 km @ HM-Tempo, 2' Trabpause", "hm", "hm"),
        ("HM-Tempo 3 km", "4×3 km @ HM-Tempo, 2' Trabpause", "hm", "hm"),
        ("HM-Tempo 5 km", "2×5 km @ HM-Tempo, 3' Trabpause", "hm", "hm"),
        ("HM-Tempo 4 km", "3×4 km @ HM-Tempo, 2' Trabpause", "hm", "hm"),
    ],
    "taper": [
        ("HM-Schärfe", "3×2 km @ HM-Tempo, 2' Trabpause", "hm", "hm"),
        ("Aktivierung", "4×1 km @ HM-Tempo, 2' Trabpause", "hm", "hm"),
    ],
}

THU = {
    "base1": [
        ("Steigerungslauf", "letzte 10' @ M-Tempo", "marathon", "steady"),
        ("Steigerungslauf", "letzte 15' @ M-Tempo", "marathon", "steady"),
        ("Steigerungslauf", "letzte 20' @ M-Tempo", "marathon", "steady"),
        ("Steigerungslauf", "letzte 20' @ M-Tempo, letzte 5' @ HM-Tempo", "marathon", "steady"),
        ("Steigerungslauf", "letzte 25' @ M-Tempo", "marathon", "steady"),
        ("Steigerungslauf", "letzte 25' @ M-Tempo, letzte 5' @ HM-Tempo", "marathon", "steady"),
    ],
    "base2": [
        ("Schwelle", "3×8' @ T-Tempo, 2' Trabpause", "threshold", "threshold"),
        ("Schwelle", "3×10' @ T-Tempo, 2' Trabpause", "threshold", "threshold"),
        ("Schwelle", "4×8' @ T-Tempo, 90\" Trabpause", "threshold", "threshold"),
        ("Schwelle", "2×15' @ T-Tempo, 3' Trabpause", "threshold", "threshold"),
        ("Schwelle", "3×12' @ T-Tempo, 2' Trabpause", "threshold", "threshold"),
        ("Schwelle", "4×10' @ T-Tempo, 2' Trabpause", "threshold", "threshold"),
    ],
    "build": [
        ("Tempodauerlauf", "20' @ T-Tempo am Stück", "threshold", "threshold"),
        ("Schwelle", "3×12' @ T-Tempo, 2' Trabpause", "threshold", "threshold"),
        ("Tempodauerlauf", "25' @ T-Tempo am Stück", "threshold", "threshold"),
        ("Schwelle", "2×20' @ T-Tempo, 3' Trabpause", "threshold", "threshold"),
        ("Schwelle", "4×10' @ T-Tempo, 90\" Trabpause", "threshold", "threshold"),
        ("Tempodauerlauf", "30' @ T-Tempo am Stück", "threshold", "threshold"),
        ("Schwelle", "3×15' @ T-Tempo, 2' Trabpause", "threshold", "threshold"),
    ],
    "specific": [
        ("VO2max-Erhalt", "5×1000 m @ I-Tempo, 2:30' Trabpause", "interval", "vo2"),
        ("Schwelle 2 km", "4×2 km @ T-Tempo, 90\" Trabpause", "threshold", "threshold"),
        ("VO2max-Erhalt", "6×1000 m @ I-Tempo, 2:30' Trabpause", "interval", "vo2"),
        ("Schwelle 2 km", "3×2 km @ T-Tempo, 90\" Trabpause", "threshold", "threshold"),
    ],
    "taper": [
        ("Schwelle kurz", "3×1600 m @ T-Tempo, 90\" Trabpause", "threshold", "threshold"),
        ("Locker + Steigerungen", "30' locker + 4×20\" Steigerungen", "easy", "easy"),
    ],
}

SUN = {
    "base1": [("Langer Lauf", "gleichmäßig locker", "easy", "long")],
    "base2": [
        ("Langer Lauf", "gleichmäßig locker", "easy", "long"),
        ("Langer Lauf", "letzte 3 km @ M-Tempo", "easy", "long"),
    ],
    "build": [
        ("Langer Lauf", "letzte 5 km @ M-Tempo", "easy", "long"),
        ("Langer Lauf", "gleichmäßig locker", "easy", "long"),
        ("Langer Lauf", "letzte 6 km @ M-Tempo", "easy", "long"),
        ("Langer Lauf", "letzte 4 km @ HM-Tempo", "easy", "long"),
        ("Langer Lauf", "gleichmäßig locker", "easy", "long"),
        ("Langer Lauf", "letzte 6 km @ HM-Tempo", "easy", "long"),
    ],
    "specific": [
        ("Langer Lauf HM-spezifisch", "darin 8 km @ HM-Tempo", "easy", "long"),
        ("Langer Lauf HM-spezifisch", "darin 3×4 km @ HM-Tempo, 1 km locker", "easy", "long"),
        ("Langer Lauf HM-spezifisch", "darin 10 km @ HM-Tempo", "easy", "long"),
        ("Langer Lauf", "gleichmäßig locker, letzte 3 km zügig", "easy", "long"),
    ],
    "taper": [("Langer Lauf kurz", "darin 5 km @ HM-Tempo", "easy", "long")],
}


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _phases(n_weeks: int) -> list[str]:
    taper, specific, build = 2, 4, 7
    reset = 2 if n_weeks >= 20 else 1
    base = max(0, n_weeks - taper - specific - build - reset)
    base1 = base // 2
    phases = ["reset"] * reset + ["base1"] * base1 + ["base2"] * (base - base1)
    phases += ["build"] * build + ["specific"] * specific + ["taper"] * taper
    # Short runway: drop from the front so the race still lines up.
    return phases[-n_weeks:]


def week_skeleton(cfg: Config) -> list[dict]:
    """Phase, deload flag and index-within-phase for every plan week."""
    start = monday_of(cfg.plan_start)
    race_monday = monday_of(cfg.race_date)
    n_weeks = (race_monday - start).days // 7 + 1
    phases = _phases(n_weeks)
    weeks, counts, since_deload = [], {}, 0
    for i, phase in enumerate(phases):
        in_load_phase = phase in ("base1", "base2", "build", "specific")
        since_deload = since_deload + 1 if in_load_phase else 0
        deload = in_load_phase and since_deload == 4 and phases[min(i + 1, n_weeks - 1)] != "taper"
        if deload:
            since_deload = 0
        idx = counts.get(phase, 0)
        counts[phase] = idx + (0 if deload else 1)
        weeks.append({
            "index": i,
            "start": start + timedelta(weeks=i),
            "phase": phase,
            "deload": deload,
            "phase_week": idx,
            "race_week": i == n_weeks - 1,
        })
    return weeks


def _progress(weeks: list[dict], i: int) -> float:
    """0 at plan start → 1 at the last pre-taper week."""
    last_build = max(w["index"] for w in weeks if w["phase"] != "taper")
    return min(1.0, i / max(1, last_build - 1))


def _pick(lib: dict, phase: str, idx: int):
    items = lib.get(phase) or lib["base1"]
    return items[min(idx, len(items) - 1)]


def _run(day: date, title: str, detail: str, km: float, pace_key: str, profile: str,
         paces: dict, key: bool) -> dict:
    easy_mid = sum(paces["easy"]) / 2
    target = paces.get(pace_key, paces["easy"])
    # Quality runs mix warm-up/cool-down at easy pace with a faster main set.
    avg_pace = easy_mid if profile in ("easy", "long", "hills", "recovery") else (easy_mid + sum(target) / 2) / 2
    return {
        "id": f"{day.isoformat()}-run-{profile}",
        "date": day.isoformat(),
        "sport": "run",
        "profile": profile,
        "key": key,
        "title": title,
        "detail": detail,
        "distance_km": round(km, 1),
        "duration_min": round(km * avg_pace / 60),
        "pace_key": pace_key,
        "pace_range": [round(target[0]), round(target[1])],
        "pace_label": f"{fmt_pace(target[0])}–{fmt_pace(target[1])} /km",
    }


def _other(day: date, sport: str, profile: str, title: str, detail: str, minutes: float) -> dict:
    return {
        "id": f"{day.isoformat()}-{sport}-{profile}",
        "date": day.isoformat(),
        "sport": sport,
        "profile": profile,
        "key": False,
        "title": title,
        "detail": detail,
        "duration_min": int(round(minutes / 5) * 5),
    }


def _week_targets(cfg: Config, weeks: list[dict], w: dict) -> tuple[float, float]:
    peak_km, peak_h = cfg.peak_run_km, cfg.peak_hours
    p = _progress(weeks, w["index"])
    km = cfg.start_run_km + (peak_km - cfg.start_run_km) * p
    hours = cfg.start_hours + (peak_h - cfg.start_hours) * p
    if w["phase"] == "reset":
        km, hours = cfg.start_run_km * 0.8, cfg.start_hours * 0.85
    if w["deload"]:
        km, hours = km * 0.75, hours * 0.75
    if w["phase"] == "taper":
        factor = 0.45 if w["race_week"] else 0.7
        km, hours = peak_km * factor, peak_h * factor
    return km, hours


def build_week(cfg: Config, weeks: list[dict], w: dict, paces: dict, scale: float = 1.0) -> dict:
    km, hours = _week_targets(cfg, weeks, w)
    prev_sailing = sum(1 for i in range(1, 8) if sailing_day(cfg, w["start"] - timedelta(days=i)))
    reentry = prev_sailing >= 5 and not w["race_week"]
    if reentry:
        # Back from a long sailing block: don't jump straight to full volume.
        km, hours = km * 0.85, hours * 0.85
    if not w["race_week"]:
        km, hours = km * scale, hours * scale
    d = [w["start"] + timedelta(days=i) for i in range(7)]
    phase, idx = w["phase"], w["phase_week"]
    days: list[list[dict]] = [[] for _ in range(7)]

    if w["race_week"]:
        race_km = cfg.race_distance_m / 1000
        rest_km = max(12.0, km - race_km)
        t = _pick(TUE, "taper", 1)
        days[1].append(_run(d[1], t[0], f"2 km ein, {t[1]}, 2 km aus", rest_km * 0.45, t[2], t[3], paces, True))
        days[3].append(_run(d[3], "Locker + Steigerungen", "30' locker + 4×20\" Steigerungen", rest_km * 0.35, "easy", "easy", paces, False))
        days[5].append(_run(d[5], "Shakeout", "20' sehr locker + 3 kurze Steigerungen", rest_km * 0.2, "recovery", "recovery", paces, False))
        race = _run(d[6], cfg.race, f"Wettkampf – Ziel {_fmt_goal(cfg)}", race_km, "hm", "race", paces, True)
        race["duration_min"] = round(cfg.target_seconds / 60)
        days[6].append(race)
        days[2].append(_other(d[2], "bike", "easy", "Rad locker", "Beine lockern, Z1", 45))
    else:
        split = {"tue": 0.24, "thu": 0.24, "sat": 0.18, "sun": 0.34}
        long_km = min(km * split["sun"], cfg.long_run_cap_km)
        spill = km * split["sun"] - long_km
        if phase == "reset":
            days[1].append(_run(d[1], "Lockerer Lauf + Steigerungen", "locker, am Ende 6×20\" Steigerungen", km * split["tue"], "easy", "easy", paces, False))
            days[3].append(_run(d[3], "Lockerer Lauf", "ruhig, nach Gefühl", km * split["thu"], "easy", "easy", paces, False))
            days[6].append(_run(d[6], "Langer Lauf", "locker, gerne im Gelände", long_km, "easy", "long", paces, True))
        elif w["deload"]:
            days[1].append(_run(d[1], "Lockerer Lauf + Steigerungen", "locker, am Ende 8×20\" Steigerungen", km * split["tue"], "easy", "easy", paces, False))
            days[3].append(_run(d[3], "Schwelle kurz", "2 km ein, 2×8' @ T-Tempo 2' Trabpause, 2 km aus", km * split["thu"], "threshold", "threshold", paces, True))
            days[6].append(_run(d[6], "Langer Lauf (Entlastung)", "gleichmäßig locker", long_km, "easy", "long", paces, True))
        else:
            t, h, s = _pick(TUE, phase, idx), _pick(THU, phase, idx), _pick(SUN, phase, idx)
            days[1].append(_run(d[1], t[0], f"2–3 km ein, {t[1]}, 2 km aus", km * split["tue"] + spill / 2, t[2], t[3], paces, True))
            days[3].append(_run(d[3], h[0], f"2–3 km ein, {h[1]}, 2 km aus" if h[3] != "steady" else f"locker beginnen, {h[1]}", km * split["thu"] + spill / 2, h[2], h[3], paces, True))
            days[6].append(_run(d[6], s[0], s[1], long_km, s[2], s[3], paces, True))
        days[5].append(_run(d[5], "Lockerer Lauf + Steigerungen", "locker, am Ende 5×20\" Steigerungen", km * split["sat"], "easy", "easy", paces, False))

        strength_min = {"reset": 45, "base1": 60, "base2": 60, "build": 55, "specific": 45, "taper": 30}[phase]
        n_strength = 1 if phase == "taper" else 2
        focus_a = "Maximalkraft Beine/Hüfte: Kniebeuge, Kreuzheben, Split Squats, Wadenheben" if phase in ("base1", "base2") else "Kraft-Erhalt: Kniebeuge, Kreuzheben rumänisch, Step-ups – 2–3 Sätze schwer"
        focus_b = "Rumpf & Stabi + Sprungkraft (Pogo Jumps, Box Jumps, Skippings)"
        days[1].append(_other(d[1], "strength", "strength", "Krafttraining A", focus_a, strength_min))
        if n_strength == 2:
            days[4].append(_other(d[4], "strength", "strength", "Krafttraining B", focus_b, strength_min))

        run_min = sum(s["duration_min"] for day in days for s in day if s["sport"] == "run")
        strength_total = strength_min * n_strength
        cross = max(0.0, hours * 60 - run_min - strength_total)
        wed = min(cross * 0.6, 150)
        fri = min(cross * 0.4, 75)
        sat = max(0.0, cross - wed - fri)
        if wed >= 30:
            days[2].append(_other(d[2], "bike", "z2", "Rad Grundlage", "GA1, Trittfrequenz 85–95, Puls Zone 2", wed))
        if fri >= 20:
            days[4].append(_other(d[4], "row", "z2", "Rudern/Rad locker", "Zone 1–2, locker bleiben", fri))
        if sat >= 30:
            days[5].append(_other(d[5], "bike", "z2", "Rad locker (optional)", "Zone 1–2", min(sat, 120)))

    sailing = _apply_sailing(cfg, d, days, paces)

    sessions = [s for day in days for s in day]
    training = [s for s in sessions if s["sport"] != "sail" and not s.get("optional")]
    return {
        "start": w["start"].isoformat(),
        "index": w["index"],
        "phase": phase,
        "phase_label": PHASE_LABELS[phase],
        "deload": w["deload"],
        "race_week": w["race_week"],
        "scale": round(scale, 3),
        "run_km": round(sum(s.get("distance_km", 0) for s in training if s["sport"] == "run"), 1),
        "hours": round(sum(s["duration_min"] for s in training) / 60, 1),
        "reentry": reentry,
        "sailing_days": len(sailing),
        "sailing": sorted({b for b in sailing}),
        "sessions": sessions,
    }


def _sail(day: date, regatta: bool, title: str, tentative: bool) -> dict:
    return {
        "id": f"{day.isoformat()}-sail-{'regatta' if regatta else 'training'}",
        "date": day.isoformat(),
        "sport": "sail",
        "profile": "sail",
        "key": False,
        "priority": True,
        "title": ("Regatta: " if regatta else "Segeltraining: ") + title,
        "detail": ("Segeln hat Priorität – Wettkampftag" if regatta else "Segeln hat Priorität – Training am Wasser")
                  + (" (Termin noch offen)" if tentative else ""),
        "duration_min": 300 if regatta else 240,
    }


def _apply_sailing(cfg: Config, d: list[date], days: list[list[dict]], paces: dict) -> list[str]:
    """Give sailing days priority: clear them, keep short runs, move key runs.

    Regatta days only get an optional 20' activation jog. Training days on the
    water get at most two short morning runs per week. Key runs that fall on a
    sailing day move to the nearest free day of the same week that keeps a day
    without key run on both sides; if there is none they are dropped.
    """
    flags = [sailing_day(cfg, x) for x in d]
    if not any(flags):
        return []
    displaced: list[tuple[int, dict]] = []
    camp_idx: list[int] = []
    titles: list[str] = []
    for i, flag in enumerate(flags):
        if not flag:
            continue
        block = flag["block"]
        titles.append(block["title"])
        displaced += [(i, s) for s in days[i] if s.get("key") and s["sport"] == "run" and s["profile"] != "race"]
        days[i] = [_sail(d[i], flag["regatta"], block["title"], block["tentative"])]
        if flag["regatta"]:
            opt = _run(d[i], "Optional: Aktivierung", "20' ganz locker + Mobility vor dem Auslaufen", 3.5, "recovery", "recovery", paces, False)
            opt["optional"] = True
            days[i].append(opt)
        else:
            camp_idx.append(i)
    picks = (camp_idx[1:] or camp_idx)[::3][:2]
    for n, i in enumerate(picks):
        detail = "35' locker morgens vor dem Segeln" + (", am Ende 5×20\" Steigerungen" if n == 0 else "")
        days[i].append(_run(d[i], "Morgenlauf", detail, 6.5, "easy", "easy", paces, False))

    def has_key(j: int) -> bool:
        return 0 <= j < 7 and any(s.get("key") and s["sport"] == "run" for s in days[j])

    # Long run first: it matters most for a half marathon.
    for orig, s in sorted(displaced, key=lambda x: x[1]["profile"] != "long"):
        free = [j for j in range(7) if not flags[j] and not has_key(j) and not has_key(j - 1) and not has_key(j + 1)]
        free.sort(key=lambda j: (j == 0, abs(j - orig)))  # Monday stays rest day if possible
        if not free:
            continue
        j = free[0]
        moved = dict(s, date=d[j].isoformat(), id=s["id"].replace(d[orig].isoformat(), d[j].isoformat()),
                     adjusted=f"Von {WEEKDAYS_LONG[orig]} verschoben – Segeln hat Priorität")
        days[j] = [x for x in days[j] if x["sport"] != "run"] + [moved]
    return titles


def _fmt_goal(cfg: Config) -> str:
    s = int(cfg.target_seconds)
    return f"unter {s // 3600}:{(s % 3600) // 60 + (1 if s % 60 else 0):02d}"


def build_plan(cfg: Config, vdot: float, scales: dict[str, float] | None = None) -> list[dict]:
    scales = scales or {}
    paces = training_paces(vdot)
    weeks = week_skeleton(cfg)
    return [build_week(cfg, weeks, w, paces, scales.get(w["start"].isoformat(), 1.0)) for w in weeks]


def sessions_on(plan: list[dict], day: date) -> list[dict]:
    iso = day.isoformat()
    monday = monday_of(day).isoformat()
    for week in plan:
        if week["start"] == monday:
            return [s for s in week["sessions"] if s["date"] == iso]
    return []


def week_of(plan: list[dict], day: date) -> dict | None:
    monday = monday_of(day).isoformat()
    return next((w for w in plan if w["start"] == monday), None)


def predicted_race_seconds(vdot: float, distance_m: float = HM_METERS) -> float:
    return race_seconds(vdot, distance_m)
