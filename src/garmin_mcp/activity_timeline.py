"""Pause-aware, time-weighted FIT bins, extracted from PR #295.

Read-only numeric streams only: no raw-message presentation, physiology store,
coaching, profile mutation, or new dependencies. Legacy analyses are unchanged.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import math
import statistics
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from typing import Any

import fitparse

MAX_FIT_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 100_000
MAX_EVENTS = 1000
MAX_DURATION_S = 7 * 86400
MAX_INTERPOLATION_GAP_S = 5.0
SUPPORTED_RESOLUTIONS = {"5s": 5.0, "30s": 30.0, "60s": 60.0}
FIELD_ALIASES = {
    "hr": ("hr", "heart_rate", "heart_rate_bpm"),
    "power": ("power", "power_w"),
    "speed": ("speed", "enhanced_speed", "speed_mps"),
    "cadence": ("cadence", "cadence_rpm"),
    "altitude": ("altitude", "enhanced_altitude", "altitude_m"),
    "temperature": ("temperature", "temperature_c"),
    "grade": ("grade", "grade_pct"),
    "distance": ("distance", "distance_m"),
}
TAIL_FIELDS = {"altitude", "distance"}


class ActivityStreamsError(ValueError):
    """A stable, user-facing activity streams error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValueSegment:
    """An active interval for which the source record is valid."""

    elapsed_start_s: float
    elapsed_end_s: float
    active_start_s: float
    active_end_s: float
    values: dict[str, float | None]
    source_timestamp: datetime

    @property
    def duration_s(self) -> float:
        return self.active_end_s - self.active_start_s


@dataclass(frozen=True)
class MissingSegment:
    """An active interval with no usable source record."""

    elapsed_start_s: float
    elapsed_end_s: float
    active_start_s: float
    active_end_s: float
    reason: str

    @property
    def duration_s(self) -> float:
        return self.active_end_s - self.active_start_s


@dataclass
class CanonicalTimeline:
    """Pause-aware, time-weighted representation of an activity."""

    start_timestamp: datetime
    end_timestamp: datetime
    sport: str | None
    samples: list[dict[str, Any]]
    value_segments: list[ValueSegment]
    missing_segments: list[MissingSegment]
    pause_intervals: list[tuple[float, float]]
    total_elapsed_s: float
    total_active_s: float
    dropped_records: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


def _as_datetime(value: Any) -> datetime | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        number = float(value)
        if number > 100_000_000_000:
            number /= 1000.0
        try:
            parsed = datetime.fromtimestamp(number, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _value_from_aliases(record: Mapping[str, Any], public_name: str) -> float | None:
    for alias in FIELD_ALIASES[public_name]:
        if alias in record and record[alias] is not None:
            return _number(record[alias])
    return None


def _normalize_fields(fields: Sequence[str]) -> list[str]:
    if isinstance(fields, str):
        fields = [fields]
    normalized: list[str] = []
    reverse_aliases = {
        alias: public for public, aliases in FIELD_ALIASES.items() for alias in aliases
    }
    for raw in fields:
        name = reverse_aliases.get(str(raw).strip().lower(), str(raw).strip().lower())
        if name not in FIELD_ALIASES:
            raise ActivityStreamsError(
                "unsupported_field",
                f"Unsupported stream field {raw!r}; choose from {sorted(FIELD_ALIASES)}",
            )
        if name not in normalized:
            normalized.append(name)
    if not normalized:
        raise ActivityStreamsError(
            "empty_fields", "At least one stream field is required"
        )
    return normalized


def _message_value(message: Any, *names: str) -> Any:
    for name in names:
        try:
            value = message.get_value(name)
        except (KeyError, AttributeError, TypeError):
            continue
        if value is not None:
            return value
    return None


def _timer_action(event_type: Any) -> str | None:
    value = str(event_type or "").lower()
    if value in {"start", "start_all", "begin_deprecated"}:
        return "start"
    if value.startswith("stop") or value in {"end", "end_all"}:
        return "stop"
    return None


def _build_pause_intervals(
    events: Sequence[Mapping[str, Any]], start: datetime, end: datetime
) -> list[tuple[float, float]]:
    relevant: list[tuple[datetime, str]] = []
    for event in events:
        event_name = str(event.get("event") or "timer").lower()
        if event_name != "timer":
            continue
        timestamp = _as_datetime(event.get("timestamp"))
        action = _timer_action(event.get("event_type"))
        if timestamp is not None and action is not None and start <= timestamp <= end:
            relevant.append((timestamp, action))
    relevant.sort(key=lambda item: item[0])

    pauses: list[tuple[float, float]] = []
    pause_start: float | None = None
    for timestamp, action in relevant:
        offset = min(
            max((timestamp - start).total_seconds(), 0.0), (end - start).total_seconds()
        )
        if action == "stop" and pause_start is None:
            pause_start = offset
        elif action == "start" and pause_start is not None:
            if offset > pause_start:
                pauses.append((pause_start, offset))
            pause_start = None
    if pause_start is not None and pause_start < (end - start).total_seconds():
        pauses.append((pause_start, (end - start).total_seconds()))

    # Defensive merge for duplicate or slightly out-of-order timer events.
    merged: list[tuple[float, float]] = []
    for pause_start, pause_end in sorted(pauses):
        if merged and pause_start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], pause_end))
        else:
            merged.append((pause_start, pause_end))
    return merged


def _pause_overlap(
    start_s: float, end_s: float, pauses: Sequence[tuple[float, float]]
) -> float:
    return sum(
        max(0.0, min(end_s, p_end) - max(start_s, p_start)) for p_start, p_end in pauses
    )


def _active_at(elapsed_s: float, pauses: Sequence[tuple[float, float]]) -> float:
    return elapsed_s - _pause_overlap(0.0, elapsed_s, pauses)


def _active_parts(
    start_s: float, end_s: float, pauses: Sequence[tuple[float, float]]
) -> list[tuple[float, float]]:
    if end_s <= start_s:
        return []
    cursor = start_s
    parts: list[tuple[float, float]] = []
    for pause_start, pause_end in pauses:
        if pause_end <= cursor:
            continue
        if pause_start >= end_s:
            break
        if pause_start > cursor:
            parts.append((cursor, min(pause_start, end_s)))
        cursor = max(cursor, pause_end)
        if cursor >= end_s:
            break
    if cursor < end_s:
        parts.append((cursor, end_s))
    return [(a, b) for a, b in parts if b - a > 1e-9]


def build_timeline(
    records: Sequence[Mapping[str, Any]],
    timer_events: Sequence[Mapping[str, Any]] | None = None,
    session: Mapping[str, Any] | None = None,
    max_gap_s: float = MAX_INTERPOLATION_GAP_S,
) -> CanonicalTimeline:
    """Build a canonical timeline from provider-neutral records and FIT events.

    A source value is carried forward only until the next record when that gap
    is at most ``max_gap_s``.  Across larger gaps only one nominal sample period
    is credited; the remaining active time is explicitly represented as
    missing.  Paused time never contributes to active duration or coverage.
    """

    if not math.isfinite(max_gap_s) or max_gap_s <= 0:
        raise ActivityStreamsError("invalid_gap", "max_gap_s must be positive")
    if len(records) > MAX_RECORDS or len(timer_events or []) > MAX_EVENTS:
        raise ActivityStreamsError(
            "input_limit", "FIT record/event count exceeds processing limit"
        )
    timer_events = timer_events or []
    session = dict(session or {})

    normalized: list[tuple[datetime, dict[str, float | None]]] = []
    dropped = 0
    for record in records:
        timestamp = _as_datetime(record.get("timestamp"))
        if timestamp is None:
            dropped += 1
            continue
        values = {name: _value_from_aliases(record, name) for name in FIELD_ALIASES}
        normalized.append((timestamp, values))
    if not normalized:
        raise ActivityStreamsError(
            "no_timestamped_records", "FIT activity has no timestamped records"
        )
    normalized.sort(key=lambda item: item[0])

    # Merge duplicate timestamps without discarding complementary fields.
    deduped: list[tuple[datetime, dict[str, float | None]]] = []
    for timestamp, values in normalized:
        if deduped and timestamp == deduped[-1][0]:
            merged = dict(deduped[-1][1])
            merged.update(
                {key: value for key, value in values.items() if value is not None}
            )
            deduped[-1] = (timestamp, merged)
        else:
            deduped.append((timestamp, values))

    diffs = [
        (right[0] - left[0]).total_seconds()
        for left, right in pairwise(deduped)
        if 0 < (right[0] - left[0]).total_seconds() <= max_gap_s
    ]
    nominal_period_s = statistics.median(diffs) if diffs else 1.0
    nominal_period_s = min(max(nominal_period_s, 0.001), max_gap_s)

    first_record = deduped[0][0]
    last_record = deduped[-1][0]
    session_start = _as_datetime(
        session.get("start_time") or session.get("start_timestamp")
    )
    start = (
        min(session_start, first_record) if session_start is not None else first_record
    )

    elapsed_hint = _number(
        session.get("total_elapsed_time_s")
        if "total_elapsed_time_s" in session
        else session.get("total_elapsed_time")
    )
    hinted_end = (
        start + timedelta(seconds=elapsed_hint)
        if elapsed_hint and elapsed_hint > 0
        else None
    )
    minimum_end = last_record + timedelta(seconds=nominal_period_s)
    # FIT session elapsed time is authoritative when it reaches the last
    # record.  Adding another nominal period here would invent activity time
    # (and make zone totals fail to close against the FIT session duration).
    end = (
        hinted_end
        if hinted_end is not None and hinted_end >= last_record
        else minimum_end
    )
    total_elapsed_s = max(0.0, (end - start).total_seconds())
    if total_elapsed_s > MAX_DURATION_S:
        raise ActivityStreamsError(
            "duration_limit", "Activity duration exceeds seven-day processing limit"
        )
    pauses = _build_pause_intervals(timer_events, start, end)
    total_active_s = total_elapsed_s - _pause_overlap(0.0, total_elapsed_s, pauses)

    value_segments: list[ValueSegment] = []
    missing_segments: list[MissingSegment] = []
    sample_rows: list[dict[str, Any]] = []

    def add_missing(elapsed_start: float, elapsed_end: float, reason: str) -> None:
        for part_start, part_end in _active_parts(elapsed_start, elapsed_end, pauses):
            missing_segments.append(
                MissingSegment(
                    elapsed_start_s=part_start,
                    elapsed_end_s=part_end,
                    active_start_s=_active_at(part_start, pauses),
                    active_end_s=_active_at(part_end, pauses),
                    reason=reason,
                )
            )

    first_offset = (first_record - start).total_seconds()
    if first_offset > 0:
        add_missing(0.0, first_offset, "before_first_record")

    for index, (timestamp, values) in enumerate(deduped):
        elapsed_start = max(0.0, (timestamp - start).total_seconds())
        if index + 1 < len(deduped):
            elapsed_end = max(
                elapsed_start, (deduped[index + 1][0] - start).total_seconds()
            )
        else:
            elapsed_end = total_elapsed_s
        parts = _active_parts(elapsed_start, elapsed_end, pauses)
        covered_end = elapsed_start
        covered_duration = 0.0

        # A record can only describe the uninterrupted active interval that
        # begins at its timestamp.  It is never propagated across a pause.
        if parts and math.isclose(parts[0][0], elapsed_start, abs_tol=1e-6):
            first_part_start, first_part_end = parts[0]
            source_gap = elapsed_end - elapsed_start
            allowed = (
                first_part_end - first_part_start
                if source_gap <= max_gap_s
                else min(nominal_period_s, first_part_end - first_part_start)
            )
            if allowed > 0:
                covered_end = first_part_start + allowed
                value_segments.append(
                    ValueSegment(
                        elapsed_start_s=first_part_start,
                        elapsed_end_s=covered_end,
                        active_start_s=_active_at(first_part_start, pauses),
                        active_end_s=_active_at(covered_end, pauses),
                        values=dict(values),
                        source_timestamp=timestamp,
                    )
                )
                covered_duration = allowed

        for part_start, part_end in parts:
            missing_start = part_start
            if math.isclose(part_start, elapsed_start, abs_tol=1e-6):
                missing_start = max(part_start, covered_end)
            if part_end > missing_start + 1e-9:
                reason = (
                    "record_gap"
                    if elapsed_end - elapsed_start > max_gap_s
                    else "after_pause"
                )
                missing_segments.append(
                    MissingSegment(
                        elapsed_start_s=missing_start,
                        elapsed_end_s=part_end,
                        active_start_s=_active_at(missing_start, pauses),
                        active_end_s=_active_at(part_end, pauses),
                        reason=reason,
                    )
                )

        sample_rows.append(
            {
                "timestamp": timestamp,
                "elapsed_offset_s": elapsed_start,
                "active_offset_s": _active_at(elapsed_start, pauses),
                "duration_s": covered_duration,
                "values": dict(values),
            }
        )

    value_segments.sort(key=lambda segment: segment.active_start_s)
    missing_segments.sort(key=lambda segment: segment.active_start_s)
    covered_s = sum(segment.duration_s for segment in value_segments)
    missing_s = sum(segment.duration_s for segment in missing_segments)
    # Float arithmetic aside, every active second must be classified exactly
    # once as sampled or missing.  Treat violations as a construction bug.
    if not math.isclose(covered_s + missing_s, total_active_s, abs_tol=1e-6):
        raise RuntimeError(
            "canonical timeline invariant failed: sampled + missing != active duration"
        )

    return CanonicalTimeline(
        start_timestamp=start,
        end_timestamp=end,
        sport=str(session.get("sport")) if session.get("sport") is not None else None,
        samples=sample_rows,
        value_segments=value_segments,
        missing_segments=missing_segments,
        pause_intervals=pauses,
        total_elapsed_s=total_elapsed_s,
        total_active_s=total_active_s,
        dropped_records=dropped,
        metadata={
            "nominal_sample_period_s": nominal_period_s,
            "max_interpolation_gap_s": max_gap_s,
        },
    )


def decode_fit_timeline(fit_bytes: bytes) -> CanonicalTimeline:
    """Decode raw, gzip or Garmin ZIP-wrapped FIT bytes into a timeline."""

    raw = _extract_fit_bytes(bytes(fit_bytes))
    try:
        fit_file = fitparse.FitFile(io.BytesIO(raw))
        messages = fit_file.get_messages()
    except Exception as exc:
        raise ActivityStreamsError(
            "invalid_fit", f"Unable to open FIT data: {exc}"
        ) from exc

    records: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    session: dict[str, Any] = {}
    session_count = 0
    try:
        for message in messages:
            if message.name == "record":
                if len(records) >= MAX_RECORDS:
                    raise ActivityStreamsError(
                        "input_limit", "FIT record count exceeds processing limit"
                    )
                records.append(
                    {
                        "timestamp": _message_value(message, "timestamp"),
                        "hr": _message_value(message, "heart_rate"),
                        "power": _message_value(message, "power"),
                        "speed": _message_value(message, "enhanced_speed", "speed"),
                        "cadence": _message_value(message, "cadence"),
                        "altitude": _message_value(
                            message, "enhanced_altitude", "altitude"
                        ),
                        "temperature": _message_value(message, "temperature"),
                        "grade": _message_value(message, "grade"),
                        "distance": _message_value(message, "distance"),
                    }
                )
            elif message.name == "event":
                event_name = _message_value(message, "event")
                event_type = _message_value(message, "event_type")
                if str(event_name or "").lower() == "timer" and _timer_action(
                    event_type
                ):
                    if len(events) >= MAX_EVENTS:
                        raise ActivityStreamsError(
                            "input_limit",
                            "FIT timer event count exceeds processing limit",
                        )
                    events.append(
                        {
                            "timestamp": _message_value(message, "timestamp"),
                            "event": event_name,
                            "event_type": event_type,
                        }
                    )
            elif message.name == "session":
                session_count += 1
                if session_count > 1:
                    raise ActivityStreamsError(
                        "multiple_sessions",
                        "Multiple FIT sessions are not supported; use get_activity_fit_messages",
                    )
                session = {
                    "sport": _message_value(message, "sport"),
                    "start_time": _message_value(message, "start_time"),
                    "total_elapsed_time": _message_value(message, "total_elapsed_time"),
                    "total_timer_time": _message_value(message, "total_timer_time"),
                }
    except ActivityStreamsError:
        raise
    except Exception as exc:
        raise ActivityStreamsError(
            "invalid_fit", f"Unable to decode FIT messages: {exc}"
        ) from exc
    timeline = build_timeline(records, events, session)
    timeline.metadata["source_sha256"] = hashlib.sha256(raw).hexdigest()
    return timeline


def _elapsed_for_active_offset(timeline: CanonicalTimeline, active_s: float) -> float:
    elapsed = active_s
    for pause_start, pause_end in timeline.pause_intervals:
        if pause_start <= elapsed + 1e-9:
            elapsed += pause_end - pause_start
        else:
            break
    return min(max(elapsed, 0.0), timeline.total_elapsed_s)


def _display_number(value: float) -> int | float:
    rounded = round(float(value), 3)
    return int(rounded) if rounded.is_integer() else rounded


def resample_timeline(
    timeline: CanonicalTimeline,
    fields: Sequence[str],
    resolution: str = "30s",
    time_basis: str = "active",
    *,
    offset: int = 0,
    page_size: int = 500,
) -> list[dict[str, Any]]:
    """Return a bounded page of time-weighted, fixed-width stream bins."""

    selected = _normalize_fields(fields)
    resolution = str(resolution).lower()
    if resolution not in SUPPORTED_RESOLUTIONS:
        raise ActivityStreamsError(
            "invalid_resolution",
            f"resolution must be one of {sorted(SUPPORTED_RESOLUTIONS)}",
        )
    if time_basis not in {"active", "elapsed"}:
        raise ActivityStreamsError(
            "invalid_time_basis", "time_basis must be active or elapsed"
        )

    width = float(SUPPORTED_RESOLUTIONS[resolution])
    total = (
        timeline.total_active_s if time_basis == "active" else timeline.total_elapsed_s
    )
    if total <= 0:
        return []
    segments = timeline.value_segments
    points = []
    segment_index = 0
    bin_count = math.ceil(total / width)

    for bin_index in range(offset, min(bin_count, offset + page_size)):
        bin_start = bin_index * width
        bin_end = min(total, bin_start + width)
        while segment_index < len(segments):
            candidate = segments[segment_index]
            candidate_end = (
                candidate.active_end_s
                if time_basis == "active"
                else candidate.elapsed_end_s
            )
            if candidate_end > bin_start + 1e-9:
                break
            segment_index += 1

        numerators = {name: 0.0 for name in selected}
        denominators = {name: 0.0 for name in selected}
        tails: dict[str, tuple[float, float]] = {}
        covered_s = 0.0
        cursor = segment_index
        while cursor < len(segments):
            segment = segments[cursor]
            segment_start = (
                segment.active_start_s
                if time_basis == "active"
                else segment.elapsed_start_s
            )
            segment_end = (
                segment.active_end_s
                if time_basis == "active"
                else segment.elapsed_end_s
            )
            if segment_start >= bin_end - 1e-9:
                break
            overlap = max(
                0.0, min(bin_end, segment_end) - max(bin_start, segment_start)
            )
            if overlap > 0:
                covered_s += overlap
                for name in selected:
                    value = segment.values.get(name)
                    if value is None:
                        continue
                    if name in TAIL_FIELDS:
                        denominators[name] += overlap
                        tails[name] = (segment_start, value)
                    else:
                        numerators[name] += value * overlap
                        denominators[name] += overlap
            cursor += 1

        elapsed_offset = (
            _elapsed_for_active_offset(timeline, bin_start)
            if time_basis == "active"
            else bin_start
        )
        point = {
            "timestamp": _iso(
                timeline.start_timestamp + timedelta(seconds=elapsed_offset)
            ),
            "offset_s": _display_number(bin_start),
            "duration_s": _display_number(bin_end - bin_start),
            "covered_s": _display_number(covered_s),
            "field_coverage_s": {
                name: _display_number(denominators[name]) for name in selected
            },
        }
        for name in selected:
            if name in tails and name in TAIL_FIELDS:
                point[name] = _display_number(tails[name][1])
            elif denominators[name] > 0:
                point[name] = _display_number(numerators[name] / denominators[name])
        points.append(point)
    return points


def _coverage(
    timeline: CanonicalTimeline, fields: Sequence[str]
) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for name in _normalize_fields(fields):
        available = sum(
            segment.duration_s
            for segment in timeline.value_segments
            if segment.values.get(name) is not None
        )
        result[name] = {
            "available_s": round(available, 3),
            "active_s": round(timeline.total_active_s, 3),
            "pct": round(100.0 * available / timeline.total_active_s, 2)
            if timeline.total_active_s
            else 0.0,
        }
    return result


def _extract_fit_bytes(raw: bytes) -> bytes:
    """Read one FIT payload in memory, with bounded archive expansion."""
    if len(raw) > MAX_FIT_BYTES:
        raise ActivityStreamsError("input_limit", "FIT download exceeds size limit")
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = [
                info
                for info in archive.infolist()
                if info.filename.lower().endswith(".fit")
            ]
            if len(names) != 1:
                raise ActivityStreamsError(
                    "invalid_fit", "Archive must contain exactly one FIT file"
                )
            if names[0].file_size > MAX_FIT_BYTES:
                raise ActivityStreamsError(
                    "input_limit", "Uncompressed FIT exceeds size limit"
                )
            with archive.open(names[0]) as stream:
                data = stream.read(MAX_FIT_BYTES + 1)
    elif raw[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            data = stream.read(MAX_FIT_BYTES + 1)
    else:
        data = raw
    if len(data) > MAX_FIT_BYTES:
        raise ActivityStreamsError("input_limit", "Uncompressed FIT exceeds size limit")
    return data


def validate_request(
    fields: Sequence[str], resolution: str, time_basis: str, offset: int, page_size: int
) -> list[str]:
    """Validate before downloading or resampling; names match the MCP tool."""
    selected = _normalize_fields(fields)
    if resolution not in SUPPORTED_RESOLUTIONS:
        raise ActivityStreamsError(
            "invalid_resolution", "resolution must be 5s, 30s or 60s"
        )
    if time_basis not in {"active", "elapsed"}:
        raise ActivityStreamsError(
            "invalid_time_basis", "time_basis must be active or elapsed"
        )
    if type(offset) is not int or offset < 0:
        raise ActivityStreamsError(
            "invalid_offset", "offset must be a non-negative integer"
        )
    if type(page_size) is not int or not 1 <= page_size <= 500:
        raise ActivityStreamsError(
            "invalid_page_size", "page_size must be between 1 and 500"
        )
    return selected


def stream_response(
    activity_id: int,
    timeline: CanonicalTimeline,
    fields: Sequence[str],
    resolution: str = "30s",
    time_basis: str = "active",
    *,
    offset: int = 0,
    page_size: int = 500,
) -> dict[str, Any]:
    """Bounded page of weighted bins and full-activity coverage (not raw records)."""
    selected = validate_request(fields, resolution, time_basis, offset, page_size)
    total = (
        timeline.total_active_s if time_basis == "active" else timeline.total_elapsed_s
    )
    total_points = math.ceil(total / SUPPORTED_RESOLUTIONS[resolution])
    if offset > total_points:
        raise ActivityStreamsError(
            "invalid_offset", "offset exceeds the number of stream bins"
        )
    page = resample_timeline(
        timeline, selected, resolution, time_basis, offset=offset, page_size=page_size
    )
    next_offset = offset + len(page)
    return {
        "status": "ok",
        "activity_id": activity_id,
        "sport": timeline.sport,
        "resolution": resolution,
        "time_basis": time_basis,
        "fields": selected,
        "source_sha256": timeline.metadata.get("source_sha256"),
        "summary": {
            "start_timestamp": _iso(timeline.start_timestamp),
            "end_timestamp": _iso(timeline.end_timestamp),
            "total_elapsed_s": timeline.total_elapsed_s,
            "total_active_s": timeline.total_active_s,
            "sampled_active_s": sum(s.duration_s for s in timeline.value_segments),
            "missing_active_s": sum(s.duration_s for s in timeline.missing_segments),
            "source_record_count": len(timeline.samples),
            "dropped_record_count": timeline.dropped_records,
            "pause_count": len(timeline.pause_intervals),
            "gap_count": len(timeline.missing_segments),
            "coverage": _coverage(timeline, selected),
        },
        "data": page,
        "pagination": {
            "offset": offset,
            "returned_points": len(page),
            "total_points": total_points,
            "has_more": next_offset < total_points,
            "next_offset": next_offset if next_offset < total_points else None,
        },
    }
