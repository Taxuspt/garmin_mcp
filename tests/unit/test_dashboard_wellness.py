"""Dashboard: Garmin retry/backoff, per-day wellness cache and readiness verdict."""
import datetime
import json
import sys
from pathlib import Path

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dashboard import app as dash  # noqa: E402

pytestmark = pytest.mark.unit

TODAY = datetime.date.today()


def day(offset: int) -> str:
    return (TODAY - datetime.timedelta(days=offset)).isoformat()


class FakeGarmin:
    """Minimal stand-in for garminconnect.Garmin's wellness endpoints."""

    def __init__(self, data=None, failing=(), training_readiness=None):
        self.data = data or {}  # date -> {"rhr", "bb", "stress", "sleep_s", "score", "hrv", "hrv_status"}
        self.failing = set(failing)  # (method, date) pairs that raise
        self.training_readiness = training_readiness if training_readiness is not None else []
        self.calls = []

    def _maybe_fail(self, method, d):
        self.calls.append((method, d))
        if (method, d) in self.failing:
            raise GarminConnectConnectionError("API client error (404): nope")

    def get_stats(self, d):
        self._maybe_fail("stats", d)
        v = self.data.get(d, {})
        return {
            "restingHeartRate": v.get("rhr"),
            "bodyBatteryMostRecentValue": v.get("bb"),
            "bodyBatteryAtWakeTime": v.get("bb_wake"),
            "bodyBatteryChargedValue": v.get("bb_charged"),
            "averageStressLevel": v.get("stress"),
        }

    def get_sleep_data(self, d):
        self._maybe_fail("sleep", d)
        v = self.data.get(d, {})
        if v.get("sleep_s") is None:
            # Garmin still sends a DTO with stray sub-metrics on nights it didn't record
            return {"dailySleepDTO": {"averageRespirationValue": 14.0}}
        return {"dailySleepDTO": {
            "sleepTimeSeconds": v["sleep_s"],
            "sleepScores": {"overall": {"value": v.get("score")}},
            "deepSleepSeconds": v.get("deep_s"),
            "lightSleepSeconds": v.get("light_s"),
            "remSleepSeconds": v.get("rem_s"),
            "awakeSleepSeconds": v.get("awake_s"),
            "averageRespirationValue": v.get("resp"),
            "averageSpO2Value": v.get("spo2"),
            "lowestSpO2Value": v.get("spo2_low"),
            "avgSleepStress": v.get("sleep_stress"),
        }}

    def get_hrv_data(self, d):
        self._maybe_fail("hrv", d)
        v = self.data.get(d, {})
        if v.get("hrv") is None:
            return None
        return {"hrvSummary": {"lastNightAvg": v["hrv"], "status": v.get("hrv_status", "BALANCED")}}

    def get_training_readiness(self, d):
        if isinstance(self.training_readiness, Exception):
            raise self.training_readiness
        return self.training_readiness


def full_day(rhr=50, sleep_h=7.5, hrv=60, stress=25):
    return {"rhr": rhr, "bb": 70, "bb_wake": 80, "bb_charged": 45, "stress": stress,
            "sleep_s": int(sleep_h * 3600), "score": 80, "hrv": hrv}


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(dash, "WELLNESS_CACHE_PATH", tmp_path / "wellness_cache.json")
    monkeypatch.setattr(dash, "COACH_DATA_DIR", tmp_path)
    monkeypatch.setattr(dash, "_wellness_cache", {})
    monkeypatch.setattr(dash, "_recent_wellness_cache", {})
    monkeypatch.setattr(dash, "_wellness_day_locks", {})
    monkeypatch.setattr(dash.time, "sleep", lambda s: None)
    monkeypatch.setattr(dash, "_plan_for_date", lambda d: None)
    monkeypatch.setattr(dash, "_next_plan_after", lambda d: None)
    return tmp_path


# --------------------------------------------------------------------------- retry

class Flaky:
    def __init__(self, errors, result="ok"):
        self.errors = list(errors)
        self.result = result
        self.calls = 0

    def __call__(self, *args):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.result


def test_retry_recovers_from_rate_limit():
    fn = Flaky([GarminConnectTooManyRequestsError("429")])
    assert dash._call_with_retry(fn) == "ok"
    assert fn.calls == 2


def test_retry_recovers_from_server_error():
    fn = Flaky([GarminConnectConnectionError("HTTP error: 503"), GarminConnectConnectionError("HTTP error: 502")])
    assert dash._call_with_retry(fn) == "ok"
    assert fn.calls == 3


def test_retry_gives_up_after_max_attempts():
    fn = Flaky([GarminConnectTooManyRequestsError("429")] * 5)
    with pytest.raises(GarminConnectTooManyRequestsError):
        dash._call_with_retry(fn)
    assert fn.calls == dash.RETRY_MAX_ATTEMPTS


@pytest.mark.parametrize("exc", [
    GarminConnectConnectionError("API client error (404): not found"),
    GarminConnectAuthenticationError("401"),
    ValueError("bug"),
])
def test_no_retry_on_client_auth_or_programming_errors(exc):
    fn = Flaky([exc])
    with pytest.raises(type(exc)):
        dash._call_with_retry(fn)
    assert fn.calls == 1


def test_backoff_honors_retry_after_header_and_caps_it():
    class Resp:
        headers = {"Retry-After": "3"}

    class HTTPErr(Exception):
        response = Resp()

    exc = GarminConnectTooManyRequestsError("429")
    exc.__cause__ = HTTPErr()
    assert dash._backoff_delay(1, exc) == 3.0

    Resp.headers = {"Retry-After": "999"}
    assert dash._backoff_delay(1, exc) == dash.RETRY_MAX_DELAY_S


def test_backoff_grows_exponentially_within_jitter():
    exc = GarminConnectTooManyRequestsError("429")
    for attempt, base in [(1, 0.5), (2, 1.0), (3, 2.0)]:
        delay = dash._backoff_delay(attempt, exc)
        assert base * 0.8 <= delay <= base * 1.2


def test_proxy_wraps_client_methods():
    class Client:
        n = 0

        def get_stats(self, d):
            Client.n += 1
            if Client.n == 1:
                raise GarminConnectTooManyRequestsError("429")
            return {"d": d}

    proxy = dash._RetryingGarmin(Client())
    assert proxy.get_stats("2026-01-01") == {"d": "2026-01-01"}
    assert Client.n == 2


# --------------------------------------------------------------------------- cache

def test_settled_past_day_is_cached_in_memory_and_on_disk(isolated_cache):
    client = FakeGarmin({day(3): full_day()})
    first = dash._wellness_days(client, TODAY - datetime.timedelta(days=3), TODAY - datetime.timedelta(days=3))
    assert first[0]["sleep_hours"] == 7.5 and first[0]["sleep_score"] == 80
    assert len(client.calls) == 3

    dash._wellness_days(client, TODAY - datetime.timedelta(days=3), TODAY - datetime.timedelta(days=3))
    assert len(client.calls) == 3  # served from cache

    on_disk = json.loads((isolated_cache / "wellness_cache.json").read_text(encoding="utf-8"))
    assert on_disk[day(3)]["hrv_avg"] == 60


def test_cache_survives_restart(isolated_cache):
    client = FakeGarmin({day(5): full_day()})
    dash._wellness_day(client, day(5))
    dash._save_wellness_cache_to_disk()
    dash._wellness_cache.clear()

    dash._load_wellness_cache_from_disk()
    client2 = FakeGarmin()
    assert dash._wellness_day(client2, day(5))["resting_hr"] == 50
    assert client2.calls == []


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(dash, "_monotonic", lambda: now[0])
    return now


@pytest.mark.parametrize("offset", [0, 1])
def test_today_and_yesterday_are_never_persisted(offset):
    client = FakeGarmin({day(offset): full_day()})
    dash._wellness_day(client, day(offset))
    assert day(offset) not in dash._wellness_cache


@pytest.mark.parametrize("offset", [0, 1])
def test_recent_day_served_from_short_cache_then_refetched(offset, clock):
    client = FakeGarmin({day(offset): full_day()})
    dash._wellness_day(client, day(offset))
    dash._wellness_day(client, day(offset))
    assert len(client.calls) == 3  # second call within TTL: no Garmin request

    clock[0] += dash.WELLNESS_RECENT_TTL_S + 1
    dash._wellness_day(client, day(offset))
    assert len(client.calls) == 6  # TTL expired: picks up a fresh watch sync


def test_failed_recent_fetch_is_retried_immediately(clock):
    client = FakeGarmin({day(0): full_day()}, failing={("sleep", day(0))})
    dash._wellness_day(client, day(0))
    client.failing.clear()
    assert dash._wellness_day(client, day(0))["sleep_hours"] == 7.5
    assert len(client.calls) == 6


def test_concurrent_requests_for_same_day_share_one_fetch():
    import threading

    release = threading.Event()

    class SlowGarmin(FakeGarmin):
        def get_stats(self, d):
            release.wait(timeout=5)
            return super().get_stats(d)

    client = SlowGarmin({day(0): full_day()})
    results = []
    threads = [threading.Thread(target=lambda: results.append(dash._wellness_day(client, day(0))))
               for _ in range(3)]
    for t in threads:
        t.start()
    release.set()
    for t in threads:
        t.join(timeout=5)
    assert len(results) == 3
    assert len(client.calls) == 3  # one stats + sleep + hrv fetch, not three of each


def test_recent_day_without_sleep_is_not_cached_yet():
    client = FakeGarmin({day(3): {"rhr": 50}})  # watch not synced overnight data yet
    dash._wellness_day(client, day(3))
    assert day(3) not in dash._wellness_cache


def test_old_empty_day_is_cached():
    client = FakeGarmin({})  # e.g. watch not worn that day
    dash._wellness_day(client, day(dash.WELLNESS_SETTLED_DAYS))
    assert day(dash.WELLNESS_SETTLED_DAYS) in dash._wellness_cache


def test_failed_fetch_is_never_cached():
    client = FakeGarmin({day(10): full_day()}, failing={("hrv", day(10))})
    result = dash._wellness_day(client, day(10))
    assert result["errors"] == ["hrv"]
    assert result["sleep_hours"] == 7.5  # other sources still returned
    assert day(10) not in dash._wellness_cache


def test_old_entries_are_pruned_on_save(isolated_cache):
    ancient = (TODAY - datetime.timedelta(days=dash.WELLNESS_CACHE_MAX_AGE_DAYS + 1)).isoformat()
    dash._wellness_cache[ancient] = {"date": ancient}
    dash._wellness_cache[day(10)] = {"date": day(10)}
    dash._save_wellness_cache_to_disk()
    on_disk = json.loads((isolated_cache / "wellness_cache.json").read_text(encoding="utf-8"))
    assert list(on_disk) == [day(10)]


# ----------------------------------------------------------------------- readiness

def readiness_with(client, monkeypatch):
    monkeypatch.setattr(dash, "get_client", lambda: client)
    return dash.api_readiness()


def history(**today_overrides):
    data = {day(i): full_day() for i in range(1, 9)}
    data[day(0)] = {**full_day(), **today_overrides}
    return data


def test_readiness_is_pending_not_apto_when_sleep_missing(monkeypatch):
    r = readiness_with(FakeGarmin(history(sleep_s=None, hrv=None)), monkeypatch)
    assert r["verdict"] == "pendente"
    assert r["confidence"] == "parcial"
    assert any("ainda nao sincronizado" in x for x in r["reasons"])


def test_readiness_apto_with_high_confidence_when_all_data_present(monkeypatch):
    r = readiness_with(FakeGarmin(history()), monkeypatch)
    assert r["verdict"] == "apto"
    assert r["confidence"] == "alta"


def test_readiness_keeps_warning_flags_even_without_sleep(monkeypatch):
    r = readiness_with(FakeGarmin(history(sleep_s=None, rhr=60)), monkeypatch)  # RHR +10 vs baseline
    assert r["verdict"] == "cautela"
    assert r["confidence"] == "parcial"


def test_readiness_reports_sleep_fetch_failure_distinctly(monkeypatch):
    client = FakeGarmin(history(), failing={("sleep", day(0))})
    r = readiness_with(client, monkeypatch)
    assert r["verdict"] == "pendente"
    assert any("Falha ao obter o sono" in x for x in r["reasons"])


def test_endpoints_share_the_cache(monkeypatch):
    client = FakeGarmin(history())
    monkeypatch.setattr(dash, "get_client", lambda: client)
    dash.api_readiness()          # 9 days
    first = len(client.calls)
    dash.api_overtraining_risk(days=21)
    dash.api_recovery(days=14)
    # Only the 12 older overtraining days not covered by readiness's window are
    # new; today/yesterday come from the short-lived recent cache.
    assert len(client.calls) - first == 3 * 12


# ------------------------------------------------- stress + Garmin Training Readiness

def test_negative_stress_means_no_data():
    client = FakeGarmin({day(10): {**full_day(), "stress": -1}})
    assert dash._wellness_day(client, day(10))["avg_stress"] is None


def test_cache_entries_from_older_version_are_dropped(isolated_cache):
    (isolated_cache / "wellness_cache.json").write_text(json.dumps({
        day(10): {"date": day(10), "resting_hr": 50},  # v1: no version, no avg_stress
        day(11): {"date": day(11), "resting_hr": 51, "avg_stress": 20, "v": dash.WELLNESS_CACHE_VERSION},
    }), encoding="utf-8")
    dash._load_wellness_cache_from_disk()
    assert list(dash._wellness_cache) == [day(11)]
    assert "v" not in dash._wellness_cache[day(11)]


def test_short_sleep_after_stressful_day_adds_a_second_flag(monkeypatch):
    data = history(sleep_s=int(5.5 * 3600))
    data[day(1)]["stress"] = 50
    r = readiness_with(FakeGarmin(data), monkeypatch)
    assert r["flags"] == 2 and r["verdict"] == "nao_apto"
    assert r["yesterday_stress"] == 50
    assert any("stress elevado" in x for x in r["reasons"])


def test_short_sleep_after_calm_day_is_a_single_flag(monkeypatch):
    r = readiness_with(FakeGarmin(history(sleep_s=int(5.5 * 3600))), monkeypatch)
    assert r["flags"] == 1 and r["verdict"] == "cautela"


def test_low_garmin_training_readiness_is_a_flag(monkeypatch):
    tr = [{"score": 55, "level": "MODERATE", "timestamp": "2026-01-01T06:00:00"},
          {"score": 32, "level": "LOW", "timestamp": "2026-01-01T09:00:00"}]
    r = readiness_with(FakeGarmin(history(), training_readiness=tr), monkeypatch)
    assert r["training_readiness"] == {"score": 32, "level": "LOW"}  # latest entry wins
    assert r["verdict"] == "cautela"


def test_good_garmin_training_readiness_is_informational(monkeypatch):
    tr = [{"score": 78, "level": "HIGH", "timestamp": "2026-01-01T06:00:00"}]
    r = readiness_with(FakeGarmin(history(), training_readiness=tr), monkeypatch)
    assert r["verdict"] == "apto"
    assert any("78/100" in x for x in r["reasons"])


@pytest.mark.parametrize("tr", [[], GarminConnectConnectionError("API client error (404)")])
def test_missing_training_readiness_is_ignored(monkeypatch, tr):
    # e.g. Forerunner 255: Garmin returns [] -- the verdict relies on our signals only
    r = readiness_with(FakeGarmin(history(), training_readiness=tr), monkeypatch)
    assert r["training_readiness"] is None
    assert r["verdict"] == "apto"
    assert not any("Training Readiness" in x for x in r["reasons"])


# ------------------------------------------------------ weekly recovery comparison

def week(sleep_h=7.5, hrv=60, rhr=50, stress=25, bb=40, deep=90, rem=80, n=7):
    return [
        {"sleep_hours": sleep_h, "hrv_avg": hrv, "resting_hr": rhr, "avg_stress": stress, "body_battery": bb,
         "sleep_deep_min": deep, "sleep_rem_min": rem}
        for _ in range(n)
    ]


def test_weekly_recovery_stable():
    rec = dash._weekly_recovery_comparison(week(), week())
    assert rec["bottlenecks"] == ["Recuperacao estavel vs semana anterior"]
    assert rec["confidence"] == "alta"
    assert rec["delta"] == {k: 0 for k in dash.RECOVERY_METRICS}
    assert isinstance(rec["current"]["hrv_avg"], int)  # renders "60ms", not "60.0ms"
    assert isinstance(rec["delta"]["resting_hr"], int)


def test_weekly_recovery_flags_declines():
    rec = dash._weekly_recovery_comparison(
        week(sleep_h=6.0, hrv=50, rhr=54, stress=48),
        week(sleep_h=7.5, hrv=60, rhr=50, stress=30),
    )
    text = " | ".join(rec["bottlenecks"])
    assert "abaixo de 6.5h" in text
    assert "Sono caiu 20%" in text
    assert "HRV media caiu 17%" in text
    assert "FC repouso media subiu 4bpm" in text
    assert "Stress medio elevado" in text
    assert "Stress subiu 60%" in text
    assert rec["delta"]["sleep_hours"] == -1.5 and rec["delta"]["resting_hr"] == 4


def test_weekly_recovery_calls_out_sparse_data():
    sparse = week(n=2) + [{} for _ in range(5)]
    rec = dash._weekly_recovery_comparison(sparse, [{} for _ in range(7)])
    assert rec["confidence"] == "baixa"
    assert rec["delta"]["sleep_hours"] is None
    assert any("HRV so em 2 de 7" in b for b in rec["bottlenecks"])


def test_weekly_closing_includes_recovery(monkeypatch):
    monday = TODAY - datetime.timedelta(days=TODAY.weekday())
    data = {}
    for i in range(1, 15):  # the two full weeks before this one
        d = (monday - datetime.timedelta(days=i)).isoformat()
        data[d] = full_day(sleep_h=7.0 if i <= 7 else 8.0)
    client = FakeGarmin(data)
    client.get_activities_by_date = lambda start, end: []
    monkeypatch.setattr(dash, "get_client", lambda: client)
    monkeypatch.setattr(dash, "_week_target_from_mesocycle", lambda d: None)
    rec = dash.api_weekly_closing()["recovery"]
    assert rec["current"]["sleep_hours"] == 7.0
    assert rec["previous"]["sleep_hours"] == 8.0
    assert rec["delta"]["sleep_hours"] == -1.0
    assert any("Sono caiu 12%" in b for b in rec["bottlenecks"])


# ------------------------------------------------------- Body Battery at wake time

def test_evening_drained_body_battery_does_not_flip_the_verdict(monkeypatch):
    # Woke up at 82; by the evening the live value has drained to 15.
    r = readiness_with(FakeGarmin(history(bb=15, bb_wake=82, bb_charged=44)), monkeypatch)
    assert r["verdict"] == "apto"
    assert r["reasons"] == [
        "Sinais de recuperacao dentro do normal",
        "Body Battery ao acordar: 82/100 (+44 recarregado durante a noite)",
    ]


def test_low_body_battery_at_wake_is_a_flag(monkeypatch):
    r = readiness_with(FakeGarmin(history(bb=70, bb_wake=20, bb_charged=5)), monkeypatch)
    assert r["verdict"] == "cautela"
    assert "Body Battery baixo ao acordar: 20/100 (+5 recarregado durante a noite)" in r["reasons"]


def test_missing_wake_body_battery_is_ignored(monkeypatch):
    r = readiness_with(FakeGarmin(history(bb=10, bb_wake=None, bb_charged=None)), monkeypatch)
    assert r["verdict"] == "apto"
    assert not any("Body Battery" in x for x in r["reasons"])


# ----------------------------------------------- sleep stages, respiration, SpO2

def test_sleep_stages_respiration_and_spo2_are_extracted():
    night = {**full_day(sleep_h=7.3), "deep_s": 5340, "light_s": 17160, "rem_s": 3840, "awake_s": 0,
             "resp": 15.0, "spo2": 95.0, "spo2_low": 85, "sleep_stress": 20.0}
    d = dash._wellness_day(FakeGarmin({day(10): night}), day(10))
    assert (d["sleep_deep_min"], d["sleep_light_min"], d["sleep_rem_min"], d["sleep_awake_min"]) == (89, 286, 64, 0)
    assert (d["respiration_avg"], d["spo2_avg"], d["spo2_lowest"], d["sleep_stress"]) == (15.0, 95.0, 85, 20.0)


def test_sleep_submetrics_are_empty_on_unrecorded_nights():
    d = dash._wellness_day(FakeGarmin({day(10): {"rhr": 50}}), day(10))
    assert d["sleep_hours"] is None
    assert all(d[k] is None for k in (
        "sleep_deep_min", "sleep_light_min", "sleep_rem_min", "sleep_awake_min",
        "respiration_avg", "spo2_avg", "spo2_lowest", "sleep_stress",
    ))


def test_weekly_recovery_includes_deep_and_rem_sleep():
    rec = dash._weekly_recovery_comparison(week(deep=95, rem=70), week(deep=80, rem=75))
    assert rec["current"]["deep_sleep_min"] == 95 and rec["delta"]["deep_sleep_min"] == 15
    assert rec["current"]["rem_sleep_min"] == 70 and rec["delta"]["rem_sleep_min"] == -5
    assert rec["bottlenecks"] == ["Recuperacao estavel vs semana anterior"]  # stages never alert
