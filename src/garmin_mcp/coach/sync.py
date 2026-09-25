"""``garmin-coach``: sync Garmin, adapt the plan, rate sessions, push, build the dashboard.

Designed to run every ~30 minutes from GitHub Actions. All state lives in one
JSON document (encrypted at rest when ``COACH_PASSPHRASE`` is set) so a run is
idempotent: already-rated activities and already-sent messages are skipped.
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import adapt, crypto, garmin_source, notify
from .config import Config, load_config
from .evaluate import SPORT_LABELS, evaluate, match_session
from .load import acwr, daily_loads, pmc
from .plan import WEEKDAYS, WEEKDAYS_LONG, build_plan, monday_of, predicted_race_seconds
from .vdot import fmt_duration, fmt_pace, training_paces, vdot_from_race

KEEP_DAYS = 150
PACE_LABELS = {
    "easy": "Locker (E)",
    "marathon": "Marathon (M)",
    "hm": "Halbmarathon (HM)",
    "threshold": "Schwelle (T)",
    "interval": "Intervall (I)",
    "recovery": "Regeneration",
}


# --------------------------------------------------------------------------- state

def new_state(cfg: Config, now: datetime) -> dict:
    return {
        "version": 1,
        "created": now.isoformat(),
        "notify_since": now.isoformat(),
        "activities": {},
        "evaluations": {},
        "notified": [],
        "wellness": {},
        "metrics": {},
        "vdot": cfg.start_vdot,
        "scales": {},
        "current_scale": 1.0,
        "last_week_processed": None,
        "day_overrides": {},
        "readiness": {},
        "log": [],
        "sent": {},
    }


def read_blob(path: Path, passphrase: str | None):
    if passphrase and path.with_suffix(".enc").exists():
        return crypto.decrypt_json(path.with_suffix(".enc").read_text(), passphrase)
    if path.with_suffix(".json").exists():
        return json.loads(path.with_suffix(".json").read_text())
    return None


def write_blob(path: Path, obj, passphrase: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if passphrase:
        path.with_suffix(".enc").write_text(crypto.encrypt_json(obj, passphrase))
        path.with_suffix(".json").unlink(missing_ok=True)
    else:
        path.with_suffix(".json").write_text(json.dumps(obj, ensure_ascii=False, indent=1))


def log(state: dict, now: datetime, kind: str, text: str) -> None:
    state["log"].append({"at": now.isoformat(timespec="minutes"), "kind": kind, "text": text})
    state["log"] = state["log"][-60:]
    print(f"[coach] {kind}: {text}", file=sys.stderr)


# --------------------------------------------------------------------------- plan helpers

def scales_for(state: dict, plan_weeks: list[str], this_monday: date) -> dict[str, float]:
    out = {}
    for start in plan_weeks:
        if start in state["scales"]:
            out[start] = state["scales"][start]
        elif start >= this_monday.isoformat():
            out[start] = state["current_scale"]
    return out


def current_plan(cfg: Config, state: dict, today: date) -> list[dict]:
    skeleton = build_plan(cfg, state["vdot"])
    return build_plan(cfg, state["vdot"], scales_for(state, [w["start"] for w in skeleton], monday_of(today)))


def sessions_by_day(plan: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for week in plan:
        for s in week["sessions"]:
            out.setdefault(s["date"], []).append(s)
    return out


def effective_sessions(plan_days: dict[str, list[dict]], state: dict, iso: str) -> list[dict]:
    override = state["day_overrides"].get(iso)
    return override["sessions"] if override else plan_days.get(iso, [])


def session_line(s: dict) -> str:
    size = f"{s['distance_km']:.1f} km".replace(".", ",") if s.get("distance_km") else f"{s['duration_min']}'"
    pace = f" · {s['pace_label']}" if s.get("pace_label") and s["profile"] not in ("hills",) else ""
    return f"**{s['title']}** ({size}{pace}) – {s['detail']}"


# --------------------------------------------------------------------------- sync steps

def sync_garmin(client, cfg: Config, state: dict, today: date) -> None:
    acts = state["activities"]
    since = today - timedelta(days=120 if not acts else 3)
    for act in garmin_source.fetch_activities(client, since, today):
        acts[act["id"]] = act
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    for aid in [a for a, v in acts.items() if v["date"] < cutoff]:
        acts.pop(aid)
        state["evaluations"].pop(aid, None)

    backfill = 14 if not state["wellness"] else 2
    for i in range(backfill):
        d = today - timedelta(days=i)
        if i > 1 and d.isoformat() in state["wellness"]:
            continue
        state["wellness"][d.isoformat()] = garmin_source.fetch_wellness(client, d)
    for d in sorted(state["wellness"])[:-60]:
        state["wellness"].pop(d)

    last = state["metrics"].get("updated")
    if not last or date.fromisoformat(last) <= today - timedelta(days=3):
        state["metrics"].update(garmin_source.fetch_metrics(client, today))
        state["metrics"]["updated"] = today.isoformat()


def load_series(cfg: Config, state: dict, today: date) -> list[dict]:
    max_hr = state["metrics"].get("max_hr") or cfg.max_hr
    rest = state["wellness"].get(today.isoformat(), {}).get("rhr") or cfg.rest_hr
    loads = daily_loads(list(state["activities"].values()), max_hr, rest)
    return pmc(loads, today - timedelta(days=KEEP_DAYS - 1), today)


def weekly_adaptation(cfg: Config, state: dict, today: date, now: datetime, series: list[dict]) -> None:
    this_monday = monday_of(today)
    if state["last_week_processed"] == this_monday.isoformat():
        return
    state["last_week_processed"] = this_monday.isoformat()
    last_monday = this_monday - timedelta(days=7)
    goal_vdot = vdot_from_race(cfg.race_distance_m, cfg.target_seconds)

    new_vdot, why = adapt.update_vdot(state["vdot"], state["metrics"].get("lt_pace"), goal_vdot)
    if why:
        state["vdot"] = new_vdot
        log(state, now, "tempo", why)

    if last_monday < monday_of(cfg.plan_start):
        return
    plan = current_plan(cfg, state, last_monday)
    days = sessions_by_day(plan)
    week_iso = [(last_monday + timedelta(days=i)).isoformat() for i in range(7)]
    keys = [s for d in week_iso for s in effective_sessions(days, state, d) if s.get("key")]
    sail_days = sum(1 for d in week_iso if any(s["sport"] == "sail" for s in effective_sessions(days, state, d)))
    done_ids = {e["session_id"] for e in state["evaluations"].values() if e.get("session_id")}
    compliance = sum(1 for s in keys if s["id"] in done_ids) / len(keys) if keys else None
    planned_km = sum(s.get("distance_km", 0) for d in week_iso for s in effective_sessions(days, state, d) if s["sport"] == "run")
    done_km = sum(a["distance_m"] / 1000 for a in state["activities"].values() if a["sport"] == "run" and a["date"] in week_iso and a.get("distance_m"))
    ratio = done_km / planned_km if planned_km else None
    if sail_days >= 3:
        # Sailing has priority: a week shaped by it says nothing about running compliance.
        compliance, ratio = None, None
    ready = [state["readiness"][d]["score"] for d in week_iso if d in state["readiness"]]
    avg_ready = sum(ready) / len(ready) if ready else None
    new_scale, reason = adapt.weekly_scale(state["current_scale"], compliance, ratio, avg_ready, acwr(series))
    state["scales"].setdefault(last_monday.isoformat(), state["current_scale"])
    if new_scale != state["current_scale"]:
        log(state, now, "umfang", f"Umfang ab {this_monday:%d.%m.}: {state['current_scale']:.0%} → {new_scale:.0%} ({reason})")
    state["current_scale"] = new_scale
    state["last_week_stats"] = {"week": last_monday.isoformat(), "compliance": compliance, "run_ratio": ratio,
                                "done_km": round(done_km, 1), "planned_km": round(planned_km, 1), "avg_readiness": avg_ready, "reason": reason}


def daily_adaptation(cfg: Config, state: dict, today: date, now: datetime, plan_days: dict, series: list[dict]) -> None:
    iso = today.isoformat()
    if iso in state["readiness"] or now.hour < 5:
        return
    well = state["wellness"].get(iso, {})
    has_signal = any(k in well for k in ("readiness", "hrv_status", "sleep_score"))
    if not has_signal and now.hour < 10:
        return  # wait for the watch to sync the night
    status = adapt.readiness_status(well, series[-1]["tsb"] if series else None)
    state["readiness"][iso] = status
    for d in sorted(state["readiness"])[:-60]:
        state["readiness"].pop(d)
    overrides = adapt.adapt_day(today, plan_days, status)
    for d, ov in overrides.items():
        state["day_overrides"][d] = ov
        if d == iso and ov.get("note"):
            log(state, now, "tag", f"{WEEKDAYS_LONG[today.weekday()]}: {ov['note']}")
    for d in sorted(state["day_overrides"])[:-60]:
        state["day_overrides"].pop(d)


def rate_activities(cfg: Config, state: dict, plan_days: dict, series: list[dict], now: datetime, push: bool) -> None:
    taken = {e["session_id"] for e in state["evaluations"].values() if e.get("session_id")}
    notify_since = state["notify_since"][:19].replace("T", " ")
    for act in sorted(state["activities"].values(), key=lambda a: a["start"]):
        if act["id"] in state["evaluations"]:
            continue
        session = match_session(act, effective_sessions(plan_days, state, act["date"]), taken)
        ev = evaluate(act, session, state["metrics"].get("lt_hr"))
        state["evaluations"][act["id"]] = ev
        if session:
            taken.add(session["id"])
        fresh = act["start"] >= notify_since and act["id"] not in state["notified"]
        if push and fresh:
            send_evaluation(cfg, state, act, ev, plan_days, series, now)
            state["notified"].append(act["id"])
    state["notified"] = state["notified"][-200:]


def next_session_text(state: dict, plan_days: dict, after: date, skip_today_done: bool = True) -> str | None:
    for i in range(0, 8):
        d = after + timedelta(days=i)
        sessions = effective_sessions(plan_days, state, d.isoformat())
        done = {e["session_id"] for e in state["evaluations"].values()}
        todo = [s for s in sessions if s["id"] not in done]
        if todo:
            when = "Heute" if i == 0 else "Morgen" if i == 1 else WEEKDAYS_LONG[d.weekday()]
            return f"{when}: " + " + ".join(f"{s['title']} ({s['distance_km']:.1f} km)".replace(".", ",") if s.get("distance_km") else f"{s['title']} ({s['duration_min']}')" for s in todo)
    return None


def send_evaluation(cfg, state, act, ev, plan_days, series, now) -> None:
    body = list(ev["lines"])
    body += [f"• {t}" for t in ev["tips"]]
    if series:
        p = series[-1]
        body.append(f"**Form:** Fitness {p['ctl']:.0f} · Ermüdung {p['atl']:.0f} · Frische {p['tsb']:+.0f}")
    nxt = next_session_text(state, plan_days, date.fromisoformat(act["date"]))
    if nxt:
        body.append(f"**Als Nächstes:** {nxt}")
    notify.send(ev["headline"], "\n".join(body), tags=[ev["tag"], "runner" if act["sport"] == "run" else "muscle"])
    log(state, now, "bewertung", ev["headline"])


def morning_brief(cfg, state, today, now, plan_days, series) -> None:
    iso = today.isoformat()
    if state["sent"].get("morning") == iso or now.hour < cfg.morning_brief_hour:
        return
    if iso not in state["readiness"] and now.hour < cfg.morning_brief_hour + 3:
        return
    state["sent"]["morning"] = iso
    sessions = effective_sessions(plan_days, state, iso)
    status = state["readiness"].get(iso)
    lamp = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    lines = []
    if status:
        lines.append(f"{lamp[status['level']]} **Readiness {status['score']}** – {', '.join(status['reasons']) or 'keine Daten'}")
    days_left = (cfg.race_date - today).days
    if sessions:
        lines += [f"• {session_line(s)}" + (f"\n  _{s['adjusted']}_" if s.get("adjusted") else "") for s in sessions]
    else:
        lines.append("Ruhetag – Beine hoch, gut essen, früh schlafen.")
    note = state["day_overrides"].get(iso, {}).get("note")
    if note:
        lines.append(f"**Anpassung:** {note}")
    if series:
        lines.append(f"Frische {series[-1]['tsb']:+.0f} · noch {days_left} Tage bis {cfg.race}")
    title = f"{WEEKDAYS_LONG[today.weekday()]}: " + (" + ".join(s["title"] for s in sessions) if sessions else "Ruhetag")
    notify.send(title, "\n".join(lines), tags=["calendar"], priority=3)
    log(state, now, "morgen", title)


def weekly_review(cfg, state, today, now, plan_days) -> None:
    if today.weekday() != cfg.weekly_review_weekday or now.hour < cfg.weekly_review_hour:
        return
    monday = monday_of(today)
    if state["sent"].get("weekly") == monday.isoformat():
        return
    state["sent"]["weekly"] = monday.isoformat()
    week_iso = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
    planned = [s for d in week_iso for s in effective_sessions(plan_days, state, d)]
    evals = [e for e in state["evaluations"].values() if e.get("session_id") in {s["id"] for s in planned}]
    acts = [a for a in state["activities"].values() if a["date"] in week_iso]
    run_km = sum(a.get("distance_m", 0) for a in acts if a["sport"] == "run") / 1000
    plan_km = sum(s.get("distance_km", 0) for s in planned if s["sport"] == "run")
    hours = sum(a.get("duration_s", 0) for a in acts) / 3600
    plan_h = sum(s["duration_min"] for s in planned) / 60
    scores = [e["score"] for e in evals if e.get("score") is not None]
    keys = [s for s in planned if s.get("key")]
    key_done = sum(1 for s in keys if s["id"] in {e["session_id"] for e in evals})
    lines = [
        f"**Laufen:** {run_km:.1f} / {plan_km:.1f} km".replace(".", ","),
        f"**Gesamt:** {hours:.1f} / {plan_h:.1f} h".replace(".", ","),
        f"**Schlüsseleinheiten:** {key_done}/{len(keys)}",
    ]
    if scores:
        lines.append(f"**Ø Bewertung:** {sum(scores) / len(scores):.0f}/100")
    nxt_monday = monday + timedelta(days=7)
    nxt = [w for w in current_plan(cfg, state, nxt_monday) if w["start"] == nxt_monday.isoformat()]
    if nxt:
        w = nxt[0]
        lines.append(f"**Nächste Woche:** {w['phase_label']}{' (Entlastung)' if w['deload'] else ''} · {w['run_km']:.0f} km · {w['hours']:.1f} h".replace(".", ","))
        lines += [f"• {WEEKDAYS[date.fromisoformat(s['date']).weekday()]}: {s['title']}" for s in w["sessions"] if s.get("key")]
    notify.send(f"Wochenbilanz KW {today.isocalendar().week}", "\n".join(lines), tags=["bar_chart"])
    log(state, now, "woche", f"Wochenbilanz KW {today.isocalendar().week} verschickt")


# --------------------------------------------------------------------------- dashboard

def build_view(cfg: Config, state: dict, today: date, now: datetime, plan: list[dict], series: list[dict]) -> dict:
    plan_days = sessions_by_day(plan)
    evals = state["evaluations"]
    by_session = {e["session_id"]: aid for aid, e in evals.items() if e.get("session_id")}
    acts = state["activities"]

    def session_view(s: dict) -> dict:
        v = dict(s)
        aid = by_session.get(s["id"])
        if aid:
            v["status"] = "done"
            v["activity_id"] = aid
            v["score"] = evals[aid].get("score")
        elif s["date"] < today.isoformat():
            v["status"] = "skipped" if s.get("optional") or s["sport"] == "sail" else "missed"
        elif s["date"] == today.isoformat():
            v["status"] = "today"
        else:
            v["status"] = "planned"
        return v

    monday = monday_of(today)
    week = next((w for w in plan if w["start"] == monday.isoformat()), None)
    week_days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        iso = d.isoformat()
        sessions = [session_view(s) for s in effective_sessions(plan_days, state, iso)]
        extra = [{"id": a["id"], "name": a["name"], "sport": a["sport"], "score": None}
                 for a in acts.values() if a["date"] == iso and evals.get(a["id"], {}).get("session_id") is None]
        week_days.append({"date": iso, "weekday": WEEKDAYS[i], "sessions": sessions, "unplanned": extra,
                          "note": state["day_overrides"].get(iso, {}).get("note")})

    def actual_week(start: str) -> dict:
        s = date.fromisoformat(start)
        days = {(s + timedelta(days=i)).isoformat() for i in range(7)}
        out = {"run_km": 0.0, "hours": 0.0, "sail_h": 0.0, "by_sport": {}}
        for a in acts.values():
            if a["date"] in days:
                h = a.get("duration_s", 0) / 3600
                if a["sport"] == "sail":
                    out["sail_h"] += h
                else:
                    out["hours"] += h
                out["by_sport"][a["sport"]] = round(out["by_sport"].get(a["sport"], 0) + h, 2)
                if a["sport"] == "run":
                    out["run_km"] += a.get("distance_m", 0) / 1000
        out["run_km"], out["hours"], out["sail_h"] = round(out["run_km"], 1), round(out["hours"], 1), round(out["sail_h"], 1)
        return out

    plan_view = []
    for w in plan:
        status = "past" if w["start"] < monday.isoformat() else "current" if w["start"] == monday.isoformat() else "future"
        item = {k: w[k] for k in ("start", "index", "phase", "phase_label", "deload", "race_week", "run_km", "hours", "scale", "reentry", "sailing_days", "sailing")}
        item["status"] = status
        item["key_sessions"] = [{"day": WEEKDAYS[date.fromisoformat(s["date"]).weekday()], "title": s["title"], "detail": s["detail"],
                                 "distance_km": s.get("distance_km"), "pace_label": s.get("pace_label")} for s in w["sessions"] if s.get("key")]
        if status != "future":
            item["actual"] = actual_week(w["start"])
        plan_view.append(item)

    # Weekly volume history incl. weeks before the plan started.
    history = []
    for i in range(11, -1, -1):
        start = (monday - timedelta(weeks=i)).isoformat()
        pw = next((w for w in plan if w["start"] == start), None)
        history.append({"start": start, **actual_week(start), "planned_km": pw["run_km"] if pw else None, "planned_h": pw["hours"] if pw else None})

    recent = sorted(acts.values(), key=lambda a: a["start"], reverse=True)[:30]
    activities = []
    for a in recent:
        e = evals.get(a["id"], {})
        activities.append({
            "id": a["id"], "name": a["name"], "sport": a["sport"], "sport_label": SPORT_LABELS.get(a["sport"], "Training"),
            "start": a["start"], "distance_km": round(a.get("distance_m", 0) / 1000, 2), "duration": fmt_duration(a.get("duration_s")),
            "pace": fmt_pace(a["duration_s"] / (a["distance_m"] / 1000)) if a["sport"] == "run" and a.get("distance_m") else None,
            "avg_hr": a.get("avg_hr"), "load": round(a["training_load"]) if a.get("training_load") else None,
            "te_aerobic": a.get("te_aerobic"), "te_anaerobic": a.get("te_anaerobic"), "hr_zones_s": a.get("hr_zones_s"),
            "score": e.get("score"), "rating": e.get("rating"), "headline": e.get("headline"), "lines": e.get("lines", []), "tips": e.get("tips", []),
        })

    goal_vdot = vdot_from_race(cfg.race_distance_m, cfg.target_seconds)
    paces = training_paces(state["vdot"])
    goal_paces = training_paces(goal_vdot)
    last = series[-1] if series else {"ctl": 0, "atl": 0, "tsb": 0}
    wellness = [dict(v, date=d) for d, v in sorted(state["wellness"].items())][-30:]
    today_ready = state["readiness"].get(today.isoformat())
    return {
        "generated_at": now.isoformat(timespec="minutes"),
        "athlete": cfg.name,
        "goal": {
            "race": cfg.race, "date": cfg.race_date.isoformat(), "days_to_go": (cfg.race_date - today).days,
            "target": fmt_duration(cfg.target_seconds), "target_pace": fmt_pace(cfg.target_seconds / (cfg.race_distance_m / 1000)),
            "predicted": fmt_duration(predicted_race_seconds(state["vdot"], cfg.race_distance_m)),
            "predicted_s": round(predicted_race_seconds(state["vdot"], cfg.race_distance_m)),
            "target_s": cfg.target_seconds, "vdot": state["vdot"], "goal_vdot": round(goal_vdot, 1),
            "plan_start": cfg.plan_start.isoformat(),
            "week_no": (week["index"] + 1) if week else None, "weeks_total": len(plan),
        },
        "today": {"date": today.isoformat(), "readiness": today_ready, "wellness": state["wellness"].get(today.isoformat(), {}),
                  "note": state["day_overrides"].get(today.isoformat(), {}).get("note"),
                  "sessions": [session_view(s) for s in effective_sessions(plan_days, state, today.isoformat())],
                  "next": next_session_text(state, plan_days, today)},
        "week": {"start": monday.isoformat(), "phase_label": week["phase_label"] if week else "Vorbereitung",
                 "deload": week["deload"] if week else False, "planned_km": week["run_km"] if week else 0,
                 "planned_h": week["hours"] if week else 0, "actual": actual_week(monday.isoformat()), "days": week_days},
        "plan": plan_view,
        "history": history,
        "pmc": series[-120:],
        "metrics": {**{k: v for k, v in state["metrics"].items() if k != "updated"}, "ctl": last["ctl"], "atl": last["atl"],
                    "tsb": last["tsb"], "acwr": acwr(series), "scale": state["current_scale"]},
        "paces": [{"key": k, "label": PACE_LABELS[k], "now": f"{fmt_pace(paces[k][0])}–{fmt_pace(paces[k][1])}",
                   "goal": f"{fmt_pace(goal_paces[k][0])}–{fmt_pace(goal_paces[k][1])}"} for k in ("easy", "marathon", "hm", "threshold", "interval")],
        "wellness": wellness,
        "sailing": [{"title": b["title"], "start": b["start"].isoformat(), "end": b["end"].isoformat(), "tentative": b["tentative"],
                     "regatta_from": b["regatta_from"].isoformat() if b["regatta_from"] else None} for b in cfg.sailing],
        "activities": activities,
        "log": list(reversed(state["log"]))[:25],
        "last_week": state.get("last_week_stats"),
    }


# --------------------------------------------------------------------------- entry points

def run_sync(cfg: Config, client, state: dict | None, now: datetime, push: bool = True) -> tuple[dict, dict]:
    today = now.date()
    state = state or new_state(cfg, now)
    sync_garmin(client, cfg, state, today)
    series = load_series(cfg, state, today)
    weekly_adaptation(cfg, state, today, now, series)
    plan = current_plan(cfg, state, today)
    plan_days = sessions_by_day(plan)
    daily_adaptation(cfg, state, today, now, plan_days, series)
    rate_activities(cfg, state, plan_days, series, now, push)
    if push:
        morning_brief(cfg, state, today, now, plan_days, series)
        weekly_review(cfg, state, today, now, plan_days)
    state["updated"] = now.isoformat()
    return state, build_view(cfg, state, today, now, plan, series)


def cmd_sync(args) -> int:
    cfg = load_config(args.config)
    passphrase = os.getenv("COACH_PASSPHRASE")
    if not passphrase and not args.allow_plain:
        print("COACH_PASSPHRASE is not set; refusing to write health data unencrypted (use --allow-plain for local tests).", file=sys.stderr)
        return 2
    state_dir, site_dir = Path(args.state_dir), Path(args.site_dir)
    now = datetime.now(ZoneInfo(cfg.timezone)).replace(tzinfo=None)

    tokens_blob = read_blob(state_dir / "tokens", passphrase)
    candidates = [t for t in ((tokens_blob or {}).get("tokens"), os.getenv("GARMIN_TOKENS")) if t]
    has_credentials = bool(os.getenv("GARMIN_EMAIL") and os.getenv("GARMIN_PASSWORD"))
    if not candidates and not has_credentials:
        print("No Garmin tokens: run the 'Garmin Login' workflow or set GARMIN_TOKENS.", file=sys.stderr)
        return 2
    client, last_err = None, None
    for tokens in candidates:
        try:
            client = garmin_source.login(tokens)
            break
        except Exception as err:
            last_err = err
    if client is None and has_credentials:
        # Tokens gone or expired: a plain password login works unless Garmin asks for MFA.
        from .login import login_with_credentials

        try:
            client = login_with_credentials(allow_mfa=False)
        except Exception as err:
            last_err = err
    if client is None:
        print(f"Garmin login failed: {last_err}", file=sys.stderr)
        marker = state_dir / "login_failed"
        if not marker.exists() or marker.read_text().strip() != now.date().isoformat():
            # Once a day is enough; the workflow retries every 30 minutes.
            notify.send("Garmin-Coach: Login fehlgeschlagen", "Die Garmin-Anmeldung ist abgelaufen. Auf GitHub unter **Actions → Garmin Login → Run workflow** neu anmelden.", tags=["warning"], priority=4)
            state_dir.mkdir(parents=True, exist_ok=True)
            marker.write_text(now.date().isoformat())
        return 1
    (state_dir / "login_failed").unlink(missing_ok=True)

    state = read_blob(state_dir / "state", passphrase)
    state, view = run_sync(cfg, client, state, now, push=not args.no_push)
    write_blob(state_dir / "state", state, passphrase)
    if passphrase:
        write_blob(state_dir / "tokens", {"tokens": garmin_source.dump_tokens(client), "saved": now.isoformat()}, passphrase)
    write_blob(site_dir / "data", view, passphrase)
    print(f"[coach] synced {len(state['activities'])} activities, plan week {view['goal']['week_no']}", file=sys.stderr)
    return 0


def cmd_demo(args) -> int:
    from .demo import FakeGarmin

    cfg = load_config(args.config)
    now = datetime.fromisoformat(args.now)
    client = FakeGarmin(cfg, now)
    state = None
    # Replay the days so adaptations and ratings build up like in production.
    start = now - timedelta(days=21)
    t = start.replace(hour=5, minute=30)
    while t <= now:
        client.now = t
        state, view = run_sync(cfg, client, state, t, push=False)
        t += timedelta(hours=6)
    state, view = run_sync(cfg, client, state, now, push=False)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(view, ensure_ascii=False, indent=1))
    print(f"demo dashboard data written to {out}")
    return 0


def cmd_login(args) -> int:
    """Log in with GARMIN_EMAIL/GARMIN_PASSWORD (MFA code via ntfy) and store the tokens."""
    from .login import login_with_credentials

    passphrase = os.getenv("COACH_PASSPHRASE")
    if not passphrase:
        print("COACH_PASSPHRASE is not set.", file=sys.stderr)
        return 2
    try:
        client = login_with_credentials(allow_mfa=True)
    except Exception as err:
        print(f"Garmin login failed: {err}", file=sys.stderr)
        notify.send("Garmin-Login fehlgeschlagen", f"{str(err)[:300]}\nBitte E-Mail/Passwort in den GitHub-Secrets prüfen und erneut starten.", tags=["warning"], priority=4)
        return 1
    state_dir = Path(args.state_dir)
    write_blob(state_dir / "tokens", {"tokens": garmin_source.dump_tokens(client), "saved": datetime.now().isoformat()}, passphrase)
    (state_dir / "login_failed").unlink(missing_ok=True)
    notify.send("✅ Garmin verbunden", "Die Anmeldung hat geklappt. Der Coach gleicht ab jetzt alle 30 Minuten ab.", tags=["white_check_mark"])
    print("[coach] Garmin login ok, tokens stored", file=sys.stderr)
    return 0


def cmd_encrypt_tokens(args) -> int:
    """Print base64 of the local Garmin token file, ready for the GARMIN_TOKENS secret."""
    import base64

    path = Path(os.path.expanduser(args.path))
    if path.is_dir():
        path = path / "garmin_tokens.json"
    print(base64.b64encode(path.read_bytes()).decode())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="garmin-coach", description="Adaptiver Trainingscoach für Garmin")
    parser.add_argument("--config", default=None, help="Pfad zur athlete.json")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sync", help="Garmin abgleichen, Plan anpassen, Pushes senden, Dashboard-Daten schreiben")
    p.add_argument("--state-dir", default=".coach-data")
    p.add_argument("--site-dir", default="_site")
    p.add_argument("--no-push", action="store_true")
    p.add_argument("--allow-plain", action="store_true", help="ohne COACH_PASSPHRASE unverschlüsselt schreiben (nur lokal)")
    p.set_defaults(fn=cmd_sync)
    p = sub.add_parser("login", help="Mit GARMIN_EMAIL/GARMIN_PASSWORD anmelden (Code per ntfy) und Tokens speichern")
    p.add_argument("--state-dir", default=".coach-data")
    p.set_defaults(fn=cmd_login)
    p = sub.add_parser("demo", help="Demo-Daten für das Dashboard erzeugen (ohne Garmin)")
    p.add_argument("--now", default="2026-10-22T19:30:00")
    p.add_argument("--out", default="dashboard/demo-data.json")
    p.set_defaults(fn=cmd_demo)
    p = sub.add_parser("export-tokens", help="Garmin-Tokens als base64 für das GitHub-Secret ausgeben")
    p.add_argument("path", nargs="?", default="~/.garminconnect")
    p.set_defaults(fn=cmd_encrypt_tokens)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
