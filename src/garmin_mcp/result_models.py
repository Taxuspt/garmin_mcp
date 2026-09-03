"""Stable structured-output models for the new intent-level MCP tools.

The tools deliberately return useful error and partial-success envelopes rather
than raising for every provider failure.  Consequently only ``status`` is
required on the common envelope; success-only fields remain optional while
still being described precisely in MCP ``outputSchema``.  ``extra="allow"``
preserves forward compatibility with new diagnostics and provider metadata.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExtensibleResult(BaseModel):
    """Base envelope shared by tools with explicit success/error statuses."""

    model_config = ConfigDict(extra="allow")

    status: str = Field(description="Stable outcome code for this tool call")


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str | None = None
    message: str | None = None


class StreamCoverage(BaseModel):
    model_config = ConfigDict(extra="allow")

    available_s: float
    active_s: float
    pct: float


class StreamSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    start_timestamp: str
    end_timestamp: str
    total_elapsed_s: float
    total_active_s: float
    sampled_active_s: float
    missing_active_s: float
    source_record_count: int
    dropped_record_count: int
    coverage: dict[str, StreamCoverage]


class StreamPagination(BaseModel):
    model_config = ConfigDict(extra="allow")

    offset: int
    returned_points: int
    total_points: int
    has_more: bool
    next_cursor: str | None = None


class ActivityStreamsResult(ExtensibleResult):
    schema_version: str | None = None
    algorithm_version: str | None = None
    activity_id: int | str | None = None
    sport: str | None = None
    resolution: str | None = None
    time_basis: str | None = None
    fields: list[str] | None = None
    summary: StreamSummary | None = None
    pauses: list[dict[str, Any]] | None = None
    gaps: list[dict[str, Any]] | None = None
    data: list[dict[str, Any]] | None = None
    pagination: StreamPagination | None = None
    error: ErrorDetail | str | None = None


class DecouplingWindow(BaseModel):
    model_config = ConfigDict(extra="allow")

    selection: str | None = None
    start_offset_s: float | None = None
    end_offset_s: float | None = None
    duration_s: float | None = None


class DecouplingQuality(BaseModel):
    model_config = ConfigDict(extra="allow")

    paired_coverage_pct: float | None = None
    rolling_5min_effort_cv_pct: float | None = None


class DecouplingHalf(BaseModel):
    model_config = ConfigDict(extra="allow")

    duration_s: float | None = None
    paired_coverage_pct: float | None = None
    avg_effort: float | None = None
    avg_hr_bpm: float | None = None
    efficiency_factor: float | None = None
    avg_temperature_c: float | None = None


class DecouplingResult(ExtensibleResult):
    algorithm_version: str | None = None
    activity_id: int | str | None = None
    metric: str | None = None
    effort_unit: str | None = None
    applicable: bool | None = None
    reason: str | None = None
    active_duration_s: float | None = None
    window: DecouplingWindow | None = None
    quality: DecouplingQuality | None = None
    first_half: DecouplingHalf | None = None
    second_half: DecouplingHalf | None = None
    decoupling_pct: float | None = None
    temperature_delta_c: float | None = None
    interpretation: str | None = None
    warnings: list[str] | None = None
    candidates: list[dict[str, Any]] | None = None
    storage: dict[str, Any] | None = None
    error: ErrorDetail | str | None = None


class ZoneSliceRow(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    seconds: float
    pct_of_classified: float
    pct_of_active: float


class ResliceZonesResult(ExtensibleResult):
    algorithm_version: str | None = None
    activity_id: int | str | None = None
    sport: str | None = None
    metric: str | None = None
    model: dict[str, Any] | None = None
    total_active_s: float | None = None
    classified_s: float | None = None
    missing_s: float | None = None
    coverage_pct: float | None = None
    zones: list[ZoneSliceRow] | None = None
    segments: list[dict[str, Any]] | None = None
    storage: dict[str, Any] | None = None
    warnings: list[str] | None = None
    error: ErrorDetail | str | None = None


class DateRange(BaseModel):
    model_config = ConfigDict(extra="allow")

    start: str
    end: str


class TimeDistribution(BaseModel):
    model_config = ConfigDict(extra="allow")

    seconds: dict[str, float]
    percent: dict[str, float]


class SessionDistribution(BaseModel):
    model_config = ConfigDict(extra="allow")

    count: dict[str, int]
    percent: dict[str, float]


class PolarizationAuditResult(ExtensibleResult):
    algorithm_version: str | None = None
    date_range: DateRange | None = None
    target: str | None = None
    model: dict[str, Any] | None = None
    time_distribution: TimeDistribution | None = None
    session_distribution: SessionDistribution | None = None
    alerts: list[str] | None = None
    activities: list[dict[str, Any]] | None = None
    errors: list[dict[str, Any]] | None = None
    storage: dict[str, Any] | None = None
    warnings: list[str] | None = None
    error: ErrorDetail | str | None = None


class CyclingWorkoutResult(ExtensibleResult):
    dry_run: bool | None = None
    write_performed: bool | None = None
    workout: dict[str, Any] | None = None
    would_schedule: str | None = None
    workout_id: int | str | None = None
    name: str | None = None
    read_back: dict[str, Any] | None = None
    read_back_validated: bool | None = None
    schedule: dict[str, Any] | None = None
    rollback: dict[str, Any] | None = None
    preview: dict[str, Any] | None = None
    warning: str | None = None
    message: str | None = None
    error: ErrorDetail | str | None = None


class FitFileSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    path: str
    size_bytes: int
    sha256: str
    fit_valid: bool
    record_count: int
    session: dict[str, Any] = Field(default_factory=dict)


class FitUploadResult(ExtensibleResult):
    dry_run: bool | None = None
    write_performed: bool | None = None
    source_unchanged: bool | None = None
    repair_profile: str | None = None
    transformations: list[dict[str, Any] | str] | None = None
    file: FitFileSummary | None = None
    upload_result: Any | None = None
    preview: dict[str, Any] | None = None
    requested: dict[str, Any] | None = None
    idempotent: bool | None = None
    message: str | None = None
    error: ErrorDetail | str | None = None


class PlanStorage(BaseModel):
    model_config = ConfigDict(extra="allow")

    plan_revision_id: str
    logical_plan_id: str
    created: bool


class TrainingBlockResult(ExtensibleResult):
    plan_id: str | None = None
    revision: int | None = None
    policy_version: str | None = None
    sport: str | None = None
    goal: str | None = None
    race_date: str | None = None
    weeks: list[dict[str, Any]] | None = None
    load_curve: list[dict[str, Any]] | None = None
    storage: PlanStorage | dict[str, Any] | None = None
    error: ErrorDetail | str | None = None


class CoachingChangeSet(BaseModel):
    model_config = ConfigDict(extra="allow")

    total: int | None = None
    creates: int | None = None
    no_ops: int | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)


class ApplyTrainingBlockResult(ExtensibleResult):
    dry_run: bool | None = None
    write_performed: bool | None = None
    plan_id: str | None = None
    plan_revision_id: str | None = None
    revision: int | None = None
    change_set: CoachingChangeSet | None = None
    created: list[dict[str, Any]] | None = None
    idempotent_no_ops: list[dict[str, Any]] | None = None
    rollback: list[dict[str, Any]] | dict[str, Any] | None = None
    recovery_checklist: list[str] | None = None
    preview: dict[str, Any] | None = None
    warnings: list[str] | None = None
    message: str | None = None
    error: ErrorDetail | str | None = None


class AdaptWeekResult(ExtensibleResult):
    adaptation_id: str | None = None
    week_of: str | None = None
    readiness_state: str | None = None
    volume_multiplier: float | None = None
    reasons: list[str] | None = None
    changes: list[dict[str, Any] | str] | None = None
    patched_week: dict[str, Any] | None = None
    input_snapshot: dict[str, Any] | None = None
    stored: dict[str, Any] | None = None
    garmin_write_performed: bool | None = None
    error: ErrorDetail | str | None = None


class ApplyWeekAdaptationResult(ExtensibleResult):
    dry_run: bool | None = None
    write_performed: bool | None = None
    adaptation_id: str | None = None
    base_plan_revision_id: str | None = None
    new_plan_revision_id: str | None = None
    new_revision: int | None = None
    changes: list[dict[str, Any] | str] | None = None
    existing_generated_workouts_to_supersede: list[dict[str, Any]] | None = None
    new_sessions: list[dict[str, Any]] | None = None
    remote_result: dict[str, Any] | None = None
    superseded: list[dict[str, Any]] | None = None
    recovery_checklist: list[str] | None = None
    garmin_write_performed: bool | None = None
    preview: dict[str, Any] | None = None
    message: str | None = None
    error: ErrorDetail | str | None = None
