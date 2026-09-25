"""Deterministic fake Garmin client for the dashboard demo and tests.

Produces raw payloads shaped like the real Garmin endpoints, following the
plan with realistic noise (skipped sessions, fast easy runs, a bad-sleep dip)
so every dashboard state and adaptation path shows up.
"""

import random
from datetime import date, datetime, timedelta

from .config import Config
from .plan import build_plan

TYPE_KEYS = {"run": "running", "bike": "road_biking", "strength": "strength_training", "row": "indoor_rowing", "sail": "sailing_v2"}

# Zone split (share of time Z1..Z5) and aerobic/anaerobic TE per profile.
ZONES = {
    "easy": ([0.35, 0.5, 0.12, 0.03, 0], (2.6, 0.3)),
    "recovery": ([0.6, 0.38, 0.02, 0, 0], (1.8, 0)),
    "long": ([0.2, 0.55, 0.18, 0.07, 0], (3.4, 0.4)),
    "hills": ([0.3, 0.4, 0.15, 0.1, 0.05], (3.0, 1.8)),
    "steady": ([0.15, 0.35, 0.35, 0.15, 0], (3.3, 0.8)),
    "threshold": ([0.15, 0.25, 0.2, 0.38, 0.02], (3.7, 1.2)),
    "vo2": ([0.2, 0.25, 0.15, 0.25, 0.15], (4.0, 2.6)),
    "hm": ([0.15, 0.2, 0.2, 0.43, 0.02], (3.9, 1.0)),
    "z2": ([0.3, 0.6, 0.1, 0, 0], (2.4, 0.1)),
    "strength": ([0.6, 0.3, 0.1, 0, 0], (1.6, 0.8)),
    "sail": ([0.55, 0.35, 0.1, 0, 0], (2.0, 0.6)),
}


class FakeGarmin:
    def __init__(self, cfg: Config, now: datetime, seed: int = 7):
        self.cfg, self.now = cfg, now
        rng = random.Random(seed)
        self._acts: list[dict] = []
        self._well: dict[str, dict] = {}
        plan_days: dict[str, list[dict]] = {}
        for week in build_plan(cfg, cfg.start_vdot):
            for s in week["sessions"]:
                plan_days.setdefault(s["date"], []).append(s)

        start = now.date() - timedelta(days=130)
        aid = 20_000_000_000
        d = start
        while d <= now.date():
            iso = d.isoformat()
            poor_night = (d - start).days % 17 == 11
            ready = rng.randint(28, 38) if poor_night else rng.randint(52, 88)
            self._well[iso] = {
                "hrv": rng.randint(38, 46) if poor_night else rng.randint(52, 68),
                "status": "UNBALANCED" if poor_night else "BALANCED",
                "sleep": rng.randint(45, 58) if poor_night else rng.randint(68, 90),
                "ready": ready,
                "rhr": rng.randint(52, 56) if poor_night else rng.randint(45, 50),
                "bb": rng.randint(40, 60) if poor_night else rng.randint(70, 98),
            }
            if d < cfg.plan_start:
                sessions = self._pre_plan(d)
            else:
                sessions = plan_days.get(iso, [])
            for s in sessions:
                skip = 0.7 if s.get("optional") else 0.12 if not s.get("key") else 0.06
                if s["sport"] != "sail" and rng.random() < skip:
                    continue  # skipped
                aid += rng.randint(1000, 9000)
                self._acts.append(self._activity(aid, d, s, rng))
            d += timedelta(days=1)

    @staticmethod
    def _pre_plan(d: date) -> list[dict]:
        wd = d.weekday()
        mk = lambda sport, profile, minutes, km=None: {"sport": sport, "profile": profile, "duration_min": minutes, "distance_km": km, "title": ""}
        return {
            1: [mk("run", "easy", 48, 9)],
            2: [mk("strength", "strength", 60)],
            3: [mk("run", "threshold", 50, 10), mk("bike", "z2", 80)],
            4: [mk("strength", "strength", 55)],
            5: [mk("bike", "z2", 120)],
            6: [mk("run", "long", 85, 16)],
        }.get(wd, [])

    def _activity(self, aid: int, d: date, s: dict, rng: random.Random) -> dict:
        sport = s["sport"]
        noise = rng.uniform(0.85, 1.08)
        minutes = s["duration_min"] * noise
        hour = 7 if sport == "strength" or s.get("title") == "Morgenlauf" else 10 if sport == "sail" else 18
        profile = s["profile"] if s["profile"] in ZONES else "easy"
        zones, (te_a, te_an) = ZONES[profile]
        if profile == "easy" and rng.random() < 0.3:
            zones = [0.15, 0.4, 0.3, 0.15, 0]  # ran the easy day too hard
            te_a += 0.6
        secs = minutes * 60
        act = {
            "activityId": aid,
            "activityName": {"run": "Lauf", "bike": "Radfahrt", "strength": "Krafttraining", "row": "Rudern", "sail": "Segeln"}[sport],
            "startTimeLocal": f"{d.isoformat()} {hour:02d}:{rng.randint(0, 59):02d}:00",
            "activityType": {"typeKey": TYPE_KEYS[sport]},
            "duration": secs * 1.03,
            "movingDuration": secs,
            "averageHR": round(118 + 45 * (zones[2] * 0.5 + zones[3] + zones[4] * 1.3) + rng.uniform(-3, 3)),
            "maxHR": 185,
            "aerobicTrainingEffect": round(min(5, te_a + rng.uniform(-0.3, 0.3)), 1),
            "anaerobicTrainingEffect": round(max(0, te_an + rng.uniform(-0.3, 0.3)), 1),
            "activityTrainingLoad": round(minutes * (1.1 + 1.6 * (zones[3] + zones[4]) + 0.5 * zones[2]) * rng.uniform(0.9, 1.1)),
            "calories": round(minutes * 11),
        }
        for i, z in enumerate(zones, start=1):
            act[f"hrTimeInZone_{i}"] = round(secs * z)
        if sport == "run" and s.get("distance_km"):
            act["distance"] = s["distance_km"] * 1000 * noise * rng.uniform(0.97, 1.03)
            act["averageSpeed"] = act["distance"] / secs
        elif sport == "bike":
            act["averageSpeed"] = rng.uniform(7.6, 8.9)
            act["distance"] = act["averageSpeed"] * secs
            act["avgPower"] = rng.randint(135, 165)
        if sport in ("strength", "row"):
            act["activityTrainingLoad"] = round(act["activityTrainingLoad"] * 0.5)
        elif sport == "sail":
            act["activityTrainingLoad"] = round(act["activityTrainingLoad"] * 0.3)
        return act

    # --- Garmin API surface used by garmin_source ---------------------------------
    def get_activities_by_date(self, start: str, end: str | None = None):
        cutoff = self.now.strftime("%Y-%m-%d %H:%M:%S")
        return [a for a in self._acts if start <= a["startTimeLocal"][:10] <= (end or "9999") and a["startTimeLocal"] <= cutoff]

    def _w(self, iso: str) -> dict:
        return self._well.get(iso, {}) if iso <= self.now.date().isoformat() else {}

    def get_hrv_data(self, iso: str):
        w = self._w(iso)
        return {"hrvSummary": {"lastNightAvg": w["hrv"], "weeklyAvg": 58, "status": w["status"],
                               "baseline": {"balancedLow": 52, "balancedUpper": 66}}} if w else None

    def get_sleep_data(self, iso: str):
        w = self._w(iso)
        return {"dailySleepDTO": {"sleepTimeSeconds": 60 * (330 + w["sleep"] * 2), "sleepScores": {"overall": {"value": w["sleep"]}}}} if w else {}

    def get_training_readiness(self, iso: str):
        w = self._w(iso)
        return [{"score": w["ready"], "level": "HIGH" if w["ready"] > 75 else "MODERATE" if w["ready"] > 50 else "LOW", "timestamp": f"{iso}T06:00:00"}] if w else []

    def get_user_summary(self, iso: str):
        w = self._w(iso)
        return {"restingHeartRate": w["rhr"], "bodyBatteryHighestValue": w["bb"]} if w else {}

    def get_lactate_threshold(self, latest: bool = True, **_):
        return {"speed_and_heart_rate": {"speed": 1 / 4.02, "heartRate": 176}}

    def get_max_metrics(self, iso: str):
        return [{"generic": {"vo2MaxPreciseValue": 52.4}}]
