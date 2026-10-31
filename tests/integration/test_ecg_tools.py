"""
Integration tests for the ecg module MCP tools

Response shapes mirror what ghs.garmin.com/ghs/ecg/v1/ecg returned for a
live account with ECG App recordings; ids and values are made up.
"""
import json
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import ecg


def _summary(detail_id, start_local, rhythm="SINUS_NORMAL", hr=56.0, start_ms=1789534226000):
    return {
        "detailId": detail_id,
        "startTime": start_ms,
        "startTimeLocal": start_local,
        "timeZoneOffsetInSeconds": 7200,
        "rhythmClassification": rhythm,
        "mountingSide": "LEFT",
        "rmssdHrv": 44,
        "heartRateAverage": hr,
        "ecgAppVersion": "1.1.4",
        "udi": "(01)00860009117716(10)1.1.4",
        "symptoms": [],
        "deviceInfo": {
            "productName": "EPIX_GEN2_PRO_47",
            "productId": 4313,
            "firmwareVersion": "27.18",
            "partNumber": "006-B4313-00",
        },
        "healthDeviceId": "1000001",
    }


MOCK_ECG_DETAIL = {
    "summary": _summary("00000000-0000-4000-8000-000000000001", "2026-09-16T06:50:26.0", "AFIB", 97.0),
    "reading": {
        "sampleRate": 128.0,
        "rawSampleRate": 512.0,
        "durationInSeconds": 30,
        "samples": [-0.026499808, 0.0045911074, -0.5929708, 0.17407411],
        "leadType": "Lead I",
    },
    "processingInfo": {"uploadTime": 1789534294115, "apiVersion": "25.16.0"},
}


def _response(status=200, payload=None):
    resp = Mock()
    resp.status_code = status
    resp.content = b"" if payload is None else json.dumps(payload).encode()
    resp.json.return_value = payload
    return resp


@pytest.fixture
def ecg_client(mock_garmin_client):
    inner = Mock()
    inner.domain = "garmin.com"
    inner.is_authenticated = True
    inner._token_expires_soon.return_value = False
    inner.get_api_headers.return_value = {"Authorization": "Bearer x"}
    inner.session = Mock()
    inner._fresh_api_session.return_value = inner.session
    mock_garmin_client.client = inner
    return inner


@pytest.fixture
def app_with_ecg(mock_garmin_client, ecg_client):
    ecg.configure(mock_garmin_client)
    app = FastMCP("Test ECG")
    return ecg.register_tools(app)


def _text(result):
    content = result[0] if isinstance(result, tuple) else result
    return content[0].text


@pytest.mark.asyncio
async def test_get_ecg_recordings_curates_and_targets_ghs(app_with_ecg, ecg_client):
    ecg_client.session.get.return_value = _response(
        payload={
            "content": [
                _summary("b2", "2026-10-28T07:29:50.0", "INCONCLUSIVE_HR_UNDER_50", 49.0, 1793168990000),
                _summary("a1", "2026-10-31T07:26:03.0", "AFIB", 99.0, 1793427963000),
            ],
            "transformErrors": [],
        }
    )

    text = _text(await app_with_ecg.call_tool(
        "get_ecg_recordings", {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    ))
    data = json.loads(text)

    url = ecg_client.session.get.call_args.args[0]
    params = ecg_client.session.get.call_args.kwargs["params"]
    assert url == "https://ghs.garmin.com/ghs/ecg/v1/ecg/daily"
    assert params == {"firstDay": "2026-10-01", "lastDay": "2026-10-31"}
    assert data["count"] == 2
    first = data["recordings"][0]  # sorted newest first
    assert first["ecg_id"] == "a1"
    assert first["rhythm_classification"] == "AFIB"
    assert first["heart_rate_average_bpm"] == 99.0
    assert first["device"] == "EPIX_GEN2_PRO_47"
    assert "udi" not in first and "truncated" not in data


@pytest.mark.asyncio
async def test_get_ecg_recordings_splits_long_ranges_and_truncates(app_with_ecg, ecg_client):
    """Long ranges are read in windows; duplicates merge; newest first; limit applies."""
    older = {"content": [_summary(f"o{i}", "2026-03-01T07:00:00.0", start_ms=1772000000000 + i) for i in range(3)]}
    newer = {"content": [
        _summary("o0", "2026-03-01T07:00:00.0", start_ms=1772000000000),
        _summary("n0", "2026-06-01T07:00:00.0", start_ms=1780000000000),
    ]}
    ecg_client.session.get.side_effect = [_response(payload=older), _response(payload=newer)]

    data = json.loads(_text(await app_with_ecg.call_tool(
        "get_ecg_recordings", {"start_date": "2026-03-01", "end_date": "2026-06-30", "limit": 3}
    )))

    windows = [c.kwargs["params"] for c in ecg_client.session.get.call_args_list]
    assert windows == [
        {"firstDay": "2026-03-01", "lastDay": "2026-05-31"},
        {"firstDay": "2026-06-01", "lastDay": "2026-06-30"},
    ]
    assert data["total_in_range"] == 4
    assert [r["ecg_id"] for r in data["recordings"]] == ["n0", "o2", "o1"]
    assert data["truncated"] is True


@pytest.mark.asyncio
async def test_get_ecg_recordings_empty(app_with_ecg, ecg_client):
    ecg_client.session.get.return_value = _response(payload={"content": [], "transformErrors": []})
    text = _text(await app_with_ecg.call_tool(
        "get_ecg_recordings", {"start_date": "2026-10-01", "end_date": "2026-10-02"}
    ))
    assert text.startswith("No ECG recordings found")


@pytest.mark.asyncio
async def test_get_ecg_recordings_rejects_bad_dates(app_with_ecg, ecg_client):
    text = _text(await app_with_ecg.call_tool(
        "get_ecg_recordings", {"start_date": "2026-10-05", "end_date": "2026-10-01"}
    ))
    assert text.startswith("Error")
    text = _text(await app_with_ecg.call_tool("get_ecg_recordings", {"start_date": "10/05/2026"}))
    assert "YYYY-MM-DD" in text
    ecg_client.session.get.assert_not_called()


@pytest.mark.asyncio
async def test_get_ecg_recordings_retries_once_on_401(app_with_ecg, ecg_client):
    ecg_client.session.get.side_effect = [
        _response(status=401),
        _response(payload={"content": [_summary("a1", "2026-10-31T07:26:03.0")]}),
    ]
    data = json.loads(_text(await app_with_ecg.call_tool(
        "get_ecg_recordings", {"start_date": "2026-10-31", "end_date": "2026-10-31"}
    )))
    ecg_client._refresh_session.assert_called_once()
    assert data["count"] == 1


@pytest.mark.asyncio
async def test_get_ecg_recordings_404_explains_missing_feature(app_with_ecg, ecg_client):
    ecg_client.session.get.return_value = _response(status=404)
    text = _text(await app_with_ecg.call_tool(
        "get_ecg_recordings", {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    ))
    assert text.startswith("Error retrieving ECG recordings")
    assert "ECG feature" in text


@pytest.mark.asyncio
async def test_get_ecg_recording_without_samples(app_with_ecg, ecg_client):
    ecg_client.session.get.return_value = _response(payload=MOCK_ECG_DETAIL)

    data = json.loads(_text(await app_with_ecg.call_tool(
        "get_ecg_recording", {"ecg_id": "00000000-0000-4000-8000-000000000001"}
    )))

    url = ecg_client.session.get.call_args.args[0]
    assert url == "https://ghs.garmin.com/ghs/ecg/v1/ecg/00000000-0000-4000-8000-000000000001"
    assert data["rhythm_classification"] == "AFIB"
    assert data["reading"] == {
        "lead_type": "Lead I",
        "sample_rate_hz": 128.0,
        "raw_sample_rate_hz": 512.0,
        "duration_seconds": 30,
        "sample_count": 4,
        "amplitude_min_mv": -0.593,
        "amplitude_max_mv": 0.1741,
    }


@pytest.mark.asyncio
async def test_get_ecg_recording_with_samples(app_with_ecg, ecg_client):
    ecg_client.session.get.return_value = _response(payload=MOCK_ECG_DETAIL)
    data = json.loads(_text(await app_with_ecg.call_tool(
        "get_ecg_recording", {"ecg_id": "abc-123", "include_samples": True}
    )))
    assert data["reading"]["samples_mv"] == [-0.0265, 0.0046, -0.593, 0.1741]


@pytest.mark.asyncio
async def test_get_ecg_recording_rejects_path_like_id(app_with_ecg, ecg_client):
    text = _text(await app_with_ecg.call_tool("get_ecg_recording", {"ecg_id": "../list"}))
    assert text.startswith("Error")
    ecg_client.session.get.assert_not_called()


@pytest.mark.asyncio
async def test_get_ecg_recording_server_error(app_with_ecg, ecg_client):
    ecg_client.session.get.return_value = _response(status=500)
    text = _text(await app_with_ecg.call_tool("get_ecg_recording", {"ecg_id": "abc"}))
    assert text.startswith("Error retrieving ECG recording")
    assert "500" in text
