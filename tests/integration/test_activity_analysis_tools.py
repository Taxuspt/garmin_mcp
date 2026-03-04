"""
Integration tests for activity_analysis module MCP tools

Tests the get_activity_fit_data tool using mocked Garmin API and fitparse responses.
"""
import json
import pytest
from unittest.mock import Mock, patch, MagicMock
from mcp.server.fastmcp import FastMCP

from garmin_mcp import activity_analysis


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
