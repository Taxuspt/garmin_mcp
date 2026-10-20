"""Local-time handling for writes when the server runs in another zone (#422)."""

import datetime
import time

import pytest
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
async def test_blood_pressure_without_timestamp_or_tz_is_unchanged(client, monkeypatch):
    monkeypatch.delenv("GARMIN_TZ", raising=False)

    await _call(data_management, "set_blood_pressure", {"systolic": 120, "diastolic": 80, "pulse": 60})

    client.set_blood_pressure.assert_called_once_with(120, 80, 60, notes=None)


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


def test_bad_date_is_rejected(monkeypatch):
    monkeypatch.delenv("GARMIN_TZ", raising=False)
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        resolve_local_time("10/12/2026")
