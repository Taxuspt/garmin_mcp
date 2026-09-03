"""Canonical FIT streams and reproducible activity analytics.

This module deliberately contains no MCP-specific business logic.  FIT records
are decoded once into a timestamped, pause-aware timeline and all downstream
operations (resampling, decoupling and zone slicing) consume that same model.

The legacy :mod:`garmin_mcp.activity_analysis` module is intentionally left
untouched.  Its JSON response and record-count based metrics remain backwards
compatible while new tools can opt into the stricter time-weighted semantics in
this module.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import math
import statistics
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from garmin_mcp.result_models import (
    ActivityStreamsResult,
    DecouplingResult,
    PolarizationAuditResult,
    ResliceZonesResult,
)

try:
    import fitparse

    FITPARSE_AVAILABLE = True
except ImportError:  # pragma: no cover - dependency is present in supported installs
    fitparse = None
    FITPARSE_AVAILABLE = False


SCHEMA_VERSION = "1.0"
ALGORITHM_VERSION = "activity-streams-v1"
MAX_INTERPOLATION_GAP_S = 5.0
SUPPORTED_RESOLUTIONS = {"raw": None, "1s": 1.0, "5s": 5.0, "30s": 30.0, "60s": 60.0}

# Public names are intentionally short and provider-neutral.  The aliases make
# pure-domain tests and callers able to pass either FIT-style or legacy fields.
FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "hr": ("hr", "heart_rate", "heart_rate_bpm"),
    "power": ("power", "power_w"),
    "speed": ("speed", "enhanced_speed", "speed_mps"),
    "cadence": ("cadence", "cadence_rpm"),
    "altitude": ("altitude", "enhanced_altitude", "altitude_m"),
    "temperature": ("temperature", "temperature_c"),
    "grade": ("grade", "grade_pct"),
    "latitude": ("latitude", "position_lat", "lat_deg"),
    "longitude": ("longitude", "position_long", "lon_deg"),
    "distance": ("distance", "distance_m"),
}
MEAN_FIELDS = {"hr", "power", "speed", "cadence", "temperature", "grade"}
TAIL_FIELDS = {"altitude", "latitude", "longitude", "distance"}


garmin_client = None
zone_model_resolver = None
_analysis_sink = None


def configure(client, model_resolver=None, analysis_sink=None):
    """Configure the Garmin provider and an optional stored-zone resolver.

    ``model_resolver`` is a callable accepting a model id and returning a dict
    or :class:`ZoneModel`.  It keeps this module usable without SQLite while
    allowing the optional physiology store to be wired in by the application.
    ``analysis_sink`` receives one deterministic persistence payload after a
    successful analysis.  Persistence failures are reflected as warnings and
    never invalidate the analysis itself.
    """

    global garmin_client, zone_model_resolver, _analysis_sink
    garmin_client = client
    zone_model_resolver = model_resolver
    _analysis_sink = analysis_sink


class ActivityStreamsError(ValueError):
    """A stable, user-facing activity streams error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ZoneBand(BaseModel):
    """One lower-inclusive, upper-exclusive zone."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    lower_inclusive: Optional[float] = None
    upper_exclusive: Optional[float] = None

    @model_validator(mode="after")
    def validate_bounds(self):
        if (
            self.lower_inclusive is not None
            and self.upper_exclusive is not None
            and self.lower_inclusive >= self.upper_exclusive
        ):
            raise ValueError("lower_inclusive must be less than upper_exclusive")
        return self


class ZoneModel(BaseModel):
    """Provider-neutral, gap-free zone model used for historical re-slicing."""

    model_config = ConfigDict(extra="forbid")

    sport: str = Field(min_length=1)
    metric: Literal["hr", "power", "speed"]
    zones: List[ZoneBand] = Field(min_length=1)
    vt1: Optional[float] = None
    vt2: Optional[float] = None
    source: str = "inline"
    version: str = "1"
    timestamp: Optional[datetime] = None

    @field_validator("metric", mode="before")
    @classmethod
    def normalize_metric(cls, value):
        aliases = {
            "heart_rate": "hr",
            "heart_rate_bpm": "hr",
            "power_w": "power",
            "speed_mps": "speed",
        }
        return aliases.get(str(value).lower(), str(value).lower())

    @model_validator(mode="after")
    def validate_topology(self):
        names = [zone.name for zone in self.zones]
        if len(set(names)) != len(names):
            raise ValueError("zone names must be unique")

        if self.zones[-1].upper_exclusive is not None:
            raise ValueError("last zone must have upper_exclusive=null")

        for previous, current in zip(self.zones, self.zones[1:]):
            if previous.upper_exclusive is None:
                raise ValueError("only the last zone may be upper-unbounded")
            if current.lower_inclusive is None:
                raise ValueError("only the first zone may be lower-unbounded")
            if not math.isclose(
                float(previous.upper_exclusive),
                float(current.lower_inclusive),
                rel_tol=0,
                abs_tol=1e-9,
            ):
                relation = (
                    "overlap"
                    if current.lower_inclusive < previous.upper_exclusive
                    else "gap"
                )
                raise ValueError(f"zone {relation} between {previous.name!r} and {current.name!r}")

        if self.vt1 is not None and self.vt2 is not None and self.vt1 >= self.vt2:
            raise ValueError("vt1 must be less than vt2")
        return self


@dataclass(frozen=True)
class ValueSegment:
    """An active interval for which the source record is valid."""

    elapsed_start_s: float
    elapsed_end_s: float
    active_start_s: float
    active_end_s: float
    values: Dict[str, Optional[float]]
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
    sport: Optional[str]
    samples: List[Dict[str, Any]]
    value_segments: List[ValueSegment]
    missing_segments: List[MissingSegment]
    pause_intervals: List[Tuple[float, float]]
    total_elapsed_s: float
    total_active_s: float
    dropped_records: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        number = float(value)
        if number > 100_000_000_000:
            number /= 1000.0
        parsed = datetime.fromtimestamp(number, tz=timezone.utc)
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


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _semicircles_to_degrees(value: Any) -> Optional[float]:
    number = _number(value)
    if number is None:
        return None
    return number * (180.0 / 2**31)


def _value_from_aliases(record: Mapping[str, Any], public_name: str) -> Optional[float]:
    for alias in FIELD_ALIASES[public_name]:
        if alias in record and record[alias] is not None:
            if public_name in {"latitude", "longitude"} and alias.startswith("position_"):
                return _semicircles_to_degrees(record[alias])
            return _number(record[alias])
    return None


def _normalize_fields(fields: Sequence[str]) -> List[str]:
    if isinstance(fields, str):
        fields = [fields]
    normalized: List[str] = []
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
        raise ActivityStreamsError("empty_fields", "At least one stream field is required")
    return normalized


def _extract_fit_bytes(raw: bytes) -> bytes:
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".fit")]
            if not names:
                raise ActivityStreamsError("invalid_fit", "ZIP archive contains no .fit file")
            return archive.read(names[0])
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    return raw


def _message_value(message: Any, *names: str) -> Any:
    for name in names:
        try:
            value = message.get_value(name)
        except (KeyError, AttributeError, TypeError):
            continue
        if value is not None:
            return value
    return None


def _timer_action(event_type: Any) -> Optional[str]:
    value = str(event_type or "").lower()
    if value in {"start", "start_all", "begin_deprecated"}:
        return "start"
    if value.startswith("stop") or value in {"end", "end_all"}:
        return "stop"
    return None


def _build_pause_intervals(
    events: Sequence[Mapping[str, Any]], start: datetime, end: datetime
) -> List[Tuple[float, float]]:
    relevant: List[Tuple[datetime, str]] = []
    for event in events:
        event_name = str(event.get("event") or "timer").lower()
        if event_name != "timer":
            continue
        timestamp = _as_datetime(event.get("timestamp"))
        action = _timer_action(event.get("event_type"))
        if timestamp is not None and action is not None and start <= timestamp <= end:
            relevant.append((timestamp, action))
    relevant.sort(key=lambda item: item[0])

    pauses: List[Tuple[float, float]] = []
    pause_start: Optional[float] = None
    for timestamp, action in relevant:
        offset = min(max((timestamp - start).total_seconds(), 0.0), (end - start).total_seconds())
        if action == "stop" and pause_start is None:
            pause_start = offset
        elif action == "start" and pause_start is not None:
            if offset > pause_start:
                pauses.append((pause_start, offset))
            pause_start = None
    if pause_start is not None and pause_start < (end - start).total_seconds():
        pauses.append((pause_start, (end - start).total_seconds()))

    # Defensive merge for duplicate or slightly out-of-order timer events.
    merged: List[Tuple[float, float]] = []
    for pause_start, pause_end in sorted(pauses):
        if merged and pause_start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], pause_end))
        else:
            merged.append((pause_start, pause_end))
    return merged


def _pause_overlap(start_s: float, end_s: float, pauses: Sequence[Tuple[float, float]]) -> float:
    return sum(max(0.0, min(end_s, p_end) - max(start_s, p_start)) for p_start, p_end in pauses)


def _active_at(elapsed_s: float, pauses: Sequence[Tuple[float, float]]) -> float:
    return elapsed_s - _pause_overlap(0.0, elapsed_s, pauses)


def _active_parts(
    start_s: float, end_s: float, pauses: Sequence[Tuple[float, float]]
) -> List[Tuple[float, float]]:
    if end_s <= start_s:
        return []
    cursor = start_s
    parts: List[Tuple[float, float]] = []
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
    timer_events: Optional[Sequence[Mapping[str, Any]]] = None,
    session: Optional[Mapping[str, Any]] = None,
    max_gap_s: float = MAX_INTERPOLATION_GAP_S,
) -> CanonicalTimeline:
    """Build a canonical timeline from provider-neutral records and FIT events.

    A source value is carried forward only until the next record when that gap
    is at most ``max_gap_s``.  Across larger gaps only one nominal sample period
    is credited; the remaining active time is explicitly represented as
    missing.  Paused time never contributes to active duration or coverage.
    """

    if max_gap_s <= 0:
        raise ActivityStreamsError("invalid_gap", "max_gap_s must be positive")
    timer_events = timer_events or []
    session = dict(session or {})

    normalized: List[Tuple[datetime, Dict[str, Optional[float]]]] = []
    dropped = 0
    for record in records:
        timestamp = _as_datetime(record.get("timestamp"))
        if timestamp is None:
            dropped += 1
            continue
        values = {name: _value_from_aliases(record, name) for name in FIELD_ALIASES}
        normalized.append((timestamp, values))
    if not normalized:
        raise ActivityStreamsError("no_timestamped_records", "FIT activity has no timestamped records")
    normalized.sort(key=lambda item: item[0])

    # Merge duplicate timestamps without discarding complementary fields.
    deduped: List[Tuple[datetime, Dict[str, Optional[float]]]] = []
    for timestamp, values in normalized:
        if deduped and timestamp == deduped[-1][0]:
            merged = dict(deduped[-1][1])
            merged.update({key: value for key, value in values.items() if value is not None})
            deduped[-1] = (timestamp, merged)
        else:
            deduped.append((timestamp, values))

    diffs = [
        (right[0] - left[0]).total_seconds()
        for left, right in zip(deduped, deduped[1:])
        if 0 < (right[0] - left[0]).total_seconds() <= max_gap_s
    ]
    nominal_period_s = statistics.median(diffs) if diffs else 1.0
    nominal_period_s = min(max(nominal_period_s, 0.001), max_gap_s)

    first_record = deduped[0][0]
    last_record = deduped[-1][0]
    session_start = _as_datetime(session.get("start_time") or session.get("start_timestamp"))
    start = min(session_start, first_record) if session_start is not None else first_record

    elapsed_hint = _number(
        session.get("total_elapsed_time_s")
        if "total_elapsed_time_s" in session
        else session.get("total_elapsed_time")
    )
    hinted_end = start + timedelta(seconds=elapsed_hint) if elapsed_hint and elapsed_hint > 0 else None
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
    pauses = _build_pause_intervals(timer_events, start, end)
    total_active_s = total_elapsed_s - _pause_overlap(0.0, total_elapsed_s, pauses)

    value_segments: List[ValueSegment] = []
    missing_segments: List[MissingSegment] = []
    sample_rows: List[Dict[str, Any]] = []

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
            elapsed_end = max(elapsed_start, (deduped[index + 1][0] - start).total_seconds())
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
                reason = "record_gap" if elapsed_end - elapsed_start > max_gap_s else "after_pause"
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

    if not FITPARSE_AVAILABLE:
        raise ActivityStreamsError("fitparse_unavailable", "fitparse is not installed")
    raw = _extract_fit_bytes(bytes(fit_bytes))
    try:
        fit_file = fitparse.FitFile(io.BytesIO(raw))
        messages = fit_file.get_messages()
    except Exception as exc:
        raise ActivityStreamsError("invalid_fit", f"Unable to open FIT data: {exc}") from exc

    records: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    session: Dict[str, Any] = {}
    try:
        for message in messages:
            if message.name == "record":
                records.append(
                    {
                        "timestamp": _message_value(message, "timestamp"),
                        "hr": _message_value(message, "heart_rate"),
                        "power": _message_value(message, "power"),
                        "speed": _message_value(message, "enhanced_speed", "speed"),
                        "cadence": _message_value(message, "cadence"),
                        "altitude": _message_value(message, "enhanced_altitude", "altitude"),
                        "temperature": _message_value(message, "temperature"),
                        "grade": _message_value(message, "grade"),
                        "position_lat": _message_value(message, "position_lat"),
                        "position_long": _message_value(message, "position_long"),
                        "distance": _message_value(message, "distance"),
                    }
                )
            elif message.name == "event":
                event_name = _message_value(message, "event")
                event_type = _message_value(message, "event_type")
                if str(event_name or "").lower() == "timer" and _timer_action(event_type):
                    events.append(
                        {
                            "timestamp": _message_value(message, "timestamp"),
                            "event": event_name,
                            "event_type": event_type,
                        }
                    )
            elif message.name == "session":
                session = {
                    "sport": _message_value(message, "sport"),
                    "start_time": _message_value(message, "start_time"),
                    "total_elapsed_time": _message_value(message, "total_elapsed_time"),
                    "total_timer_time": _message_value(message, "total_timer_time"),
                }
    except Exception as exc:
        raise ActivityStreamsError("invalid_fit", f"Unable to decode FIT messages: {exc}") from exc
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


def _display_number(value: float) -> Union[int, float]:
    rounded = round(float(value), 3)
    return int(rounded) if rounded.is_integer() else rounded


def _analysis_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _attach_analysis_storage(
    result: Dict[str, Any],
    *,
    analysis_type: str,
    request: Mapping[str, Any],
    activity_id: Optional[Union[int, str]] = None,
) -> Dict[str, Any]:
    """Best-effort persistence that only decorates a successful result.

    The sink contract is deliberately provider-neutral: it receives one dict
    containing ``analysis_type``, optional ``activity_id``, algorithm version,
    normalized request, immutable result snapshot, ``result_hash`` and an
    ``input_hash`` over version + request + result hash.
    """
    if _analysis_sink is None or result.get("status") != "ok":
        return result
    snapshot = json.loads(json.dumps(result, ensure_ascii=False, default=str))
    normalized_request = json.loads(
        json.dumps(dict(request), ensure_ascii=False, default=str)
    )
    algorithm_version = str(result.get("algorithm_version") or ALGORITHM_VERSION)
    result_hash = _analysis_digest(snapshot)
    input_hash = _analysis_digest(
        {
            "algorithm_version": algorithm_version,
            "request": normalized_request,
            "result_hash": result_hash,
        }
    )
    payload = {
        "analysis_type": analysis_type,
        "activity_id": str(activity_id) if activity_id is not None else None,
        "algorithm_version": algorithm_version,
        "request": normalized_request,
        "result": snapshot,
        "result_hash": result_hash,
        "input_hash": input_hash,
    }
    try:
        stored = _analysis_sink(payload)
        metadata: Dict[str, Any] = {
            "status": "stored",
            "input_hash": input_hash,
            "result_hash": result_hash,
        }
        if isinstance(stored, Mapping):
            if stored.get("id") is not None:
                metadata["id"] = stored["id"]
            if stored.get("created") is not None:
                metadata["created"] = bool(stored["created"])
        result["storage"] = metadata
    except Exception as exc:  # Analysis is valid even when optional SQLite is not.
        warning = f"Optional analysis persistence failed: {exc}"
        warnings = result.setdefault("warnings", [])
        if isinstance(warnings, list):
            warnings.append(warning)
        result["storage"] = {
            "status": "error",
            "input_hash": input_hash,
            "result_hash": result_hash,
        }
    return result


def resample_timeline(
    timeline: CanonicalTimeline,
    fields: Sequence[str],
    resolution: str = "30s",
    time_basis: str = "active",
) -> List[Dict[str, Any]]:
    """Return raw records or time-weighted, fixed-width stream bins."""

    selected = _normalize_fields(fields)
    resolution = str(resolution).lower()
    if resolution not in SUPPORTED_RESOLUTIONS:
        raise ActivityStreamsError(
            "invalid_resolution",
            f"resolution must be one of {sorted(SUPPORTED_RESOLUTIONS)}",
        )
    if time_basis not in {"active", "elapsed"}:
        raise ActivityStreamsError("invalid_time_basis", "time_basis must be active or elapsed")

    if resolution == "raw":
        points: List[Dict[str, Any]] = []
        for sample in timeline.samples:
            if time_basis == "active" and any(
                pause_start <= sample["elapsed_offset_s"] < pause_end
                for pause_start, pause_end in timeline.pause_intervals
            ):
                # FIT devices may continue emitting record messages while the
                # timer is stopped.  They have no active duration and would
                # otherwise create duplicate active offsets.  The elapsed raw
                # stream remains a lossless view and deliberately keeps them.
                continue
            point: Dict[str, Any] = {
                "timestamp": _iso(sample["timestamp"]),
                "elapsed_offset_s": _display_number(sample["elapsed_offset_s"]),
                "active_offset_s": _display_number(sample["active_offset_s"]),
                "duration_s": _display_number(sample["duration_s"]),
            }
            for name in selected:
                value = sample["values"].get(name)
                if value is not None:
                    point[name] = _display_number(value)
            points.append(point)
        return points

    width = float(SUPPORTED_RESOLUTIONS[resolution])
    total = timeline.total_active_s if time_basis == "active" else timeline.total_elapsed_s
    if total <= 0:
        return []
    segments = timeline.value_segments
    points = []
    segment_index = 0
    bin_count = int(math.ceil(total / width))

    for bin_index in range(bin_count):
        bin_start = bin_index * width
        bin_end = min(total, bin_start + width)
        while segment_index < len(segments):
            candidate = segments[segment_index]
            candidate_end = (
                candidate.active_end_s if time_basis == "active" else candidate.elapsed_end_s
            )
            if candidate_end > bin_start + 1e-9:
                break
            segment_index += 1

        numerators = {name: 0.0 for name in selected}
        denominators = {name: 0.0 for name in selected}
        tails: Dict[str, Tuple[float, float]] = {}
        covered_s = 0.0
        cursor = segment_index
        while cursor < len(segments):
            segment = segments[cursor]
            segment_start = (
                segment.active_start_s if time_basis == "active" else segment.elapsed_start_s
            )
            segment_end = (
                segment.active_end_s if time_basis == "active" else segment.elapsed_end_s
            )
            if segment_start >= bin_end - 1e-9:
                break
            overlap = max(0.0, min(bin_end, segment_end) - max(bin_start, segment_start))
            if overlap > 0:
                covered_s += overlap
                for name in selected:
                    value = segment.values.get(name)
                    if value is None:
                        continue
                    if name in TAIL_FIELDS:
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
            "timestamp": _iso(timeline.start_timestamp + timedelta(seconds=elapsed_offset)),
            "offset_s": _display_number(bin_start),
            "duration_s": _display_number(bin_end - bin_start),
            "covered_s": _display_number(covered_s),
        }
        for name in selected:
            if name in tails and name in TAIL_FIELDS:
                point[name] = _display_number(tails[name][1])
            elif denominators[name] > 0:
                point[name] = _display_number(numerators[name] / denominators[name])
        points.append(point)
    return points


def _coverage(timeline: CanonicalTimeline, fields: Sequence[str]) -> Dict[str, Dict[str, float]]:
    result: Dict[str, Dict[str, float]] = {}
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


def _timeline_gaps(timeline: CanonicalTimeline) -> List[Dict[str, Any]]:
    return [
        {
            "start_timestamp": _iso(
                timeline.start_timestamp + timedelta(seconds=segment.elapsed_start_s)
            ),
            "end_timestamp": _iso(
                timeline.start_timestamp + timedelta(seconds=segment.elapsed_end_s)
            ),
            "active_start_s": round(segment.active_start_s, 3),
            "active_end_s": round(segment.active_end_s, 3),
            "missing_s": round(segment.duration_s, 3),
            "reason": segment.reason,
        }
        for segment in timeline.missing_segments
    ]


def _timeline_pauses(timeline: CanonicalTimeline) -> List[Dict[str, Any]]:
    return [
        {
            "start_timestamp": _iso(timeline.start_timestamp + timedelta(seconds=start)),
            "end_timestamp": _iso(timeline.start_timestamp + timedelta(seconds=end)),
            "elapsed_start_s": round(start, 3),
            "elapsed_end_s": round(end, 3),
            "duration_s": round(end - start, 3),
        }
        for start, end in timeline.pause_intervals
    ]


def _cursor_context(
    activity_id: Union[int, str],
    timeline: CanonicalTimeline,
    fields: Sequence[str],
    resolution: str,
    time_basis: str,
    total_points: int,
) -> str:
    canonical = json.dumps(
        {
            "activity_id": str(activity_id),
            "start": _iso(timeline.start_timestamp),
            "end": _iso(timeline.end_timestamp),
            "fields": list(fields),
            "resolution": resolution,
            "time_basis": time_basis,
            "total_points": total_points,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:20]


def _encode_cursor(offset: int, context: str) -> str:
    payload = json.dumps(
        {"v": 1, "offset": offset, "context": context},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    checksum = hashlib.sha256(payload).hexdigest()[:16].encode()
    return base64.urlsafe_b64encode(payload + b"." + checksum).decode().rstrip("=")


def _decode_cursor(cursor: str, context: str, total_points: int) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode())
        payload, checksum = raw.rsplit(b".", 1)
        if hashlib.sha256(payload).hexdigest()[:16].encode() != checksum:
            raise ValueError("checksum")
        decoded = json.loads(payload)
        if decoded.get("v") != 1 or decoded.get("context") != context:
            raise ValueError("context")
        offset = int(decoded["offset"])
        if offset < 0 or offset > total_points:
            raise ValueError("offset")
        return offset
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ActivityStreamsError(
            "invalid_cursor", "Cursor is invalid or belongs to a different stream request"
        ) from exc


def stream_response(
    activity_id: Union[int, str],
    timeline: CanonicalTimeline,
    fields: Sequence[str],
    resolution: str = "30s",
    time_basis: str = "active",
    cursor: Optional[str] = None,
    page_size: int = 5000,
) -> Dict[str, Any]:
    """Create a stable, cursor-paginated stream response from a timeline."""

    selected = _normalize_fields(fields)
    if not isinstance(page_size, int) or page_size < 1 or page_size > 5000:
        raise ActivityStreamsError("invalid_page_size", "page_size must be between 1 and 5000")
    points = resample_timeline(timeline, selected, resolution, time_basis)
    context = _cursor_context(
        activity_id, timeline, selected, resolution, time_basis, len(points)
    )
    offset = _decode_cursor(cursor, context, len(points)) if cursor else 0
    page = points[offset : offset + page_size]
    next_offset = offset + len(page)
    has_more = next_offset < len(points)
    sampled_s = sum(segment.duration_s for segment in timeline.value_segments)
    missing_s = sum(segment.duration_s for segment in timeline.missing_segments)
    return {
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "activity_id": int(activity_id) if str(activity_id).isdigit() else str(activity_id),
        "sport": timeline.sport,
        "resolution": resolution,
        "time_basis": time_basis,
        "fields": selected,
        "summary": {
            "start_timestamp": _iso(timeline.start_timestamp),
            "end_timestamp": _iso(timeline.end_timestamp),
            "total_elapsed_s": round(timeline.total_elapsed_s, 3),
            "total_active_s": round(timeline.total_active_s, 3),
            "sampled_active_s": round(sampled_s, 3),
            "missing_active_s": round(missing_s, 3),
            "source_record_count": len(timeline.samples),
            "dropped_record_count": timeline.dropped_records,
            "coverage": _coverage(timeline, selected),
        },
        "pauses": _timeline_pauses(timeline),
        "gaps": _timeline_gaps(timeline),
        "data": page,
        "pagination": {
            "offset": offset,
            "returned_points": len(page),
            "total_points": len(points),
            "has_more": has_more,
            "next_cursor": _encode_cursor(next_offset, context) if has_more else None,
        },
    }


def download_activity_timeline(
    activity_id: Union[int, str], client: Any = None
) -> CanonicalTimeline:
    """Download an activity and decode it into the shared canonical timeline.

    Domain services may pass their configured provider explicitly.  MCP-facing
    stream tools continue to use this module's configured provider, so there is
    still exactly one decoder and one pause/gap interpretation.
    """
    provider = client if client is not None else garmin_client
    if provider is None:
        raise ActivityStreamsError("not_configured", "Garmin client is not configured")
    try:
        numeric_id = int(activity_id)
    except (TypeError, ValueError) as exc:
        raise ActivityStreamsError("invalid_activity_id", "activity_id must be an integer") from exc
    try:
        from garminconnect import Garmin

        payload = provider.download_activity(
            numeric_id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL
        )
    except Exception as exc:
        raise ActivityStreamsError("download_failed", f"Unable to download activity: {exc}") from exc
    if not payload:
        raise ActivityStreamsError("no_fit_data", f"No FIT data returned for activity {numeric_id}")
    return decode_fit_timeline(bytes(payload))


def _download_activity_timeline(activity_id: Union[int, str]) -> CanonicalTimeline:
    """Backward-compatible internal alias for the configured provider."""
    return download_activity_timeline(activity_id)


def get_activity_streams_data(
    activity_id: Union[int, str],
    fields: Sequence[str],
    resolution: str = "30s",
    time_basis: str = "active",
    cursor: Optional[str] = None,
    page_size: int = 5000,
) -> Dict[str, Any]:
    timeline = _download_activity_timeline(activity_id)
    return stream_response(
        activity_id, timeline, fields, resolution, time_basis, cursor, page_size
    )


def _metric_field(metric: str) -> Tuple[str, str]:
    normalized = str(metric).lower().replace(":", "_").replace("/", "_")
    if normalized in {"pw_hr", "power_hr", "power"}:
        return "pw_hr", "power"
    if normalized in {"pa_hr", "pace_hr", "speed_hr", "speed"}:
        return "pa_hr", "speed"
    if normalized != "auto":
        raise ActivityStreamsError("invalid_metric", "metric must be auto, pw_hr, or pa_hr")
    return "auto", ""


def _paired_coverage(timeline: CanonicalTimeline, effort_field: str) -> float:
    paired = sum(
        segment.duration_s
        for segment in timeline.value_segments
        if (segment.values.get(effort_field) or 0) > 0 and (segment.values.get("hr") or 0) > 0
    )
    return paired / timeline.total_active_s if timeline.total_active_s else 0.0


def _window_exact_stats(
    timeline: CanonicalTimeline, effort_field: str, start_s: float, end_s: float
) -> Dict[str, Optional[float]]:
    duration = max(0.0, end_s - start_s)
    paired_s = effort_sum = hr_sum = temp_sum = temp_s = 0.0
    for segment in timeline.value_segments:
        overlap = max(
            0.0,
            min(end_s, segment.active_end_s) - max(start_s, segment.active_start_s),
        )
        if overlap <= 0:
            continue
        effort = segment.values.get(effort_field)
        hr = segment.values.get("hr")
        if effort is not None and effort > 0 and hr is not None and hr > 0:
            paired_s += overlap
            effort_sum += effort * overlap
            hr_sum += hr * overlap
        temperature = segment.values.get("temperature")
        if temperature is not None:
            temp_sum += temperature * overlap
            temp_s += overlap
    avg_effort = effort_sum / paired_s if paired_s else None
    avg_hr = hr_sum / paired_s if paired_s else None
    return {
        "duration_s": duration,
        "paired_s": paired_s,
        "coverage": paired_s / duration if duration else 0.0,
        "avg_effort": avg_effort,
        "avg_hr": avg_hr,
        "efficiency_factor": avg_effort / avg_hr if avg_effort is not None and avg_hr else None,
        "avg_temperature_c": temp_sum / temp_s if temp_s else None,
    }


def _candidate_bins(
    timeline: CanonicalTimeline, effort_field: str
) -> Tuple[List[Optional[float]], List[float], List[Optional[float]]]:
    points = resample_timeline(timeline, [effort_field, "hr"], "30s", "active")
    efforts: List[Optional[float]] = []
    for point in points:
        effort = _number(point.get(effort_field))
        efforts.append(effort)

    # A non-null bin average is not the same as full coverage: one valid second
    # in a 30-second bin must count as 1/30, not as a fully paired bin.
    paired_seconds = [0.0] * len(points)
    for segment in timeline.value_segments:
        effort = segment.values.get(effort_field)
        hr = segment.values.get("hr")
        if effort is None or effort <= 0 or hr is None or hr <= 0:
            continue
        first_bin = max(0, int(segment.active_start_s // 30.0))
        last_bin = min(
            len(points) - 1,
            int(max(segment.active_start_s, segment.active_end_s - 1e-9) // 30.0),
        )
        for bin_index in range(first_bin, last_bin + 1):
            bin_start = bin_index * 30.0
            bin_end = min(timeline.total_active_s, bin_start + 30.0)
            paired_seconds[bin_index] += max(
                0.0,
                min(bin_end, segment.active_end_s)
                - max(bin_start, segment.active_start_s),
            )

    rolling: List[Optional[float]] = [None] * len(points)
    width = 10  # 5 minutes at 30-second resolution
    for index in range(width - 1, len(points)):
        window = efforts[index - width + 1 : index + 1]
        available = [value for value in window if value is not None and value > 0]
        if len(available) >= math.ceil(width * 0.8):
            rolling[index] = sum(available) / len(available)
    return efforts, paired_seconds, rolling


def _range_cv(values: Sequence[Optional[float]], start: int, end: int) -> Optional[float]:
    selected = [value for value in values[start:end] if value is not None]
    if len(selected) < 2:
        return None
    mean = statistics.fmean(selected)
    return statistics.pstdev(selected) / mean if mean > 0 else None


def _select_steady_window(
    timeline: CanonicalTimeline,
    effort_field: str,
    min_duration_s: float = 2400.0,
    min_coverage: float = 0.8,
    max_rolling_cv: float = 0.12,
) -> Tuple[Optional[Tuple[float, float, float, float]], List[Dict[str, Any]]]:
    _, paired_seconds, rolling = _candidate_bins(timeline, effort_field)
    total_bins = len(paired_seconds)
    min_bins = int(math.ceil(min_duration_s / 30.0))
    if total_bins < min_bins:
        return None, []

    pair_prefix = [0.0]
    for value in paired_seconds:
        pair_prefix.append(pair_prefix[-1] + value)

    near_candidates: List[Dict[str, Any]] = []
    for length in range(total_bins, min_bins - 1, -1):
        for start in range(0, total_bins - length + 1):
            end = start + length
            candidate_start_s = start * 30.0
            candidate_end_s = min(timeline.total_active_s, end * 30.0)
            candidate_duration_s = candidate_end_s - candidate_start_s
            coverage = (
                (pair_prefix[end] - pair_prefix[start]) / candidate_duration_s
                if candidate_duration_s > 0
                else 0.0
            )
            stability_start = min(end, start + 9)
            cv = _range_cv(rolling, stability_start, end)
            candidate = {
                "start_offset_s": candidate_start_s,
                "end_offset_s": candidate_end_s,
                "duration_s": candidate_duration_s,
                "paired_coverage_pct": round(coverage * 100, 2),
                "rolling_5min_effort_cv_pct": round(cv * 100, 2) if cv is not None else None,
            }
            if len(near_candidates) < 5 and coverage >= min_coverage:
                near_candidates.append(candidate)
            if coverage >= min_coverage and cv is not None and cv <= max_rolling_cv:
                return (
                    candidate["start_offset_s"],
                    candidate["end_offset_s"],
                    coverage,
                    cv,
                ), near_candidates
    return None, near_candidates


def analyze_decoupling_timeline(
    timeline: CanonicalTimeline,
    metric: str = "auto",
    start_offset_s: Optional[float] = None,
    end_offset_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Analyze Pw:Hr or Pa:Hr using active-time weighted halves.

    Positive decoupling means the effort-to-HR efficiency factor fell in the
    second half (HR rose relative to power/speed).
    """

    normalized_metric, effort_field = _metric_field(metric)
    if normalized_metric == "auto":
        # Prefer power whenever the activity has a meaningful power stream.
        if _paired_coverage(timeline, "power") >= 0.1:
            normalized_metric, effort_field = "pw_hr", "power"
        else:
            normalized_metric, effort_field = "pa_hr", "speed"

    explicit_window = start_offset_s is not None or end_offset_s is not None
    warnings: List[str] = []
    candidates: List[Dict[str, Any]] = []
    if explicit_window:
        start = 0.0 if start_offset_s is None else float(start_offset_s)
        end = timeline.total_active_s if end_offset_s is None else float(end_offset_s)
        if start < 0 or end <= start or end > timeline.total_active_s + 1e-6:
            raise ActivityStreamsError(
                "invalid_window",
                "Offsets must satisfy 0 <= start_offset_s < end_offset_s <= active duration",
            )
        _, paired_seconds, rolling = _candidate_bins(timeline, effort_field)
        first_bin = max(0, int(math.floor(start / 30.0)))
        last_bin = min(len(paired_seconds), int(math.ceil(end / 30.0)))
        coverage = 0.0  # exact paired coverage is calculated below
        cv = _range_cv(rolling, min(last_bin, first_bin + 9), last_bin)
    else:
        selected, candidates = _select_steady_window(timeline, effort_field)
        if selected is None:
            return {
                "status": "insufficient_quality",
                "algorithm_version": ALGORITHM_VERSION,
                "metric": normalized_metric,
                "applicable": False,
                "reason": "No >=40 minute window met 80% paired coverage and 12% rolling-effort CV",
                "active_duration_s": round(timeline.total_active_s, 3),
                "candidates": candidates,
            }
        start, end, coverage, cv = selected

    duration = end - start
    midpoint = start + duration / 2.0
    first = _window_exact_stats(timeline, effort_field, start, midpoint)
    second = _window_exact_stats(timeline, effort_field, midpoint, end)
    whole = _window_exact_stats(timeline, effort_field, start, end)
    ef_first = first["efficiency_factor"]
    ef_second = second["efficiency_factor"]
    decoupling = (
        (ef_first - ef_second) / ef_first * 100.0
        if ef_first is not None and ef_second is not None and ef_first > 0
        else None
    )
    applicable = bool(
        duration >= 2400
        and whole["coverage"] is not None
        and whole["coverage"] >= 0.8
        and cv is not None
        and cv <= 0.12
        and decoupling is not None
    )
    if duration < 2400:
        warnings.append("Window is shorter than the 40-minute applicability minimum")
    if whole["coverage"] is not None and whole["coverage"] < 0.8:
        warnings.append("Paired effort/HR coverage is below 80%")
    if cv is None or cv > 0.12:
        warnings.append("Five-minute rolling effort CV exceeds 12% or cannot be calculated")
    if normalized_metric == "pa_hr":
        warnings.append("Pa:Hr is sensitive to grade, wind and surface changes")

    first_temp = first.get("avg_temperature_c")
    second_temp = second.get("avg_temperature_c")
    temperature_delta = (
        second_temp - first_temp
        if first_temp is not None and second_temp is not None
        else None
    )
    if temperature_delta is not None and abs(temperature_delta) >= 5:
        warnings.append("Temperature changed by at least 5 C across the window")

    if decoupling is None:
        interpretation = "not_calculable"
    elif decoupling < 0:
        interpretation = "negative_decoupling"
    elif decoupling < 5:
        interpretation = "well_coupled"
    elif decoupling < 10:
        interpretation = "moderate_decoupling"
    else:
        interpretation = "significant_decoupling"

    def half_output(stats: Mapping[str, Optional[float]]) -> Dict[str, Any]:
        return {
            "duration_s": round(float(stats["duration_s"] or 0), 3),
            "paired_coverage_pct": round(float(stats["coverage"] or 0) * 100, 2),
            "avg_effort": round(float(stats["avg_effort"]), 3)
            if stats["avg_effort"] is not None
            else None,
            "avg_hr_bpm": round(float(stats["avg_hr"]), 3)
            if stats["avg_hr"] is not None
            else None,
            "efficiency_factor": round(float(stats["efficiency_factor"]), 6)
            if stats["efficiency_factor"] is not None
            else None,
            "avg_temperature_c": round(float(stats["avg_temperature_c"]), 2)
            if stats["avg_temperature_c"] is not None
            else None,
        }

    return {
        "status": "ok" if applicable else "insufficient_quality",
        "algorithm_version": ALGORITHM_VERSION,
        "metric": normalized_metric,
        "effort_unit": "W" if effort_field == "power" else "m/s",
        "applicable": applicable,
        "window": {
            "selection": "explicit" if explicit_window else "automatic_longest_steady",
            "start_offset_s": round(start, 3),
            "end_offset_s": round(end, 3),
            "duration_s": round(duration, 3),
        },
        "quality": {
            "paired_coverage_pct": round(float(whole["coverage"] or coverage) * 100, 2),
            "rolling_5min_effort_cv_pct": round(cv * 100, 2) if cv is not None else None,
        },
        "first_half": half_output(first),
        "second_half": half_output(second),
        "decoupling_pct": round(decoupling, 2) if decoupling is not None else None,
        "temperature_delta_c": round(temperature_delta, 2)
        if temperature_delta is not None
        else None,
        "interpretation": interpretation,
        "warnings": warnings,
        "note": "Positive decoupling means effort-to-HR efficiency declined in the second half.",
    }


def analyze_activity_decoupling(
    activity_id: Union[int, str],
    metric: str = "auto",
    start_offset_s: Optional[float] = None,
    end_offset_s: Optional[float] = None,
) -> Dict[str, Any]:
    timeline = _download_activity_timeline(activity_id)
    result = analyze_decoupling_timeline(
        timeline, metric, start_offset_s, end_offset_s
    )
    result["activity_id"] = int(activity_id) if str(activity_id).isdigit() else str(activity_id)
    return _attach_analysis_storage(
        result,
        analysis_type="decoupling",
        activity_id=activity_id,
        request={
            "activity_id": str(activity_id),
            "metric": metric,
            "start_offset_s": start_offset_s,
            "end_offset_s": end_offset_s,
        },
    )


def _resolve_zone_model(
    model: Optional[Union[ZoneModel, Mapping[str, Any]]], model_id: Optional[str]
) -> ZoneModel:
    if (model is None) == (model_id is None):
        raise ActivityStreamsError(
            "invalid_zone_model_source", "Provide exactly one of model or model_id"
        )
    if model_id is not None:
        if zone_model_resolver is None:
            raise ActivityStreamsError(
                "zone_store_disabled",
                "model_id requires the optional physiology store; pass an inline model instead",
            )
        model = zone_model_resolver(model_id)
        if model is None:
            raise ActivityStreamsError("zone_model_not_found", f"Zone model {model_id!r} was not found")
    try:
        return _coerce_zone_model(model)
    except Exception as exc:
        raise ActivityStreamsError("invalid_zone_model", str(exc)) from exc


def _coerce_zone_model(model: Union[ZoneModel, Mapping[str, Any]]) -> ZoneModel:
    """Accept both the public shape and a physiology-store row.

    SQLite rows intentionally contain audit fields and store the list under
    ``zones_json``.  Normalizing at this boundary keeps the domain model strict
    without forcing the optional store to expose persistence details publicly.
    """

    if isinstance(model, ZoneModel):
        return model
    payload = dict(model)
    if "zones" not in payload and "zones_json" in payload:
        payload["zones"] = payload["zones_json"]
    if "timestamp" not in payload and "observed_at" in payload:
        payload["timestamp"] = payload["observed_at"]
    allowed = {
        "sport",
        "metric",
        "zones",
        "vt1",
        "vt2",
        "source",
        "version",
        "timestamp",
    }
    return ZoneModel.model_validate({key: value for key, value in payload.items() if key in allowed})


def _zone_for_value(model: ZoneModel, value: float) -> Optional[str]:
    for zone in model.zones:
        lower_ok = zone.lower_inclusive is None or value >= zone.lower_inclusive
        upper_ok = zone.upper_exclusive is None or value < zone.upper_exclusive
        if lower_ok and upper_ok:
            return zone.name
    return None


def reslice_timeline(
    timeline: CanonicalTimeline,
    model: Union[ZoneModel, Mapping[str, Any]],
    include_segments: bool = False,
) -> Dict[str, Any]:
    """Recalculate time in zone with active-time weighting."""

    zone_model = _coerce_zone_model(model)
    metric = zone_model.metric
    totals = {zone.name: 0.0 for zone in zone_model.zones}
    classified: List[Tuple[float, float, Optional[str], str]] = []

    for segment in timeline.value_segments:
        value = segment.values.get(metric)
        zone_name = _zone_for_value(zone_model, value) if value is not None else None
        if zone_name is not None:
            totals[zone_name] += segment.duration_s
            classified.append(
                (segment.active_start_s, segment.active_end_s, zone_name, "classified")
            )
        else:
            classified.append(
                (segment.active_start_s, segment.active_end_s, None, "missing_metric")
            )
    for segment in timeline.missing_segments:
        classified.append(
            (segment.active_start_s, segment.active_end_s, None, segment.reason)
        )
    classified.sort(key=lambda item: item[0])

    classified_s = sum(totals.values())
    missing_s = timeline.total_active_s - classified_s
    zone_rows = []
    for zone in zone_model.zones:
        seconds = totals[zone.name]
        zone_rows.append(
            {
                "name": zone.name,
                "seconds": round(seconds, 3),
                "pct_of_classified": round(seconds / classified_s * 100, 2)
                if classified_s
                else 0.0,
                "pct_of_active": round(seconds / timeline.total_active_s * 100, 2)
                if timeline.total_active_s
                else 0.0,
            }
        )

    result: Dict[str, Any] = {
        "status": "ok",
        "algorithm_version": ALGORITHM_VERSION,
        "sport": timeline.sport,
        "metric": metric,
        "model": zone_model.model_dump(mode="json"),
        "total_active_s": round(timeline.total_active_s, 3),
        "classified_s": round(classified_s, 3),
        "missing_s": round(missing_s, 3),
        "coverage_pct": round(classified_s / timeline.total_active_s * 100, 2)
        if timeline.total_active_s
        else 0.0,
        "zones": zone_rows,
    }
    if include_segments:
        merged: List[Dict[str, Any]] = []
        for start, end, zone_name, reason in classified:
            label = zone_name or "missing"
            if (
                merged
                and merged[-1]["zone"] == label
                and merged[-1]["reason"] == reason
                and math.isclose(merged[-1]["end_offset_s"], start, abs_tol=1e-6)
            ):
                merged[-1]["end_offset_s"] = round(end, 3)
                merged[-1]["duration_s"] = round(
                    merged[-1]["end_offset_s"] - merged[-1]["start_offset_s"], 3
                )
            else:
                merged.append(
                    {
                        "start_offset_s": round(start, 3),
                        "end_offset_s": round(end, 3),
                        "duration_s": round(end - start, 3),
                        "zone": label,
                        "reason": reason,
                    }
                )
        result["segments"] = merged
    return result


def reslice_activity_zones(
    activity_id: Union[int, str],
    model: Optional[Union[ZoneModel, Mapping[str, Any]]] = None,
    model_id: Optional[str] = None,
    include_segments: bool = False,
) -> Dict[str, Any]:
    zone_model = _resolve_zone_model(model, model_id)
    timeline = _download_activity_timeline(activity_id)
    result = reslice_timeline(timeline, zone_model, include_segments)
    result["activity_id"] = int(activity_id) if str(activity_id).isdigit() else str(activity_id)
    return _attach_analysis_storage(
        result,
        analysis_type="zone_reslice",
        activity_id=activity_id,
        request={
            "activity_id": str(activity_id),
            "model_id": model_id,
            "model": zone_model.model_dump(mode="json"),
            "include_segments": bool(include_segments),
        },
    )


def _polarized_buckets(
    timeline: CanonicalTimeline, model: ZoneModel
) -> Dict[str, float]:
    if model.vt1 is None or model.vt2 is None:
        raise ActivityStreamsError(
            "thresholds_required", "polarization audit requires both vt1 and vt2"
        )
    totals = {"low": 0.0, "black_hole": 0.0, "high": 0.0}
    for segment in timeline.value_segments:
        value = segment.values.get(model.metric)
        if value is None:
            continue
        if value < model.vt1:
            totals["low"] += segment.duration_s
        elif value < model.vt2:
            totals["black_hole"] += segment.duration_s
        else:
            totals["high"] += segment.duration_s
    return totals


def polarization_audit_data(
    start_date: str,
    end_date: str,
    model: Optional[Union[ZoneModel, Mapping[str, Any]]] = None,
    model_id: Optional[str] = None,
    target: str = "polarized_80_20",
) -> Dict[str, Any]:
    """Audit both time-in-intensity and dominant-session distributions."""

    if target != "polarized_80_20":
        raise ActivityStreamsError("invalid_target", "Only polarized_80_20 is supported")
    try:
        start = datetime.fromisoformat(start_date).date()
        end = datetime.fromisoformat(end_date).date()
    except ValueError as exc:
        raise ActivityStreamsError("invalid_date", "Dates must use YYYY-MM-DD") from exc
    if end < start:
        raise ActivityStreamsError("invalid_date_range", "end_date must not precede start_date")
    if garmin_client is None:
        raise ActivityStreamsError("not_configured", "Garmin client is not configured")

    zone_model = _resolve_zone_model(model, model_id)
    if zone_model.vt1 is None or zone_model.vt2 is None:
        raise ActivityStreamsError(
            "thresholds_required", "polarization audit requires both vt1 and vt2"
        )
    try:
        activities = garmin_client.get_activities_by_date(
            start_date, end_date, zone_model.sport
        )
    except Exception as exc:
        raise ActivityStreamsError("activity_list_failed", f"Unable to list activities: {exc}") from exc

    time_totals = {"low": 0.0, "black_hole": 0.0, "high": 0.0}
    session_totals = {"low": 0, "black_hole": 0, "high": 0}
    activity_rows: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for activity in activities or []:
        activity_id = activity.get("activityId") or activity.get("id")
        if activity_id is None:
            continue
        try:
            timeline = _download_activity_timeline(activity_id)
            buckets = _polarized_buckets(timeline, zone_model)
            for name in time_totals:
                time_totals[name] += buckets[name]
            # Dominant-time classification is deterministic and kept separate
            # from time-in-zone so the two 80/20 concepts are never conflated.
            dominant = max(("low", "black_hole", "high"), key=lambda name: buckets[name])
            if sum(buckets.values()) > 0:
                session_totals[dominant] += 1
            activity_rows.append(
                {
                    "activity_id": activity_id,
                    "start_time": activity.get("startTimeLocal") or activity.get("start_time"),
                    "active_s": round(timeline.total_active_s, 3),
                    "classified_s": round(sum(buckets.values()), 3),
                    "dominant_session_class": dominant,
                    "time_s": {key: round(value, 3) for key, value in buckets.items()},
                }
            )
        except Exception as exc:
            errors.append({"activity_id": activity_id, "error": str(exc)})

    classified_time = sum(time_totals.values())
    session_count = sum(session_totals.values())
    time_pct = {
        key: round(value / classified_time * 100, 2) if classified_time else 0.0
        for key, value in time_totals.items()
    }
    session_pct = {
        key: round(value / session_count * 100, 2) if session_count else 0.0
        for key, value in session_totals.items()
    }
    alerts = []
    if time_pct["black_hole"] > 10:
        alerts.append("black_hole_time_above_10pct")
    if time_pct["low"] < 75:
        alerts.append("low_intensity_time_below_75pct")
    result = {
        "status": "partial" if errors else "ok",
        "algorithm_version": ALGORITHM_VERSION,
        "date_range": {"start": start_date, "end": end_date},
        "target": target,
        "model": zone_model.model_dump(mode="json"),
        "time_distribution": {
            "seconds": {key: round(value, 3) for key, value in time_totals.items()},
            "percent": time_pct,
        },
        "session_distribution": {"count": session_totals, "percent": session_pct},
        "alerts": alerts,
        "activities": activity_rows,
        "errors": errors,
    }
    return _attach_analysis_storage(
        result,
        analysis_type="polarization_audit",
        request={
            "start_date": start_date,
            "end_date": end_date,
            "model_id": model_id,
            "model": zone_model.model_dump(mode="json"),
            "target": target,
        },
    )


def _error_result(exc: Exception) -> Dict[str, Any]:
    if isinstance(exc, ActivityStreamsError):
        return {"status": "error", "error": {"code": exc.code, "message": exc.message}}
    return {"status": "error", "error": {"code": "unexpected_error", "message": str(exc)}}


def register_tools(app):
    """Register activity streams and analysis tools on a FastMCP app."""

    from mcp.types import ToolAnnotations

    read_only = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )

    @app.tool(annotations=read_only, structured_output=True)
    async def get_activity_streams(
        activity_id: Union[int, str],
        fields: List[str],
        resolution: str = "30s",
        time_basis: str = "active",
        cursor: Optional[str] = None,
        page_size: int = 5000,
    ) -> ActivityStreamsResult:
        """Return full-resolution or downsampled FIT streams without silent truncation.

        Fields: hr, power, speed, cadence, altitude, temperature, grade,
        latitude, longitude and distance.  Resolution: raw, 1s, 5s, 30s or
        60s.  Follow ``pagination.next_cursor`` until ``has_more`` is false.
        """

        try:
            return get_activity_streams_data(
                activity_id, fields, resolution, time_basis, cursor, page_size
            )
        except Exception as exc:
            return _error_result(exc)

    @app.tool(annotations=read_only, structured_output=True)
    async def analyze_decoupling(
        activity_id: Union[int, str],
        metric: str = "auto",
        start_offset_s: Optional[float] = None,
        end_offset_s: Optional[float] = None,
    ) -> DecouplingResult:
        """Calculate time-weighted aerobic decoupling from a FIT activity.

        Cycling uses Pw:Hr when meaningful power data exists and otherwise
        falls back to Pa:Hr.  Positive values mean efficiency declined.  With
        no offsets the longest qualifying steady window of at least 40 minutes
        is selected automatically.
        """

        try:
            return analyze_activity_decoupling(
                activity_id, metric, start_offset_s, end_offset_s
            )
        except Exception as exc:
            return _error_result(exc)

    @app.tool(annotations=read_only, structured_output=True)
    async def reslice_zones(
        activity_id: Union[int, str],
        model: Optional[Dict[str, Any]] = None,
        model_id: Optional[str] = None,
        include_segments: bool = False,
    ) -> ResliceZonesResult:
        """Re-slice an activity with exactly one inline or stored zone model."""

        try:
            return reslice_activity_zones(
                activity_id, model, model_id, include_segments
            )
        except Exception as exc:
            return _error_result(exc)

    @app.tool(annotations=read_only, structured_output=True)
    async def polarization_audit(
        start_date: str,
        end_date: str,
        model: Optional[Dict[str, Any]] = None,
        model_id: Optional[str] = None,
        target: str = "polarized_80_20",
    ) -> PolarizationAuditResult:
        """Audit low/black-hole/high time and dominant-session distributions."""

        try:
            return polarization_audit_data(
                start_date, end_date, model, model_id, target
            )
        except Exception as exc:
            return _error_result(exc)

    return app
