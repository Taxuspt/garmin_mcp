"""Cycling-first physiology domain services and MCP tools.

The module keeps evidence, accepted profile values, and Garmin configuration
separate.  Automatic estimates are deterministic candidates; only an explicit
accept/apply call changes an active value or Garmin Connect.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import statistics
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, TypeVar

from mcp.server.fastmcp import Context
from pydantic import BaseModel, ConfigDict, Field

from garmin_mcp import activity_streams, write_confirmation
from garmin_mcp.physiology_store import PhysiologyStore, configured_data_dir, utc_now


ALGORITHM_VERSION = "threshold-evidence-v1"
FIELD_EVIDENCE_VERSION = "field-threshold-evidence-v1"
DEFAULT_ATHLETE_PROVIDER = "garmin"
DEFAULT_ATHLETE_EXTERNAL_ID = "local"
MAX_IMPORT_BYTES = 50 * 1024 * 1024
MAX_IMPORT_ROWS = 500_000

METRIC_UNITS = {
    "vt1": "bpm",
    "vt2": "bpm",
    "lthr": "bpm",
    "ftp": "W",
    "max_hr": "bpm",
    "resting_hr": "bpm",
}
FRESHNESS_DAYS = {
    "vt1": 90,
    "vt2": 90,
    "lthr": 90,
    "ftp": 90,
    "max_hr": 365,
    "resting_hr": 28,
}
THRESHOLD_METRICS = ("vt1", "vt2", "lthr", "ftp")
PROFILE_METHODS = {"accepted_estimate", "profile_value"}

_FIELD_TEST_NAME_RE = re.compile(
    r"(?:\b(?:race|test|ftp)\b|\btt\b|time[\s_-]*trial|"
    r"\u6bd4\u8d5b|\u6d4b\u8bd5|\u8ba1\u65f6)",
    re.IGNORECASE,
)

_METRIC_ALIASES = {
    "vt1": "vt1",
    "get": "vt1",
    "aerobic_threshold": "vt1",
    "lt1": "vt1",
    "vt2": "vt2",
    "rcp": "vt2",
    "lt2": "vt2",
    "lthr": "lthr",
    "lactate_threshold_hr": "lthr",
    "lactate_threshold_heart_rate": "lthr",
    "ftp": "ftp",
    "functional_threshold_power": "ftp",
    "max_hr": "max_hr",
    "hrmax": "max_hr",
    "maximum_heart_rate": "max_hr",
    "resting_hr": "resting_hr",
    "rhr": "resting_hr",
    "resting_heart_rate": "resting_hr",
}

_CANONICAL_COLUMN_ALIASES = {
    "timestamp": {"timestamp", "time_stamp", "datetime", "date_time", "sample_time"},
    "elapsed_seconds": {"elapsed", "elapsed_time", "elapsed_seconds", "time", "seconds", "sec"},
    "heart_rate_bpm": {"hr", "heart_rate", "heart_rate_bpm", "heartrate", "pulse"},
    "power_watts": {"power", "watts", "power_watts", "work_rate", "workload"},
    "cadence_rpm": {"cadence", "cadence_rpm", "rpm"},
    "temperature_c": {"temperature", "temperature_c", "temp", "ambient_temperature"},
    "vo2": {"vo2", "oxygen_uptake"},
    "vco2": {"vco2", "carbon_dioxide_output"},
    "ventilation_l_min": {"ve", "ventilation", "minute_ventilation"},
    "rer": {"rer", "respiratory_exchange_ratio"},
    "ve_vo2": {"ve_vo2", "ventilatory_equivalent_o2"},
    "ve_vco2": {"ve_vco2", "ventilatory_equivalent_co2"},
    "pet_o2": {"peto2", "pet_o2", "end_tidal_o2"},
    "pet_co2": {"petco2", "pet_co2", "end_tidal_co2"},
    "blood_lactate_mmol_l": {"lactate", "blood_lactate", "lactate_mmol_l"},
    "stage": {"stage", "step", "protocol_stage"},
    "vt1_bpm": {"vt1", "vt1_hr", "vt1_bpm", "get_hr", "lt1_hr"},
    "vt2_bpm": {"vt2", "vt2_hr", "vt2_bpm", "rcp_hr", "lt2_hr"},
    "lthr_bpm": {"lthr", "lthr_hr", "lthr_bpm", "lactate_threshold_hr"},
    "ftp_watts": {"ftp", "ftp_watts", "functional_threshold_power"},
    "max_hr_bpm": {"max_hr", "max_hr_bpm", "hrmax", "maximum_heart_rate"},
    "resting_hr_bpm": {"resting_hr", "resting_hr_bpm", "rhr"},
}

_THRESHOLD_COLUMNS = {
    "vt1_bpm": "vt1",
    "vt2_bpm": "vt2",
    "lthr_bpm": "lthr",
    "ftp_watts": "ftp",
    "max_hr_bpm": "max_hr",
    "resting_hr_bpm": "resting_hr",
}


garmin_client = None
_store: Optional[PhysiologyStore] = None
_store_lock = threading.RLock()


class PhysiologyDataDirectory(BaseModel):
    """Non-secret local data-directory selection for form elicitation."""

    data_dir: str = Field(description="Private directory for the local physiology SQLite database")


class PhysiologyToolResult(BaseModel):
    """Common, forward-compatible envelope for physiology tool results.

    Physiology operations can legitimately return preview, partial, conflict,
    confirmation, or error states.  Keeping ``status`` open-ended makes those
    states explicit without forcing clients to treat every non-``ok`` result as
    a schema failure.  Extra fields are intentionally retained so adding
    evidence or provider metadata remains backwards compatible.
    """

    model_config = ConfigDict(extra="allow")

    status: str
    error: Optional[str] = None
    message: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class PhysiologyStoreResult(PhysiologyToolResult):
    """Optional SQLite store configuration and health status."""

    enabled: Optional[bool] = None
    configured_from_environment: Optional[bool] = None
    data_dir: Optional[str] = None
    database_path: Optional[str] = None
    schema_version: Optional[int] = None
    health_data_notice: Optional[str] = None


class PhysiologyObservationResult(PhysiologyToolResult):
    """One evidence or active-profile observation."""

    id: Optional[str] = None
    sport: Optional[str] = None
    metric: Optional[str] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    observed_at: Optional[str] = None
    source: Optional[str] = None
    method: Optional[str] = None
    confidence: Optional[float] = None


class PhysiologyObservationsResult(PhysiologyToolResult):
    """Collection envelope for local physiology evidence."""

    observations: list[dict[str, Any]] = Field(default_factory=list)


class PhysiologyProfileResult(PhysiologyToolResult):
    """Accepted local profile values and their freshness context."""

    sport: Optional[str] = None
    as_of: Optional[str] = None
    values: dict[str, Any] = Field(default_factory=dict)
    latest_evidence: dict[str, Any] = Field(default_factory=dict)
    active_zone_models: list[dict[str, Any]] = Field(default_factory=list)
    freshness_days: dict[str, int] = Field(default_factory=dict)


class ZoneModelResult(PhysiologyToolResult):
    """Provider-neutral saved ZoneModel."""

    id: Optional[str] = None
    sport: Optional[str] = None
    metric: Optional[str] = None
    name: Optional[str] = None
    zones: list[dict[str, Any]] = Field(default_factory=list)
    vt1: Optional[float] = None
    vt2: Optional[float] = None
    source: Optional[str] = None
    version: Optional[str] = None
    timestamp: Optional[str] = None
    active: Optional[bool] = None


class ZoneModelsResult(PhysiologyToolResult):
    """Collection envelope for saved ZoneModels."""

    zone_models: list[dict[str, Any]] = Field(default_factory=list)


class TestFileInspectionResult(PhysiologyToolResult):
    """Privacy-preserving physiology test-file inspection."""

    format: Optional[str] = None
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    row_count: Optional[int] = None
    columns: list[dict[str, Any]] = Field(default_factory=list)
    delimiter: Optional[str] = None
    inferred_test_type: Optional[str] = None
    inferred_column_mapping: dict[str, str] = Field(default_factory=dict)
    privacy: Optional[str] = None


class PhysiologyTestImportResult(PhysiologyToolResult):
    """Preview or committed external physiology-test import."""

    dry_run: Optional[bool] = None
    test_type: Optional[str] = None
    sport: Optional[str] = None
    sha256: Optional[str] = None
    row_count: Optional[int] = None
    column_mapping: dict[str, str] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    observed_at: Optional[str] = None
    threshold_observations: list[dict[str, Any]] = Field(default_factory=list)
    source_file_copied: Optional[bool] = None
    created: Optional[bool] = None
    import_record: Optional[dict[str, Any]] = Field(default=None, alias="import")
    created_observations: list[dict[str, Any]] = Field(default_factory=list)


class ThresholdEstimatesResult(PhysiologyToolResult):
    """Evidence-backed candidates which have not been activated."""

    sport: Optional[str] = None
    algorithm_version: Optional[str] = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    not_identifiable: list[dict[str, Any]] = Field(default_factory=list)
    activity_evidence: dict[str, Any] = Field(default_factory=dict)
    disclaimer: Optional[str] = None


class ThresholdAcceptanceResult(PhysiologyToolResult):
    """Explicit threshold acceptance and resulting profile observation."""

    estimate: Optional[dict[str, Any]] = None
    profile_observation: Optional[dict[str, Any]] = None


class ProfileSyncResult(PhysiologyToolResult):
    """Preview, confirmation, commit, or reconciliation result for Garmin sync."""

    dry_run: Optional[bool] = None
    sport: Optional[str] = None
    fields: list[str] = Field(default_factory=list)
    current: dict[str, Any] = Field(default_factory=dict)
    target: dict[str, Any] = Field(default_factory=dict)
    payload: list[dict[str, Any]] = Field(default_factory=list)
    write_performed: Optional[bool] = None
    confirmed: Any = None
    mismatches: list[dict[str, Any]] = Field(default_factory=list)
    recovery_checklist: list[str] = Field(default_factory=list)


_ResultModel = TypeVar("_ResultModel", bound=PhysiologyToolResult)


def _as_tool_result(
    model: type[_ResultModel], payload: Mapping[str, Any], *, success_status: str = "ok"
) -> _ResultModel:
    """Validate a domain payload against a concrete MCP result model."""

    normalized = dict(payload)
    normalized.setdefault("status", success_status)
    return model.model_validate(normalized)


def configure(client=None, data_dir: Optional[str] = None) -> Optional[PhysiologyStore]:
    """Configure Garmin access and initialize the store only when opted in."""
    global garmin_client, _store
    if client is not None:
        garmin_client = client
    directory = configured_data_dir(data_dir)
    with _store_lock:
        _store = PhysiologyStore(directory) if directory is not None else None
    return _store


def configure_store(data_dir: str) -> PhysiologyStore:
    """Explicitly enable or move the local store for this server process."""
    if not isinstance(data_dir, str) or not data_dir.strip():
        raise ValueError("data_dir must be a non-empty directory path")
    return configure(data_dir=data_dir)  # type: ignore[return-value]


def current_store() -> Optional[PhysiologyStore]:
    return _store


def get_store(*, required: bool = False) -> Optional[PhysiologyStore]:
    """Public store accessor for other domain services such as coaching."""
    return require_store() if required else current_store()


def require_store() -> PhysiologyStore:
    if _store is None:
        raise RuntimeError(
            "Physiology store is disabled. Set GARMIN_DATA_DIR before server startup "
            "or call configure_physiology_store with a local directory."
        )
    return _store


def _athlete_id(store: PhysiologyStore) -> str:
    return store.ensure_athlete(DEFAULT_ATHLETE_PROVIDER, DEFAULT_ATHLETE_EXTERNAL_ID)


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def normalize_sport(sport: str) -> str:
    normalized = _normalize_token(sport)
    aliases = {"bike": "cycling", "biking": "cycling", "cycle": "cycling", "run": "running"}
    normalized = aliases.get(normalized, normalized)
    if not normalized:
        raise ValueError("sport must be non-empty")
    return normalized


def normalize_metric(metric: str) -> str:
    normalized = _normalize_token(metric)
    try:
        return _METRIC_ALIASES[normalized]
    except KeyError as exc:
        raise ValueError(f"Unsupported physiology metric: {metric}") from exc


def _parse_timestamp(value: Optional[str], *, field: str = "timestamp") -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    text = value.strip()
    if not text:
        raise ValueError(f"{field} must not be blank")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        try:
            parsed = datetime.combine(date.fromisoformat(text), datetime.min.time())
        except ValueError:
            raise ValueError(f"{field} must be an ISO-8601 date or timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_timestamp(value: Optional[str]) -> str:
    return _parse_timestamp(value).replace(microsecond=0).isoformat()


def _validate_value(metric: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError("value must be a finite number")
    numeric = float(value)
    if metric == "ftp":
        if not 0 < numeric <= 3000:
            raise ValueError("FTP must be between 0 and 3000 W")
    elif not 0 < numeric <= 300:
        raise ValueError(f"{metric} must be between 0 and 300 bpm")
    return numeric


def store_status() -> dict[str, Any]:
    store = current_store()
    directory = configured_data_dir()
    return {
        "enabled": store is not None,
        "configured_from_environment": directory is not None,
        "data_dir": str(store.data_dir) if store else (str(directory) if directory else None),
        "database_path": str(store.database_path) if store else None,
        "schema_version": store.schema_version if store else None,
        "health_data_notice": (
            "This local database contains sensitive health data; keep it private and out of source control."
        ),
    }


def record_observation(
    *,
    metric: str,
    value: float,
    sport: str = "cycling",
    observed_at: Optional[str] = None,
    source: str = "field",
    method: str = "manual",
    confidence: float = 0.5,
    lower_bound: Optional[float] = None,
    upper_bound: Optional[float] = None,
    provenance: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    store = require_store()
    athlete_id = _athlete_id(store)
    metric_key = normalize_metric(metric)
    numeric = _validate_value(metric_key, value)
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    low = numeric if lower_bound is None else _validate_value(metric_key, lower_bound)
    high = numeric if upper_bound is None else _validate_value(metric_key, upper_bound)
    if not low <= numeric <= high:
        raise ValueError("lower_bound <= value <= upper_bound is required")
    return store.add_observation(
        athlete_id=athlete_id,
        sport=normalize_sport(sport),
        metric=metric_key,
        value=numeric,
        unit=METRIC_UNITS[metric_key],
        lower_bound=low,
        upper_bound=high,
        observed_at=_iso_timestamp(observed_at),
        source=_normalize_token(source) or "unknown",
        method=_normalize_token(method) or "unknown",
        confidence=float(confidence),
        provenance=provenance,
    )


def set_profile_value(
    *,
    metric: str,
    value: float,
    sport: str = "cycling",
    observed_at: Optional[str] = None,
    source: str = "user",
    confidence: float = 1.0,
    provenance: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Explicitly set a local active profile value without touching Garmin."""
    store = require_store()
    athlete_id = _athlete_id(store)
    metric_key = normalize_metric(metric)
    sport_key = normalize_sport(sport)
    existing = store.list_observations(athlete_id=athlete_id, sport=sport_key, metrics=[metric_key])
    active = next((item for item in existing if item["method"] in PROFILE_METHODS), None)
    numeric = _validate_value(metric_key, value)
    if isinstance(confidence, bool) or not 0 <= float(confidence) <= 1:
        raise ValueError("confidence must be between 0 and 1")
    return store.add_observation(
        athlete_id=athlete_id,
        sport=sport_key,
        metric=metric_key,
        value=numeric,
        unit=METRIC_UNITS[metric_key],
        lower_bound=numeric,
        upper_bound=numeric,
        observed_at=_iso_timestamp(observed_at),
        source=_normalize_token(source) or "user",
        method="profile_value",
        confidence=float(confidence),
        provenance=provenance,
        supersedes_id=active["id"] if active else None,
    )


def list_observation_records(
    *,
    sport: Optional[str] = None,
    metrics: Optional[Sequence[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[dict[str, Any]]:
    store = require_store()
    normalized_metrics = [normalize_metric(item) for item in metrics] if metrics else None
    return store.list_observations(
        athlete_id=_athlete_id(store),
        sport=normalize_sport(sport) if sport else None,
        metrics=normalized_metrics,
        start_date=_iso_timestamp(start_date) if start_date else None,
        end_date=_iso_timestamp(end_date) if end_date else None,
    )


def validate_zone_model(model: Mapping[str, Any]) -> dict[str, Any]:
    required = {"sport", "metric", "zones"}
    missing = sorted(required - set(model))
    if missing:
        raise ValueError(f"ZoneModel is missing required fields: {', '.join(missing)}")
    sport = normalize_sport(str(model["sport"]))
    raw_metric = _normalize_token(str(model["metric"]))
    metric = "heart_rate" if raw_metric in {"hr", "heart_rate", "bpm"} else raw_metric
    if metric not in {"heart_rate", "power"}:
        raise ValueError("ZoneModel metric must be heart_rate or power")
    raw_zones = model["zones"]
    if not isinstance(raw_zones, list) or not raw_zones:
        raise ValueError("ZoneModel zones must be a non-empty list")

    zones: list[dict[str, Any]] = []
    zone_names: set[str] = set()
    prior_upper: Optional[float] = None
    for index, raw_zone in enumerate(raw_zones):
        if not isinstance(raw_zone, Mapping):
            raise ValueError(f"zone {index + 1} must be an object")
        name = str(raw_zone.get("name") or f"Z{index + 1}").strip()
        if not name:
            raise ValueError(f"zone {index + 1} name must be non-empty")
        if name in zone_names:
            raise ValueError(f"ZoneModel zone names must be unique: {name!r}")
        zone_names.add(name)
        lower = raw_zone.get("lower_inclusive")
        upper = raw_zone.get("upper_exclusive")
        if isinstance(lower, bool) or not isinstance(lower, (int, float)) or not math.isfinite(float(lower)):
            raise ValueError(f"zone {name} lower_inclusive must be a finite number")
        lower_value = float(lower)
        upper_value: Optional[float]
        if upper is None:
            if index != len(raw_zones) - 1:
                raise ValueError("Only the final zone may be open-ended")
            upper_value = None
        elif isinstance(upper, bool) or not isinstance(upper, (int, float)) or not math.isfinite(float(upper)):
            raise ValueError(f"zone {name} upper_exclusive must be a finite number or null")
        else:
            upper_value = float(upper)
            if upper_value <= lower_value:
                raise ValueError(f"zone {name} must have upper_exclusive > lower_inclusive")
        if prior_upper is not None and lower_value != prior_upper:
            relation = "overlap" if lower_value < prior_upper else "gap"
            raise ValueError(f"ZoneModel contains a {relation} before {name}; zone bounds must be contiguous")
        if metric == "heart_rate" and not (0 < lower_value <= 300 and (upper_value is None or upper_value <= 301)):
            raise ValueError("heart-rate zone bounds must be between 1 and 300 bpm")
        zones.append(
            {
                "name": name,
                "lower_inclusive": int(lower_value) if lower_value.is_integer() else lower_value,
                "upper_exclusive": (
                    int(upper_value) if upper_value is not None and upper_value.is_integer() else upper_value
                ),
            }
        )
        prior_upper = upper_value

    vt1 = model.get("vt1")
    vt2 = model.get("vt2")
    if vt1 is not None:
        vt1 = float(vt1)
        if not math.isfinite(vt1):
            raise ValueError("ZoneModel vt1 must be finite")
    if vt2 is not None:
        vt2 = float(vt2)
        if not math.isfinite(vt2):
            raise ValueError("ZoneModel vt2 must be finite")
    if vt1 is not None and vt2 is not None and vt1 >= vt2:
        raise ValueError("ZoneModel requires vt1 < vt2")
    return {
        "sport": sport,
        "metric": metric,
        "name": str(model.get("name") or "Custom zones"),
        "zones": zones,
        "vt1": vt1,
        "vt2": vt2,
        "source": str(model.get("source") or "user"),
        "version": str(model.get("version") or "1"),
        "observed_at": _iso_timestamp(model.get("timestamp") or model.get("observed_at")),
    }


def serialize_zone_model(model: Mapping[str, Any]) -> dict[str, Any]:
    """Return the stable provider-neutral public ZoneModel shape.

    Persistence names such as ``zones_json`` and ``observed_at`` deliberately
    stop at this boundary.  The explicit allowlist also prevents athlete and
    database audit fields from leaking through MCP structured output.
    """
    raw_metric = _normalize_token(str(model.get("metric") or ""))
    metric = "hr" if raw_metric in {"hr", "heart_rate", "bpm"} else raw_metric
    raw_zones = model.get("zones")
    if raw_zones is None:
        raw_zones = model.get("zones_json")
    zones = [dict(zone) for zone in raw_zones] if isinstance(raw_zones, list) else []
    timestamp = model.get("timestamp") or model.get("observed_at")
    public = {
        "id": str(model["id"]) if model.get("id") is not None else None,
        "sport": normalize_sport(str(model.get("sport") or "")),
        "metric": metric,
        "name": str(model.get("name") or "Custom zones"),
        "zones": zones,
        "vt1": model.get("vt1"),
        "vt2": model.get("vt2"),
        "source": str(model.get("source") or "unknown"),
        "version": str(model.get("version") or "1"),
        "timestamp": _iso_timestamp(str(timestamp)) if timestamp is not None else None,
        "active": bool(model.get("active", False)),
    }
    return public


def save_zone_model(model: Mapping[str, Any], *, active: bool = False) -> dict[str, Any]:
    normalized = validate_zone_model(model)
    store = require_store()
    return serialize_zone_model(
        store.save_zone_model(
            athlete_id=_athlete_id(store),
            active=active,
            **normalized,
        )
    )


def list_saved_zone_models(
    *, sport: Optional[str] = None, metric: Optional[str] = None, active_only: bool = False
) -> list[dict[str, Any]]:
    store = require_store()
    normalized_metric = None
    if metric:
        normalized_metric = _normalize_token(metric)
        normalized_metric = "heart_rate" if normalized_metric in {"hr", "bpm"} else normalized_metric
    return [
        serialize_zone_model(item)
        for item in store.list_zone_models(
            athlete_id=_athlete_id(store),
            sport=normalize_sport(sport) if sport else None,
            metric=normalized_metric,
            active_only=active_only,
        )
    ]


def activate_saved_zone_model(model_id: str) -> dict[str, Any]:
    store = require_store()
    return serialize_zone_model(
        store.activate_zone_model(athlete_id=_athlete_id(store), model_id=model_id)
    )


def _normalized_header(value: str) -> str:
    text = value.strip().lower().replace("₂", "2").replace("/", "_")
    text = re.sub(r"\([^)]*\)|\[[^]]*\]", "", text)
    return _normalize_token(text)


def _infer_column_mapping(columns: Sequence[str]) -> dict[str, str]:
    inferred: dict[str, str] = {}
    used: set[str] = set()
    for column in columns:
        normalized = _normalized_header(column)
        candidates = [
            canonical
            for canonical, aliases in _CANONICAL_COLUMN_ALIASES.items()
            if normalized in aliases and canonical not in inferred
        ]
        if len(candidates) == 1 and column not in used:
            inferred[candidates[0]] = column
            used.add(column)
    return inferred


def _infer_unit(column: str) -> Optional[str]:
    match = re.search(r"(?:\(([^)]+)\)|\[([^]]+)\])", column)
    return (match.group(1) or match.group(2)).strip() if match else None


def _open_csv(path: Path):
    return path.open("r", encoding="utf-8-sig", newline="")


def _csv_dialect(path: Path) -> csv.Dialect:
    with _open_csv(path) as handle:
        sample = handle.read(8192)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        return csv.excel_tab if path.suffix.lower() == ".tsv" else csv.excel


def _validate_test_path(path: str) -> Path:
    resolved = Path(path).expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("path must point to a regular file")
    if resolved.suffix.lower() not in {".csv", ".tsv", ".txt"}:
        raise ValueError("Only CSV, TSV, and delimited text physiology files are supported")
    size = resolved.stat().st_size
    if size > MAX_IMPORT_BYTES:
        raise ValueError(f"Test file exceeds the {MAX_IMPORT_BYTES // (1024 * 1024)} MiB safety limit")
    return resolved


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_test_file_data(path: str) -> dict[str, Any]:
    resolved = _validate_test_path(path)
    dialect = _csv_dialect(resolved)
    with _open_csv(resolved) as handle:
        reader = csv.DictReader(handle, dialect=dialect)
        columns = list(reader.fieldnames or [])
        if not columns:
            raise ValueError("The test file has no header row")
        missing = {column: 0 for column in columns}
        numeric = {column: 0 for column in columns}
        row_count = 0
        for row in reader:
            row_count += 1
            if row_count > MAX_IMPORT_ROWS:
                raise ValueError(f"Test file exceeds the {MAX_IMPORT_ROWS} row safety limit")
            for column in columns:
                raw = (row.get(column) or "").strip()
                if not raw or raw.lower() in {"na", "n/a", "null", "none", "nan", "-"}:
                    missing[column] += 1
                    continue
                try:
                    float(raw.replace(",", "."))
                    numeric[column] += 1
                except ValueError:
                    pass
    inferred = _infer_column_mapping(columns)
    inferred_type = _infer_test_type(inferred)
    return {
        "status": "ok",
        "format": "delimited_text",
        "sha256": _hash_file(resolved),
        "size_bytes": resolved.stat().st_size,
        "row_count": row_count,
        "columns": [
            {
                "name": column,
                "unit_hint": _infer_unit(column),
                "missing_count": missing[column],
                "numeric_count": numeric[column],
            }
            for column in columns
        ],
        "delimiter": dialect.delimiter,
        "inferred_test_type": inferred_type,
        "inferred_column_mapping": inferred,
        "warnings": _test_quality_warnings(inferred, row_count),
        "privacy": "No sample values are returned and the source file is not copied.",
    }


def _infer_test_type(mapping: Mapping[str, str]) -> str:
    keys = set(mapping)
    if {"vo2", "vco2", "ventilation_l_min"} & keys:
        return "cpet"
    if "blood_lactate_mmol_l" in keys:
        return "lactate"
    if "power_watts" in keys or "ftp_watts" in keys:
        return "power_test"
    return "generic"


def _test_quality_warnings(mapping: Mapping[str, str], row_count: int) -> list[str]:
    warnings: list[str] = []
    keys = set(mapping)
    if "timestamp" not in keys and "elapsed_seconds" not in keys:
        warnings.append("No time column was identified; time-series alignment cannot be audited.")
    if "heart_rate_bpm" not in keys:
        warnings.append("No heart-rate column was identified.")
    if row_count == 0:
        warnings.append("The file contains no data rows.")
    if {"vo2", "vco2", "ventilation_l_min"} & keys and not {"vo2", "vco2", "ventilation_l_min"} <= keys:
        warnings.append("CPET-like columns are incomplete; automated ventilatory threshold inference is unsafe.")
    return warnings


def _parse_float(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in {"na", "n/a", "null", "none", "nan", "-"}:
        return None
    try:
        value = float(text.replace(",", "."))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _parse_elapsed(raw: Any) -> Optional[float]:
    numeric = _parse_float(raw)
    if numeric is not None:
        return numeric
    text = str(raw or "").strip()
    parts = text.split(":")
    if len(parts) not in {2, 3}:
        return None
    try:
        values = [float(item) for item in parts]
    except ValueError:
        return None
    if len(values) == 2:
        return values[0] * 60 + values[1]
    return values[0] * 3600 + values[1] * 60 + values[2]


def _resolve_column_mapping(columns: Sequence[str], supplied: Optional[Mapping[str, str]]) -> dict[str, str]:
    mapping = dict(supplied or _infer_column_mapping(columns))
    if not mapping:
        raise ValueError("No physiology columns were mapped; supply column_mapping as canonical_name -> CSV column")
    unknown = sorted(set(mapping) - set(_CANONICAL_COLUMN_ALIASES))
    if unknown:
        raise ValueError(f"Unknown canonical column(s): {', '.join(unknown)}")
    missing = sorted({column for column in mapping.values() if column not in columns})
    if missing:
        raise ValueError(f"Mapped source column(s) not found: {', '.join(missing)}")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("Each source column may map to only one canonical field")
    return mapping


def _read_normalized_samples(
    path: Path, column_mapping: Optional[Mapping[str, str]]
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    dialect = _csv_dialect(path)
    with _open_csv(path) as handle:
        reader = csv.DictReader(handle, dialect=dialect)
        columns = list(reader.fieldnames or [])
        mapping = _resolve_column_mapping(columns, column_mapping)
        units = {canonical: unit for canonical, column in mapping.items() if (unit := _infer_unit(column))}
        samples: list[dict[str, Any]] = []
        for index, row in enumerate(reader):
            if index >= MAX_IMPORT_ROWS:
                raise ValueError(f"Test file exceeds the {MAX_IMPORT_ROWS} row safety limit")
            sample: dict[str, Any] = {}
            for canonical, column in mapping.items():
                raw = row.get(column)
                if canonical == "timestamp":
                    if raw and raw.strip():
                        try:
                            sample["observed_at"] = _iso_timestamp(raw)
                        except ValueError:
                            sample["timestamp_unparsed"] = raw.strip()
                elif canonical == "elapsed_seconds":
                    sample[canonical] = _parse_elapsed(raw)
                elif canonical == "stage":
                    sample[canonical] = str(raw).strip() if raw is not None else None
                else:
                    sample[canonical] = _parse_float(raw)
            samples.append({key: value for key, value in sample.items() if value is not None})
    return samples, mapping, units


def _preview_threshold_observations(
    samples: Sequence[Mapping[str, Any]], *, test_type: str, source: str, confidence: float
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for column, metric in _THRESHOLD_COLUMNS.items():
        values = sorted({float(sample[column]) for sample in samples if sample.get(column) is not None})
        if not values:
            continue
        # A summary column repeated on every breath/stage is one observation.
        value = statistics.median(values)
        observations.append(
            {
                "metric": metric,
                "value": value,
                "unit": METRIC_UNITS[metric],
                "lower_bound": min(values),
                "upper_bound": max(values),
                "source": source,
                "method": f"{test_type}_import",
                "confidence": confidence,
                "distinct_values": len(values),
            }
        )
    return observations


def import_test_file(
    *,
    path: str,
    test_type: str = "auto",
    column_mapping: Optional[Mapping[str, str]] = None,
    sport: str = "cycling",
    source: Optional[str] = None,
    observed_at: Optional[str] = None,
    confidence: Optional[float] = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    resolved = _validate_test_path(path)
    samples, mapping, units = _read_normalized_samples(resolved, column_mapping)
    normalized_type = _normalize_token(test_type)
    if normalized_type == "auto":
        normalized_type = _infer_test_type(mapping)
    if normalized_type not in {"generic", "cpet", "lactate", "power_test"}:
        raise ValueError("test_type must be auto, generic, cpet, lactate, or power_test")
    source_key = _normalize_token(source or ("field" if normalized_type == "power_test" else "lab"))
    confidence_value = float(confidence if confidence is not None else (0.65 if source_key == "field" else 0.75))
    if not 0 <= confidence_value <= 1:
        raise ValueError("confidence must be between 0 and 1")
    file_hash = _hash_file(resolved)
    timestamp = _iso_timestamp(observed_at) if observed_at else next(
        (sample["observed_at"] for sample in samples if sample.get("observed_at")),
        datetime.fromtimestamp(resolved.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat(),
    )
    observation_previews = _preview_threshold_observations(
        samples, test_type=normalized_type, source=source_key, confidence=confidence_value
    )
    preview = {
        "status": "preview" if dry_run else "imported",
        "dry_run": bool(dry_run),
        "test_type": normalized_type,
        "sport": normalize_sport(sport),
        "sha256": file_hash,
        "row_count": len(samples),
        "column_mapping": mapping,
        "units": units,
        "observed_at": timestamp,
        "threshold_observations": observation_previews,
        "warnings": _test_quality_warnings(mapping, len(samples)),
        "source_file_copied": False,
    }
    if dry_run:
        return preview

    store = require_store()
    athlete_id = _athlete_id(store)
    import_record, created = store.save_test_import(
        athlete_id=athlete_id,
        sport=normalize_sport(sport),
        test_type=normalized_type,
        source_name=resolved.name,
        file_sha256=file_hash,
        column_map=mapping,
        units=units,
        observed_at=timestamp,
        samples=samples,
    )
    created_observations = []
    if created:
        for candidate in observation_previews:
            created_observations.append(
                store.add_observation(
                    athlete_id=athlete_id,
                    sport=normalize_sport(sport),
                    metric=candidate["metric"],
                    value=candidate["value"],
                    unit=candidate["unit"],
                    lower_bound=candidate["lower_bound"],
                    upper_bound=candidate["upper_bound"],
                    observed_at=timestamp,
                    source=source_key,
                    method=candidate["method"],
                    confidence=confidence_value,
                    provenance={"import_id": import_record["id"], "file_sha256": file_hash},
                )
            )
    preview.update(
        {
            "import": import_record,
            "created": created,
            "created_observations": created_observations,
            "status": "imported" if created else "already_imported",
        }
    )
    return preview


def _source_class(observation: Mapping[str, Any]) -> str:
    source = _normalize_token(str(observation.get("source") or ""))
    method = _normalize_token(str(observation.get("method") or ""))
    if source in {"configured", "garmin", "garmin_metadata", "device_configuration"}:
        return "metadata"
    if method in PROFILE_METHODS or source == "derived":
        return "derived"
    if source in {"lab", "laboratory", "cpet", "lactate"} or any(
        token in method for token in ("cpet", "laboratory", "lactate")
    ):
        return "lab"
    if source in {"field", "ride", "activity", "time_trial", "tt"} or any(
        token in method for token in ("field", "time_trial", "sustained", "decoupling")
    ):
        return "field"
    return "other"


def _is_hot(observation: Mapping[str, Any]) -> bool:
    provenance = observation.get("provenance_json") or {}
    if not isinstance(provenance, Mapping):
        return False
    for key in ("ambient_temperature_c", "temperature_c", "weather_temperature_c"):
        try:
            if provenance.get(key) is not None and float(provenance[key]) >= 28:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _effective_weight(observation: Mapping[str, Any]) -> float:
    confidence = max(0.0, min(1.0, float(observation.get("confidence", 0))))
    return confidence * (0.8 if _is_hot(observation) else 1.0)


def _weighted_median(observations: Sequence[Mapping[str, Any]]) -> float:
    ordered = sorted(observations, key=lambda item: (float(item["value"]), str(item["id"])))
    total = sum(_effective_weight(item) for item in ordered)
    if total <= 0:
        return statistics.median(float(item["value"]) for item in ordered)
    midpoint = total / 2
    cumulative = 0.0
    for item in ordered:
        cumulative += _effective_weight(item)
        if cumulative >= midpoint:
            return float(item["value"])
    return float(ordered[-1]["value"])


def _primary_source_for(metric: str, eligible: Mapping[str, Sequence[Mapping[str, Any]]]) -> Optional[str]:
    # Select the source that best identifies this physiological construct.  We
    # intentionally never pool lab and field values into one mean.
    preference = {
        "vt1": ("lab", "field"),
        "vt2": ("lab", "field"),
        "lthr": ("field", "lab"),
        "ftp": ("field", "lab"),
    }[metric]
    for source_class in preference:
        evidence = eligible[source_class]
        minimum = 1 if source_class == "lab" else 2
        if len(evidence) >= minimum:
            return source_class
    return None


def _related_metrics(metric: str) -> set[str]:
    # VT2/RCP and operational cycling LTHR are not identical constructs, but a
    # large disagreement is important reconciliation evidence.
    return {"vt2", "lthr"} if metric in {"vt2", "lthr"} else {metric}


def _conflict_tolerance(metric: str, estimate: float, other: float) -> float:
    midpoint = (abs(estimate) + abs(other)) / 2
    return max(10.0, midpoint * 0.05) if metric == "ftp" else max(5.0, midpoint * 0.03)


def _estimate_range(metric: str, evidence: Sequence[Mapping[str, Any]], center: float) -> tuple[float, float]:
    values_low = [
        float(item["lower_bound"] if item.get("lower_bound") is not None else item["value"])
        for item in evidence
    ]
    values_high = [
        float(item["upper_bound"] if item.get("upper_bound") is not None else item["value"])
        for item in evidence
    ]
    precision = 5.0 if metric == "ftp" else 2.0
    return min(min(values_low), center - precision), max(max(values_high), center + precision)


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _field_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _activity_summary(activity_id: str) -> Mapping[str, Any]:
    if garmin_client is None:
        raise RuntimeError("Garmin client is not configured")
    result = garmin_client.get_activity(int(activity_id))
    if not isinstance(result, Mapping) or not result:
        raise ValueError(f"No activity summary returned for {activity_id}")
    return result


def _summary_scalar(activity: Mapping[str, Any], *keys: str) -> Any:
    summary = activity.get("summaryDTO")
    containers = (summary, activity) if isinstance(summary, Mapping) else (activity,)
    for container in containers:
        for key in keys:
            value = container.get(key)
            if value is not None:
                return value
    return None


def _summary_sport(activity: Mapping[str, Any]) -> Optional[str]:
    activity_type = activity.get("activityTypeDTO") or activity.get("activityType")
    if isinstance(activity_type, Mapping):
        value = activity_type.get("typeKey") or activity_type.get("type")
    else:
        value = activity_type
    return str(value).strip().lower() if value else None


def _summary_qualification(activity: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if activity is None:
        return {
            "qualified": False,
            "name_match": False,
            "aerobic_training_effect": None,
            "basis": [],
        }
    name = str(activity.get("activityName") or activity.get("name") or "").strip()
    event = activity.get("eventTypeDTO") or activity.get("eventType")
    event_type = (
        str(event.get("typeKey") or "").strip().lower()
        if isinstance(event, Mapping)
        else str(event or "").strip().lower()
    )
    name_match = bool(_FIELD_TEST_NAME_RE.search(name)) or event_type == "race"
    aerobic_te = _field_number(
        _summary_scalar(activity, "trainingEffect", "aerobicTrainingEffect", "aerobicEffect")
    )
    basis = []
    if name_match:
        basis.append("race_tt_test_or_ftp_label")
    if aerobic_te is not None and aerobic_te >= 4.0:
        basis.append("aerobic_training_effect_gte_4")
    return {
        "qualified": bool(basis),
        "name": name or None,
        "event_type": event_type or None,
        "name_match": name_match,
        "aerobic_training_effect": aerobic_te,
        "basis": basis,
    }


def _weather_temperature_c(weather: Any) -> Optional[float]:
    if not isinstance(weather, Mapping):
        return None
    for key in (
        "temperatureCelsius",
        "ambientTemperatureCelsius",
        "temperature_c",
        "tempC",
        "temp_c",
    ):
        value = _field_number(weather.get(key))
        if value is not None:
            return value

    unit = str(
        weather.get("temperatureUnit")
        or weather.get("temperature_unit")
        or weather.get("unit")
        or ""
    ).strip().lower()
    for key in ("temperature", "ambientTemperature"):
        value = _field_number(weather.get(key))
        if value is not None and unit:
            return (value - 32.0) * 5.0 / 9.0 if unit.startswith("f") else value

    # Garmin's activity-weather endpoint exposes station ``temp`` in
    # Fahrenheit even on metric accounts.  This is intentionally distinct
    # from the FIT device-temperature channel.
    fahrenheit = _field_number(weather.get("temp"))
    return (fahrenheit - 32.0) * 5.0 / 9.0 if fahrenheit is not None else None


def _fit_temperature_c(timeline: activity_streams.CanonicalTimeline) -> Optional[float]:
    weighted = 0.0
    duration = 0.0
    for segment in timeline.value_segments:
        temperature = _field_number(segment.values.get("temperature"))
        if temperature is None:
            continue
        weighted += temperature * segment.duration_s
        duration += segment.duration_s
    return weighted / duration if duration else None


def _temperature_context(
    activity_id: str, timeline: activity_streams.CanonicalTimeline
) -> dict[str, Any]:
    weather_error = None
    weather = None
    if garmin_client is not None and hasattr(garmin_client, "get_activity_weather"):
        try:
            weather = garmin_client.get_activity_weather(int(activity_id))
        except Exception as exc:  # Weather context must never discard field evidence.
            weather_error = str(exc)
    ambient = _weather_temperature_c(weather)
    if ambient is not None:
        return {
            "ambient_temperature_c": round(ambient, 2),
            "temperature_source": "garmin_activity_weather",
            "weather_error": weather_error,
        }
    fit_temperature = _fit_temperature_c(timeline)
    return {
        "ambient_temperature_c": round(fit_temperature, 2)
        if fit_temperature is not None
        else None,
        "temperature_source": "fit_device_temperature_fallback"
        if fit_temperature is not None
        else "unavailable",
        "weather_error": weather_error,
    }


def _download_canonical_timeline(activity_id: str) -> activity_streams.CanonicalTimeline:
    """Download one activity through the shared canonical FIT decoder."""
    return activity_streams.download_activity_timeline(activity_id, client=garmin_client)


def _select_lthr_field_window(
    timeline: activity_streams.CanonicalTimeline,
) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
    """Select the highest-HR qualified 30--40 minute steady power window."""
    _efforts, paired_seconds, rolling_power = activity_streams._candidate_bins(
        timeline, "power"
    )
    total_bins = len(paired_seconds)
    minimum_bins = 60
    maximum_bins = 80
    if total_bins < minimum_bins:
        return None, []

    paired_prefix = [0.0]
    for value in paired_seconds:
        paired_prefix.append(paired_prefix[-1] + value)

    qualified: list[dict[str, Any]] = []
    near: list[dict[str, Any]] = []
    for length in range(min(maximum_bins, total_bins), minimum_bins - 1, -1):
        for start_bin in range(0, total_bins - length + 1):
            end_bin = start_bin + length
            start_s = start_bin * 30.0
            end_s = min(timeline.total_active_s, end_bin * 30.0)
            duration_s = end_s - start_s
            if duration_s < 1800.0 or duration_s > 2400.0 + 1e-6:
                continue
            coverage = (paired_prefix[end_bin] - paired_prefix[start_bin]) / duration_s
            cv = activity_streams._range_cv(
                rolling_power, min(end_bin, start_bin + 9), end_bin
            )
            stats = activity_streams._window_exact_stats(
                timeline, "power", start_s, end_s
            )
            row = {
                "start_offset_s": round(start_s, 3),
                "end_offset_s": round(end_s, 3),
                "duration_s": round(duration_s, 3),
                "paired_coverage_pct": round(float(stats["coverage"] or coverage) * 100, 2),
                "rolling_5min_power_cv_pct": round(cv * 100, 2) if cv is not None else None,
                "avg_power_w": round(float(stats["avg_effort"]), 2)
                if stats["avg_effort"] is not None
                else None,
                "avg_hr_bpm": round(float(stats["avg_hr"]), 2)
                if stats["avg_hr"] is not None
                else None,
            }
            if len(near) < 5:
                near.append(row)
            if (
                stats["coverage"] is not None
                and stats["coverage"] >= 0.8
                and cv is not None
                and cv <= 0.12
                and stats["avg_hr"] is not None
                and stats["avg_effort"] is not None
            ):
                qualified.append(row)

    if not qualified:
        return None, near
    # A threshold effort is represented by the strongest sustainable HR
    # window, with longer duration and better coverage used as tie-breakers.
    selected = max(
        qualified,
        key=lambda item: (
            float(item["avg_hr_bpm"]),
            float(item["duration_s"]),
            float(item["paired_coverage_pct"]),
            -float(item["rolling_5min_power_cv_pct"]),
        ),
    )
    return selected, near


def _vt1_decoupling_evidence(
    timeline: activity_streams.CanonicalTimeline,
) -> dict[str, Any]:
    result = activity_streams.analyze_decoupling_timeline(timeline, metric="pw_hr")
    if result.get("status") != "ok" or not result.get("applicable"):
        return {
            "eligible": False,
            "reason": result.get("reason") or "Pw:Hr window did not meet steady-state quality gates",
            "analysis": result,
        }
    window = result["window"]
    stats = activity_streams._window_exact_stats(
        timeline,
        "power",
        float(window["start_offset_s"]),
        float(window["end_offset_s"]),
    )
    decoupling = _field_number(result.get("decoupling_pct"))
    avg_hr = _field_number(stats.get("avg_hr"))
    if decoupling is None or avg_hr is None:
        return {
            "eligible": False,
            "reason": "Pw:Hr decoupling or average HR was not calculable",
            "analysis": result,
        }
    return {
        "eligible": True,
        "state": "stable" if decoupling < 5.0 else "unstable",
        "avg_hr_bpm": round(avg_hr, 2),
        "avg_power_w": round(float(stats["avg_effort"]), 2)
        if stats.get("avg_effort") is not None
        else None,
        "decoupling_pct": round(decoupling, 2),
        "window": window,
        "quality": result.get("quality", {}),
    }


def _activity_observed_at(
    activity: Optional[Mapping[str, Any]], timeline: activity_streams.CanonicalTimeline
) -> str:
    if activity is not None:
        raw = _summary_scalar(
            activity, "startTimeGMT", "startTimeLocal", "start_time_gmt", "start_time_local"
        )
        if raw:
            try:
                return _iso_timestamp(str(raw))
            except ValueError:
                pass
    return timeline.start_timestamp.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _field_evidence_key(kind: str, identity: Mapping[str, Any]) -> str:
    return _canonical_hash(
        {"field_evidence_version": FIELD_EVIDENCE_VERSION, "kind": kind, **dict(identity)}
    )


def _record_field_evidence(
    *,
    store: PhysiologyStore,
    athlete_id: str,
    existing: list[dict[str, Any]],
    metric: str,
    value: float,
    lower_bound: float,
    upper_bound: float,
    observed_at: str,
    method: str,
    provenance: Mapping[str, Any],
    evidence_key: str,
    confidence: float,
) -> tuple[dict[str, Any], bool]:
    duplicate = next(
        (
            item
            for item in existing
            if item.get("metric") == metric
            and (item.get("provenance_json") or {}).get("evidence_key") == evidence_key
        ),
        None,
    )
    if duplicate is not None:
        return duplicate, False
    payload = {**dict(provenance), "evidence_key": evidence_key}
    observation = store.add_observation(
        athlete_id=athlete_id,
        sport="cycling",
        metric=metric,
        value=float(value),
        unit=METRIC_UNITS[metric],
        lower_bound=float(lower_bound),
        upper_bound=float(upper_bound),
        observed_at=observed_at,
        source="field",
        method=method,
        confidence=confidence,
        provenance=payload,
    )
    existing.append(observation)
    return observation, True


def _pair_vt1_brackets(
    activities: Sequence[Mapping[str, Any]],
) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    stable = sorted(
        (item for item in activities if item.get("state") == "stable"),
        key=lambda item: (float(item["avg_hr_bpm"]), str(item["activity_id"])),
    )
    unstable = sorted(
        (item for item in activities if item.get("state") == "unstable"),
        key=lambda item: (float(item["avg_hr_bpm"]), str(item["activity_id"])),
    )
    pairs: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    unused_unstable = list(unstable)
    for stable_item in stable:
        match_index = next(
            (
                index
                for index, unstable_item in enumerate(unused_unstable)
                if float(unstable_item["avg_hr_bpm"]) > float(stable_item["avg_hr_bpm"])
            ),
            None,
        )
        if match_index is None:
            continue
        pairs.append((stable_item, unused_unstable.pop(match_index)))
    return pairs


def _collect_activity_field_evidence(
    *,
    store: PhysiologyStore,
    athlete_id: str,
    activity_ids: Sequence[str | int],
) -> dict[str, Any]:
    normalized_ids = list(dict.fromkeys(str(int(item)) for item in activity_ids))
    existing = store.list_observations(
        athlete_id=athlete_id, sport="cycling", metrics=("lthr", "vt1")
    )
    activity_rows: list[dict[str, Any]] = []
    vt1_inputs: list[dict[str, Any]] = []
    lthr_created: list[str] = []
    lthr_reused: list[str] = []

    for activity_id in normalized_ids:
        row: dict[str, Any] = {"activity_id": activity_id, "status": "ok", "errors": []}
        activity: Optional[Mapping[str, Any]] = None
        try:
            activity = _activity_summary(activity_id)
        except Exception as exc:
            row["errors"].append({"source": "summary", "message": str(exc)})
        qualification = _summary_qualification(activity)
        row["summary_qualification"] = qualification

        try:
            timeline = _download_canonical_timeline(activity_id)
        except Exception as exc:
            row.update(
                {
                    "status": "error",
                    "lthr": {"eligible": False, "reason": "canonical_timeline_unavailable"},
                    "vt1_decoupling": {
                        "eligible": False,
                        "reason": "canonical_timeline_unavailable",
                    },
                }
            )
            row["errors"].append({"source": "fit", "message": str(exc)})
            activity_rows.append(row)
            continue

        observed_at = _activity_observed_at(activity, timeline)
        temperature = _temperature_context(activity_id, timeline)
        row["temperature"] = temperature
        row["timeline"] = {
            "active_duration_s": round(timeline.total_active_s, 3),
            "start_timestamp": timeline.start_timestamp.astimezone(timezone.utc).isoformat(),
        }

        sport = _summary_sport(activity) if activity is not None else None
        sport_ok = sport is None or any(token in sport for token in ("cycl", "bik", "bike"))
        window, near_windows = _select_lthr_field_window(timeline)
        if not sport_ok:
            row["lthr"] = {
                "eligible": False,
                "reason": f"activity sport {sport!r} is not cycling",
                "candidate_windows": near_windows,
            }
        elif not qualification["qualified"]:
            row["lthr"] = {
                "eligible": False,
                "reason": "summary lacks race/TT/test/FTP label and aerobic training effect >=4",
                "candidate_window": window,
                "candidate_windows": near_windows,
            }
        elif window is None:
            row["lthr"] = {
                "eligible": False,
                "reason": "no 30-40 minute window met 80% power+HR coverage and 12% rolling-power CV",
                "candidate_windows": near_windows,
            }
        else:
            identity = {
                "activity_id": activity_id,
                "source_sha256": timeline.metadata.get("source_sha256"),
                "window": window,
            }
            evidence_key = _field_evidence_key("lthr_steady_30_40min", identity)
            provenance = {
                "activity_id": activity_id,
                "evidence_type": "lthr_steady_30_40min",
                "field_evidence_version": FIELD_EVIDENCE_VERSION,
                "source_sha256": timeline.metadata.get("source_sha256"),
                "window": window,
                "summary_qualification": qualification,
                "ambient_temperature_c": temperature["ambient_temperature_c"],
                "temperature_source": temperature["temperature_source"],
            }
            value = float(window["avg_hr_bpm"])
            observation, created = _record_field_evidence(
                store=store,
                athlete_id=athlete_id,
                existing=existing,
                metric="lthr",
                value=value,
                lower_bound=value - 2.0,
                upper_bound=value + 2.0,
                observed_at=observed_at,
                method="field_steady_power_30_40min",
                provenance=provenance,
                evidence_key=evidence_key,
                confidence=0.7,
            )
            (lthr_created if created else lthr_reused).append(observation["id"])
            row["lthr"] = {
                "eligible": True,
                "window": window,
                "observation_id": observation["id"],
                "created": created,
            }

        try:
            decoupling = _vt1_decoupling_evidence(timeline)
        except Exception as exc:
            decoupling = {"eligible": False, "reason": str(exc)}
        row["vt1_decoupling"] = decoupling
        if decoupling.get("eligible"):
            vt1_inputs.append(
                {
                    "activity_id": activity_id,
                    "observed_at": observed_at,
                    "temperature": temperature,
                    "source_sha256": timeline.metadata.get("source_sha256"),
                    **decoupling,
                }
            )
        activity_rows.append(row)

    pairs = _pair_vt1_brackets(vt1_inputs)
    vt1_created: list[str] = []
    vt1_reused: list[str] = []
    bracket_rows: list[dict[str, Any]] = []
    if len(pairs) >= 2:
        for stable, unstable in pairs:
            stable_hr = float(stable["avg_hr_bpm"])
            unstable_hr = float(unstable["avg_hr_bpm"])
            identity = {
                "stable_activity_id": str(stable["activity_id"]),
                "unstable_activity_id": str(unstable["activity_id"]),
                "stable_window": stable["window"],
                "unstable_window": unstable["window"],
                "stable_avg_hr_bpm": stable["avg_hr_bpm"],
                "unstable_avg_hr_bpm": unstable["avg_hr_bpm"],
                "stable_decoupling_pct": stable["decoupling_pct"],
                "unstable_decoupling_pct": unstable["decoupling_pct"],
                "stable_source_sha256": stable.get("source_sha256"),
                "unstable_source_sha256": unstable.get("source_sha256"),
            }
            evidence_key = _field_evidence_key("vt1_decoupling_bracket", identity)
            stable_temperature = stable["temperature"]
            unstable_temperature = unstable["temperature"]
            temperatures = [
                float(value)
                for value in (
                    stable_temperature.get("ambient_temperature_c"),
                    unstable_temperature.get("ambient_temperature_c"),
                )
                if value is not None
            ]
            provenance = {
                "activity_ids": [str(stable["activity_id"]), str(unstable["activity_id"])],
                "stable_activity_id": str(stable["activity_id"]),
                "unstable_activity_id": str(unstable["activity_id"]),
                "evidence_type": "vt1_decoupling_bracket",
                "field_evidence_version": FIELD_EVIDENCE_VERSION,
                "source_sha256": {
                    "stable": stable.get("source_sha256"),
                    "unstable": unstable.get("source_sha256"),
                },
                "stable": {
                    "avg_hr_bpm": stable_hr,
                    "decoupling_pct": stable["decoupling_pct"],
                    "window": stable["window"],
                },
                "unstable": {
                    "avg_hr_bpm": unstable_hr,
                    "decoupling_pct": unstable["decoupling_pct"],
                    "window": unstable["window"],
                },
                "temperature_context": {
                    "stable": stable_temperature,
                    "unstable": unstable_temperature,
                },
                # Heat weighting is conservative: the hotter member controls.
                "ambient_temperature_c": max(temperatures) if temperatures else None,
                "temperature_source": "per_activity_weather_then_fit_fallback",
            }
            value = (stable_hr + unstable_hr) / 2.0
            observed_at = max(str(stable["observed_at"]), str(unstable["observed_at"]))
            observation, created = _record_field_evidence(
                store=store,
                athlete_id=athlete_id,
                existing=existing,
                metric="vt1",
                value=value,
                lower_bound=stable_hr,
                upper_bound=unstable_hr,
                observed_at=observed_at,
                method="field_decoupling_bracket",
                provenance=provenance,
                evidence_key=evidence_key,
                confidence=0.65,
            )
            (vt1_created if created else vt1_reused).append(observation["id"])
            bracket_rows.append(
                {
                    "stable_activity_id": str(stable["activity_id"]),
                    "unstable_activity_id": str(unstable["activity_id"]),
                    "lower_hr_bpm": stable_hr,
                    "upper_hr_bpm": unstable_hr,
                    "boundary_candidate_bpm": round(value, 2),
                    "observation_id": observation["id"],
                    "created": created,
                }
            )
    else:
        bracket_rows = [
            {
                "stable_activity_id": str(stable["activity_id"]),
                "unstable_activity_id": str(unstable["activity_id"]),
                "lower_hr_bpm": stable["avg_hr_bpm"],
                "upper_hr_bpm": unstable["avg_hr_bpm"],
                "stored": False,
            }
            for stable, unstable in pairs
        ]

    return {
        "status": "ok",
        "field_evidence_version": FIELD_EVIDENCE_VERSION,
        "activities": activity_rows,
        "lthr": {
            "qualified_activity_count": sum(
                bool(item.get("lthr", {}).get("eligible")) for item in activity_rows
            ),
            "created_observation_ids": lthr_created,
            "reused_observation_ids": lthr_reused,
        },
        "vt1": {
            "status": "sufficient" if len(pairs) >= 2 else "insufficient",
            "required_independent_brackets": 2,
            "independent_bracket_count": len(pairs),
            "steady_activity_count": len(vt1_inputs),
            "brackets": bracket_rows,
            "created_observation_ids": vt1_created,
            "reused_observation_ids": vt1_reused,
            "reason": None
            if len(pairs) >= 2
            else "VT1 requires at least two disjoint stable(<5%) to higher-HR unstable(>=5%) Pw:Hr brackets.",
        },
    }


def estimate_threshold_candidates(
    *,
    sport: str = "cycling",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    activity_ids: Optional[Sequence[str | int]] = None,
) -> dict[str, Any]:
    store = require_store()
    athlete_id = _athlete_id(store)
    sport_key = normalize_sport(sport)
    start = _iso_timestamp(start_date) if start_date else None
    end = _iso_timestamp(end_date) if end_date else None
    activity_evidence: dict[str, Any] = {
        "status": "not_requested",
        "field_evidence_version": FIELD_EVIDENCE_VERSION,
        "activities": [],
    }
    if activity_ids:
        if sport_key == "cycling":
            activity_evidence = _collect_activity_field_evidence(
                store=store,
                athlete_id=athlete_id,
                activity_ids=activity_ids,
            )
        else:
            activity_evidence = {
                "status": "not_supported",
                "field_evidence_version": FIELD_EVIDENCE_VERSION,
                "sport": sport_key,
                "activities": [],
                "reason": "Automatic field evidence collection currently supports cycling only.",
            }
    observations = store.list_observations(
        athlete_id=athlete_id,
        sport=sport_key,
        metrics={"vt1", "vt2", "lthr", "ftp"},
        start_date=start,
        end_date=end,
    )
    activity_filter = {str(item) for item in activity_ids or []}
    if activity_filter:
        def selected_activity_evidence(item: Mapping[str, Any]) -> bool:
            if _source_class(item) != "field":
                return True
            provenance = item.get("provenance_json") or {}
            if not isinstance(provenance, Mapping):
                return False
            if str(provenance.get("activity_id")) in activity_filter:
                return True
            provenance_ids = {str(value) for value in provenance.get("activity_ids") or []}
            return bool(provenance_ids) and provenance_ids.issubset(activity_filter)

        observations = [item for item in observations if selected_activity_evidence(item)]

    candidates: list[dict[str, Any]] = []
    not_identifiable: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for metric in THRESHOLD_METRICS:
        direct = [item for item in observations if item["metric"] == metric]
        eligible: dict[str, list[dict[str, Any]]] = {"lab": [], "field": []}
        for item in direct:
            source_class = _source_class(item)
            minimum_confidence = 0.6 if source_class == "lab" else 0.5
            if source_class in eligible and float(item["confidence"]) >= minimum_confidence:
                eligible[source_class].append(item)
        primary_source = _primary_source_for(metric, eligible)
        if primary_source is None:
            not_identifiable.append(
                {
                    "metric": metric,
                    "reason": (
                        "Requires at least one quality lab observation or two independent quality field observations."
                    ),
                    "lab_evidence_count": len(eligible["lab"]),
                    "field_evidence_count": len(eligible["field"]),
                }
            )
            continue

        primary = eligible[primary_source]
        center = _weighted_median(primary)
        lower, upper = _estimate_range(metric, primary, center)
        weighted_confidence = sum(_effective_weight(item) for item in primary) / len(primary)
        confidence = min(0.95, weighted_confidence + (0.05 if len(primary) >= 2 else 0.0))
        observed_dt = max(_parse_timestamp(str(item["observed_at"])) for item in primary)
        expires_dt = observed_dt + timedelta(days=FRESHNESS_DAYS[metric])

        comparison = [
            item
            for item in observations
            if item["metric"] in _related_metrics(metric)
            and item["id"] not in {selected["id"] for selected in primary}
            and _source_class(item) in {"lab", "field"}
            and float(item.get("confidence", 0)) >= 0.5
        ]
        conflicts = []
        for item in comparison:
            difference = abs(center - float(item["value"]))
            tolerance = _conflict_tolerance(metric, center, float(item["value"]))
            if difference > tolerance:
                conflicts.append(
                    {
                        "observation_id": item["id"],
                        "construct": item["metric"],
                        "source": item["source"],
                        "value": item["value"],
                        "difference": round(difference, 3),
                        "tolerance": round(tolerance, 3),
                    }
                )

        evidence_summary = [
            {
                "observation_id": item["id"],
                "construct": item["metric"],
                "source": item["source"],
                "method": item["method"],
                "value": item["value"],
                "unit": item["unit"],
                "confidence": item["confidence"],
                "observed_at": item["observed_at"],
                "heat_flag": _is_hot(item),
                "effective_weight": round(_effective_weight(item), 4),
            }
            for item in primary
        ]
        input_payload = {
            "algorithm_version": ALGORITHM_VERSION,
            "athlete_id": athlete_id,
            "sport": sport_key,
            "metric": metric,
            "primary_source": primary_source,
            "evidence_ids": sorted(item["id"] for item in primary),
            "comparison_ids": sorted(item["id"] for item in comparison),
        }
        estimate = store.put_threshold_estimate(
            {
                "athlete_id": athlete_id,
                "sport": sport_key,
                "metric": metric,
                "value": round(center, 3),
                "unit": METRIC_UNITS[metric],
                "lower_bound": round(lower, 3),
                "upper_bound": round(upper, 3),
                "confidence": round(confidence, 4),
                "status": "conflict" if conflicts else "pending",
                "primary_source": primary_source,
                "evidence": evidence_summary,
                "conflicts": conflicts,
                "observed_at": observed_dt.replace(microsecond=0).isoformat(),
                "expires_at": expires_dt.replace(microsecond=0).isoformat(),
                "algorithm_version": ALGORITHM_VERSION,
                "input_hash": _canonical_hash(input_payload),
            }
        )
        estimate["stale"] = expires_dt < now
        estimate["interpretation"] = (
            "Candidate only; source constructs were reconciled without averaging lab and field evidence."
        )
        candidates.append(estimate)
    return {
        "status": "ok",
        "sport": sport_key,
        "algorithm_version": ALGORITHM_VERSION,
        "candidates": candidates,
        "not_identifiable": not_identifiable,
        "activity_evidence": activity_evidence,
        "disclaimer": "Training decision support only; not a medical diagnosis.",
    }


def accept_estimate(estimate_id: str, *, acknowledge_conflicts: bool = False) -> dict[str, Any]:
    store = require_store()
    athlete_id = _athlete_id(store)
    estimate = store.get_threshold_estimate(estimate_id)
    if estimate is None or estimate["athlete_id"] != athlete_id:
        raise KeyError(f"Unknown threshold estimate: {estimate_id}")
    if estimate["status"] == "conflict" and not acknowledge_conflicts:
        raise ValueError(
            "This estimate has conflicting evidence. Re-run with acknowledge_conflicts=true only after reviewing it."
        )
    accepted, observation = store.accept_threshold_estimate(
        athlete_id=athlete_id,
        estimate_id=estimate_id,
        allow_conflict=acknowledge_conflicts,
    )
    return {"status": "accepted", "estimate": accepted, "profile_observation": observation}


def physiology_profile(*, sport: str = "cycling", as_of: Optional[str] = None) -> dict[str, Any]:
    store = require_store()
    athlete_id = _athlete_id(store)
    sport_key = normalize_sport(sport)
    as_of_dt = _parse_timestamp(as_of) if as_of else datetime.now(timezone.utc)
    observations = store.list_observations(athlete_id=athlete_id, sport=sport_key)
    values: dict[str, Any] = {}
    evidence_latest: dict[str, Any] = {}
    for metric in METRIC_UNITS:
        metric_observations = [item for item in observations if item["metric"] == metric]
        if metric_observations:
            evidence_latest[metric] = metric_observations[0]
        active = next((item for item in metric_observations if item["method"] in PROFILE_METHODS), None)
        if active is None:
            values[metric] = None
            continue
        observed = _parse_timestamp(active["observed_at"])
        expires = observed + timedelta(days=FRESHNESS_DAYS[metric])
        values[metric] = {
            **active,
            "expires_at": expires.replace(microsecond=0).isoformat(),
            "stale": expires < as_of_dt,
        }
    zone_models = list_saved_zone_models(
        sport=sport_key, active_only=True
    )
    return {
        "status": "ok",
        "sport": sport_key,
        "as_of": as_of_dt.replace(microsecond=0).isoformat(),
        "values": values,
        "latest_evidence": evidence_latest,
        "active_zone_models": zone_models,
        "freshness_days": FRESHNESS_DAYS,
    }


def _garmin_sport_key(sport: str) -> str:
    return normalize_sport(sport).upper()


def sync_profile(
    *, sport: str,
    fields: Sequence[str],
    dry_run: bool = True,
) -> dict[str, Any]:
    if garmin_client is None:
        raise RuntimeError("Garmin client is not configured")
    requested = {_normalize_token(item) for item in fields}
    aliases = {"lactate_threshold_hr": "lthr", "zone_model": "zones", "heart_rate_zones": "zones"}
    requested = {aliases.get(item, item) for item in requested}
    allowed = {"max_hr", "resting_hr", "lthr", "zones"}
    if not requested:
        raise ValueError("fields must request at least one of max_hr, resting_hr, lthr, or zones")
    unknown = sorted(requested - allowed)
    if unknown:
        raise ValueError(
            f"Unsupported Garmin profile field(s): {', '.join(unknown)}. FTP and power-zone writes are not enabled."
        )
    profile = physiology_profile(sport=sport)
    kwargs: dict[str, Any] = {}
    for field in requested - {"zones"}:
        value = profile["values"].get(field)
        if value is None:
            raise ValueError(f"No active local profile value is available for {field}")
        if value["stale"]:
            raise ValueError(f"The active local profile value for {field} is stale; reassess it before sync")
        public_name = "lactate_threshold_hr" if field == "lthr" else field
        kwargs[public_name] = round(float(value["value"]))

    if "zones" in requested:
        hr_models = [item for item in profile["active_zone_models"] if item["metric"] == "hr"]
        if not hr_models:
            raise ValueError("No active heart-rate ZoneModel is available for this sport")
        model = hr_models[0]
        zones = model["zones"]
        if len(zones) != 5:
            raise ValueError("Garmin sync currently requires exactly five heart-rate zones")
        kwargs["calculation_method"] = "custom_bpm"
        kwargs["zone_boundaries"] = [round(float(zone["lower_inclusive"])) for zone in zones]

    from garmin_mcp import user_profile

    prepared = user_profile.prepare_heart_rate_zone_update(sport=sport, **kwargs)
    warnings = [
        "This changes future Garmin zone configuration; recorded activities are not retroactively re-sliced."
    ]
    result = {
        "status": "preview" if dry_run else "applied",
        "dry_run": bool(dry_run),
        "sport": _garmin_sport_key(sport),
        "fields": sorted(requested),
        "current": prepared["current"],
        "target": kwargs,
        "payload": [prepared["payload"]],
        "warnings": warnings,
        "write_performed": False,
    }
    return commit_profile_sync_preview(result) if not dry_run else result


def commit_profile_sync_preview(preview: Mapping[str, Any]) -> dict[str, Any]:
    """Commit the exact payload a caller already previewed and read it back."""
    from garmin_mcp import user_profile

    payloads = preview.get("payload")
    sport = preview.get("sport")
    if (
        not isinstance(payloads, list)
        or len(payloads) != 1
        or not isinstance(payloads[0], dict)
        or not isinstance(sport, str)
    ):
        raise ValueError("profile sync preview does not contain one valid payload")
    result = dict(preview)
    result.update({"status": "applied", "dry_run": False, "write_performed": True})
    prepared = {"sport": sport, "payload": dict(payloads[0])}
    try:
        result["confirmed"] = user_profile.apply_heart_rate_zone_update(prepared)
    except user_profile.HeartRateZoneReadbackMismatch as exc:
        result.update(
            {
                "status": "failed_readback_mismatch",
                "confirmed": exc.confirmed,
                "mismatches": exc.mismatches,
                "recovery_checklist": [
                    "Review the confirmed Garmin profile before retrying; the write is not automatically repeated.",
                    "If necessary, restore the previous values from current using a separately confirmed write.",
                ],
            }
        )
    except Exception as exc:
        result.update(
            {
                "status": "failed_write_outcome_unknown",
                "error": f"{type(exc).__name__}: {exc}",
                "recovery_checklist": [
                    "Read the Garmin heart-rate profile again before retrying; the request may have been committed despite the error.",
                    "Do not automatically repeat the write until the remote values are reconciled.",
                ],
            }
        )
    return result


def _tool_result(callable_, *args, **kwargs) -> Any:
    try:
        return callable_(*args, **kwargs)
    except (ValueError, RuntimeError, KeyError, OSError, csv.Error) as exc:
        return {"status": "error", "error": str(exc)}


def register_tools(app):
    """Register structured-output physiology tools."""

    @app.tool(structured_output=True)
    async def get_physiology_store_status() -> PhysiologyStoreResult:
        """Report whether the optional local physiology database is enabled."""
        return _as_tool_result(PhysiologyStoreResult, store_status())

    @app.tool(structured_output=True)
    async def configure_physiology_store(
        ctx: Context, data_dir: Optional[str] = None
    ) -> PhysiologyStoreResult:
        """Enable the private local physiology store, eliciting a directory if omitted."""
        if data_dir is None:
            elicited = await ctx.elicit(
                "Choose a private local directory for physiology data. Do not enter credentials or MFA codes.",
                PhysiologyDataDirectory,
            )
            if elicited.action != "accept" or elicited.data is None:
                return _as_tool_result(
                    PhysiologyStoreResult,
                    {
                        "status": "needs_input",
                        "message": "Data-directory selection was not accepted; the store remains unchanged.",
                    },
                )
            data_dir = elicited.data.data_dir
        result = _tool_result(configure_store, data_dir)
        payload = store_status() if isinstance(result, PhysiologyStore) else result
        return _as_tool_result(PhysiologyStoreResult, payload)

    @app.tool(structured_output=True)
    async def add_physiology_observation(
        metric: str,
        value: float,
        sport: str = "cycling",
        observed_at: Optional[str] = None,
        source: str = "field",
        method: str = "manual",
        confidence: float = 0.5,
        lower_bound: Optional[float] = None,
        upper_bound: Optional[float] = None,
        provenance: Optional[dict[str, Any]] = None,
    ) -> PhysiologyObservationResult:
        """Record threshold/profile evidence without activating or syncing it."""
        return _as_tool_result(
            PhysiologyObservationResult,
            _tool_result(
                record_observation,
                metric=metric,
                value=value,
                sport=sport,
                observed_at=observed_at,
                source=source,
                method=method,
                confidence=confidence,
                lower_bound=lower_bound,
                upper_bound=upper_bound,
                provenance=provenance,
            ),
        )

    @app.tool(structured_output=True)
    async def set_physiology_profile_value(
        metric: str,
        value: float,
        sport: str = "cycling",
        observed_at: Optional[str] = None,
        source: str = "user",
        confidence: float = 1.0,
        provenance: Optional[dict[str, Any]] = None,
    ) -> PhysiologyObservationResult:
        """Explicitly activate a local profile value; this does not write Garmin."""
        return _as_tool_result(
            PhysiologyObservationResult,
            _tool_result(
                set_profile_value,
                metric=metric,
                value=value,
                sport=sport,
                observed_at=observed_at,
                source=source,
                confidence=confidence,
                provenance=provenance,
            ),
        )

    @app.tool(structured_output=True)
    async def get_physiology_observations(
        sport: Optional[str] = None,
        metrics: Optional[list[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> PhysiologyObservationsResult:
        """List local evidence records with provenance and confidence."""
        result = _tool_result(
            list_observation_records,
            sport=sport,
            metrics=metrics,
            start_date=start_date,
            end_date=end_date,
        )
        payload = result if isinstance(result, dict) else {"status": "ok", "observations": result}
        return _as_tool_result(PhysiologyObservationsResult, payload)

    @app.tool(structured_output=True)
    async def get_physiology_profile(
        sport: str = "cycling", as_of: Optional[str] = None
    ) -> PhysiologyProfileResult:
        """Get accepted values, freshness, latest evidence, and active zone models."""
        return _as_tool_result(
            PhysiologyProfileResult,
            _tool_result(physiology_profile, sport=sport, as_of=as_of),
        )

    @app.tool(structured_output=True)
    async def create_zone_model(
        model: dict[str, Any], active: bool = False
    ) -> ZoneModelResult:
        """Validate and save a contiguous ZoneModel; optionally make it active."""
        return _as_tool_result(
            ZoneModelResult, _tool_result(save_zone_model, model, active=active)
        )

    @app.tool(structured_output=True)
    async def get_zone_models(
        sport: Optional[str] = None,
        metric: Optional[str] = None,
        active_only: bool = False,
    ) -> ZoneModelsResult:
        """List saved ZoneModels."""
        result = _tool_result(
            list_saved_zone_models, sport=sport, metric=metric, active_only=active_only
        )
        payload = result if isinstance(result, dict) else {"status": "ok", "zone_models": result}
        return _as_tool_result(ZoneModelsResult, payload)

    @app.tool(structured_output=True)
    async def activate_zone_model(model_id: str) -> ZoneModelResult:
        """Make one local ZoneModel active for its sport and metric."""
        return _as_tool_result(
            ZoneModelResult, _tool_result(activate_saved_zone_model, model_id)
        )

    @app.tool(structured_output=True)
    async def inspect_test_file(path: str) -> TestFileInspectionResult:
        """Inspect a CSV/TSV physiology file without returning private sample values."""
        return _as_tool_result(
            TestFileInspectionResult, _tool_result(inspect_test_file_data, path)
        )

    @app.tool(structured_output=True)
    async def import_physiology_test(
        path: str,
        test_type: str = "auto",
        column_mapping: Optional[dict[str, str]] = None,
        sport: str = "cycling",
        source: Optional[str] = None,
        observed_at: Optional[str] = None,
        confidence: Optional[float] = None,
        dry_run: bool = True,
    ) -> PhysiologyTestImportResult:
        """Preview or import normalized CPET, lactate, power-test, or generic CSV data."""
        return _as_tool_result(
            PhysiologyTestImportResult,
            _tool_result(
                import_test_file,
                path=path,
                test_type=test_type,
                column_mapping=column_mapping,
                sport=sport,
                source=source,
                observed_at=observed_at,
                confidence=confidence,
                dry_run=dry_run,
            ),
        )

    @app.tool(structured_output=True)
    async def estimate_thresholds(
        sport: str = "cycling",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        activity_ids: Optional[list[str]] = None,
    ) -> ThresholdEstimatesResult:
        """Create evidence-backed threshold candidates; never activate or sync them."""
        return _as_tool_result(
            ThresholdEstimatesResult,
            _tool_result(
                estimate_threshold_candidates,
                sport=sport,
                start_date=start_date,
                end_date=end_date,
                activity_ids=activity_ids,
            ),
        )

    @app.tool(structured_output=True)
    async def accept_threshold_estimate(
        estimate_id: str, acknowledge_conflicts: bool = False
    ) -> ThresholdAcceptanceResult:
        """Explicitly accept a candidate into the local profile, with conflict acknowledgement."""
        return _as_tool_result(
            ThresholdAcceptanceResult,
            _tool_result(
                accept_estimate, estimate_id, acknowledge_conflicts=acknowledge_conflicts
            ),
        )

    @app.tool(structured_output=True)
    async def sync_profile_to_garmin(
        ctx: Context,
        sport: str,
        fields: list[str],
        dry_run: bool = True,
    ) -> ProfileSyncResult:
        """Preview-first sync of accepted HR profile fields; FTP/power writes are unsupported."""
        preview = _tool_result(sync_profile, sport=sport, fields=fields, dry_run=True)
        if dry_run or preview.get("status") == "error":
            return _as_tool_result(ProfileSyncResult, preview)
        confirmed, message = await write_confirmation.confirm_garmin_write(
            ctx,
            action="synchronize the accepted heart-rate profile to Garmin",
            summary={
                "sport": preview["sport"],
                "fields": preview["fields"],
                "changes": {
                    key: {
                        "current": preview["current"].get(key),
                        "target": preview["payload"][0].get(key),
                    }
                    for key in preview["payload"][0]
                    if key != "changeState"
                    and preview["current"].get(key)
                    != preview["payload"][0].get(key)
                },
                "payload_sha256": _canonical_hash(
                    {"payload": preview["payload"], "sport": preview["sport"]}
                ),
            },
        )
        if not confirmed:
            return _as_tool_result(
                ProfileSyncResult,
                write_confirmation.needs_confirmation_result(
                    preview=preview, message=message
                ),
            )
        return _as_tool_result(
            ProfileSyncResult, commit_profile_sync_preview(preview)
        )

    return app
