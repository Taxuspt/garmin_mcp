import os

from garmin_mcp.physiology_store import PhysiologyStore, SCHEMA_VERSION


def test_store_migrates_schema_and_secures_database(tmp_path):
    store = PhysiologyStore(tmp_path)

    assert store.schema_version == SCHEMA_VERSION
    with store._connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert {
        "athletes",
        "observations",
        "threshold_estimates",
        "zone_models",
        "analysis_results",
        "plans",
        "workout_links",
        "adaptations",
        "audit_log",
        "physiology_test_imports",
        "physiology_test_samples",
    } <= tables
    if os.name != "nt":
        assert store.database_path.stat().st_mode & 0o077 == 0


def test_store_migrates_v1_workout_links_to_schedule_ids(tmp_path):
    store = PhysiologyStore(tmp_path)
    with store._connect() as connection:
        connection.execute("PRAGMA user_version = 1")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS migration_probe (value TEXT)"
        )
        connection.commit()

    migrated = PhysiologyStore(tmp_path)

    assert migrated.schema_version == SCHEMA_VERSION
    with migrated._connect() as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(workout_links)")
        }
    assert "external_schedule_id" in columns


def test_plan_revisions_are_immutable_idempotent_and_queryable(tmp_path):
    store = PhysiologyStore(tmp_path)
    athlete_id = store.ensure_athlete()

    first, created = store.save_plan(
        athlete_id=athlete_id,
        sport="cycling",
        plan={"weeks": [1]},
        input_hash="first",
        revision=1,
        plan_id="logical-plan",
    )
    duplicate, duplicate_created = store.save_plan(
        athlete_id=athlete_id,
        sport="cycling",
        plan={"weeks": [999]},
        input_hash="first",
        revision=1,
        plan_id="logical-plan",
    )
    second, second_created = store.save_plan(
        athlete_id=athlete_id,
        sport="cycling",
        plan={"weeks": [1, 2]},
        input_hash="second",
        revision=2,
        plan_id="logical-plan",
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate["id"] == first["id"]
    assert duplicate["plan_json"] == {"weeks": [1]}
    assert second_created is True
    assert second["id"] != first["id"]
    assert second["logical_plan_id"] == first["logical_plan_id"] == "logical-plan"
    assert store.get_plan("logical-plan")["revision"] == 2
    assert store.get_plan("logical-plan", revision=1)["id"] == first["id"]
    assert store.get_latest_plan(sport="cycling", athlete_id=athlete_id)["id"] == second["id"]


def test_analysis_results_are_immutable_and_idempotent(tmp_path):
    store = PhysiologyStore(tmp_path)
    athlete_id = store.ensure_athlete()

    first, created = store.put_analysis_result(
        athlete_id=athlete_id,
        analysis_type="decoupling",
        activity_id="42",
        result={"status": "ok", "decoupling_pct": 3.2},
        algorithm_version="activity-streams.v1",
        input_hash="analysis-input",
    )
    duplicate, duplicate_created = store.put_analysis_result(
        athlete_id=athlete_id,
        analysis_type="decoupling",
        activity_id="42",
        result={"status": "ok", "decoupling_pct": 99.0},
        algorithm_version="activity-streams.v1",
        input_hash="analysis-input",
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate["id"] == first["id"]
    assert duplicate["result_json"] == {"status": "ok", "decoupling_pct": 3.2}

    with store._connect() as connection:
        audit = connection.execute(
            "SELECT details_json FROM audit_log WHERE object_type = 'analysis_result'"
        ).fetchall()
    assert len(audit) == 1


def test_adaptation_and_workout_link_support_apply_and_compensation(tmp_path):
    store = PhysiologyStore(tmp_path)
    athlete_id = store.ensure_athlete()
    plan, _ = store.save_plan(
        athlete_id=athlete_id,
        sport="cycling",
        plan={"weeks": []},
        input_hash="plan",
        plan_id="logical-plan",
    )

    adaptation, created = store.save_adaptation(
        plan_id="logical-plan",
        week_of="2026-09-07",
        revision=1,
        patch={"volume_factor": 0.8},
        reasons=["yellow readiness"],
        input_hash="adaptation",
    )
    same, same_created = store.save_adaptation(
        plan_id=plan["id"],
        week_of="2026-09-07",
        revision=1,
        patch={"volume_factor": 1.5},
        reasons=[],
        input_hash="adaptation",
    )
    assert created is True
    assert same_created is False
    assert same["id"] == adaptation["id"]
    applied = store.update_adaptation_status(adaptation_id=adaptation["id"], status="applied")
    assert applied["status"] == "applied"

    link = store.put_workout_link(
        plan_id="logical-plan",
        local_workout_key="week-1-tuesday",
        provider="garmin",
        state="creating",
    )
    linked = store.update_workout_link_state(
        link_id=link["id"],
        state="created",
        external_workout_id="123",
        external_schedule_id="456",
    )
    assert linked["external_workout_id"] == "123"
    assert linked["external_schedule_id"] == "456"
    assert store.list_workout_links(
        plan_id="logical-plan", local_workout_key="week-1-tuesday"
    ) == [linked]
