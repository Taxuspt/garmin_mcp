"""
ECG recordings from the Garmin ECG App for Garmin Connect MCP Server

The ECG service is not on connectapi.<domain> but on ghs.<domain>
(``/ghs/ecg/v1/ecg``), the host the Garmin Connect web app calls.
garminconnect 0.3.2 has no ECG methods and its ``connectapi()`` always
prefixes the connectapi host, so the request is sent here with the
library's own authenticated session and headers.

Verified against a live account (2026-10): ``/daily?firstDay&lastDay``
returns every recording in the range unpaged; ``/{detailId}`` returns the
summary plus the 128 Hz Lead I waveform.
"""
import datetime
import json
import re
from typing import Any, Dict, List, Optional

# The garmin_client will be set by the main file
garmin_client = None

_ECG_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
_WINDOW_DAYS = 92
_MAX_LIMIT = 200
_REQUEST_TIMEOUT = 30


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


def _validate_date(value: str, field: str) -> str:
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a date in YYYY-MM-DD format, got {value!r}")


def _ecg_base_url(client) -> str:
    domain = getattr(client, "domain", None) or "garmin.com"
    return f"https://ghs.{domain}/ghs/ecg/v1/ecg"


def _ghs_get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """GET a path below the ECG service with the library's auth headers."""
    client = garmin_client.client
    if client.is_authenticated and client._token_expires_soon():
        client._refresh_session()
    url = f"{_ecg_base_url(client)}/{path.lstrip('/')}"
    session = client._fresh_api_session()

    response = session.get(
        url, headers=client.get_api_headers(), params=params, timeout=_REQUEST_TIMEOUT
    )
    if response.status_code == 401:
        client._refresh_session()
        response = session.get(
            url, headers=client.get_api_headers(), params=params, timeout=_REQUEST_TIMEOUT
        )

    if response.status_code == 404:
        raise LookupError(
            "Garmin ECG service returned 404: the recording does not exist, or the "
            "account has no ECG feature (ECG App on a supported watch)"
        )
    if response.status_code == 429:
        raise RuntimeError("Garmin rate limit hit; wait a few minutes before retrying")
    if response.status_code >= 400:
        raise RuntimeError(f"Garmin ECG service returned HTTP {response.status_code}")
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def _curate_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    device = item.get("deviceInfo") or {}
    summary = {
        "ecg_id": item.get("detailId"),
        "start_time_local": item.get("startTimeLocal"),
        "start_time_gmt_ms": item.get("startTime"),
        "timezone_offset_seconds": item.get("timeZoneOffsetInSeconds"),
        "rhythm_classification": item.get("rhythmClassification"),
        "heart_rate_average_bpm": item.get("heartRateAverage"),
        "rmssd_hrv_ms": item.get("rmssdHrv"),
        "symptoms": item.get("symptoms"),
        "mounting_side": item.get("mountingSide"),
        "device": device.get("productName"),
        "device_firmware": device.get("firmwareVersion"),
        "ecg_app_version": item.get("ecgAppVersion"),
    }
    return {k: v for k, v in summary.items() if v is not None}


def register_tools(app):
    """Register all ECG tools with the MCP server app"""

    @app.tool()
    async def get_ecg_recordings(
        start_date: str, end_date: Optional[str] = None, limit: int = 50
    ) -> str:
        """List ECG App recordings (rhythm result, average HR) in a date range

        Returns one entry per 30-second ECG recording taken with the Garmin
        ECG App, newest first: the device's rhythm classification (e.g.
        SINUS_NORMAL, AFIB, INCONCLUSIVE_HR_UNDER_50), average heart rate,
        RMSSD HRV, logged symptoms and device. Pass an ecg_id to
        get_ecg_recording for the waveform. The classification is the
        device's own result, not a medical diagnosis.

        Args:
            start_date: First day in YYYY-MM-DD format
            end_date: Last day in YYYY-MM-DD format (default: today)
            limit: Maximum number of recordings to return (1-200, default 50)
        """
        try:
            start = _validate_date(start_date, "start_date")
            end = _validate_date(end_date, "end_date") if end_date else datetime.date.today().isoformat()
            if start > end:
                return "Error: start_date must not be after end_date"
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_LIMIT:
                return f"Error: limit must be an integer between 1 and {_MAX_LIMIT}"

            # The list endpoint's "next" token is not accepted back as a
            # query parameter (it returns page one again), so recordings are
            # read from the daily endpoint, which is unpaged, in windows.
            by_id: Dict[str, Dict[str, Any]] = {}
            window_start = datetime.date.fromisoformat(start)
            last_day = datetime.date.fromisoformat(end)
            while window_start <= last_day:
                window_end = min(window_start + datetime.timedelta(days=_WINDOW_DAYS - 1), last_day)
                data = _ghs_get(
                    "daily",
                    {"firstDay": window_start.isoformat(), "lastDay": window_end.isoformat()},
                ) or {}
                for item in data.get("content") or []:
                    if isinstance(item, dict) and item.get("detailId"):
                        by_id[item["detailId"]] = _curate_summary(item)
                window_start = window_end + datetime.timedelta(days=1)

            recordings = sorted(
                by_id.values(), key=lambda r: r.get("start_time_gmt_ms") or 0, reverse=True
            )
            has_more = len(recordings) > limit

            if not recordings:
                return f"No ECG recordings found between {start} and {end}"

            result = {
                "start_date": start,
                "end_date": end,
                "count": min(len(recordings), limit),
                "total_in_range": len(recordings),
                "recordings": recordings[:limit],
            }
            if has_more:
                result["truncated"] = True
                result["note"] = "Only the newest recordings are listed; raise limit or narrow the date range."
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error retrieving ECG recordings: {str(e)}"

    @app.tool()
    async def get_ecg_recording(ecg_id: str, include_samples: bool = False) -> str:
        """Get one ECG App recording: result, recording parameters, optional waveform

        Returns the recording's summary (rhythm classification, average HR,
        RMSSD HRV, symptoms, device) and the reading's parameters (lead type,
        sample rate, duration, sample count, amplitude range). With
        include_samples=true the full single-lead waveform is included as
        Garmin stores it (128 Hz x 30 s = 3840 values, in mV), which is large;
        leave it off unless the waveform itself is needed.

        Args:
            ecg_id: Recording id (ecg_id from get_ecg_recordings)
            include_samples: Include the raw waveform samples (default false)
        """
        try:
            if not isinstance(ecg_id, str) or not _ECG_ID_RE.fullmatch(ecg_id):
                return "Error: ecg_id must be an alphanumeric identifier from get_ecg_recordings"

            data = _ghs_get(ecg_id)
            if not data:
                return f"No ECG recording found with id {ecg_id}"

            result = _curate_summary(data.get("summary") or {})
            reading = data.get("reading") or {}
            samples = reading.get("samples") or []
            numeric = [s for s in samples if isinstance(s, (int, float)) and not isinstance(s, bool)]
            result["reading"] = {
                k: v
                for k, v in {
                    "lead_type": reading.get("leadType"),
                    "sample_rate_hz": reading.get("sampleRate"),
                    "raw_sample_rate_hz": reading.get("rawSampleRate"),
                    "duration_seconds": reading.get("durationInSeconds"),
                    "sample_count": len(samples),
                    "amplitude_min_mv": round(min(numeric), 4) if numeric else None,
                    "amplitude_max_mv": round(max(numeric), 4) if numeric else None,
                }.items()
                if v is not None
            }
            if include_samples:
                result["reading"]["samples_mv"] = [
                    round(s, 4) if isinstance(s, float) else s for s in samples
                ]
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error retrieving ECG recording: {str(e)}"

    return app
