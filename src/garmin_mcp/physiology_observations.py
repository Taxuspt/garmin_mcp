"""Opt-in, append-only local physiology observations extracted from PR #295.

This intentionally omits estimates, active profiles, zone sync and coaching.
One configured directory is one local notebook; use distinct directories for
accounts/athletes. Nothing authenticates to or writes to Garmin.
"""

import math
import os
import sqlite3
import stat
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

METRIC_UNITS = {
    "vt1": "bpm",
    "vt2": "bpm",
    "lthr": "bpm",
    "ftp": "W",
    "max_hr": "bpm",
    "resting_hr": "bpm",
}
SPORTS = frozenset({"cycling", "running", "swimming", "other"})
SCHEMA_VERSION = 1
DATABASE_NAME = "observations.sqlite3"
_COLUMNS = (
    "id",
    "sport",
    "metric",
    "value",
    "unit",
    "observed_at",
    "source",
    "method",
    "confidence",
    "lower_bound",
    "upper_bound",
    "created_at",
)


def configured_directory():
    """Read only server-controlled configuration; never create directories here."""
    raw = os.getenv("GARMIN_DATA_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(
            f"{name} must be finite" + (" and positive" if positive else "")
        )
    return number


def validate_observation(value):
    """Normalize user-supplied evidence, not a derived or accepted threshold."""
    if not isinstance(value, dict):
        raise TypeError("observation must be an object")
    allowed = set(_COLUMNS) - {"id", "created_at"}
    if value.keys() - allowed:
        raise ValueError("Unknown observation fields")
    if value.get("sport") not in SPORTS:
        raise ValueError("sport must be cycling, running, swimming or other")
    metric = value.get("metric")
    if metric not in METRIC_UNITS:
        raise ValueError("metric must be vt1, vt2, lthr, ftp, max_hr or resting_hr")
    if value.get("unit") != METRIC_UNITS[metric]:
        raise ValueError(f"{metric} requires unit {METRIC_UNITS[metric]}")
    numeric = _number(value.get("value"), "value", positive=True)
    if METRIC_UNITS[metric] == "bpm" and numeric > 300:
        raise ValueError("Heart-rate observations must be at most 300 bpm")
    timestamp = value.get("observed_at")
    if not isinstance(timestamp, str) or len(timestamp) > 64:
        raise ValueError(
            "observed_at requires an ISO timestamp with an explicit UTC offset"
        )
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("missing offset")
        observed_at = parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError) as exc:
        raise ValueError(
            "observed_at requires an ISO timestamp with an explicit UTC offset"
        ) from exc
    labels = {}
    for key, default in (("source", None), ("method", "user_report")):
        label = value.get(key, default)
        if not isinstance(label, str) or not label.strip() or len(label) > 256:
            raise ValueError(f"{key} requires 1-256 non-blank characters")
        labels[key] = label
    confidence = value.get("confidence")
    if confidence is not None:
        confidence = _number(confidence, "confidence")
        if not 0 <= confidence <= 1:
            raise ValueError(
                "confidence must be between 0 and 1 (user supplied, not computed)"
            )
    lower, upper = value.get("lower_bound"), value.get("upper_bound")
    if (lower is None) != (upper is None):
        raise ValueError("lower_bound and upper_bound must be supplied together")
    if lower is not None:
        lower = _number(lower, "lower_bound", positive=True)
        upper = _number(upper, "upper_bound", positive=True)
        if not lower <= numeric <= upper:
            raise ValueError("Bounds must contain the observation value")
        if METRIC_UNITS[metric] == "bpm" and upper > 300:
            raise ValueError("Heart-rate bounds must be at most 300 bpm")
    return {
        "sport": value["sport"],
        "metric": metric,
        "value": numeric,
        "unit": value["unit"],
        "observed_at": observed_at,
        **labels,
        "confidence": confidence,
        "lower_bound": lower,
        "upper_bound": upper,
    }


def validate_query(sport=None, metric=None, limit=50, offset=0):
    if sport is not None and sport not in SPORTS:
        raise ValueError("Unknown sport filter")
    if metric is not None and metric not in METRIC_UNITS:
        raise ValueError("Unknown metric filter")
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    if type(offset) is not int or not 0 <= offset <= 1_000_000:
        raise ValueError("offset must be between 0 and 1000000")


def _private(path, *, directory=False):
    if path.is_symlink():
        raise ValueError("Observation storage refuses symlink database files")
    metadata = path.stat()
    if not (
        stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    ):
        raise ValueError(
            "Observation storage requires a regular database and directory"
        )
    if os.name == "posix" and (
        metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077
    ):
        raise ValueError(
            "Observation storage must be owned by this user with owner-only permissions"
        )


class ObservationStore:
    """A lazily opened notebook, scoped only to its configured directory.

    Constructors and reads never create storage. A write creates a private leaf
    directory and database on POSIX, but never chmods existing user directories.
    Windows relies on the configured directory's inherited filesystem ACLs.
    """

    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        self.path = self.directory / DATABASE_NAME

    def _prepare(self, create=False):
        if create:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.directory.exists():
            return False
        _private(self.directory, directory=True)
        if self.path.is_symlink():
            raise ValueError("Observation storage refuses symlink database files")
        if create:
            try:
                descriptor = os.open(
                    self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
        if not self.path.exists():
            return False
        _private(self.path)
        return True

    @staticmethod
    def _check_schema(connection, create=False):
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0 and create:
            connection.execute("""CREATE TABLE observations (
                id TEXT PRIMARY KEY, sport TEXT NOT NULL, metric TEXT NOT NULL,
                value REAL NOT NULL CHECK(value > 0), unit TEXT NOT NULL,
                observed_at TEXT NOT NULL, source TEXT NOT NULL, method TEXT NOT NULL,
                confidence REAL CHECK(confidence BETWEEN 0 AND 1),
                lower_bound REAL, upper_bound REAL, created_at TEXT NOT NULL)""")
            connection.execute(
                "CREATE INDEX observation_lookup ON observations(sport, metric, observed_at DESC, created_at DESC, id DESC)"
            )
            connection.execute("PRAGMA user_version = 1")
        elif version != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported observation schema version {version}; database was not migrated"
            )

    def record(self, observation):
        normalized = validate_observation(observation)
        self._prepare(create=True)
        row = {
            "id": uuid.uuid4().hex,
            **normalized,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            try:
                # Serialize first-time schema initialization across threads/processes.
                connection.execute("BEGIN IMMEDIATE")
                self._check_schema(connection, create=True)
                connection.execute(
                    """INSERT INTO observations
                    (id, sport, metric, value, unit, observed_at, source, method,
                     confidence, lower_bound, upper_bound, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    tuple(row[column] for column in _COLUMNS),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return row

    def list(self, sport=None, metric=None, limit=50, offset=0):
        validate_query(sport, metric, limit, offset)
        if not self._prepare():
            return {
                "status": "ok",
                "observations": [],
                "has_more": False,
                "next_offset": None,
            }
        with closing(
            sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10)
        ) as connection:
            connection.row_factory = sqlite3.Row
            self._check_schema(connection)
            rows = connection.execute(
                """SELECT * FROM observations
                WHERE (? IS NULL OR sport = ?) AND (? IS NULL OR metric = ?)
                ORDER BY observed_at DESC, created_at DESC, id DESC LIMIT ? OFFSET ?""",
                (sport, sport, metric, metric, limit + 1, offset),
            ).fetchall()
        has_more = len(rows) > limit
        return {
            "status": "ok",
            "observations": [dict(row) for row in rows[:limit]],
            "has_more": has_more,
            "next_offset": offset + limit if has_more else None,
        }
