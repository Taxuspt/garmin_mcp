"""
Integration tests for activity_analysis module MCP tools

Tests the get_activity_fit_data tool using mocked Garmin API and fitparse responses.
"""
import datetime
import io
import json
import os
import zipfile
import pytest
from unittest.mock import Mock, patch, MagicMock
from fitparse.profile import MESSAGE_TYPES
from fitparse.records import DataMessage, FieldData
from mcp.server.fastmcp import FastMCP

from garmin_mcp import activity_analysis
from garmin_mcp.activity_analysis import (
    _compute_power_duration_curve,
    _detect_climbs,
    _compute_hr_drift,
    _compute_temperature_stats,
    _grade_analysis,
    _compute_shift_summary,
    _compute_hrv_metrics,
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


def _make_generic_fit_message(name, fields, global_message_number=None):
    """Create a message with real FieldData-like objects for generic parsing."""
    message = Mock()
    message.name = name
    message.fields = []
    if global_message_number is not None:
        message.mesg_num = global_message_number
    else:
        del message.mesg_num

    for definition_number, field_name, value, units in fields:
        field = Mock()
        field.name = field_name
        field.value = value
        field.units = units
        field.field_def = Mock(def_num=definition_number)
        if field_name == "category":
            field.type = activity_analysis.FIT_FIELD_TYPES["exercise_category"]
        else:
            del field.type
        del field.raw_value
        message.fields.append(field)
    return message


def _make_profile_fit_message(message_number, fields):
    """Create a DataMessage with the actual fitparse profile field objects."""
    message_type = MESSAGE_TYPES[message_number]
    field_data = [
        FieldData(
            field_def=None,
            field=message_type.fields[definition_number],
            parent_field=None,
            value=value,
            raw_value=raw_value,
        )
        for definition_number, value, raw_value in fields
    ]
    return DataMessage(header=None, def_mesg=message_type, fields=field_data)


# ---------------------------------------------------------------------------
# Basic tool behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_activity_fit_messages_preserves_all_message_types_and_fields(
    app_with_activity_analysis, mock_garmin_client
):
    """Generic FIT tool does not discard strength or unknown FIT fields."""
    from garminconnect import Garmin

    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    exercise_title = _make_generic_fit_message(
        "exercise_title",
        [
            (254, "message_index", 0, None),
            (0, "exercise_category", "bench_press", None),
            (1, "exercise_name", "barbell_bench_press", None),
        ],
        global_message_number=264,
    )
    set_message = _make_generic_fit_message(
        "set",
        [
            (3, "repetitions", 8, "repetitions"),
            (4, "weight", 61.2349, "kg"),
            (7, "category", [8, 21], None),
            (8, "category_subtype", [0, None], None),
            (5, "set_type", "active", None),
            (99, "unknown_99", b"\x01\x02", None),
        ],
        global_message_number=225,
    )

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([exercise_title, set_message])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_messages", {"activity_id": ACTIVITY_ID}
        )

    mock_garmin_client.download_activity.assert_called_once_with(
        ACTIVITY_ID,
        dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL,
    )
    data = json.loads(result[0][0].text)
    assert data["source"] == "garmin_original_fit"
    assert data["message_counts"] == {"exercise_title": 1, "set": 1}
    assert [message["type"] for message in data["messages"]] == [
        "exercise_title", "set"
    ]
    assert data["messages"][0]["global_message_number"] == 264
    assert data["messages"][1]["fields"] == [
        {
            "name": "repetitions",
            "value": 8,
            "units": "repetitions",
            "definition_number": 3,
        },
        {
            "name": "weight",
            "value": 61.2349,
            "units": "kg",
            "definition_number": 4,
        },
        {
            "name": "category",
            "value": [8, 21],
            "definition_number": 7,
            "profile_type": "exercise_category",
            "value_names": ["deadlift", "pull_up"],
        },
        {
            "name": "category_subtype",
            "value": [0, None],
            "definition_number": 8,
            "value_names": ["barbell_deadlift", None],
        },
        {"name": "set_type", "value": "active", "definition_number": 5},
        {
            "name": "unknown_99",
            "value": {"encoding": "hex", "value": "0102"},
            "definition_number": 99,
        },
    ]


@pytest.mark.asyncio
async def test_get_activity_fit_messages_resolves_standard_contextual_enums(
    app_with_activity_analysis, mock_garmin_client
):
    """Real fitparse fields retain labels for context-dependent FIT enums."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    messages = [
        _make_profile_fit_message(
            225,
            [
                (7, "squat", 28),
                (8, 6, 6),
            ],
        ),
        _make_profile_fit_message(
            264,
            [
                (0, "squat", 28),
                (1, 6, 6),
            ],
        ),
    ]

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile(messages)
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_messages", {"activity_id": ACTIVITY_ID}
        )

    data = json.loads(result[0][0].text)
    fields_by_message = [
        {field["name"]: field for field in message["fields"]}
        for message in data["messages"]
    ]
    assert fields_by_message[0]["category"]["value_name"] == "squat"
    assert (
        fields_by_message[0]["category_subtype"]["value_name"]
        == "barbell_back_squat"
    )
    assert (
        fields_by_message[1]["exercise_name"]["value_name"]
        == "barbell_back_squat"
    )
    assert fields_by_message[1]["exercise_name"]["raw_value"] == 6


@pytest.mark.asyncio
async def test_get_activity_fit_messages_omits_records_by_default(
    app_with_activity_analysis, mock_garmin_client
):
    """Record messages are inventoried but do not inflate the default result."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    messages = [
        _make_generic_fit_message("record", [(3, "heart_rate", 140, "bpm")]),
        _make_generic_fit_message("set", [(3, "repetitions", 10, "repetitions")]),
        _make_generic_fit_message("record", [(3, "heart_rate", 141, "bpm")]),
    ]

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile(messages)
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_messages", {"activity_id": ACTIVITY_ID}
        )

    data = json.loads(result[0][0].text)
    assert data["message_counts"] == {"record": 2, "set": 1}
    assert data["returned_message_counts"] == {"set": 1}
    assert [message["type"] for message in data["messages"]] == ["set"]
    assert data["record_stream"]["included"] is False
    assert data["record_stream"]["total_count"] == 2
    assert data["record_stream"]["returned_count"] == 0


@pytest.mark.asyncio
async def test_get_activity_fit_messages_paginates_records(
    app_with_activity_analysis, mock_garmin_client
):
    """Callers can walk a large record stream in bounded pages."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    messages = [
        _make_generic_fit_message("record", [(3, "heart_rate", bpm, "bpm")])
        for bpm in (140, 141, 142)
    ]

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp, patch(
        "garmin_mcp.activity_analysis._serialize_fit_field",
        wraps=activity_analysis._serialize_fit_field,
    ) as serialize_field:
        mock_fp.FitFile.return_value = _mock_fitfile(messages)
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_messages",
            {
                "activity_id": ACTIVITY_ID,
                "include_records": True,
                "message_offset": 1,
                "message_limit": 1,
            },
        )

    data = json.loads(result[0][0].text)
    assert data["record_stream"] == {
        "included": True,
        "total_count": 3,
        "returned_count": 1,
    }
    assert data["pagination"] == {
        "total_eligible_count": 3,
        "returned_count": 1,
        "offset": 1,
        "limit": 1,
        "next_offset": 2,
    }
    assert data["messages"][0]["type_index"] == 1
    assert data["messages"][0]["fields"][0]["value"] == 141
    assert serialize_field.call_count == 1


@pytest.mark.asyncio
async def test_get_activity_fit_messages_filters_returned_types_but_keeps_inventory(
    app_with_activity_analysis, mock_garmin_client
):
    """message_types narrows output without hiding what the FIT file contains."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    messages = [
        _make_generic_fit_message("session", [(5, "sport", "training", None)]),
        _make_generic_fit_message("set", [(3, "repetitions", 5, "repetitions")]),
    ]

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile(messages)
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_messages",
            {"activity_id": ACTIVITY_ID, "message_types": ["SET"]},
        )

    data = json.loads(result[0][0].text)
    assert data["message_counts"] == {"session": 1, "set": 1}
    assert data["returned_message_counts"] == {"set": 1}
    assert [message["type"] for message in data["messages"]] == ["set"]


@pytest.mark.asyncio
async def test_get_activity_fit_messages_rejects_unbounded_message_limit(
    app_with_activity_analysis, mock_garmin_client
):
    """Message pages have a hard maximum to protect MCP response size."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_messages",
            {
                "activity_id": ACTIVITY_ID,
                "include_records": True,
                "message_limit": 5001,
            },
        )

    data = json.loads(result[0][0].text)
    assert data["error"] == "message_limit must be between 1 and 5000"


@pytest.mark.asyncio
async def test_get_activity_fit_messages_omits_other_high_frequency_types_by_default(
    app_with_activity_analysis, mock_garmin_client
):
    """Unknown vendor sample streams cannot inflate an unfiltered response."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    messages = [
        _make_generic_fit_message("session", [(5, "sport", "training", None)])
    ] + [
        _make_generic_fit_message("unknown_233", [(0, "unknown_0", index, None)])
        for index in range(101)
    ]

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp, patch(
        "garmin_mcp.activity_analysis._serialize_fit_field",
        wraps=activity_analysis._serialize_fit_field,
    ) as serialize_field:
        mock_fp.FitFile.return_value = _mock_fitfile(messages)
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_messages", {"activity_id": ACTIVITY_ID}
        )

    data = json.loads(result[0][0].text)
    assert [message["type"] for message in data["messages"]] == ["session"]
    assert data["message_counts"]["unknown_233"] == 101
    assert data["omitted_high_frequency_message_types"]["unknown_233"]["count"] == 101
    assert serialize_field.call_count == 1

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
    text = result[0][0].text
    assert "No FIT data" in text


@pytest.mark.asyncio
async def test_get_activity_fit_data_none_response(app_with_activity_analysis, mock_garmin_client):
    """Tool handles None response from download_activity gracefully"""
    mock_garmin_client.download_activity.return_value = None

    result = await app_with_activity_analysis.call_tool(
        "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
    )

    assert result is not None
    text = result[0][0].text
    assert "No FIT data" in text or "Error" in text


@pytest.mark.asyncio
async def test_get_activity_fit_data_error_handling(app_with_activity_analysis, mock_garmin_client):
    """Tool returns error message when download raises an exception"""
    mock_garmin_client.download_activity.side_effect = Exception("network error")

    result = await app_with_activity_analysis.call_tool(
        "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
    )

    assert result is not None
    text = result[0][0].text
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
        "left_right_balance": None,
    })

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([session_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    text = result[0][0].text
    data = json.loads(text)

    assert data["session"]["sport"] == "cycling"
    assert data["session"]["avg_power_w"] == 185
    assert data["session"]["normalized_power_w"] == 210
    assert data["session"]["avg_cadence_rpm"] == 83
    assert data["session"]["avg_left_pco_mm"] == 3
    assert data["session"]["avg_right_pco_mm"] == -8
    assert "multisport" not in data
    assert "sessions" not in data
    assert "analyzed_session_index" not in data


_TRI_START = datetime.datetime(2024, 6, 1, 8, 0, 0)

# (sport, start offset s, duration s, record power W) for a run-bike-run brick:
# 10 min run at 350 W, 1 min transition, 20 min bike at 200 W, 10 min run.
_BRICK_LEGS = [
    ("running", 0, 600, 350),
    ("transition", 600, 60, None),
    ("cycling", 660, 1200, 200),
    ("running", 1860, 600, 350),
]


def _multisport_messages(legs=_BRICK_LEGS, with_timestamps=True):
    """Records for every leg, then one session message per leg (FIT order)."""
    messages = []
    for _, offset, duration, power in legs:
        for i in range(duration):
            fields = {"heart_rate": 150}
            if power is not None:
                fields["power"] = power
            if with_timestamps:
                fields["timestamp"] = _TRI_START + datetime.timedelta(seconds=offset + i)
            messages.append(_make_mock_fit_message("record", fields))
    for sport, offset, duration, _ in legs:
        messages.append(_make_mock_fit_message("session", {
            "sport": sport,
            "start_time": _TRI_START + datetime.timedelta(seconds=offset),
            "total_elapsed_time": float(duration),
            "avg_power": 200 if sport == "cycling" else None,
        }))
    return messages


async def _call_fit_data(app, mock_garmin_client, messages):
    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    mock_garmin_client.get_body_composition.return_value = None
    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile(messages)
        result = await app.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )
    return json.loads(result[0][0].text)


@pytest.mark.asyncio
async def test_get_activity_fit_data_multisport_lists_every_session(
    app_with_activity_analysis, mock_garmin_client
):
    """Each leg of a multisport file is reported; session is the bike leg."""
    data = await _call_fit_data(
        app_with_activity_analysis, mock_garmin_client, _multisport_messages()
    )

    assert data["multisport"] is True
    assert [s["sport"] for s in data["sessions"]] == [
        "running", "transition", "cycling", "running"
    ]
    assert data["sessions"][2]["start_time"] == "2024-06-01 08:11:00"
    assert data["sessions"][2]["total_elapsed_time_s"] == 1200.0
    assert data["analyzed_session_index"] == 2
    assert data["session"]["sport"] == "cycling"
    assert data["session"]["avg_power_w"] == 200
    assert "leg 2 (cycling)" in data["analysis_note"]


@pytest.mark.asyncio
async def test_get_activity_fit_data_multisport_power_curve_is_bike_only(
    app_with_activity_analysis, mock_garmin_client
):
    """Run-leg power (350 W) does not leak into the bike power curve (200 W)."""
    data = await _call_fit_data(
        app_with_activity_analysis, mock_garmin_client, _multisport_messages()
    )

    pdc = data["power_duration_curve"]
    assert pdc["5s"] == 200
    assert pdc["20min"] == 200
    assert "60min" not in pdc  # the bike leg is only 20 min long


@pytest.mark.asyncio
async def test_get_activity_fit_data_multisport_without_timestamps_falls_back(
    app_with_activity_analysis, mock_garmin_client
):
    """Without record timestamps, analyses keep covering all legs and say so."""
    data = await _call_fit_data(
        app_with_activity_analysis,
        mock_garmin_client,
        _multisport_messages(with_timestamps=False),
    )

    assert data["session"]["sport"] == "cycling"
    assert data["power_duration_curve"]["5s"] == 350
    assert "cover all legs" in data["analysis_note"]


def test_primary_session_index_falls_back_to_longest_leg():
    """With no single cycling leg, the longest leg is analysed."""
    sessions = [
        {"sport": "cycling", "total_elapsed_time_s": 900.0},
        {"sport": "running", "total_elapsed_time_s": 2400.0},
        {"sport": "cycling", "total_elapsed_time_s": 1800.0},
    ]
    assert activity_analysis._primary_session_index(sessions) == 1
    assert activity_analysis._primary_session_index(
        [{"sport": "swimming", "total_elapsed_time_s": 1200.0},
         {"sport": "running", "total_elapsed_time_s": 600.0}]
    ) == 0


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

    # gear_change_data (FIT profile layout): front=53t, front_num=2, rear=16t, rear_num=5
    # packed: (front_teeth << 24) | (front_num << 16) | (rear_teeth << 8) | rear_num
    gear_data = (53 << 24) | (2 << 16) | (16 << 8) | 5

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

    text = result[0][0].text
    data = json.loads(text)

    assert len(data["shifts"]) == 1
    shift = data["shifts"][0]
    assert shift["quality"] == "reactive"          # cadence was 65 (<70)
    assert shift["cadence_at_shift_rpm"] == 65
    assert shift["front_teeth"] == 53
    assert shift["rear_teeth"] == 16
    assert shift["front_gear_num"] == 2
    assert shift["rear_gear_num"] == 5
    assert shift["gear_combo"] == "53/16t"


def test_decode_gear_change_matches_fit_profile_layout():
    """The decoder uses the bit offsets fitparse's profile gives gear_change_data."""
    event_type = MESSAGE_TYPES[21]
    gear_change_data = next(
        sub for sub in event_type.fields[3].subfields if sub.name == "gear_change_data"
    )
    offsets = {c.name: c.bit_offset for c in gear_change_data.components}
    packed = (
        (5 << offsets["rear_gear_num"])
        | (21 << offsets["rear_gear"])
        | (2 << offsets["front_gear_num"])
        | (50 << offsets["front_gear"])
    )

    assert activity_analysis._decode_gear_change(packed) == {
        "rear_gear_num": 5,
        "front_gear_num": 2,
        "rear_teeth": 21,
        "front_teeth": 50,
    }


@pytest.mark.asyncio
async def test_get_activity_fit_data_proactive_shift(app_with_activity_analysis, mock_garmin_client):
    """Shift at good cadence classified as proactive"""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    record_msg = _make_mock_fit_message("record", {"cadence": 88})
    gear_data = (39 << 24) | (1 << 16) | (19 << 8) | 4
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

    text = result[0][0].text
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
            "gear_change_data": (53 << 24) | (2 << 16) | (17 << 8) | 4,
            "timestamp": f"2024-03-02 {ts}",
        }))

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile(messages)
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    text = result[0][0].text
    data = json.loads(text)

    summary = data["shift_summary"]
    assert summary["total_shifts"] == 3
    assert summary["reactive_shifts"] == 2
    assert summary["proactive_shifts"] == 1
    assert summary["gear_usage"]["53/17t"] == 3


def _shift_at(timestamp):
    return {"event": "rear_gear_change", "quality": "proactive", "timestamp": timestamp}


def test_shift_summary_spaced_shifts_are_not_panic_bursts():
    """Shifts minutes apart never count as a panic burst, however many there are."""
    shifts = [_shift_at(f"2024-06-01 10:{minute:02d}:00") for minute in range(0, 60, 5)]
    assert _compute_shift_summary(shifts)["panic_burst_episodes"] == 0


def test_shift_summary_counts_clustered_shifts_as_one_episode_each():
    """3+ shifts within 10 s form one episode; separate clusters count separately."""
    shifts = [
        _shift_at("2024-06-01 10:00:00"),
        _shift_at("2024-06-01 10:00:02"),
        _shift_at("2024-06-01 10:00:04"),
        _shift_at("2024-06-01 10:00:06"),
        _shift_at("2024-06-01 10:05:00"),
        _shift_at("2024-06-01 10:05:03"),
        _shift_at("2024-06-01 10:05:05"),
    ]
    assert _compute_shift_summary(shifts)["panic_burst_episodes"] == 2


def test_shift_summary_ignores_shifts_without_timestamps():
    """Missing or unparseable timestamps are not counted as bursts."""
    shifts = [_shift_at(""), _shift_at("not a time"), {"event": "rear_gear_change"}]
    summary = _compute_shift_summary(shifts)
    assert summary["total_shifts"] == 3
    assert summary["panic_burst_episodes"] == 0


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

    text = result[0][0].text
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

    text = result[0][0].text
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
        "left_right_balance": balance_raw,
    })

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([session_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    text = result[0][0].text
    data = json.loads(text)

    assert data["session"]["avg_left_power_pct"] == 47.0
    assert data["session"]["avg_right_power_pct"] == 53.0


@pytest.mark.asyncio
async def test_get_activity_fit_data_power_balance_uses_profile_field_types(
    app_with_activity_analysis, mock_garmin_client
):
    """Session/lap balance is uint16 x100 and record balance is uint8 whole percent."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    # 53.00% right: 0x8000 | 5300 (left_right_balance_100)
    session_msg = _make_profile_fit_message(18, [(37, 0x8000 | 5300, 0x8000 | 5300)])
    lap_msg = _make_profile_fit_message(19, [(34, 0x8000 | 5100, 0x8000 | 5100)])
    # 52% right: 0x80 | 52 (left_right_balance)
    record_msg = _make_profile_fit_message(20, [(30, 0x80 | 52, 0x80 | 52)])

    assert session_msg.get_value("left_right_balance") == 0x8000 | 5300
    assert record_msg.get_value("left_right_balance") == 0x80 | 52

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([record_msg, lap_msg, session_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data",
            {"activity_id": ACTIVITY_ID, "include_records": True},
        )

    data = json.loads(result[0][0].text)
    assert data["session"]["avg_left_power_pct"] == 47.0
    assert data["session"]["avg_right_power_pct"] == 53.0
    assert data["laps"][0]["avg_left_power_pct"] == 49.0
    assert data["laps"][0]["avg_right_power_pct"] == 51.0
    assert data["records"][0]["left_power_pct"] == 48.0
    assert data["records"][0]["right_power_pct"] == 52.0


@pytest.mark.asyncio
async def test_get_activity_fit_data_session_power_phase_uses_profile_indexes(
    app_with_activity_analysis, mock_garmin_client
):
    """Each phase array contains separate start/end angles, not one start value."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    session_msg = _make_profile_fit_message(
        18,
        [
            (116, (337.5, 22.5), (240, 16)),
            (118, (345.9375, 15.46875), (246, 11)),
        ],
    )
    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([session_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    session = json.loads(result[0][0].text)["session"]
    assert session["avg_left_power_phase_start_deg"] == 337.5
    assert session["avg_left_power_phase_end_deg"] == 22.5
    assert session["avg_right_power_phase_start_deg"] == 345.9375
    assert session["avg_right_power_phase_end_deg"] == 15.46875


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

    text = result[0][0].text
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

    text = result[0][0].text
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

    text = result[0][0].text
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

    text = result[0][0].text
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
    gear_data = (53 << 24) | (2 << 16) | (17 << 8) | 4
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

    text = result[0][0].text
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


def _mock_power_fitfile(watts, count=60):
    """Mock FitFile whose record messages all carry the given power."""
    return _mock_fitfile(
        [_make_mock_fit_message("record", {"power": watts}) for _ in range(count)]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "activity_type,expected_watts,expected_id",
    [("running", 250, 1), ("cycling", 900, 2)],
)
async def test_power_duration_curve_filters_by_requested_activity_type(
    app_with_activity_analysis, mock_garmin_client, activity_type, expected_watts, expected_id
):
    """Bike power only counts toward the curve when cycling is requested."""
    mock_garmin_client.get_activities.return_value = [
        {"activityId": 1, "startTimeLocal": "2024-06-01 08:00:00",
         "activityType": {"typeKey": "running", "parentTypeId": 17}},
        {"activityId": 2, "startTimeLocal": "2024-06-02 08:00:00",
         "activityType": {"typeKey": "road_biking", "parentTypeId": 2}},
    ]
    mock_garmin_client.download_activity.side_effect = (
        lambda activity_id, dl_fmt: b"run" if activity_id == 1 else b"ride"
    )

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.side_effect = lambda buf: _mock_power_fitfile(
            250 if buf.getvalue() == b"run" else 900
        )
        result = await app_with_activity_analysis.call_tool(
            "get_power_duration_curve", {"activity_type": activity_type}
        )

    data = json.loads(result[0][0].text)
    assert data["activities_analyzed"] == 1
    assert data["season_bests"]["5s"]["watts"] == expected_watts
    assert data["season_bests"]["5s"]["activity_id"] == expected_id


@pytest.mark.asyncio
async def test_power_duration_curve_includes_cycling_parent_subtypes(
    app_with_activity_analysis, mock_garmin_client
):
    """Garmin's cycling parent includes names without 'cycling' or 'biking'."""
    mock_garmin_client.get_activities.return_value = [
        {"activityId": i, "activityType": {"typeKey": key, "parentTypeId": 2}}
        for i, key in enumerate(
            ["virtual_ride", "cyclocross", "e_bike_fitness", "e_bike_mountain"],
            start=1,
        )
    ]
    mock_garmin_client.download_activity.return_value = b"ride"
    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.side_effect = lambda _: _mock_power_fitfile(300)
        result = await app_with_activity_analysis.call_tool("get_power_duration_curve", {})

    data = json.loads(result[0][0].text)
    assert data["activities_analyzed"] == 4
    assert [call.args[0] for call in mock_garmin_client.download_activity.call_args_list] == [1, 2, 3, 4]
    assert data["season_bests"]["5s"]["watts"] == 300


@pytest.mark.asyncio
async def test_power_duration_curve_requests_matching_activities_before_limiting(
    app_with_activity_analysis, mock_garmin_client
):
    """The requested count applies to rides, rather than an all-sport window."""
    from garminconnect import Garmin

    activities = [
        {"activityId": i, "activityType": {"typeKey": key, "parentTypeId": parent}}
        for i, key, parent in [
            (1, "running", 17), (2, "road_biking", 2),
            (3, "running", 17), (4, "road_biking", 2),
        ]
    ]
    # Exercise the installed library's activitytype -> activityType mapping,
    # replacing only the transport so this test never contacts Garmin.
    client = Garmin()
    client.connectapi = Mock(side_effect=lambda url, params: (
        [a for a in activities if a["activityType"]["parentTypeId"] == 2]
        if params.get("activityType") == "cycling" else activities
    )[int(params["start"]):int(params["start"]) + int(params["limit"])])
    mock_garmin_client.get_activities.side_effect = client.get_activities
    mock_garmin_client.download_activity.return_value = b"ride"
    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.side_effect = lambda _: _mock_power_fitfile(300)
        result = await app_with_activity_analysis.call_tool(
            "get_power_duration_curve", {"num_activities": 2, "activity_type": "CYCLING"}
        )

    data = json.loads(result[0][0].text)
    assert data["activities_analyzed"] == 2
    assert [call.args[0] for call in mock_garmin_client.download_activity.call_args_list] == [2, 4]
    client.connectapi.assert_called_once_with(
        client.garmin_connect_activities,
        params={"start": "0", "limit": "2", "activityType": "cycling"},
    )


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


@pytest.mark.parametrize(
    "second_half_hr,expected_sign,expected_interpretation",
    [
        (155, -1, "moderate_drift"),  # ratio 1.429 -> 1.290: -9.7%
        (132, 1, "power_increased_vs_hr"),  # ratio 1.429 -> 1.515: +6.1%
        (125, 1, "power_increased_vs_hr"),  # ratio 1.429 -> 1.600: +12.0%
    ],
)
def test_hr_drift_interpretation_uses_drift_sign(
    second_half_hr, expected_sign, expected_interpretation
):
    """Only negative drift is decoupling; positive drift gets its own label."""
    records = (
        [{"power_w": 200, "heart_rate_bpm": 140}] * 1900
        + [{"power_w": 200, "heart_rate_bpm": second_half_hr}] * 1900
    )
    drift = _compute_hr_drift(records)
    assert drift is not None
    assert drift["hr_drift_pct"] * expected_sign > 5
    assert drift["interpretation"] == expected_interpretation


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


def test_temperature_stats_include_zero_power_and_skip_missing_samples():
    """Coasting contributes 0 W; missing power and 0 bpm dropouts stay excluded."""
    records = [
        {"temperature_c": 10, "power_w": power, "heart_rate_bpm": hr}
        for power, hr in [(0, 0), (200, 140), (None, None)]
    ] + [{"temperature_c": 20}] * 3 + [
        {"temperature_c": 30, "power_w": power, "heart_rate_bpm": hr}
        for power, hr in [(0, 0), (0, 150), (None, None)]
    ]

    stats = _compute_temperature_stats(records)
    assert stats["avg_power_coolest_third_w"] == 100
    assert stats["avg_power_hottest_third_w"] == 0
    assert stats["avg_hr_coolest_third_bpm"] == 140
    assert stats["avg_hr_hottest_third_bpm"] == 150


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


# ---------------------------------------------------------------------------
# Smart recording: timing comes from record timestamps (unit tests)
# ---------------------------------------------------------------------------

_RIDE_START = datetime.datetime(2024, 6, 1, 10, 0, 0)


def _records_every(step_s, rows, start=_RIDE_START):
    """Records step_s seconds apart, timestamped the way _parse_fit emits them."""
    return [
        {**row, "timestamp": str(start + datetime.timedelta(seconds=i * step_s))}
        for i, row in enumerate(rows)
    ]


def test_power_duration_curve_resamples_3s_records_to_1hz():
    """20 min @300 W then 40 min @100 W, one record every 3 s."""
    records = _records_every(3, [{"power_w": 300}] * 400 + [{"power_w": 100}] * 800)
    pdc = _compute_power_duration_curve(records)
    assert pdc["20min"] == 300
    assert pdc["60min"] == 167  # (20 * 300 + 40 * 100) / 60


def test_power_duration_curve_counts_pauses_as_zero():
    """Power is not carried across a pause, and windows cannot skip over it."""
    before = _records_every(1, [{"power_w": 400}] * 60)
    after = _records_every(
        1, [{"power_w": 400}] * 60, start=_RIDE_START + datetime.timedelta(minutes=31)
    )
    pdc = _compute_power_duration_curve(before + after)
    assert pdc["1min"] == 400
    assert pdc["5min"] == 80  # 60 s @400 W + 240 s paused


def test_detect_climbs_uses_timestamps_for_3s_records():
    """Climb duration, VAM and distance use real elapsed time and odometer."""
    climbing = [
        {"grade_pct": 6.0, "power_w": 300, "speed_mps": 5.0,
         "altitude_m": 100 + 0.9 * i, "distance_m": 15.0 * i}
        for i in range(400)
    ]
    flat = [
        {"grade_pct": 0.0, "power_w": 150, "speed_mps": 9.0,
         "altitude_m": 100 + 0.9 * 399, "distance_m": 5985.0 + 27.0 * (i + 1)}
        for i in range(40)
    ]
    climbs = _detect_climbs(_records_every(3, climbing + flat))
    assert len(climbs) == 1
    climb = climbs[0]
    assert climb["duration_s"] == 1200
    assert climb["elevation_gain_m"] == 359.1
    assert climb["vam_m_per_hr"] == 1077  # 359.1 m in 20 min
    assert climb["distance_m"] == 5985


def test_detect_climbs_flat_tolerance_is_in_seconds():
    """A 45 s flat (15 records at 3 s) splits two climbs; the tolerance is 30 s."""
    climb = [{"grade_pct": 6.0, "altitude_m": 100 + 0.9 * i} for i in range(200)]
    flat = [{"grade_pct": 0.0, "altitude_m": 280.0}] * 15
    second = [{"grade_pct": 6.0, "altitude_m": 280 + 0.9 * i} for i in range(200)]
    climbs = _detect_climbs(_records_every(3, climb + flat + second))
    assert [c["duration_s"] for c in climbs] == [600, 600]


def test_detect_climbs_speed_distance_uses_record_spacing():
    """Without an odometer, each speed sample covers its 3 s interval."""
    climbing = [
        {"grade_pct": 6.0, "speed_mps": 5.0, "altitude_m": 100 + 0.9 * i}
        for i in range(400)
    ]
    climbs = _detect_climbs(_records_every(3, climbing))
    assert climbs[0]["distance_m"] == 6000


def test_hr_drift_requires_60_minutes_not_3600_records():
    """70 min recorded every 3 s qualifies; 50 min does not."""
    row = {"power_w": 200, "heart_rate_bpm": 145}
    drift = _compute_hr_drift(_records_every(3, [row] * 1400))
    assert drift is not None
    assert drift["interpretation"] == "well_coupled"
    assert _compute_hr_drift(_records_every(3, [row] * 1000)) is None


def test_grade_analysis_time_uses_record_spacing():
    """time_s per grade bin is elapsed seconds, not a record count."""
    records = _records_every(
        3, [{"grade_pct": 0.5}] * 100 + [{"grade_pct": 4.5}] * 50
    )
    result = _grade_analysis(records)
    assert result["flat"]["time_s"] == 300
    assert result["gentle"]["time_s"] == 150


@pytest.mark.asyncio
async def test_power_duration_curve_tool_uses_record_timestamps(
    app_with_activity_analysis, mock_garmin_client
):
    """get_power_duration_curve resamples each activity's records by timestamp."""
    mock_garmin_client.get_activities.return_value = [
        {"activityId": 2, "startTimeLocal": "2024-06-01 10:00:00",
         "activityType": {"typeKey": "road_biking", "parentTypeId": 2}},
    ]
    mock_garmin_client.download_activity.return_value = b"ride"
    messages = [
        _make_mock_fit_message("record", {
            "power": 300 if i < 400 else 100,
            "timestamp": _RIDE_START + datetime.timedelta(seconds=3 * i),
        })
        for i in range(1200)
    ]

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile(messages)
        result = await app_with_activity_analysis.call_tool("get_power_duration_curve", {})

    data = json.loads(result[0][0].text)
    assert data["season_bests"]["20min"]["watts"] == 300
    assert data["season_bests"]["60min"]["watts"] == 167
    assert data["ftp_estimate_w"] == 285


# ---------------------------------------------------------------------------
# HRV metrics (unit tests for _compute_hrv_metrics)
# ---------------------------------------------------------------------------

def test_compute_hrv_metrics_returns_none_below_minimum():
    """Returns None when fewer than 10 R-R intervals."""
    assert _compute_hrv_metrics([0.6] * 9) is None


def test_compute_hrv_metrics_correct_values():
    """Computes correct RMSSD, SDNN, pNN50, mean_rr for known input.

    20 alternating 600/700 ms R-R intervals (in seconds):
      mean_rr  = 650.0 ms
      RMSSD    = sqrt(10000) = 100.0 ms      (all successive diffs = ±100 ms)
      SDNN     = sqrt(50000/19) ≈ 51.3 ms   (sample SD)
      pNN50    = 100.0 %                     (all |diffs| = 100 ms > 50 ms)
      mean_hr  = round(60000/650, 1) = 92.3 bpm
    """
    rr_s = [0.6, 0.7] * 10
    result = _compute_hrv_metrics(rr_s)
    assert result is not None
    assert result["rmssd_ms"] == 100.0
    assert result["sdnn_ms"] == 51.3
    assert result["pnn50_pct"] == 100.0
    assert result["mean_rr_ms"] == 650.0
    assert result["mean_hr_bpm"] == 92.3
    assert result["rr_count"] == 20


def test_compute_hrv_metrics_pnn50_zero_when_no_large_diffs():
    """pNN50 is 0 when all successive differences are <= 50 ms."""
    rr_s = [0.6] * 20
    result = _compute_hrv_metrics(rr_s)
    assert result is not None
    assert result["pnn50_pct"] == 0.0
    assert result["rmssd_ms"] == 0.0
    assert result["sdnn_ms"] == 0.0


# ---------------------------------------------------------------------------
# HRV from FIT (integration tests through the tool)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fit_no_hrv_messages_produces_no_hrv_key(app_with_activity_analysis, mock_garmin_client):
    """Activities without hrv messages produce no hrv key (backwards compat)."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    record_msg = _make_mock_fit_message("record", {"heart_rate": 140})

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([record_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    data = json.loads(result[0][0].text)
    assert "hrv" not in data


@pytest.mark.asyncio
async def test_fit_hrv_summary_present_with_enough_intervals(app_with_activity_analysis, mock_garmin_client):
    """hrv summary key appears with correct structure when >= 10 valid R-R intervals exist."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    record_msg = _make_mock_fit_message("record", {"timestamp": "2026-05-15 10:00:00"})
    hrv_msg = _make_mock_fit_message("hrv", {"time": [0.6, 0.7] * 10})

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([record_msg, hrv_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    data = json.loads(result[0][0].text)
    assert "hrv" in data
    hrv = data["hrv"]
    assert "rmssd_ms" in hrv
    assert "sdnn_ms" in hrv
    assert "pnn50_pct" in hrv
    assert "mean_rr_ms" in hrv
    assert "rr_count" in hrv
    assert hrv["rr_count"] == 20


@pytest.mark.asyncio
async def test_fit_hrv_sentinel_values_filtered(app_with_activity_analysis, mock_garmin_client):
    """FIT sentinel values (~65.535 s) are excluded from HRV computation."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    record_msg = _make_mock_fit_message("record", {"timestamp": "2026-05-15 10:00:00"})
    hrv_msg = _make_mock_fit_message("hrv", {"time": [0.6] * 20 + [65.535] * 5})

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([record_msg, hrv_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    data = json.loads(result[0][0].text)
    assert "hrv" in data
    assert data["hrv"]["rr_count"] == 20


@pytest.mark.asyncio
async def test_fit_hrv_not_produced_below_minimum_samples(app_with_activity_analysis, mock_garmin_client):
    """No hrv key when fewer than 10 valid R-R intervals are present."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    record_msg = _make_mock_fit_message("record", {"timestamp": "2026-05-15 10:00:00"})
    hrv_msg = _make_mock_fit_message("hrv", {"time": [0.6] * 9})

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([record_msg, hrv_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    data = json.loads(result[0][0].text)
    assert "hrv" not in data


@pytest.mark.asyncio
async def test_fit_hrv_rr_intervals_excluded_by_default(app_with_activity_analysis, mock_garmin_client):
    """Raw rr_intervals_seconds is absent when include_records is not set."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    record_msg = _make_mock_fit_message("record", {"timestamp": "2026-05-15 10:00:00"})
    hrv_msg = _make_mock_fit_message("hrv", {"time": [0.65] * 20})

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([record_msg, hrv_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    data = json.loads(result[0][0].text)
    assert "rr_intervals_seconds" not in data
    assert "hrv" in data  # summary is still present


@pytest.mark.asyncio
async def test_fit_hrv_rr_intervals_included_when_records_requested(app_with_activity_analysis, mock_garmin_client):
    """Raw rr_intervals_seconds appears when include_records=True."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    messages = [
        _make_mock_fit_message("record", {"timestamp": "2026-05-15 10:00:00"}),
        _make_mock_fit_message("hrv", {"time": [0.65] * 20}),
    ]

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        # side_effect returns a fresh iterator on each FitFile() call
        mock_ff = MagicMock()
        mock_ff.get_messages.side_effect = lambda: iter(messages)
        mock_fp.FitFile.return_value = mock_ff
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data",
            {"activity_id": ACTIVITY_ID, "include_records": True}
        )

    data = json.loads(result[0][0].text)
    assert "rr_intervals_seconds" in data
    assert len(data["rr_intervals_seconds"]) == 20
    assert data["rr_intervals_seconds"][0]["rr_seconds"] == 0.65


@pytest.mark.asyncio
async def test_fit_hrv_per_lap_bucketing(app_with_activity_analysis, mock_garmin_client):
    """Per-lap HRV is computed by bucketing R-R pairs into each lap's time window."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    # Record + HRV inside lap 1's window (10:00–10:10)
    record_lap1 = _make_mock_fit_message("record", {"timestamp": "2026-05-15 10:05:00"})
    hrv_lap1 = _make_mock_fit_message("hrv", {"time": [0.6, 0.7] * 10})

    # Record + HRV inside lap 2's window (10:10–10:20)
    record_lap2 = _make_mock_fit_message("record", {"timestamp": "2026-05-15 10:15:00"})
    hrv_lap2 = _make_mock_fit_message("hrv", {"time": [0.6, 0.7] * 10})

    lap1_msg = _make_mock_fit_message("lap", {
        "start_time": "2026-05-15 10:00:00",
        "total_elapsed_time": 600.0,
    })
    lap2_msg = _make_mock_fit_message("lap", {
        "start_time": "2026-05-15 10:10:00",
        "total_elapsed_time": 600.0,
    })

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([
            record_lap1, hrv_lap1,
            record_lap2, hrv_lap2,
            lap1_msg, lap2_msg,
        ])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    data = json.loads(result[0][0].text)
    assert len(data["laps"]) == 2
    assert "hrv" in data["laps"][0], "Lap 1 should have HRV"
    assert "hrv" in data["laps"][1], "Lap 2 should have HRV"
    assert data["laps"][0]["hrv"]["rr_count"] == 20
    assert data["laps"][1]["hrv"]["rr_count"] == 20


@pytest.mark.asyncio
async def test_fit_hrv_lap_below_minimum_gets_no_hrv(app_with_activity_analysis, mock_garmin_client):
    """A lap with fewer than 10 R-R intervals in its window gets no hrv key."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    record_lap1 = _make_mock_fit_message("record", {"timestamp": "2026-05-15 10:05:00"})
    hrv_lap1 = _make_mock_fit_message("hrv", {"time": [0.6, 0.7] * 10})  # 20 → hrv

    record_lap2 = _make_mock_fit_message("record", {"timestamp": "2026-05-15 10:15:00"})
    hrv_lap2 = _make_mock_fit_message("hrv", {"time": [0.6] * 5})  # 5 → no hrv

    lap1_msg = _make_mock_fit_message("lap", {
        "start_time": "2026-05-15 10:00:00",
        "total_elapsed_time": 600.0,
    })
    lap2_msg = _make_mock_fit_message("lap", {
        "start_time": "2026-05-15 10:10:00",
        "total_elapsed_time": 600.0,
    })

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([
            record_lap1, hrv_lap1,
            record_lap2, hrv_lap2,
            lap1_msg, lap2_msg,
        ])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    data = json.loads(result[0][0].text)
    assert len(data["laps"]) == 2
    assert "hrv" in data["laps"][0]
    assert "hrv" not in data["laps"][1]


# ---------------------------------------------------------------------------
# Download config / directory resolution helpers
# ---------------------------------------------------------------------------

def test_resolve_download_dir_prefers_output_dir_arg(monkeypatch, tmp_path):
    monkeypatch.setenv("GARMIN_FIT_DOWNLOAD_DIR", str(tmp_path / "env"))
    monkeypatch.setenv("GARMIN_FIT_CONFIG", str(tmp_path / "fit_config.json"))
    activity_analysis._write_fit_config(str(tmp_path / "cfg"))
    result = activity_analysis._resolve_download_dir(str(tmp_path / "arg"))
    assert result == os.path.abspath(str(tmp_path / "arg"))


def test_resolve_download_dir_uses_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("GARMIN_FIT_DOWNLOAD_DIR", str(tmp_path / "env"))
    result = activity_analysis._resolve_download_dir(None)
    assert result == os.path.abspath(str(tmp_path / "env"))


def test_resolve_download_dir_uses_config_file(monkeypatch, tmp_path):
    monkeypatch.delenv("GARMIN_FIT_DOWNLOAD_DIR", raising=False)
    monkeypatch.setenv("GARMIN_FIT_CONFIG", str(tmp_path / "fit_config.json"))
    activity_analysis._write_fit_config(str(tmp_path / "saved"))
    result = activity_analysis._resolve_download_dir(None)
    assert result == os.path.abspath(str(tmp_path / "saved"))


def test_resolve_download_dir_returns_none_when_unconfigured(monkeypatch, tmp_path):
    monkeypatch.delenv("GARMIN_FIT_DOWNLOAD_DIR", raising=False)
    monkeypatch.setenv("GARMIN_FIT_CONFIG", str(tmp_path / "missing.json"))
    assert activity_analysis._resolve_download_dir(None) is None


def test_read_fit_config_missing_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("GARMIN_FIT_CONFIG", str(tmp_path / "missing.json"))
    assert activity_analysis._read_fit_config() == {}


def test_resolve_download_dir_config_missing_key_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv("GARMIN_FIT_DOWNLOAD_DIR", raising=False)
    cfg = tmp_path / "cfg.json"
    monkeypatch.setenv("GARMIN_FIT_CONFIG", str(cfg))
    cfg.write_text('{"other": "value"}')
    assert activity_analysis._resolve_download_dir(None) is None


def test_read_fit_config_non_dict_returns_empty(monkeypatch, tmp_path):
    cfg = tmp_path / "cfg.json"
    monkeypatch.setenv("GARMIN_FIT_CONFIG", str(cfg))
    cfg.write_text("[1, 2, 3]")
    assert activity_analysis._read_fit_config() == {}


# ---------------------------------------------------------------------------
# set_fit_download_dir tool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_fit_download_dir_persists(app_with_activity_analysis, monkeypatch, tmp_path):
    cfg = tmp_path / "fit_config.json"
    monkeypatch.setenv("GARMIN_FIT_CONFIG", str(cfg))
    target = tmp_path / "downloads"

    result = await app_with_activity_analysis.call_tool(
        "set_fit_download_dir", {"path": str(target)}
    )
    data = json.loads(result[0][0].text)

    assert data["download_dir"] == os.path.abspath(str(target))
    assert os.path.isdir(str(target))  # directory was created
    assert json.loads(cfg.read_text())["download_dir"] == os.path.abspath(str(target))


# ---------------------------------------------------------------------------
# download_activity_file tool
# ---------------------------------------------------------------------------

def _make_fit_zip(fit_bytes: bytes) -> bytes:
    """Build an in-memory ZIP containing a single .fit file (as Garmin returns)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("activity.fit", fit_bytes)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_download_activity_file_fit_saves_file(
    app_with_activity_analysis, mock_garmin_client, tmp_path
):
    from garminconnect import Garmin

    fit_bytes = b"\x0e\x10FITDATA"
    mock_garmin_client.download_activity.return_value = _make_fit_zip(fit_bytes)

    result = await app_with_activity_analysis.call_tool(
        "download_activity_file",
        {"activity_id": ACTIVITY_ID, "output_dir": str(tmp_path)},
    )
    data = json.loads(result[0][0].text)

    expected = tmp_path / f"{ACTIVITY_ID}.fit"
    assert expected.read_bytes() == fit_bytes
    assert data["file_path"] == os.path.abspath(str(expected))
    assert data["format"] == "fit"
    assert data["size_bytes"] == len(fit_bytes)
    mock_garmin_client.download_activity.assert_called_once_with(
        ACTIVITY_ID, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt,enum_attr", [
    ("gpx", "GPX"),
    ("tcx", "TCX"),
    ("csv", "CSV"),
])
async def test_download_activity_file_other_formats_write_raw_bytes(
    app_with_activity_analysis, mock_garmin_client, tmp_path, fmt, enum_attr
):
    from garminconnect import Garmin

    payload = f"<{fmt}>data</{fmt}>".encode()
    mock_garmin_client.download_activity.return_value = payload

    result = await app_with_activity_analysis.call_tool(
        "download_activity_file",
        {"activity_id": ACTIVITY_ID, "format": fmt, "output_dir": str(tmp_path)},
    )
    data = json.loads(result[0][0].text)

    expected = tmp_path / f"{ACTIVITY_ID}.{fmt}"
    assert expected.read_bytes() == payload
    assert data["format"] == fmt
    mock_garmin_client.download_activity.assert_called_once_with(
        ACTIVITY_ID, dl_fmt=getattr(Garmin.ActivityDownloadFormat, enum_attr)
    )


@pytest.mark.asyncio
async def test_download_activity_file_invalid_format(
    app_with_activity_analysis, mock_garmin_client, tmp_path
):
    result = await app_with_activity_analysis.call_tool(
        "download_activity_file",
        {"activity_id": ACTIVITY_ID, "format": "pdf", "output_dir": str(tmp_path)},
    )
    data = json.loads(result[0][0].text)

    assert "Invalid format" in data["error"]
    assert "fit" in data["valid_formats"]
    mock_garmin_client.download_activity.assert_not_called()


@pytest.mark.asyncio
async def test_download_activity_file_needs_setup(
    app_with_activity_analysis, mock_garmin_client, monkeypatch, tmp_path
):
    monkeypatch.delenv("GARMIN_FIT_DOWNLOAD_DIR", raising=False)
    monkeypatch.setenv("GARMIN_FIT_CONFIG", str(tmp_path / "missing.json"))

    result = await app_with_activity_analysis.call_tool(
        "download_activity_file", {"activity_id": ACTIVITY_ID}
    )
    data = json.loads(result[0][0].text)

    assert data["status"] == "needs_setup"
    assert "suggested_default" in data
    mock_garmin_client.download_activity.assert_not_called()


@pytest.mark.asyncio
async def test_download_activity_file_none_response(
    app_with_activity_analysis, mock_garmin_client, tmp_path
):
    mock_garmin_client.download_activity.return_value = None

    result = await app_with_activity_analysis.call_tool(
        "download_activity_file",
        {"activity_id": ACTIVITY_ID, "output_dir": str(tmp_path)},
    )
    text = result[0][0].text

    assert "No fit data returned" in text


@pytest.mark.asyncio
async def test_download_activity_file_fit_extraction_failure(
    app_with_activity_analysis, mock_garmin_client, tmp_path
):
    # A ZIP with no .fit entry makes _extract_fit_bytes raise; the tool should
    # return a debug JSON payload and write nothing.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("notes.txt", b"no fit here")
    mock_garmin_client.download_activity.return_value = buf.getvalue()

    result = await app_with_activity_analysis.call_tool(
        "download_activity_file",
        {"activity_id": ACTIVITY_ID, "output_dir": str(tmp_path)},
    )
    data = json.loads(result[0][0].text)

    assert "error" in data
    assert "first_16_bytes_hex" in data["debug"]
    assert not (tmp_path / f"{ACTIVITY_ID}.fit").exists()


@pytest.mark.asyncio
async def test_get_activity_fit_data_records_read_enhanced_altitude_and_speed(app_with_activity_analysis, mock_garmin_client):
    """Edge units write only enhanced_altitude/enhanced_speed; records must still carry altitude, speed and distance."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    modern = _make_mock_fit_message("record", {
        "timestamp": "2026-10-07 20:15:00",
        "power": 228,
        "enhanced_altitude": 312.4,
        "enhanced_speed": 2.1,
        "distance": 7412.5,
    })
    legacy = _make_mock_fit_message("record", {
        "timestamp": "2026-10-07 20:15:01",
        "altitude": 312.9,
        "speed": 2.0,
    })

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([modern, legacy])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID, "include_records": True}
        )

    records = json.loads(result[0][0].text)["records"]
    assert records[0]["altitude_m"] == 312.4
    assert records[0]["speed_mps"] == 2.1
    assert records[0]["distance_m"] == 7412.5
    assert records[1]["altitude_m"] == 312.9
    assert records[1]["speed_mps"] == 2.0


@pytest.mark.asyncio
async def test_get_activity_fit_data_session_reads_enhanced_speed(app_with_activity_analysis, mock_garmin_client):
    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    session_msg = _make_mock_fit_message("session", {"enhanced_avg_speed": 5.27, "enhanced_max_speed": 15.1})

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([session_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    session = json.loads(result[0][0].text)["session"]
    assert session["avg_speed_mps"] == 5.27
    assert session["max_speed_mps"] == 15.1


# ---------------------------------------------------------------------------
# Grade derived from altitude + distance when the device writes no grade
# ---------------------------------------------------------------------------

def _ride_messages(grade_field: bool, altitude: bool = True, distance: bool = True):
    """1 Hz ride: 60 s flat, 400 s steady 6% climb at 5 m/s, 60 s flat.

    A rear shift is logged mid-climb and another on the closing flat.
    """
    start = datetime.datetime(2024, 3, 2, 14, 0, 0)
    gear_data = (53 << 24) | (2 << 16) | (17 << 8) | 4
    messages = []
    dist = alt = 0.0
    for t in range(520):
        on_climb = 60 <= t < 460
        speed = 5.0 if on_climb else 8.0
        grade = 6.0 if on_climb else 0.0
        dist += speed
        alt += speed * grade / 100
        fields = {
            "timestamp": start + datetime.timedelta(seconds=t),
            "power": 280 if on_climb else 180,
            "cadence": 75 if on_climb else 90,
            "heart_rate": 160 if on_climb else 140,
            "enhanced_speed": speed,
        }
        if altitude:
            fields["enhanced_altitude"] = 100.0 + alt
        if distance:
            fields["distance"] = dist
        if grade_field:
            fields["grade"] = grade
        messages.append(_make_mock_fit_message("record", fields))
        if t in (300, 500):
            messages.append(_make_mock_fit_message("event", {
                "event": "rear_gear_change",
                "gear_change_data": gear_data,
                "timestamp": start + datetime.timedelta(seconds=t),
            }))
    return messages


async def _fit_data_for(app, mock_garmin_client, messages):
    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile(messages)
        result = await app.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )
    return json.loads(result[0][0].text)


@pytest.mark.asyncio
async def test_fit_data_derives_grade_when_device_writes_none(
    app_with_activity_analysis, mock_garmin_client
):
    """No record grade field: climbs, grade bins and shift terrain come from
    altitude / distance instead of silently disappearing."""
    data = await _fit_data_for(
        app_with_activity_analysis, mock_garmin_client,
        _ride_messages(grade_field=False),
    )

    assert data["grade_source"] == "derived"

    assert len(data["climbs"]) == 1
    climb = data["climbs"][0]
    assert climb["avg_grade_pct"] == pytest.approx(6.0, abs=0.5)
    assert climb["elevation_gain_m"] == pytest.approx(120, abs=10)

    bins = data["session"]["grade_analysis"]
    assert "flat" in bins
    climbing_s = (
        bins.get("gentle", {}).get("time_s", 0)
        + bins.get("moderate", {}).get("time_s", 0)
    )
    assert climbing_s > 300

    assert data["shifts"][0]["grade_at_shift_pct"] == pytest.approx(6.0, abs=0.5)
    assert data["shifts"][1]["grade_at_shift_pct"] == pytest.approx(0.0, abs=0.5)
    assert set(data["shift_summary"]["by_terrain"]) >= {"climbing", "flat"}


@pytest.mark.asyncio
async def test_fit_data_keeps_device_grade(app_with_activity_analysis, mock_garmin_client):
    """Device grade present: used as-is and labelled as device grade."""
    data = await _fit_data_for(
        app_with_activity_analysis, mock_garmin_client,
        _ride_messages(grade_field=True),
    )

    assert data["grade_source"] == "device"
    assert data["climbs"][0]["avg_grade_pct"] == 6.0
    assert data["climbs"][0]["max_grade_pct"] == 6.0
    assert data["session"]["grade_analysis"]["moderate"]["time_s"] == 400
    assert data["shifts"][0]["grade_at_shift_pct"] == 6.0
    assert data["shifts"][1]["grade_at_shift_pct"] == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["altitude", "distance"])
async def test_fit_data_no_grade_without_altitude_or_distance(
    app_with_activity_analysis, mock_garmin_client, missing
):
    """Without altitude or distance there is nothing to derive grade from."""
    data = await _fit_data_for(
        app_with_activity_analysis, mock_garmin_client,
        _ride_messages(grade_field=False, **{missing: False}),
    )

    assert "grade_source" not in data
    assert "climbs" not in data
    assert "grade_analysis" not in data["session"]
    assert all("grade_at_shift_pct" not in s for s in data["shifts"])
    assert "by_terrain" not in data["shift_summary"]


# ---------------------------------------------------------------------------
# Wrapped-around negative lap distance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_activity_fit_data_wrapped_lap_distance_is_nulled(app_with_activity_analysis, mock_garmin_client):
    """A small negative uint32 lap distance is nulled and flagged, not reported as ~42,950 km.

    Live values from a fenix 8 treadmill run: a zero-length lap at a lap/stop
    boundary recorded total_distance 0xFFFFFFFC cm (-4 cm), which fitparse
    returns as 42949672.92 m. The zero-length lap before it (0.2 m) is sane.
    """
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    sane_zero_lap = _make_mock_fit_message("lap", {
        "total_elapsed_time": 0.0,
        "total_distance": 0.2,
    })
    wrapped_lap = _make_mock_fit_message("lap", {
        "total_elapsed_time": 0.0,
        "total_distance": 42949672.92,
        "avg_heart_rate": 140,
    })

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([sane_zero_lap, wrapped_lap])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    data = json.loads(result[0][0].text)
    assert len(data["laps"]) == 2
    assert data["laps"][0]["total_distance_m"] == 0.2
    assert "distance_invalid" not in data["laps"][0]
    assert "total_distance_m" not in data["laps"][1]
    assert data["laps"][1]["distance_invalid"] is True
    assert data["laps"][1]["avg_heart_rate_bpm"] == 140


# ---------------------------------------------------------------------------
# W/kg body weight lookup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_activity_fit_data_uses_latest_weigh_in_before_ride(
    app_with_activity_analysis, mock_garmin_client
):
    """W/kg uses the latest weigh-in on or before the ride, not only same-day ones."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    mock_garmin_client.get_body_composition.return_value = {
        "dateWeightList": [
            {"calendarDate": "2024-02-10", "weight": 72000},
            {"calendarDate": "2024-02-27", "weight": 70000},
            # After the ride: must not be used.
            {"calendarDate": "2024-03-05", "weight": 68000},
        ]
    }
    session_msg = _make_mock_fit_message("session", {
        "sport": "cycling",
        "start_time": "2024-03-02 14:00:00",
        "avg_power": 210,
    })

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([session_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    data = json.loads(result[0][0].text)
    mock_garmin_client.get_body_composition.assert_called_once_with("2023-12-03", "2024-03-02")
    assert data["rider_weight_kg"] == 70.0
    assert data["rider_weight_date"] == "2024-02-27"
    assert data["session"]["avg_w_per_kg"] == 3.0


@pytest.mark.asyncio
async def test_get_activity_fit_data_without_weigh_ins_omits_w_per_kg(
    app_with_activity_analysis, mock_garmin_client
):
    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    mock_garmin_client.get_body_composition.return_value = {"dateWeightList": []}
    session_msg = _make_mock_fit_message("session", {
        "sport": "cycling",
        "start_time": "2024-03-02 14:00:00",
        "avg_power": 210,
    })

    with patch("garmin_mcp.activity_analysis.fitparse") as mock_fp:
        mock_fp.FitFile.return_value = _mock_fitfile([session_msg])
        result = await app_with_activity_analysis.call_tool(
            "get_activity_fit_data", {"activity_id": ACTIVITY_ID}
        )

    data = json.loads(result[0][0].text)
    assert "rider_weight_kg" not in data
    assert "avg_w_per_kg" not in data["session"]
