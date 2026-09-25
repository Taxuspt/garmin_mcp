"""Tests for the adaptive training coach (plan, adaptation, rating, crypto, pipeline)."""

import json
from datetime import date, datetime, timedelta

import pytest

from garmin_mcp.coach import adapt, crypto
from garmin_mcp.coach.config import load_config, sailing_day
from garmin_mcp.coach.demo import FakeGarmin
from garmin_mcp.coach.evaluate import evaluate, match_session
from garmin_mcp.coach.plan import build_plan, monday_of
from garmin_mcp.coach.sync import run_sync, sessions_by_day
from garmin_mcp.coach.vdot import HM_METERS, race_seconds, training_paces, vdot_from_race


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def plan(cfg):
    return build_plan(cfg, cfg.start_vdot)


def test_vdot_roundtrip_and_pace_order():
    v = vdot_from_race(HM_METERS, 5099)
    assert abs(race_seconds(v, HM_METERS) - 5099) < 2
    p = training_paces(v)
    # faster intensities have faster (smaller) paces
    assert p["interval"][0] < p["threshold"][0] < p["hm"][0] < p["marathon"][0] < p["easy"][0]


def test_plan_ends_with_race_on_race_day(cfg, plan):
    last = plan[-1]
    assert last["race_week"]
    race = [s for s in last["sessions"] if s["profile"] == "race"]
    assert race and race[0]["date"] == cfg.race_date.isoformat()
    assert plan[0]["start"] == monday_of(cfg.plan_start).isoformat()


def test_plan_volume_peaks_before_taper(plan):
    no_sail = [w for w in plan if not w["sailing_days"] and not w["reentry"] and not w["deload"]]
    peak = max(no_sail, key=lambda w: w["run_km"])
    assert peak["phase"] in ("build", "specific")
    assert all(w["run_km"] < peak["run_km"] for w in plan if w["phase"] == "taper")


def test_monday_is_rest_day_outside_sailing(cfg, plan):
    for w in plan:
        for s in w["sessions"]:
            d = date.fromisoformat(s["date"])
            if d.weekday() == 0 and not sailing_day(cfg, d) and not s.get("adjusted"):
                pytest.fail(f"unexpected Monday session {s['id']}")


def test_sailing_days_have_priority(cfg, plan):
    sail_days = 0
    for w in plan:
        by_day = {}
        for s in w["sessions"]:
            by_day.setdefault(s["date"], []).append(s)
        for iso, sessions in by_day.items():
            if sailing_day(cfg, date.fromisoformat(iso)):
                sail_days += 1
                assert any(s["sport"] == "sail" for s in sessions)
                assert not any(s.get("key") for s in sessions), iso
                assert not any(s["sport"] in ("strength", "bike", "row") for s in sessions), iso
    assert sail_days > 50


def test_key_runs_keep_a_gap_after_moving(plan):
    for w in plan:
        key_days = sorted(date.fromisoformat(s["date"]) for s in w["sessions"] if s.get("key") and s["sport"] == "run")
        for a, b in zip(key_days, key_days[1:]):
            assert (b - a).days >= 2 or w["race_week"], (w["start"], key_days)


def test_red_readiness_moves_or_drops_key_session(cfg, plan):
    days = sessions_by_day(plan)
    tue = next(date.fromisoformat(s["date"]) for w in plan if w["phase"] == "build" and not w["sailing_days"] and not w["deload"]
               for s in w["sessions"] if s.get("key") and date.fromisoformat(s["date"]).weekday() == 1)
    status = {"score": 25, "level": "red", "reasons": []}
    out = adapt.adapt_day(tue, days, status)
    today = out[tue.isoformat()]["sessions"]
    assert all(not s.get("key") for s in today if s["sport"] == "run")
    assert any(s["profile"] == "recovery" for s in today)


def test_readiness_never_touches_sailing(cfg, plan):
    days = sessions_by_day(plan)
    sail = next(d for d, ss in days.items() if any(s["sport"] == "sail" for s in ss) and len(ss) == 1)
    assert adapt.adapt_day(date.fromisoformat(sail), days, {"score": 10, "level": "red", "reasons": []}) == {}


def test_weekly_scale_rules():
    assert adapt.weekly_scale(1.0, 1.0, 1.0, 70, 1.1)[0] > 1.0
    assert adapt.weekly_scale(1.0, 1.0, 1.0, 70, 1.7)[0] < 1.0
    assert adapt.weekly_scale(1.0, 0.3, 0.5, 70, 1.0)[0] < 1.0
    assert adapt.weekly_scale(0.8, 0.95, 1.0, 70, 1.0)[0] > 0.8  # climbs back after a cut
    assert adapt.weekly_scale(0.76, 0.1, 0.1, 20, 2.0)[0] >= adapt.SCALE_MIN


def test_easy_run_too_fast_is_penalised(plan):
    easy = next(s for w in plan for s in w["sessions"] if s["sport"] == "run" and s["profile"] == "easy" and s.get("distance_km", 0) > 7)
    base = {"id": "1", "sport": "run", "date": easy["date"], "distance_m": easy["distance_km"] * 1000}
    slow = dict(base, duration_s=easy["distance_km"] * (easy["pace_range"][0] + 20), hr_zones_s=[600, 2000, 100, 0, 0], te_aerobic=2.5)
    fast = dict(base, duration_s=easy["distance_km"] * (easy["pace_range"][0] - 40), hr_zones_s=[100, 700, 1200, 600, 0], te_aerobic=3.9)
    good, bad = evaluate(slow, easy), evaluate(fast, easy)
    assert good["score"] >= 85
    assert bad["score"] < good["score"] - 20
    assert any("Z1–2" in t for t in bad["tips"])


def test_match_prefers_same_sport_and_skips_taken(plan):
    day = next(ss for ss in sessions_by_day(plan).values() if {"strength", "run"} <= {s["sport"] for s in ss})
    run = next(s for s in day if s["sport"] == "run")
    act = {"sport": "run", "duration_s": run["duration_min"] * 60}
    assert match_session(act, day, set())["id"] == run["id"]
    assert match_session(act, day, {run["id"]}) is None


def test_crypto_roundtrip():
    env = crypto.encrypt_json({"ä": [1, 2]}, "geheim")
    assert crypto.decrypt_json(env, "geheim") == {"ä": [1, 2]}
    with pytest.raises(Exception):
        crypto.decrypt_json(env, "falsch")
    assert set(json.loads(env)) >= {"salt", "iv", "ct", "iter"}


def test_full_pipeline_with_fake_garmin(cfg, monkeypatch):
    sent = []
    monkeypatch.setattr("garmin_mcp.coach.notify.send", lambda title, msg, **kw: sent.append(title) or True)
    now = datetime(2026, 11, 3, 5, 45)
    client = FakeGarmin(cfg, now)
    state, _ = run_sync(cfg, client, None, now - timedelta(days=1), push=True)
    assert state["activities"] and state["evaluations"]
    assert not any(t.startswith(("Lauf", "Kraft", "Rad")) for t in sent)  # history is never pushed
    client.now = now + timedelta(hours=14)
    state, view = run_sync(cfg, client, state, client.now, push=True)
    assert state["sent"].get("morning") == client.now.date().isoformat()
    assert view["goal"]["days_to_go"] == (cfg.race_date - client.now.date()).days
    assert len(view["plan"]) == len(build_plan(cfg, cfg.start_vdot))
    assert view["pmc"] and view["week"]["days"]
    json.dumps(view)  # dashboard payload must be serialisable
