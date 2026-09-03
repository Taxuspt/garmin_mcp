"""Optional, local SQLite persistence for physiology and coaching data.

The store is deliberately independent from MCP and Garmin.  It is only opened
when ``GARMIN_DATA_DIR`` is configured (or a caller supplies a directory), so
the existing stateless server remains the default.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional, Sequence


SCHEMA_VERSION = 2
DEFAULT_DATABASE_NAME = "physiology.sqlite3"


def utc_now() -> str:
    """Return a stable, timezone-aware timestamp for persisted records."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def configured_data_dir(value: Optional[str] = None) -> Optional[Path]:
    """Resolve the opt-in data directory without creating it."""
    raw = value if value is not None else os.getenv("GARMIN_DATA_DIR")
    if raw is None or not raw.strip():
        return None
    return Path(raw).expanduser().resolve()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode_json_fields(row: sqlite3.Row | Mapping[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    result = dict(row)
    for field in fields:
        value = result.get(field)
        if isinstance(value, str):
            try:
                result[field] = json.loads(value)
            except json.JSONDecodeError:
                # A corrupted optional JSON field should remain inspectable.
                pass
    for field in ("active",):
        if field in result:
            result[field] = bool(result[field])
    return result


class PhysiologyStore:
    """Small sqlite3 repository with explicit, forward-only migrations."""

    def __init__(self, data_dir: str | os.PathLike[str], database_name: str = DEFAULT_DATABASE_NAME):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.database_path = self.data_dir / database_name
        self._migration_lock = threading.RLock()
        self._ensure_database()

    @classmethod
    def from_environment(cls) -> Optional["PhysiologyStore"]:
        directory = configured_data_dir()
        return cls(directory) if directory is not None else None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_database(self) -> None:
        with self._migration_lock:
            self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                os.chmod(self.data_dir, 0o700)
            except OSError:
                pass

            connection = self._connect()
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"Physiology database schema {version} is newer than supported schema {SCHEMA_VERSION}"
                    )
                if version < 1:
                    self._migrate_v1(connection)
                    version = 1
                if version < 2:
                    self._migrate_v2(connection)
                connection.commit()
            finally:
                connection.close()
            try:
                os.chmod(self.database_path, 0o600)
            except OSError:
                pass

    @staticmethod
    def _migrate_v1(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE athletes (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                external_id TEXT NOT NULL,
                display_name TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(provider, external_id)
            );

            CREATE TABLE observations (
                id TEXT PRIMARY KEY,
                athlete_id TEXT NOT NULL REFERENCES athletes(id),
                sport TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT NOT NULL,
                lower_bound REAL,
                upper_bound REAL,
                observed_at TEXT NOT NULL,
                source TEXT NOT NULL,
                method TEXT NOT NULL,
                confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                provenance_json TEXT NOT NULL DEFAULT '{}',
                supersedes_id TEXT REFERENCES observations(id),
                created_at TEXT NOT NULL
            );
            CREATE INDEX observations_lookup
                ON observations(athlete_id, sport, metric, observed_at DESC);

            CREATE TABLE threshold_estimates (
                id TEXT PRIMARY KEY,
                athlete_id TEXT NOT NULL REFERENCES athletes(id),
                sport TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT NOT NULL,
                lower_bound REAL NOT NULL,
                upper_bound REAL NOT NULL,
                confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                status TEXT NOT NULL CHECK(status IN ('pending', 'conflict', 'accepted', 'rejected')),
                primary_source TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                conflicts_json TEXT NOT NULL DEFAULT '[]',
                observed_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                algorithm_version TEXT NOT NULL,
                input_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                accepted_at TEXT
            );
            CREATE INDEX threshold_estimates_lookup
                ON threshold_estimates(athlete_id, sport, metric, created_at DESC);

            CREATE TABLE zone_models (
                id TEXT PRIMARY KEY,
                athlete_id TEXT NOT NULL REFERENCES athletes(id),
                sport TEXT NOT NULL,
                metric TEXT NOT NULL,
                name TEXT NOT NULL,
                zones_json TEXT NOT NULL,
                vt1 REAL,
                vt2 REAL,
                source TEXT NOT NULL,
                version TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0, 1)),
                created_at TEXT NOT NULL
            );
            CREATE INDEX zone_models_lookup
                ON zone_models(athlete_id, sport, metric, active, observed_at DESC);

            CREATE TABLE analysis_results (
                id TEXT PRIMARY KEY,
                athlete_id TEXT NOT NULL REFERENCES athletes(id),
                analysis_type TEXT NOT NULL,
                activity_id TEXT,
                result_json TEXT NOT NULL,
                algorithm_version TEXT NOT NULL,
                input_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE plans (
                id TEXT PRIMARY KEY,
                logical_plan_id TEXT NOT NULL,
                athlete_id TEXT NOT NULL REFERENCES athletes(id),
                sport TEXT NOT NULL,
                revision INTEGER NOT NULL,
                status TEXT NOT NULL,
                input_hash TEXT NOT NULL UNIQUE,
                plan_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(logical_plan_id, revision)
            );
            CREATE INDEX plans_logical_lookup
                ON plans(logical_plan_id, revision DESC);

            CREATE TABLE workout_links (
                id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL REFERENCES plans(id),
                local_workout_key TEXT NOT NULL,
                provider TEXT NOT NULL,
                external_workout_id TEXT,
                scheduled_date TEXT,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(plan_id, local_workout_key)
            );

            CREATE TABLE adaptations (
                id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL REFERENCES plans(id),
                week_of TEXT NOT NULL,
                revision INTEGER NOT NULL,
                status TEXT NOT NULL,
                input_hash TEXT NOT NULL UNIQUE,
                patch_json TEXT NOT NULL,
                reasons_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE physiology_test_imports (
                id TEXT PRIMARY KEY,
                athlete_id TEXT NOT NULL REFERENCES athletes(id),
                sport TEXT NOT NULL,
                test_type TEXT NOT NULL,
                source_name TEXT,
                file_sha256 TEXT NOT NULL,
                column_map_json TEXT NOT NULL,
                units_json TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                observed_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(athlete_id, file_sha256, test_type)
            );

            CREATE TABLE physiology_test_samples (
                import_id TEXT NOT NULL REFERENCES physiology_test_imports(id) ON DELETE CASCADE,
                sample_index INTEGER NOT NULL,
                elapsed_seconds REAL,
                observed_at TEXT,
                values_json TEXT NOT NULL,
                PRIMARY KEY(import_id, sample_index)
            );

            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_id TEXT REFERENCES athletes(id),
                action TEXT NOT NULL,
                object_type TEXT NOT NULL,
                object_id TEXT,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            PRAGMA user_version = 1;
            """
        )

    @staticmethod
    def _migrate_v2(connection: sqlite3.Connection) -> None:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(workout_links)")
        }
        if "external_schedule_id" not in columns:
            connection.execute(
                "ALTER TABLE workout_links ADD COLUMN external_schedule_id TEXT"
            )
        connection.execute("PRAGMA user_version = 2")

    @property
    def schema_version(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def ensure_athlete(
        self,
        provider: str = "garmin",
        external_id: str = "local",
        display_name: Optional[str] = None,
    ) -> str:
        athlete_id = f"{provider}:{external_id}"
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO athletes(id, provider, external_id, display_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, external_id) DO UPDATE SET
                    display_name = COALESCE(excluded.display_name, athletes.display_name),
                    updated_at = excluded.updated_at
                """,
                (athlete_id, provider, external_id, display_name, now, now),
            )
        return athlete_id

    def add_observation(
        self,
        *,
        athlete_id: str,
        sport: str,
        metric: str,
        value: float,
        unit: str,
        observed_at: str,
        source: str,
        method: str,
        confidence: float,
        lower_bound: Optional[float] = None,
        upper_bound: Optional[float] = None,
        provenance: Optional[Mapping[str, Any]] = None,
        supersedes_id: Optional[str] = None,
        observation_id: Optional[str] = None,
    ) -> dict[str, Any]:
        observation_id = observation_id or str(uuid.uuid4())
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO observations(
                    id, athlete_id, sport, metric, value, unit, lower_bound, upper_bound,
                    observed_at, source, method, confidence, provenance_json, supersedes_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    athlete_id,
                    sport,
                    metric,
                    float(value),
                    unit,
                    lower_bound,
                    upper_bound,
                    observed_at,
                    source,
                    method,
                    float(confidence),
                    _json(dict(provenance or {})),
                    supersedes_id,
                    now,
                ),
            )
            self._audit(
                connection,
                athlete_id,
                "create",
                "observation",
                observation_id,
                {"sport": sport, "metric": metric, "source": source},
            )
            row = connection.execute("SELECT * FROM observations WHERE id = ?", (observation_id,)).fetchone()
        return _decode_json_fields(row, ("provenance_json",))

    def list_observations(
        self,
        *,
        athlete_id: str,
        sport: Optional[str] = None,
        metrics: Optional[Iterable[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        where = ["athlete_id = ?"]
        params: list[Any] = [athlete_id]
        if sport is not None:
            where.append("sport = ?")
            params.append(sport)
        metric_values = list(metrics or [])
        if metric_values:
            where.append(f"metric IN ({','.join('?' for _ in metric_values)})")
            params.extend(metric_values)
        if start_date is not None:
            where.append("observed_at >= ?")
            params.append(start_date)
        if end_date is not None:
            where.append("observed_at <= ?")
            params.append(end_date)
        query = f"SELECT * FROM observations WHERE {' AND '.join(where)} ORDER BY observed_at DESC, id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_decode_json_fields(row, ("provenance_json",)) for row in rows]

    def save_zone_model(
        self,
        *,
        athlete_id: str,
        sport: str,
        metric: str,
        name: str,
        zones: list[Mapping[str, Any]],
        source: str,
        version: str,
        observed_at: str,
        vt1: Optional[float] = None,
        vt2: Optional[float] = None,
        active: bool = False,
        model_id: Optional[str] = None,
    ) -> dict[str, Any]:
        model_id = model_id or str(uuid.uuid4())
        now = utc_now()
        with self.transaction() as connection:
            if active:
                connection.execute(
                    "UPDATE zone_models SET active = 0 WHERE athlete_id = ? AND sport = ? AND metric = ?",
                    (athlete_id, sport, metric),
                )
            connection.execute(
                """
                INSERT INTO zone_models(
                    id, athlete_id, sport, metric, name, zones_json, vt1, vt2,
                    source, version, observed_at, active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model_id,
                    athlete_id,
                    sport,
                    metric,
                    name,
                    _json(zones),
                    vt1,
                    vt2,
                    source,
                    version,
                    observed_at,
                    int(active),
                    now,
                ),
            )
            self._audit(connection, athlete_id, "create", "zone_model", model_id, {"active": active})
            row = connection.execute("SELECT * FROM zone_models WHERE id = ?", (model_id,)).fetchone()
        return _decode_json_fields(row, ("zones_json",))

    def list_zone_models(
        self,
        *,
        athlete_id: str,
        sport: Optional[str] = None,
        metric: Optional[str] = None,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        where = ["athlete_id = ?"]
        params: list[Any] = [athlete_id]
        for column, value in (("sport", sport), ("metric", metric)):
            if value is not None:
                where.append(f"{column} = ?")
                params.append(value)
        if active_only:
            where.append("active = 1")
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM zone_models WHERE {' AND '.join(where)} ORDER BY observed_at DESC, id",
                params,
            ).fetchall()
        return [_decode_json_fields(row, ("zones_json",)) for row in rows]

    def activate_zone_model(self, *, athlete_id: str, model_id: str) -> dict[str, Any]:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM zone_models WHERE id = ? AND athlete_id = ?",
                (model_id, athlete_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown zone model: {model_id}")
            connection.execute(
                "UPDATE zone_models SET active = 0 WHERE athlete_id = ? AND sport = ? AND metric = ?",
                (athlete_id, row["sport"], row["metric"]),
            )
            connection.execute("UPDATE zone_models SET active = 1 WHERE id = ?", (model_id,))
            self._audit(connection, athlete_id, "activate", "zone_model", model_id, {})
            updated = connection.execute("SELECT * FROM zone_models WHERE id = ?", (model_id,)).fetchone()
        return _decode_json_fields(updated, ("zones_json",))

    def put_threshold_estimate(self, estimate: Mapping[str, Any]) -> dict[str, Any]:
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM threshold_estimates WHERE input_hash = ?", (estimate["input_hash"],)
            ).fetchone()
            if existing is not None:
                return _decode_json_fields(existing, ("evidence_json", "conflicts_json"))

            estimate_id = str(estimate.get("id") or uuid.uuid4())
            now = utc_now()
            connection.execute(
                """
                INSERT INTO threshold_estimates(
                    id, athlete_id, sport, metric, value, unit, lower_bound, upper_bound,
                    confidence, status, primary_source, evidence_json, conflicts_json,
                    observed_at, expires_at, algorithm_version, input_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    estimate_id,
                    estimate["athlete_id"],
                    estimate["sport"],
                    estimate["metric"],
                    estimate["value"],
                    estimate["unit"],
                    estimate["lower_bound"],
                    estimate["upper_bound"],
                    estimate["confidence"],
                    estimate["status"],
                    estimate["primary_source"],
                    _json(estimate["evidence"]),
                    _json(estimate.get("conflicts", [])),
                    estimate["observed_at"],
                    estimate["expires_at"],
                    estimate["algorithm_version"],
                    estimate["input_hash"],
                    now,
                ),
            )
            self._audit(
                connection,
                estimate["athlete_id"],
                "create",
                "threshold_estimate",
                estimate_id,
                {"metric": estimate["metric"], "status": estimate["status"]},
            )
            row = connection.execute("SELECT * FROM threshold_estimates WHERE id = ?", (estimate_id,)).fetchone()
        return _decode_json_fields(row, ("evidence_json", "conflicts_json"))

    def get_threshold_estimate(self, estimate_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM threshold_estimates WHERE id = ?", (estimate_id,)).fetchone()
        return None if row is None else _decode_json_fields(row, ("evidence_json", "conflicts_json"))

    def list_threshold_estimates(
        self, *, athlete_id: str, sport: Optional[str] = None, metric: Optional[str] = None
    ) -> list[dict[str, Any]]:
        where = ["athlete_id = ?"]
        params: list[Any] = [athlete_id]
        if sport is not None:
            where.append("sport = ?")
            params.append(sport)
        if metric is not None:
            where.append("metric = ?")
            params.append(metric)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM threshold_estimates WHERE {' AND '.join(where)} ORDER BY created_at DESC, id",
                params,
            ).fetchall()
        return [_decode_json_fields(row, ("evidence_json", "conflicts_json")) for row in rows]

    def put_analysis_result(
        self,
        *,
        athlete_id: str,
        analysis_type: str,
        result: Mapping[str, Any],
        algorithm_version: str,
        input_hash: str,
        activity_id: Optional[str] = None,
    ) -> tuple[dict[str, Any], bool]:
        """Persist an immutable analysis result, idempotently by input hash."""
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM analysis_results WHERE input_hash = ?", (input_hash,)
            ).fetchone()
            if existing is not None:
                return _decode_json_fields(existing, ("result_json",)), False

            analysis_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO analysis_results(
                    id, athlete_id, analysis_type, activity_id, result_json,
                    algorithm_version, input_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    athlete_id,
                    analysis_type,
                    activity_id,
                    _json(dict(result)),
                    algorithm_version,
                    input_hash,
                    utc_now(),
                ),
            )
            self._audit(
                connection,
                athlete_id,
                "create",
                "analysis_result",
                analysis_id,
                {
                    "analysis_type": analysis_type,
                    "activity_id": activity_id,
                    "algorithm_version": algorithm_version,
                    "input_hash": input_hash,
                },
            )
            row = connection.execute(
                "SELECT * FROM analysis_results WHERE id = ?", (analysis_id,)
            ).fetchone()
        return _decode_json_fields(row, ("result_json",)), True

    def accept_threshold_estimate(
        self, *, athlete_id: str, estimate_id: str, allow_conflict: bool = False
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        with self.transaction() as connection:
            estimate = connection.execute(
                "SELECT * FROM threshold_estimates WHERE id = ? AND athlete_id = ?",
                (estimate_id, athlete_id),
            ).fetchone()
            if estimate is None:
                raise KeyError(f"Unknown threshold estimate: {estimate_id}")
            if estimate["status"] == "conflict" and not allow_conflict:
                raise ValueError(
                    "Conflicting threshold estimates require explicit acknowledgement before acceptance"
                )
            if estimate["status"] == "rejected":
                raise ValueError("Rejected threshold estimates cannot be accepted")

            existing_observation = connection.execute(
                "SELECT * FROM observations WHERE athlete_id = ? AND provenance_json LIKE ? ORDER BY created_at DESC LIMIT 1",
                (athlete_id, f'%"estimate_id":"{estimate_id}"%'),
            ).fetchone()
            if existing_observation is None:
                superseded = connection.execute(
                    """
                    SELECT id FROM observations
                    WHERE athlete_id = ? AND sport = ? AND metric = ? AND method = 'accepted_estimate'
                    ORDER BY observed_at DESC LIMIT 1
                    """,
                    (athlete_id, estimate["sport"], estimate["metric"]),
                ).fetchone()
                observation_id = str(uuid.uuid4())
                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO observations(
                        id, athlete_id, sport, metric, value, unit, lower_bound, upper_bound,
                        observed_at, source, method, confidence, provenance_json, supersedes_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'derived', 'accepted_estimate', ?, ?, ?, ?)
                    """,
                    (
                        observation_id,
                        athlete_id,
                        estimate["sport"],
                        estimate["metric"],
                        estimate["value"],
                        estimate["unit"],
                        estimate["lower_bound"],
                        estimate["upper_bound"],
                        estimate["observed_at"],
                        estimate["confidence"],
                        _json({"estimate_id": estimate_id, "algorithm_version": estimate["algorithm_version"]}),
                        superseded["id"] if superseded else None,
                        now,
                    ),
                )
                existing_observation = connection.execute(
                    "SELECT * FROM observations WHERE id = ?", (observation_id,)
                ).fetchone()
            accepted_at = estimate["accepted_at"] or utc_now()
            connection.execute(
                "UPDATE threshold_estimates SET status = 'accepted', accepted_at = ? WHERE id = ?",
                (accepted_at, estimate_id),
            )
            self._audit(connection, athlete_id, "accept", "threshold_estimate", estimate_id, {})
            accepted = connection.execute(
                "SELECT * FROM threshold_estimates WHERE id = ?", (estimate_id,)
            ).fetchone()
        return (
            _decode_json_fields(accepted, ("evidence_json", "conflicts_json")),
            _decode_json_fields(existing_observation, ("provenance_json",)),
        )

    def save_plan(
        self,
        *,
        athlete_id: str,
        sport: str,
        plan: Mapping[str, Any],
        input_hash: str,
        status: str = "draft",
        revision: int = 1,
        plan_id: Optional[str] = None,
        revision_id: Optional[str] = None,
    ) -> tuple[dict[str, Any], bool]:
        """Persist one immutable plan revision, idempotently by input hash."""
        with self.transaction() as connection:
            existing = connection.execute("SELECT * FROM plans WHERE input_hash = ?", (input_hash,)).fetchone()
            if existing is not None:
                return _decode_json_fields(existing, ("plan_json",)), False
            logical_plan_id = plan_id or str(uuid.uuid4())
            revision_id = revision_id or str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO plans(
                    id, logical_plan_id, athlete_id, sport, revision, status,
                    input_hash, plan_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    logical_plan_id,
                    athlete_id,
                    sport,
                    int(revision),
                    status,
                    input_hash,
                    _json(dict(plan)),
                    utc_now(),
                ),
            )
            self._audit(
                connection,
                athlete_id,
                "create",
                "plan",
                revision_id,
                {"logical_plan_id": logical_plan_id, "revision": revision},
            )
            row = connection.execute("SELECT * FROM plans WHERE id = ?", (revision_id,)).fetchone()
        return _decode_json_fields(row, ("plan_json",)), True

    def get_plan(self, plan_id: str, revision: Optional[int] = None) -> Optional[dict[str, Any]]:
        """Get a revision by row id, or the requested/latest logical plan revision."""
        with self._connect() as connection:
            if revision is None:
                row = connection.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
                if row is None:
                    row = connection.execute(
                        """
                        SELECT * FROM plans WHERE logical_plan_id = ?
                        ORDER BY revision DESC, created_at DESC LIMIT 1
                        """,
                        (plan_id,),
                    ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM plans WHERE logical_plan_id = ? AND revision = ?",
                    (plan_id, int(revision)),
                ).fetchone()
        return None if row is None else _decode_json_fields(row, ("plan_json",))

    def list_plans(
        self,
        *,
        athlete_id: Optional[str] = None,
        sport: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        for column, value in (("athlete_id", athlete_id), ("sport", sport), ("status", status)):
            if value is not None:
                where.append(f"{column} = ?")
                params.append(value)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM plans {clause} ORDER BY created_at DESC, revision DESC, id",
                params,
            ).fetchall()
        return [_decode_json_fields(row, ("plan_json",)) for row in rows]

    def get_latest_plan(
        self, *, sport: str = "cycling", athlete_id: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        plans = self.list_plans(athlete_id=athlete_id, sport=sport)
        return plans[0] if plans else None

    @staticmethod
    def _resolve_plan(connection: sqlite3.Connection, plan_id: str) -> Optional[sqlite3.Row]:
        row = connection.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            row = connection.execute(
                """
                SELECT * FROM plans WHERE logical_plan_id = ?
                ORDER BY revision DESC, created_at DESC LIMIT 1
                """,
                (plan_id,),
            ).fetchone()
        return row

    def save_adaptation(
        self,
        *,
        plan_id: str,
        week_of: str,
        revision: int,
        patch: Mapping[str, Any],
        reasons: Sequence[Any],
        input_hash: str,
        status: str = "pending",
        adaptation_id: Optional[str] = None,
    ) -> tuple[dict[str, Any], bool]:
        """Persist an immutable, idempotent weekly adaptation."""
        with self.transaction() as connection:
            plan = self._resolve_plan(connection, plan_id)
            if plan is None:
                raise KeyError(f"Unknown plan: {plan_id}")
            existing = connection.execute(
                "SELECT * FROM adaptations WHERE input_hash = ?", (input_hash,)
            ).fetchone()
            if existing is not None:
                return _decode_json_fields(existing, ("patch_json", "reasons_json")), False
            adaptation_id = adaptation_id or str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO adaptations(
                    id, plan_id, week_of, revision, status, input_hash, patch_json, reasons_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    adaptation_id,
                    plan["id"],
                    week_of,
                    int(revision),
                    status,
                    input_hash,
                    _json(dict(patch)),
                    _json(list(reasons)),
                    utc_now(),
                ),
            )
            self._audit(
                connection,
                plan["athlete_id"],
                "create",
                "adaptation",
                adaptation_id,
                {
                    "plan_id": plan["logical_plan_id"],
                    "plan_revision_id": plan["id"],
                    "week_of": week_of,
                    "revision": revision,
                },
            )
            row = connection.execute(
                "SELECT * FROM adaptations WHERE id = ?", (adaptation_id,)
            ).fetchone()
        return _decode_json_fields(row, ("patch_json", "reasons_json")), True

    def get_adaptation(self, adaptation_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM adaptations WHERE id = ?", (adaptation_id,)
            ).fetchone()
        return None if row is None else _decode_json_fields(row, ("patch_json", "reasons_json"))

    def update_adaptation_status(self, *, adaptation_id: str, status: str) -> dict[str, Any]:
        if status not in {"pending", "applied", "failed"}:
            raise ValueError("adaptation status must be pending, applied, or failed")
        with self.transaction() as connection:
            existing = connection.execute(
                """
                SELECT adaptations.*, plans.athlete_id AS owner_athlete_id
                FROM adaptations JOIN plans ON plans.id = adaptations.plan_id
                WHERE adaptations.id = ?
                """,
                (adaptation_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(f"Unknown adaptation: {adaptation_id}")
            connection.execute("UPDATE adaptations SET status = ? WHERE id = ?", (status, adaptation_id))
            self._audit(
                connection,
                existing["owner_athlete_id"],
                "update_status",
                "adaptation",
                adaptation_id,
                {"from": existing["status"], "to": status},
            )
            row = connection.execute(
                "SELECT * FROM adaptations WHERE id = ?", (adaptation_id,)
            ).fetchone()
        return _decode_json_fields(row, ("patch_json", "reasons_json"))

    def put_workout_link(
        self,
        *,
        plan_id: str,
        local_workout_key: str,
        provider: str,
        state: str,
        external_workout_id: Optional[str] = None,
        external_schedule_id: Optional[str] = None,
        scheduled_date: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create or update the provider link for a generated workout."""
        with self.transaction() as connection:
            plan = self._resolve_plan(connection, plan_id)
            if plan is None:
                raise KeyError(f"Unknown plan: {plan_id}")
            existing = connection.execute(
                "SELECT id FROM workout_links WHERE plan_id = ? AND local_workout_key = ?",
                (plan["id"], local_workout_key),
            ).fetchone()
            link_id = existing["id"] if existing else str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO workout_links(
                    id, plan_id, local_workout_key, provider, external_workout_id,
                    scheduled_date, state, created_at, external_schedule_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plan_id, local_workout_key) DO UPDATE SET
                    provider = excluded.provider,
                    external_workout_id = excluded.external_workout_id,
                    external_schedule_id = excluded.external_schedule_id,
                    scheduled_date = excluded.scheduled_date,
                    state = excluded.state
                """,
                (
                    link_id,
                    plan["id"],
                    local_workout_key,
                    provider,
                    external_workout_id,
                    scheduled_date,
                    state,
                    utc_now(),
                    external_schedule_id,
                ),
            )
            row = connection.execute("SELECT * FROM workout_links WHERE id = ?", (link_id,)).fetchone()
        return dict(row)

    def list_workout_links(
        self, *, plan_id: str, local_workout_key: Optional[str] = None
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            plan = self._resolve_plan(connection, plan_id)
            if plan is None:
                raise KeyError(f"Unknown plan: {plan_id}")
            where = ["plan_id = ?"]
            params: list[Any] = [plan["id"]]
            if local_workout_key is not None:
                where.append("local_workout_key = ?")
                params.append(local_workout_key)
            rows = connection.execute(
                f"SELECT * FROM workout_links WHERE {' AND '.join(where)} ORDER BY created_at, id",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def update_workout_link_state(
        self,
        *,
        link_id: str,
        state: str,
        external_workout_id: Optional[str] = None,
        external_schedule_id: Optional[str] = None,
        scheduled_date: Optional[str] = None,
    ) -> dict[str, Any]:
        with self.transaction() as connection:
            existing = connection.execute("SELECT * FROM workout_links WHERE id = ?", (link_id,)).fetchone()
            if existing is None:
                raise KeyError(f"Unknown workout link: {link_id}")
            connection.execute(
                """
                UPDATE workout_links SET
                    state = ?,
                    external_workout_id = COALESCE(?, external_workout_id),
                    external_schedule_id = COALESCE(?, external_schedule_id),
                    scheduled_date = COALESCE(?, scheduled_date)
                WHERE id = ?
                """,
                (
                    state,
                    external_workout_id,
                    external_schedule_id,
                    scheduled_date,
                    link_id,
                ),
            )
            row = connection.execute("SELECT * FROM workout_links WHERE id = ?", (link_id,)).fetchone()
        return dict(row)

    def add_audit(
        self,
        *,
        action: str,
        object_type: str,
        object_id: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
        athlete_id: Optional[str] = None,
    ) -> int:
        with self.transaction() as connection:
            self._audit(connection, athlete_id, action, object_type, object_id, details or {})
            return int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])

    def save_test_import(
        self,
        *,
        athlete_id: str,
        sport: str,
        test_type: str,
        source_name: Optional[str],
        file_sha256: str,
        column_map: Mapping[str, str],
        units: Mapping[str, str],
        observed_at: str,
        samples: list[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        with self.transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM physiology_test_imports
                WHERE athlete_id = ? AND file_sha256 = ? AND test_type = ?
                """,
                (athlete_id, file_sha256, test_type),
            ).fetchone()
            if existing is not None:
                return _decode_json_fields(existing, ("column_map_json", "units_json")), False

            import_id = str(uuid.uuid4())
            now = utc_now()
            connection.execute(
                """
                INSERT INTO physiology_test_imports(
                    id, athlete_id, sport, test_type, source_name, file_sha256,
                    column_map_json, units_json, row_count, observed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    import_id,
                    athlete_id,
                    sport,
                    test_type,
                    source_name,
                    file_sha256,
                    _json(dict(column_map)),
                    _json(dict(units)),
                    len(samples),
                    observed_at,
                    now,
                ),
            )
            connection.executemany(
                """
                INSERT INTO physiology_test_samples(import_id, sample_index, elapsed_seconds, observed_at, values_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        import_id,
                        index,
                        sample.get("elapsed_seconds"),
                        sample.get("observed_at"),
                        _json({k: v for k, v in sample.items() if k not in {"elapsed_seconds", "observed_at"}}),
                    )
                    for index, sample in enumerate(samples)
                ],
            )
            self._audit(
                connection,
                athlete_id,
                "import",
                "physiology_test",
                import_id,
                {"test_type": test_type, "row_count": len(samples), "file_sha256": file_sha256},
            )
            row = connection.execute(
                "SELECT * FROM physiology_test_imports WHERE id = ?", (import_id,)
            ).fetchone()
        return _decode_json_fields(row, ("column_map_json", "units_json")), True

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        athlete_id: Optional[str],
        action: str,
        object_type: str,
        object_id: Optional[str],
        details: Mapping[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_log(athlete_id, action, object_type, object_id, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (athlete_id, action, object_type, object_id, _json(dict(details)), utc_now()),
        )
