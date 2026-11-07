"""MCP registration + actual fitparse decoding; Garmin transport alone is mocked."""

import gzip
import io
import json
import zipfile

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import activity_analysis, activity_timeline
from tests.fixtures.synthetic_fit import activity_fit


@pytest.fixture
def stream_app(mock_garmin_client):
    activity_analysis.configure(mock_garmin_client)
    mock_garmin_client.download_activity.return_value = activity_fit()
    return activity_analysis.register_tools(FastMCP("Resampled stream"))


async def invoke(app, **kwargs):
    result = await app.call_tool(
        "get_activity_resampled_stream", {"activity_id": 42, **kwargs}
    )
    return json.loads(result[0][0].text)


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapper", ["raw", "gzip", "zip"])
async def test_real_fit_decode_through_mcp(stream_app, mock_garmin_client, wrapper):
    from garminconnect import Garmin

    payload = activity_fit(duration=5)
    if wrapper == "gzip":
        payload = gzip.compress(payload)
    elif wrapper == "zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("synthetic.fit", payload)
        payload = buf.getvalue()
    mock_garmin_client.download_activity.return_value = payload

    result = await invoke(stream_app, fields=["hr", "power"], resolution="5s")

    assert result["status"] == "ok"
    assert result["sport"] == "cycling"
    assert result["summary"]["total_active_s"] == 5
    assert result["data"][0]["hr"] == 116
    assert result["data"][0]["power"] == 180
    assert len(result["source_sha256"]) == 64
    mock_garmin_client.download_activity.assert_called_once_with(
        42, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL
    )
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_source_digest_guards_pagination(stream_app, mock_garmin_client):
    first = await invoke(stream_app, resolution="5s", page_size=1)
    assert first["pagination"]["has_more"] is True
    second = await invoke(
        stream_app,
        resolution="5s",
        page_size=1,
        offset=first["pagination"]["next_offset"],
        expected_source_sha256=first["source_sha256"],
    )
    assert second["data"][0]["offset_s"] == 5
    mock_garmin_client.download_activity.return_value = activity_fit(duration=15)
    changed = await invoke(
        stream_app,
        resolution="5s",
        offset=1,
        expected_source_sha256=first["source_sha256"],
    )
    assert changed["status"] == "error"
    assert changed["error"]["code"] == "source_changed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"activity_id": 0},
        {"resolution": "raw"},
        {"fields": ["location"]},
        {"page_size": 501},
        {"offset": -1},
        {"expected_source_sha256": "bad"},
    ],
)
async def test_invalid_input_never_downloads(stream_app, mock_garmin_client, kwargs):
    result = await invoke(stream_app, **kwargs)
    assert result["status"] == "error"
    mock_garmin_client.download_activity.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"", b"not a FIT file", activity_fit(sessions=2)])
async def test_invalid_or_multisession_fit_is_honestly_refused(
    stream_app, mock_garmin_client, payload
):
    mock_garmin_client.download_activity.return_value = payload
    result = await invoke(stream_app)
    assert result["status"] == "error"
    assert "data" not in result


@pytest.mark.asyncio
async def test_download_failure_is_structured(stream_app, mock_garmin_client):
    mock_garmin_client.download_activity.side_effect = RuntimeError("Unavailable")
    result = await invoke(stream_app)
    assert result["status"] == "error"
    assert "Unavailable" in result["error"]["message"]


@pytest.mark.asyncio
async def test_decode_limit_is_preserved(stream_app, mock_garmin_client, monkeypatch):
    monkeypatch.setattr(activity_timeline, "MAX_RECORDS", 2)
    result = await invoke(stream_app)
    assert result["status"] == "error"
    assert result["error"]["code"] == "input_limit"


@pytest.mark.asyncio
async def test_registration_and_activity_profile(stream_app):
    from garmin_mcp.tool_profiles import ACTIVITIES_TOOLS, MINIMAL_TOOLS

    tools = {tool.name: tool for tool in await stream_app.list_tools()}
    assert "get_activity_resampled_stream" in tools
    assert "get_activity_resampled_stream" in ACTIVITIES_TOOLS
    assert "get_activity_resampled_stream" not in MINIMAL_TOOLS
    assert "get_activity_fit_messages" in tools


@pytest.mark.asyncio
async def test_real_timer_events_are_honored(stream_app, mock_garmin_client):
    mock_garmin_client.download_activity.return_value = activity_fit(
        records=[(0, 100, 100), (2, 250, 250), (4, 250, 250), (7, 120, 200)],
        duration=10,
        events=[(2, 4), (7, 0)],  # FIT stop_all / start
    )
    result = await invoke(stream_app, fields=["hr"], resolution="5s")
    assert result["status"] == "ok"
    assert result["summary"]["total_active_s"] == 5
    assert result["summary"]["pause_count"] == 1
    assert result["data"][0]["hr"] == 112


@pytest.mark.asyncio
async def test_exact_record_limit_is_accepted(stream_app, monkeypatch):
    monkeypatch.setattr(activity_timeline, "MAX_RECORDS", 3)
    result = await invoke(stream_app)
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_timer_event_limit(stream_app, mock_garmin_client, monkeypatch):
    monkeypatch.setattr(activity_timeline, "MAX_EVENTS", 1)
    mock_garmin_client.download_activity.return_value = activity_fit(
        events=[(2, 4), (7, 0)]
    )
    result = await invoke(stream_app)
    assert result["error"]["code"] == "input_limit"
