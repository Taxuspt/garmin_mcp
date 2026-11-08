"""Append-only local observations: actual MCP + SQLite, no real Garmin calls."""

import json
import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import training


def observation(**overrides):
    return {
        "sport": "cycling",
        "metric": "ftp",
        "value": 220,
        "unit": "W",
        "observed_at": "2024-01-01T12:00:00+08:00",
        "source": "User-supplied field test",
        "method": "20-minute test",
        **overrides,
    }


@pytest.fixture
def app(mock_garmin_client):
    training.configure(mock_garmin_client)
    return training.register_tools(FastMCP("Local observations"))


async def call(app, name, **kwargs):
    result = await app.call_tool(name, kwargs)
    return json.loads(result[0][0].text)


@pytest.mark.asyncio
async def test_preview_is_default_and_creates_nothing(
    app, tmp_path, monkeypatch, mock_garmin_client
):
    directory = tmp_path / "disabled-until-write"
    monkeypatch.setenv("GARMIN_DATA_DIR", str(directory))
    result = await call(app, "record_physiology_observation", observation=observation())
    assert result["status"] == "preview"
    assert result["write_performed"] is False
    assert result["observation"]["observed_at"] == "2024-01-01T04:00:00+00:00"
    assert not directory.exists()
    assert mock_garmin_client.mock_calls == []


@pytest.mark.asyncio
async def test_unconfigured_storage_is_disabled(app, monkeypatch, mock_garmin_client):
    monkeypatch.delenv("GARMIN_DATA_DIR", raising=False)
    written = await call(
        app, "record_physiology_observation", observation=observation(), dry_run=False
    )
    listed = await call(app, "list_physiology_observations")
    assert written["status"] == listed["status"] == "disabled"
    assert written["write_performed"] is False
    assert mock_garmin_client.mock_calls == []


@pytest.mark.asyncio
async def test_read_does_not_initialize_a_database(app, tmp_path, monkeypatch):
    directory = tmp_path / "unused"
    monkeypatch.setenv("GARMIN_DATA_DIR", str(directory))
    result = await call(app, "list_physiology_observations")
    assert result["status"] == "ok"
    assert result["observations"] == []
    assert result["has_more"] is False
    assert not directory.exists()


@pytest.mark.asyncio
async def test_record_read_and_sport_metric_isolation(
    app, tmp_path, monkeypatch, mock_garmin_client
):
    monkeypatch.setenv("GARMIN_DATA_DIR", str(tmp_path / "private"))
    first = await call(
        app, "record_physiology_observation", observation=observation(), dry_run=False
    )
    await call(
        app,
        "record_physiology_observation",
        observation=observation(sport="running"),
        dry_run=False,
    )
    await call(
        app,
        "record_physiology_observation",
        observation=observation(metric="lthr", value=160, unit="bpm"),
        dry_run=False,
    )
    result = await call(
        app, "list_physiology_observations", sport="cycling", metric="ftp"
    )
    assert first["write_performed"] is True
    assert result["observations"] == [first["observation"]]
    row = result["observations"][0]
    assert row["source"] == "User-supplied field test"
    assert row["method"] == "20-minute test"
    assert row["observed_at"] == "2024-01-01T04:00:00+00:00"
    assert mock_garmin_client.mock_calls == []


@pytest.mark.asyncio
async def test_append_only_pagination_and_distinct_directories(
    app, tmp_path, monkeypatch
):
    monkeypatch.setenv("GARMIN_DATA_DIR", str(tmp_path / "athlete-a"))
    ids = []
    for value in [200, 210, 220]:
        result = await call(
            app,
            "record_physiology_observation",
            observation=observation(value=value),
            dry_run=False,
        )
        ids.append(result["observation"]["id"])
    assert len(set(ids)) == 3
    first = await call(app, "list_physiology_observations", limit=2)
    second = await call(
        app, "list_physiology_observations", limit=2, offset=first["next_offset"]
    )
    assert len(first["observations"] + second["observations"]) == 3
    assert {r["id"] for r in first["observations"] + second["observations"]} == set(ids)
    assert second["next_offset"] is None
    monkeypatch.setenv("GARMIN_DATA_DIR", str(tmp_path / "athlete-b"))
    other = await call(app, "list_physiology_observations")
    assert other["observations"] == []
    assert not (tmp_path / "athlete-b").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"sport": "run"},
        {"metric": "secret"},
        {"unit": "bpm"},
        {"value": 0},
        {"value": -1},
        {"value": True},
        {"value": float("nan")},
        {"value": float("inf")},
        {"observed_at": "2024-01-01T12:00:00"},
        {"observed_at": "bad"},
        {"source": ""},
        {"method": ""},
        {"confidence": 1.1},
        {"confidence": True},
        {"lower_bound": 225, "upper_bound": 230},
        {"lower_bound": 200},
        {"extra": "unknown"},
        {"confidence": -0.1},
        {"confidence": float("nan")},
        {"lower_bound": 200, "upper_bound": float("inf")},
        {"metric": "lthr", "unit": "bpm", "value": 301},
        {"source": "a" * 257},
    ],
)
async def test_invalid_observation_never_initializes_storage(
    app, tmp_path, monkeypatch, overrides
):
    directory = tmp_path / "must-not-exist"
    monkeypatch.setenv("GARMIN_DATA_DIR", str(directory))
    result = await call(
        app,
        "record_physiology_observation",
        observation=observation(**overrides),
        dry_run=False,
    )
    assert result["status"] == "failed"
    assert result["write_performed"] is False
    assert not directory.exists()


@pytest.mark.asyncio
async def test_zero_confidence_and_uncertainty_bounds_are_preserved(
    app, tmp_path, monkeypatch
):
    monkeypatch.setenv("GARMIN_DATA_DIR", str(tmp_path / "private"))
    result = await call(
        app,
        "record_physiology_observation",
        observation=observation(confidence=0, lower_bound=200, upper_bound=230),
        dry_run=False,
    )
    row = result["observation"]
    assert row["confidence"] == 0
    assert (row["lower_bound"], row["value"], row["upper_bound"]) == (200, 220, 230)


@pytest.mark.asyncio
async def test_query_filters_do_not_interpolate_sql(app, tmp_path, monkeypatch):
    monkeypatch.setenv("GARMIN_DATA_DIR", str(tmp_path / "private"))
    result = await call(
        app,
        "record_physiology_observation",
        observation=observation(source="test'); DROP TABLE observations; --"),
        dry_run=False,
    )
    assert result["status"] == "success"
    result = await call(app, "list_physiology_observations")
    assert len(result["observations"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"sport": "run"},
        {"metric": "ftp; DROP TABLE x"},
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
    ],
)
async def test_invalid_query_is_rejected_without_files(
    app, tmp_path, monkeypatch, kwargs
):
    directory = tmp_path / "none"
    monkeypatch.setenv("GARMIN_DATA_DIR", str(directory))
    result = await call(app, "list_physiology_observations", **kwargs)
    assert result["status"] == "failed"
    assert not directory.exists()


@pytest.mark.asyncio
async def test_future_schema_is_refused_without_overwriting(app, tmp_path, monkeypatch):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    db = directory / "observations.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version = 99")
    db.chmod(0o600)
    before = db.read_bytes()
    monkeypatch.setenv("GARMIN_DATA_DIR", str(directory))
    result = await call(
        app, "record_physiology_observation", observation=observation(), dry_run=False
    )
    assert result["status"] == "failed"
    assert "schema" in result["message"]
    assert db.read_bytes() == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership/mode contract")
@pytest.mark.asyncio
async def test_private_modes_and_symlink_refusal(app, tmp_path, monkeypatch):
    directory = tmp_path / "private"
    monkeypatch.setenv("GARMIN_DATA_DIR", str(directory))
    result = await call(
        app, "record_physiology_observation", observation=observation(), dry_run=False
    )
    assert result["status"] == "success"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    db = directory / "observations.sqlite3"
    assert stat.S_IMODE(db.stat().st_mode) == 0o600
    real = directory / "saved.sqlite3"
    db.rename(real)
    db.symlink_to(real)
    before = real.read_bytes()
    result = await call(
        app, "record_physiology_observation", observation=observation(), dry_run=False
    )
    assert result["status"] == "failed"
    assert real.read_bytes() == before


@pytest.mark.asyncio
async def test_registered_effects_and_profile(app):
    from garmin_mcp import tool_annotations, tool_profiles

    names = {t.name for t in await app.list_tools()}
    for name in ["record_physiology_observation", "list_physiology_observations"]:
        assert name in names
        assert name in tool_profiles.TRAINING_TOOLS
        assert name not in tool_profiles.MINIMAL_TOOLS
        assert tool_annotations.annotations_for(name).openWorldHint is False
    assert (
        tool_annotations.annotations_for("record_physiology_observation").readOnlyHint
        is False
    )
    assert (
        tool_annotations.annotations_for("list_physiology_observations").readOnlyHint
        is True
    )


def test_concurrent_first_writes(tmp_path):
    from garmin_mcp.physiology_observations import (
        ObservationStore,
        validate_observation,
    )

    directory = tmp_path / "private"

    def write(index):
        return ObservationStore(directory).record(
            validate_observation(observation(value=200 + index))
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        rows = list(executor.map(write, range(8)))
    assert len({r["id"] for r in rows}) == 8
    assert len(ObservationStore(directory).list()["observations"]) == 8


@pytest.mark.asyncio
async def test_same_commit_is_append_only_not_idempotent(app, tmp_path, monkeypatch):
    monkeypatch.setenv("GARMIN_DATA_DIR", str(tmp_path / "private"))
    first = await call(
        app, "record_physiology_observation", observation=observation(), dry_run=False
    )
    second = await call(
        app, "record_physiology_observation", observation=observation(), dry_run=False
    )
    assert first["observation"]["id"] != second["observation"]["id"]
    result = await call(app, "list_physiology_observations")
    assert len(result["observations"]) == 2


@pytest.mark.asyncio
async def test_order_uses_normalized_observed_time(app, tmp_path, monkeypatch):
    monkeypatch.setenv("GARMIN_DATA_DIR", str(tmp_path / "private"))
    for timestamp, value in [
        ("2024-01-01T12:00:00+08:00", 200),
        ("2024-01-01T05:00:00Z", 210),
        ("2024-01-01T03:00:00+00:00", 190),
    ]:
        await call(
            app,
            "record_physiology_observation",
            observation=observation(observed_at=timestamp, value=value),
            dry_run=False,
        )
    result = await call(app, "list_physiology_observations")
    assert [r["value"] for r in result["observations"]] == [210, 200, 190]


@pytest.mark.asyncio
async def test_records_survive_a_fresh_process(app, tmp_path, monkeypatch):
    import asyncio
    import subprocess
    import sys

    directory = tmp_path / "private"
    monkeypatch.setenv("GARMIN_DATA_DIR", str(directory))
    result = await call(
        app, "record_physiology_observation", observation=observation(), dry_run=False
    )
    code = (
        "import json,sys; from garmin_mcp.physiology_observations import ObservationStore; "
        "print(json.dumps(ObservationStore(sys.argv[1]).list()))"
    )
    stored = json.loads(
        await asyncio.to_thread(
            subprocess.check_output,
            [sys.executable, "-c", code, str(directory)],
            text=True,
            timeout=30,
        )
    )
    assert stored["observations"] == [result["observation"]]


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership/mode contract")
@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["directory", "database"])
async def test_existing_permissions_are_refused_not_silently_changed(
    app, tmp_path, monkeypatch, target
):
    directory = tmp_path / "private"
    monkeypatch.setenv("GARMIN_DATA_DIR", str(directory))
    result = await call(
        app, "record_physiology_observation", observation=observation(), dry_run=False
    )
    assert result["status"] == "success"
    db = directory / "observations.sqlite3"
    before = db.read_bytes()
    changed = directory if target == "directory" else db
    mode = 0o755 if target == "directory" else 0o644
    changed.chmod(mode)
    result = await call(
        app, "record_physiology_observation", observation=observation(), dry_run=False
    )
    assert result["status"] == "failed"
    assert stat.S_IMODE(changed.stat().st_mode) == mode
    assert db.read_bytes() == before


@pytest.mark.asyncio
async def test_reader_refuses_future_schema(app, tmp_path, monkeypatch):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    db = directory / "observations.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version = 99")
    db.chmod(0o600)
    before = db.read_bytes()
    monkeypatch.setenv("GARMIN_DATA_DIR", str(directory))
    result = await call(app, "list_physiology_observations")
    assert result["status"] == "failed"
    assert "schema" in result["message"]
    assert db.read_bytes() == before
