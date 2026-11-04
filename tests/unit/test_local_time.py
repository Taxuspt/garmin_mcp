"""Local-time handling for writes when the server runs in another zone (#422)."""

import datetime
import time

import pytest
from garminconnect import Garmin
from garminconnect.fit import FitEncoder
from mcp.server.fastmcp import FastMCP
from unittest.mock import Mock

from garmin_mcp import data_management, devices
from garmin_mcp.local_time import format_epoch_ms, resolve_local_time, to_host_naive


@pytest.fixture
def utc_host(monkeypatch):
    """Run the test as if the server host's zone were UTC (e.g. Docker)."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def client():
    client = Mock()
    client.add_body_composition.return_value = {}
    client.set_blood_pressure.return_value = {}
    data_management.configure(client)
    devices.configure(client)
    return client


async def _call(module, tool, args):
    app = module.register_tools(FastMCP("t"))
    result = await app.call_tool(tool, args)
    return result[0][0].text


def _fit_instant(naive_iso):
    """The UTC instant garminconnect's FIT encoder writes for a timestamp."""
    seconds = FitEncoder.timestamp(None, datetime.datetime.fromisoformat(naive_iso))
    return datetime.datetime.fromtimestamp(seconds + 631065600, datetime.timezone.utc)


@pytest.mark.asyncio
async def test_body_composition_date_lands_on_users_day(utc_host, client, monkeypatch):
    monkeypatch.setenv("GARMIN_TZ", "America/Los_Angeles")

    await _call(data_management, "add_body_composition", {"date": "2026-10-12", "weight": 70.0})

    sent = client.add_body_composition.call_args.args[0]
    # Noon in Los Angeles, not midnight UTC (17:00 the previous day in LA).
    assert _fit_instant(sent) == datetime.datetime(2026, 10, 12, 19, 0, tzinfo=datetime.timezone.utc)


@pytest.mark.asyncio
async def test_body_composition_accepts_local_timestamp(utc_host, client, monkeypatch):
    monkeypatch.setenv("GARMIN_TZ", "Europe/Berlin")

    await _call(data_management, "add_body_composition", {"date": "2026-10-12T07:30", "weight": 70.0})

    sent = client.add_body_composition.call_args.args[0]
    assert _fit_instant(sent) == datetime.datetime(2026, 10, 12, 5, 30, tzinfo=datetime.timezone.utc)


@pytest.mark.asyncio
async def test_blood_pressure_timestamp_in_users_zone(utc_host, client, monkeypatch):
    monkeypatch.setenv("GARMIN_TZ", "America/New_York")

    await _call(data_management, "set_blood_pressure", {
        "systolic": 120, "diastolic": 80, "pulse": 60, "timestamp": "2026-10-12T07:15:00",
    })

    kwargs = client.set_blood_pressure.call_args.kwargs
    assert kwargs["timestamp"] == "2026-10-12T07:15:00-04:00"


@pytest.mark.asyncio
async def test_blood_pressure_library_sends_local_and_gmt_times(utc_host, monkeypatch):
    monkeypatch.setenv("GARMIN_TZ", "America/New_York")
    client = Garmin.__new__(Garmin)
    client.garmin_connect_set_blood_pressure_endpoint = "/bloodpressure"
    client.client = Mock()
    client.client.post.return_value.json.return_value = {}
    data_management.configure(client)

    await _call(data_management, "set_blood_pressure", {
        "systolic": 120, "diastolic": 80, "pulse": 60, "timestamp": "2026-11-02T18:36:00",
    })

    payload = client.client.post.call_args.kwargs["json"]
    assert payload["measurementTimestampLocal"].startswith("2026-11-02T18:36:00")
    assert payload["measurementTimestampGMT"].startswith("2026-11-02T23:36:00")


@pytest.mark.asyncio
async def test_blood_pressure_without_timestamp_defaults_to_utc(client, monkeypatch):
    monkeypatch.delenv("GARMIN_TZ", raising=False)

    before = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    await _call(data_management, "set_blood_pressure", {"systolic": 120, "diastolic": 80, "pulse": 60})
    after = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)

    sent = datetime.datetime.fromisoformat(client.set_blood_pressure.call_args.kwargs["timestamp"])
    assert sent.utcoffset() == datetime.timedelta(0)
    assert before <= sent <= after


@pytest.mark.asyncio
async def test_blood_pressure_now_uses_configured_zone(client, monkeypatch):
    monkeypatch.setenv("GARMIN_TZ", "America/New_York")
    before = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    await _call(data_management, "set_blood_pressure", {"systolic": 120, "diastolic": 80, "pulse": 60})
    after = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    sent = datetime.datetime.fromisoformat(client.set_blood_pressure.call_args.kwargs["timestamp"])
    assert sent.utcoffset() in (datetime.timedelta(hours=-4), datetime.timedelta(hours=-5))
    assert before <= sent.astimezone(datetime.timezone.utc) <= after


@pytest.mark.asyncio
async def test_invalid_garmin_tz_is_reported(client, monkeypatch):
    monkeypatch.setenv("GARMIN_TZ", "Mars/Olympus")

    text = await _call(data_management, "add_body_composition", {"date": "2026-10-12", "weight": 70.0})

    assert "GARMIN_TZ" in text
    client.add_body_composition.assert_not_called()


def test_last_upload_time_has_an_offset(utc_host, monkeypatch):
    monkeypatch.setenv("GARMIN_TZ", "Asia/Tokyo")
    # 2026-10-12T00:00:00Z
    assert format_epoch_ms(1791763200000) == "2026-10-12T09:00:00+09:00"
    monkeypatch.delenv("GARMIN_TZ")
    assert format_epoch_ms(1791763200000) == "2026-10-12T00:00:00+00:00"


def test_offset_in_input_is_kept(utc_host, monkeypatch):
    monkeypatch.setenv("GARMIN_TZ", "America/Los_Angeles")
    dt = resolve_local_time("2026-10-12T07:00:00+02:00")
    assert dt.utcoffset() == datetime.timedelta(hours=2)
    assert to_host_naive(dt) == datetime.datetime(2026, 10, 12, 5, 0)


@pytest.mark.parametrize("host_zone", ["UTC", "America/Los_Angeles", "Asia/Tokyo"])
def test_unzoned_input_and_fit_encoding_do_not_depend_on_host(host_zone, monkeypatch):
    monkeypatch.delenv("GARMIN_TZ", raising=False)
    with monkeypatch.context() as host:
        host.setenv("TZ", host_zone)
        time.tzset()
        try:
            expected = datetime.datetime(2026, 11, 2, 18, 36, tzinfo=datetime.timezone.utc)
            dt = resolve_local_time("2026-11-02T18:36:00")
            assert dt == expected
            assert _fit_instant(to_host_naive(dt).isoformat()) == expected
            assert resolve_local_time("2026-11-02") == datetime.datetime(
                2026, 11, 2, 12, tzinfo=datetime.timezone.utc
            )
        finally:
            host.undo()
            time.tzset()


def test_zone_offsets_follow_seasons_and_explicit_offsets_override(monkeypatch):
    monkeypatch.setenv("GARMIN_TZ", "America/New_York")
    assert resolve_local_time("2026-07-01T12:00").utcoffset() == datetime.timedelta(hours=-4)
    assert resolve_local_time("2026-11-02T18:36").utcoffset() == datetime.timedelta(hours=-5)
    assert resolve_local_time("2026-11-02T18:36:00Z").utcoffset() == datetime.timedelta(0)
    assert resolve_local_time("2026-11-02T18:36:00+02:00").utcoffset() == datetime.timedelta(hours=2)


def test_host_dst_rollback_never_silently_encodes_wrong_instant(monkeypatch):
    with monkeypatch.context() as host:
        host.setenv("TZ", "America/Los_Angeles")
        time.tzset()
        try:
            for hour in (8, 9):
                instant = datetime.datetime(2026, 11, 1, hour, 30, tzinfo=datetime.timezone.utc)
                try:
                    encoded = to_host_naive(instant)
                except ValueError as exc:
                    assert "TZ=UTC" in str(exc)
                else:
                    assert _fit_instant(encoded.isoformat()) == instant
        finally:
            host.undo()
            time.tzset()


@pytest.mark.asyncio
async def test_body_composition_never_writes_wrong_dst_occurrence(client, monkeypatch):
    monkeypatch.delenv("GARMIN_TZ", raising=False)
    with monkeypatch.context() as host:
        host.setenv("TZ", "America/Los_Angeles")
        time.tzset()
        try:
            for hour in (8, 9):
                client.add_body_composition.reset_mock()
                text = await _call(data_management, "add_body_composition", {
                    "date": f"2026-11-01T{hour:02d}:30:00Z", "weight": 70.0,
                })
                if client.add_body_composition.called:
                    sent = client.add_body_composition.call_args.args[0]
                    assert _fit_instant(sent) == datetime.datetime(
                        2026, 11, 1, hour, 30, tzinfo=datetime.timezone.utc
                    )
                else:
                    assert "TZ=UTC" in text
        finally:
            host.undo()
            time.tzset()


def test_bad_date_is_rejected(monkeypatch):
    monkeypatch.delenv("GARMIN_TZ", raising=False)
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        resolve_local_time("10/12/2026")
