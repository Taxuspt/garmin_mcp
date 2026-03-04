"""
Integration tests for activity_analysis module MCP tools

Tests the get_activity_fit_data tool using mocked Garmin API and fitparse responses.
"""
import json
import pytest
from unittest.mock import Mock, patch, MagicMock
from mcp.server.fastmcp import FastMCP

from garmin_mcp import activity_analysis
from garmin_mcp.activity_analysis import (
    _compute_power_duration_curve,
    _detect_climbs,
    _compute_hr_drift,
    _compute_temperature_stats,
    _grade_analysis,
    _compute_shift_summary,
)


ACTIVITY_ID = 22041393449


@pytest.fixture
def app_with_activity_analysis(mock_garmin_client):
    """Create FastMCP app with activity_analysis tools registered"""
    activity_analysis.configure(mock_garmin_client)
    app = FastMCP("Test Activity Analysis")
    app = activity_analysis.register_tools(app)
    return app


def _make_mock_fit_message(name, fields: dict):
    """Create a mock fitparse message with the given name and field values."""
    msg = Mock()
    msg.name = name
    msg.get_value = lambda field, *args: fields.get(field)
    return msg


def _mock_fitfile(messages):
    """Create a mock FitFile that yields the given messages."""
    mock_ff = MagicMock()
    mock_ff.get_messages.return_value = iter(messages)
    return mock_ff


# ---------------------------------------------------------------------------
# Basic tool behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_activity_fit_data_calls_download(app_with_activity_analysis, mock_garmin_client):
    """Tool calls download_activity with ORIGINAL format"""
    from garminconnect import Garmin

    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([])
        await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    mock_garmin_client.download_activity.assert_called_once_with(
        ACTIVITY_ID,
        dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL,
    )


@pytest.mark.asyncio
async def test_get_activity_fit_data_empty_response(app_with_activity_analysis, mock_garmin_client):
    """Tool returns friendly message when download returns empty bytes"""
    mock_garmin_client.download_activity.return_value = b""

    result = await app_with_activity_analysis.call_tool(
        "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
    )

    assert result is not None
    text = result[0].text if hasattr(result[0], "text") else str(result)
    assert "No FIT data" in text


@pytest.mark.asyncio
async def test_get_activity_fit_data_none_response(app_with_activity_analysis, mock_garmin_client):
    """Tool handles None response from download_activity gracefully"""
    mock_garmin_client.download_activity.return_value = None

    result = await app_with_activity_analysis.call_tool(
        "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
    )

    assert result is not None
    text = result[0].text if hasattr(result[0], "text") else str(result)
    assert "No FIT data" in text or "Error" in text


@pytest.mark.asyncio
async def test_get_activity_fit_data_error_handling(app_with_activity_analysis, mock_garmin_client):
    """Tool returns error message when download raises an exception"""
    mock_garmin_client.download_activity.side_effect = Exception("network error")

    result = await app_with_activity_analysis.call_tool(
        "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
    )

    assert result is not None
    text = result[0].text if hasattr(result[0], "text") else str(result)
    assert "Error" in text


# ---------------------------------------------------------------------------
# Session parsing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_activity_fit_data_session_fields(app_with_activity_analysis, mock_garmin_client):
    """Parsed session fields appear in output"""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    session_msg = _make_mock_fit_message("session", {
        "sport": "cycling",
        "total_elapsed_time": 3120.0,
        "total_distance": 56320.0,
        "avg_power": 185,
        "normalized_power": 210,
        "avg_cadence": 83,
        "avg_heart_rate": 148,
        "avg_left_pco": 3,
        "avg_right_pco": -8,
        "avg_left_right_balance": None,
    })

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([session_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    text = result[0].text if hasattr(result[0], "text") else str(result)
    data = json.loads(text)

    assert data["session"]["sport"] == "cycling"
    assert data["session"]["avg_power_w"] == 185
    assert data["session"]["normalized_power_w"] == 210
    assert data["session"]["avg_cadence_rpm"] == 83
    assert data["session"]["avg_left_pco_mm"] == 3
    assert data["session"]["avg_right_pco_mm"] == -8


# ---------------------------------------------------------------------------
# Shift / DI2 parsing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_activity_fit_data_shift_events(app_with_activity_analysis, mock_garmin_client):
    """DI2 shift events are parsed and classified correctly"""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    # Simulated record setting cadence before the shift
    record_msg = _make_mock_fit_message("record", {
        "cadence": 65,  # below 70 → next shift should be "reactive"
        "power": 200,
        "heart_rate": 150,
        "speed": 8.5,
        "altitude": 120.0,
    })

    # gear_change_data: front=53t (0x35), rear=16t (0x10), front_num=2, rear_num=5
    # packed: (53 << 24) | (16 << 16) | (2 << 8) | 5
    gear_data = (53 << 24) | (16 << 16) | (2 << 8) | 5

    shift_msg = _make_mock_fit_message("event", {
        "event": "rear_gear_change",
        "gear_change_data": gear_data,
        "timestamp": "2024-03-02 14:23:45",
    })

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([record_msg, shift_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    text = result[0].text if hasattr(result[0], "text") else str(result)
    data = json.loads(text)

    assert len(data["shifts"]) == 1
    shift = data["shifts"][0]
    assert shift["quality"] == "reactive"          # cadence was 65 (<70)
    assert shift["cadence_at_shift_rpm"] == 65
    assert shift["front_teeth"] == 53
    assert shift["rear_teeth"] == 16
    assert shift["gear_combo"] == "53/16t"


@pytest.mark.asyncio
async def test_get_activity_fit_data_proactive_shift(app_with_activity_analysis, mock_garmin_client):
    """Shift at good cadence classified as proactive"""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    record_msg = _make_mock_fit_message("record", {"cadence": 88})
    gear_data = (39 << 24) | (19 << 16) | (1 << 8) | 4
    shift_msg = _make_mock_fit_message("event", {
        "event": "rear_gear_change",
        "gear_change_data": gear_data,
        "timestamp": "2024-03-02 14:30:00",
    })

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([record_msg, shift_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    text = result[0].text if hasattr(result[0], "text") else str(result)
    data = json.loads(text)

    assert data["shifts"][0]["quality"] == "proactive"


@pytest.mark.asyncio
async def test_get_activity_fit_data_shift_summary(app_with_activity_analysis, mock_garmin_client):
    """Shift summary statistics are computed correctly"""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    messages = []
    # 2 reactive shifts (cadence 60), 1 proactive (cadence 85)
    for cadence, ts in [(60, "14:20:00"), (60, "14:21:00"), (85, "14:25:00")]:
        messages.append(_make_mock_fit_message("record", {"cadence": cadence}))
        messages.append(_make_mock_fit_message("event", {
            "event": "rear_gear_change",
            "gear_change_data": (53 << 24) | (17 << 16) | (2 << 8) | 4,
            "timestamp": f"2024-03-02 {ts}",
        }))

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile(messages)
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    text = result[0].text if hasattr(result[0], "text") else str(result)
    data = json.loads(text)

    summary = data["shift_summary"]
    assert summary["total_shifts"] == 3
    assert summary["reactive_shifts"] == 2
    assert summary["proactive_shifts"] == 1
    assert summary["gear_usage"]["53/17t"] == 3


# ---------------------------------------------------------------------------
# Records (time series)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_activity_fit_data_records_excluded_by_default(app_with_activity_analysis, mock_garmin_client):
    """Full time-series records not included unless explicitly requested"""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    record_msg = _make_mock_fit_message("record", {
        "cadence": 85, "power": 210, "heart_rate": 145,
    })

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([record_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    text = result[0].text if hasattr(result[0], "text") else str(result)
    data = json.loads(text)
    assert "records" not in data


@pytest.mark.asyncio
async def test_get_activity_fit_data_records_included_when_requested(app_with_activity_analysis, mock_garmin_client):
    """Full time-series records included when include_records=True"""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    record_msg = _make_mock_fit_message("record", {
        "cadence": 85,
        "power": 210,
        "heart_rate": 145,
        "speed": 9.2,
        "altitude": 130.0,
        "timestamp": "2024-03-02 14:00:00",
        "left_pco": 3,
        "right_pco": -9,
        "left_right_balance": None,
        "left_power_phase": None,
        "right_power_phase": None,
    })

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([record_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data",
            {"activity_id": ACTIVITY_ID, "include_records": True}
        )

    text = result[0].text if hasattr(result[0], "text") else str(result)
    data = json.loads(text)

    assert "records" in data
    assert len(data["records"]) == 1
    rec = data["records"][0]
    assert rec["cadence_rpm"] == 85
    assert rec["power_w"] == 210
    assert rec["left_pco_mm"] == 3
    assert rec["right_pco_mm"] == -9


# ---------------------------------------------------------------------------
# Left/right balance decoding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_activity_fit_data_power_balance(app_with_activity_analysis, mock_garmin_client):
    """Left/right power balance decoded correctly from session message"""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    # Encode 47% left dominant: bit 15 not set, value = 47 * 100 = 4700
    balance_raw = 4700  # 47.0% left
    session_msg = _make_mock_fit_message("session", {
        "sport": "cycling",
        "avg_left_right_balance": balance_raw,
    })

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([session_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    text = result[0].text if hasattr(result[0], "text") else str(result)
    data = json.loads(text)

    assert data["session"]["avg_left_power_pct"] == 47.0
    assert data["session"]["avg_right_power_pct"] == 53.0


# ---------------------------------------------------------------------------
# No DI2 data
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_activity_fit_data_no_shifts(app_with_activity_analysis, mock_garmin_client):
    """Activity with no shift events returns informative shift_summary"""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    session_msg = _make_mock_fit_message("session", {"sport": "cycling"})

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([session_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    text = result[0].text if hasattr(result[0], "text") else str(result)
    data = json.loads(text)

    assert data["shift_summary"]["total_shifts"] == 0
    assert "note" in data["shift_summary"]
    assert data["shifts"] == []


# ---------------------------------------------------------------------------
# Variability Index
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_activity_fit_data_variability_index_session(app_with_activity_analysis, mock_garmin_client):
    """Variability Index computed for session when NP and avg_power are present"""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    session_msg = _make_mock_fit_message("session", {
        "sport": "cycling",
        "normalized_power": 210,
        "avg_power": 175,
    })

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([session_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    text = result[0].text if hasattr(result[0], "text") else str(result)
    data = json.loads(text)
    assert "variability_index" in data["session"]
    assert data["session"]["variability_index"] == round(210 / 175, 3)


@pytest.mark.asyncio
async def test_get_activity_fit_data_variability_index_lap(app_with_activity_analysis, mock_garmin_client):
    """Variability Index computed per lap"""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    lap_msg = _make_mock_fit_message("lap", {
        "normalized_power": 240,
        "avg_power": 200,
        "total_elapsed_time": 1800.0,
    })

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([lap_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    text = result[0].text if hasattr(result[0], "text") else str(result)
    data = json.loads(text)
    assert len(data["laps"]) == 1
    assert data["laps"][0]["variability_index"] == round(240 / 200, 3)


# ---------------------------------------------------------------------------
# Lap torque effectiveness + pedal smoothness
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_activity_fit_data_lap_torque_and_smoothness(app_with_activity_analysis, mock_garmin_client):
    """Torque effectiveness and pedal smoothness included in lap data"""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    lap_msg = _make_mock_fit_message("lap", {
        "avg_left_torque_effectiveness": 82.5,
        "avg_right_torque_effectiveness": 79.0,
        "avg_left_pedal_smoothness": 31.2,
        "avg_right_pedal_smoothness": 28.4,
        "total_elapsed_time": 3600.0,
    })

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([lap_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    text = result[0].text if hasattr(result[0], "text") else str(result)
    data = json.loads(text)
    lap = data["laps"][0]
    assert lap["avg_left_torque_effectiveness_pct"] == 82.5
    assert lap["avg_right_torque_effectiveness_pct"] == 79.0
    assert lap["avg_left_pedal_smoothness_pct"] == 31.2
    assert lap["avg_right_pedal_smoothness_pct"] == 28.4


# ---------------------------------------------------------------------------
# Shift-by-terrain
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_activity_fit_data_shift_terrain_classification(app_with_activity_analysis, mock_garmin_client):
    """Shifts tagged with grade and summarized by terrain"""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    # Record with climbing grade
    record_climb = _make_mock_fit_message("record", {"cadence": 75, "grade": 5.5})
    gear_data = (53 << 24) | (17 << 16) | (2 << 8) | 4
    shift_climb = _make_mock_fit_message("event", {
        "event": "rear_gear_change",
        "gear_change_data": gear_data,
        "timestamp": "2024-03-02 14:20:00",
    })
    # Record on flat
    record_flat = _make_mock_fit_message("record", {"cadence": 90, "grade": 1.0})
    shift_flat = _make_mock_fit_message("event", {
        "event": "rear_gear_change",
        "gear_change_data": gear_data,
        "timestamp": "2024-03-02 14:21:00",
    })

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([
            record_climb, shift_climb, record_flat, shift_flat
        ])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    text = result[0].text if hasattr(result[0], "text") else str(result)
    data = json.loads(text)

    # Each shift should have grade_at_shift_pct
    assert data["shifts"][0]["grade_at_shift_pct"] == 5.5
    assert data["shifts"][1]["grade_at_shift_pct"] == 1.0

    # by_terrain should appear in shift_summary
    assert "by_terrain" in data["shift_summary"]
    assert "climbing" in data["shift_summary"]["by_terrain"]
    assert "flat" in data["shift_summary"]["by_terrain"]


# ---------------------------------------------------------------------------
# Power Duration Curve (unit tests)
# ---------------------------------------------------------------------------

def test_power_duration_curve_basic():
    """PDC computes correct best power for each duration"""
    # Synthetic: 120 records, first 10 are at 500W, rest at 100W
    records = [{"power_w": 500}] * 10 + [{"power_w": 100}] * 110
    pdc = _compute_power_duration_curve(records)
    assert pdc is not None
    # 5s best should be 500W
    assert pdc["5s"] == 500
    # 1min best (60s) — can't have 60 consecutive 500W records, so best is mix
    assert pdc["1min"] < 500


def test_power_duration_curve_empty():
    """PDC returns None when no power data"""
    records = [{"cadence_rpm": 80}] * 100  # no power_w
    pdc = _compute_power_duration_curve(records)
    assert pdc is None


def test_power_duration_curve_short_ride():
    """PDC skips durations longer than the ride"""
    records = [{"power_w": 300}] * 30  # 30 seconds
    pdc = _compute_power_duration_curve(records)
    assert pdc is not None
    assert "5s" in pdc
    assert "1min" not in pdc  # 60s > 30 records


def test_power_duration_curve_sliding_window():
    """PDC sliding window finds best window, not just the start"""
    # Best 5s is in the middle
    records = (
        [{"power_w": 100}] * 10 +
        [{"power_w": 400}] * 5 +
        [{"power_w": 100}] * 10
    )
    pdc = _compute_power_duration_curve(records)
    assert pdc["5s"] == 400


# ---------------------------------------------------------------------------
# Climb detection + VAM (unit tests)
# ---------------------------------------------------------------------------

def _make_records(grade, power, cadence, hr, speed, altitude_start, count):
    """Build synthetic per-second records for climb testing."""
    records = []
    alt = altitude_start
    for i in range(count):
        alt += speed * grade / 100  # approximate altitude gain
        records.append({
            "grade_pct": grade,
            "power_w": power,
            "cadence_rpm": cadence,
            "heart_rate_bpm": hr,
            "speed_mps": speed,
            "altitude_m": alt,
            "timestamp": f"2024-03-02 14:{i // 60:02d}:{i % 60:02d}",
        })
    return records


def test_detect_climbs_basic():
    """Climb detection identifies sustained positive grade segment"""
    # 200 seconds at 5% grade (should produce ~50m gain at 5 m/s)
    climbing = _make_records(grade=5.0, power=280, cadence=75, hr=160, speed=5.0, altitude_start=100, count=200)
    flat = _make_records(grade=0.5, power=180, cadence=90, hr=140, speed=8.0, altitude_start=200, count=100)
    records = climbing + flat

    climbs = _detect_climbs(records, min_elevation_gain_m=30, min_avg_grade_pct=3.0, min_duration_s=60)
    assert len(climbs) >= 1
    c = climbs[0]
    assert c["avg_grade_pct"] == 5.0
    assert c["avg_power_w"] == 280
    assert "vam_m_per_hr" in c


def test_detect_climbs_none_when_flat():
    """No climbs detected on a flat ride"""
    flat = _make_records(grade=1.0, power=200, cadence=90, hr=140, speed=9.0, altitude_start=50, count=300)
    climbs = _detect_climbs(flat)
    assert climbs == []


def test_detect_climbs_vam_calculation():
    """VAM calculation is correct: (elevation_gain / duration) * 3600"""
    # 300 seconds at 5 m/s, 5% grade → gain ≈ 75m
    records = _make_records(grade=5.0, power=280, cadence=75, hr=155, speed=5.0, altitude_start=0, count=300)
    climbs = _detect_climbs(records, min_elevation_gain_m=30, min_duration_s=60)
    assert len(climbs) >= 1
    # VAM should be roughly (75 / 300) * 3600 = 900 m/hr (approximate)
    assert climbs[0].get("vam_m_per_hr", 0) > 500


# ---------------------------------------------------------------------------
# HR drift (unit tests)
# ---------------------------------------------------------------------------

def test_hr_drift_significant():
    """Detects significant HR drift (decoupling) when second half HR is elevated"""
    # First half: 250W at 140 bpm → ratio 1.786
    # Second half: 250W at 165 bpm → ratio 1.515 → drift = (1.515-1.786)/1.786 = -15%
    first_half = [{"power_w": 250, "heart_rate_bpm": 140}] * 1900
    second_half = [{"power_w": 250, "heart_rate_bpm": 165}] * 1900
    records = first_half + second_half
    drift = _compute_hr_drift(records)
    assert drift is not None
    assert drift["hr_drift_pct"] < -10  # significant negative drift
    assert drift["interpretation"] == "significant_decoupling"


def test_hr_drift_well_coupled():
    """Well-coupled ride shows minimal HR drift"""
    records = [{"power_w": 200, "heart_rate_bpm": 145}] * 4000
    drift = _compute_hr_drift(records)
    assert drift is not None
    assert abs(drift["hr_drift_pct"]) < 5
    assert drift["interpretation"] == "well_coupled"


def test_hr_drift_skipped_for_short_rides():
    """HR drift not computed for rides under 60 minutes"""
    records = [{"power_w": 200, "heart_rate_bpm": 145}] * 1000  # ~17 min
    drift = _compute_hr_drift(records)
    assert drift is None


# ---------------------------------------------------------------------------
# Temperature stats (unit tests)
# ---------------------------------------------------------------------------

def test_temperature_stats_basic():
    """Temperature stats computed correctly"""
    records = (
        [{"temperature_c": 15, "heart_rate_bpm": 140, "power_w": 220}] * 50 +
        [{"temperature_c": 30, "heart_rate_bpm": 160, "power_w": 210}] * 50
    )
    stats = _compute_temperature_stats(records)
    assert stats is not None
    assert stats["min_temp_c"] == 15
    assert stats["max_temp_c"] == 30
    assert stats["temp_range_c"] == 15.0


def test_temperature_stats_none_when_no_data():
    """Returns None when no temperature data"""
    records = [{"power_w": 200, "heart_rate_bpm": 145}] * 50
    assert _compute_temperature_stats(records) is None


# ---------------------------------------------------------------------------
# Grade analysis (unit tests)
# ---------------------------------------------------------------------------

def test_grade_analysis_bins_correctly():
    """Grade analysis bins records into correct terrain buckets"""
    records = (
        [{"grade_pct": 0.5, "power_w": 180, "cadence_rpm": 90, "heart_rate_bpm": 140}] * 60 +
        [{"grade_pct": 4.5, "power_w": 280, "cadence_rpm": 75, "heart_rate_bpm": 165}] * 60 +
        [{"grade_pct": 7.5, "power_w": 320, "cadence_rpm": 65, "heart_rate_bpm": 175}] * 60 +
        [{"grade_pct": -4.0, "power_w": 50, "cadence_rpm": 95, "heart_rate_bpm": 120}] * 60
    )
    result = _grade_analysis(records)
    assert result is not None
    assert "flat" in result
    assert "gentle" in result
    assert "moderate" in result
    assert "descending" in result
    # Steeper terrain should have higher avg power
    assert result["gentle"]["avg_power_w"] < result["moderate"]["avg_power_w"]
