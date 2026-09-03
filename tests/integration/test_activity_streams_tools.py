from datetime import datetime, timedelta, timezone

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import activity_streams


@pytest.fixture
def stream_timeline():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return activity_streams.build_timeline(
        [
            {
                "timestamp": start + timedelta(seconds=second),
                "hr": 140 + second,
                "power": 200,
            }
            for second in range(10)
        ],
        session={"start_time": start, "total_elapsed_time": 10, "sport": "cycling"},
    )


@pytest.fixture
def activity_streams_app(monkeypatch, stream_timeline):
    monkeypatch.setattr(
        activity_streams,
        "_download_activity_timeline",
        lambda activity_id: stream_timeline,
    )
    app = FastMCP("Activity Streams Test")
    activity_streams.register_tools(app)
    return app


@pytest.mark.asyncio
async def test_stream_tool_has_structured_output_and_pagination(activity_streams_app):
    tools = {tool.name: tool for tool in await activity_streams_app.list_tools()}
    assert tools["get_activity_streams"].outputSchema is not None
    assert tools["get_activity_streams"].annotations.readOnlyHint is True
    assert tools["analyze_decoupling"].annotations.readOnlyHint is True
    assert tools["reslice_zones"].annotations.readOnlyHint is True
    assert tools["polarization_audit"].annotations.readOnlyHint is True

    _, structured = await activity_streams_app.call_tool(
        "get_activity_streams",
        {
            "activity_id": 123,
            "fields": ["hr", "power"],
            "resolution": "raw",
            "page_size": 3,
        },
    )

    result = structured
    assert result["status"] == "ok"
    assert len(result["data"]) == 3
    assert result["pagination"]["total_points"] == 10
    assert result["pagination"]["next_cursor"]


@pytest.mark.asyncio
async def test_stream_tool_raw_active_filters_paused_records(
    activity_streams_app, monkeypatch
):
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    timeline = activity_streams.build_timeline(
        [
            {"timestamp": start + timedelta(seconds=0), "hr": 120},
            {"timestamp": start + timedelta(seconds=1), "hr": 121},
            {"timestamp": start + timedelta(seconds=2), "hr": 122},
            {"timestamp": start + timedelta(seconds=3), "hr": 123},
            {"timestamp": start + timedelta(seconds=5), "hr": 125},
            {"timestamp": start + timedelta(seconds=6), "hr": 126},
        ],
        timer_events=[
            {
                "timestamp": start + timedelta(seconds=2),
                "event": "timer",
                "event_type": "stop_all",
            },
            {
                "timestamp": start + timedelta(seconds=5),
                "event": "timer",
                "event_type": "start",
            },
        ],
        session={"start_time": start, "total_elapsed_time": 7},
    )
    monkeypatch.setattr(
        activity_streams, "_download_activity_timeline", lambda activity_id: timeline
    )

    _, active_structured = await activity_streams_app.call_tool(
        "get_activity_streams",
        {
            "activity_id": 123,
            "fields": ["hr"],
            "resolution": "raw",
            "time_basis": "active",
        },
    )
    _, elapsed_structured = await activity_streams_app.call_tool(
        "get_activity_streams",
        {
            "activity_id": 123,
            "fields": ["hr"],
            "resolution": "raw",
            "time_basis": "elapsed",
        },
    )

    active = active_structured
    elapsed = elapsed_structured
    assert [row["hr"] for row in active["data"]] == [120, 121, 125, 126]
    assert [row["hr"] for row in elapsed["data"]] == [120, 121, 122, 123, 125, 126]
    assert active["pagination"]["total_points"] == 4
    assert elapsed["pagination"]["total_points"] == 6


@pytest.mark.asyncio
async def test_reslice_tool_returns_structured_validation_error(activity_streams_app):
    _, structured = await activity_streams_app.call_tool(
        "reslice_zones",
        {
            "activity_id": 123,
            "model": {
                "sport": "cycling",
                "metric": "hr",
                "zones": [
                    {"name": "z1", "lower_inclusive": 0, "upper_exclusive": 150},
                    {"name": "z2", "lower_inclusive": 151, "upper_exclusive": None},
                ],
            },
        },
    )

    assert structured["status"] == "error"
    assert structured["error"]["code"] == "invalid_zone_model"
