import datetime as dt
import copy
from unittest.mock import Mock

import pytest

from garmin_mcp import coaching, physiology
from garmin_mcp.coaching import (
    apply_training_plan,
    apply_week_adaptation_patch,
    build_training_block,
    build_week_adaptation,
    persist_training_block,
)
from garmin_mcp.physiology_store import PhysiologyStore


def _race_in_weeks(weeks: int) -> str:
    today = dt.date.today()
    monday = today + dt.timedelta(days=(-today.weekday()) % 7)
    return (monday + dt.timedelta(weeks=weeks) - dt.timedelta(days=1)).isoformat()


def test_training_block_is_deterministic_and_respects_constraints():
    args = {
        "race_date": _race_in_weeks(12),
        "goal": "gran_fondo",
        "sport": "cycling",
        "days_per_week": 4,
        "current_ctl": 40,
    }
    first = build_training_block(**args)
    second = build_training_block(**args)
    assert first == second
    assert len(first["weeks"]) == 12
    for week in first["weeks"]:
        hard_sessions = [
            s for s in week["sessions"] if s["kind"] in {"intensity", "tempo"}
        ]
        high = len(hard_sessions)
        assert high <= 2
        for before, after in zip(hard_sessions, hard_sessions[1:]):
            delta = dt.date.fromisoformat(after["date"]) - dt.date.fromisoformat(before["date"])
            assert delta.days >= 2
    previous_load_week = None
    for week in first["weeks"]:
        if week["phase"] == "recovery":
            assert previous_load_week is not None
            assert week["target_volume_min"] == round(
                previous_load_week["target_volume_min"] * 0.75
            )
            assert week["target_load"] == round(
                previous_load_week["target_load"] * 0.75, 1
            )
        elif week["phase"] != "taper":
            if previous_load_week is not None:
                assert week["target_volume_min"] <= round(
                    previous_load_week["target_volume_min"] * 1.08
                )
                assert week["target_load"] <= round(
                    previous_load_week["target_load"] * 1.08, 1
                )
            previous_load_week = week
    assert first["weeks"][3]["phase"] == "recovery"
    assert first["weeks"][7]["phase"] == "recovery"
    assert all(week["phase"] == "taper" for week in first["weeks"][-2:])


def test_red_adaptation_replaces_intensity_and_reduces_volume():
    plan = build_training_block(
        race_date=_race_in_weeks(10),
        goal="time_trial",
        sport="cycling",
        days_per_week=4,
    )
    week = next(w for w in plan["weeks"] if w["phase"] == "build")
    proposal = build_week_adaptation(
        plan,
        week["week_of"],
        readiness=30,
        hrv_status="low",
        tsb=-20,
        completion_rate=0.4,
    )
    assert proposal["readiness_state"] == "red"
    assert all(s["kind"] not in {"intensity", "tempo"} for s in proposal["patched_week"]["sessions"])
    assert proposal["patched_week"]["target_volume_min"] < week["target_volume_min"]


def test_adaptation_never_changes_locked_race_session():
    race_date = _race_in_weeks(10)
    plan = build_training_block(
        race_date=race_date,
        goal="road_race",
        sport="cycling",
        days_per_week=4,
    )
    race_week = plan["weeks"][-1]
    locked_before = next(session for session in race_week["sessions"] if session["locked"])
    proposal = build_week_adaptation(
        plan,
        race_week["week_of"],
        readiness=20,
        hrv_status="low",
        tsb=-30,
        completion_rate=0.2,
        subjective_status="ill",
    )
    locked_after = next(
        session for session in proposal["patched_week"]["sessions"] if session["locked"]
    )
    assert locked_after == locked_before


def test_green_adaptation_never_increases_high_intensity_count():
    plan = build_training_block(
        race_date=_race_in_weeks(10),
        goal="road_race",
        sport="cycling",
        days_per_week=5,
    )
    week = next(w for w in plan["weeks"] if w["phase"] == "build")
    before = sum(s["kind"] == "intensity" for s in week["sessions"])
    proposal = build_week_adaptation(
        plan,
        week["week_of"],
        readiness=80,
        hrv_status="balanced",
        tsb=0,
        completion_rate=1.0,
    )
    after = sum(s["kind"] == "intensity" for s in proposal["patched_week"]["sessions"])
    assert proposal["readiness_state"] == "green"
    assert after == before


def test_one_good_metric_does_not_increase_training():
    plan = build_training_block(
        race_date=_race_in_weeks(10),
        goal="base",
        sport="cycling",
        days_per_week=3,
    )
    week = plan["weeks"][1]
    proposal = build_week_adaptation(
        plan,
        week["week_of"],
        readiness=90,
        hrv_status=None,
        tsb=None,
        completion_rate=None,
    )
    assert proposal["readiness_state"] == "insufficient_data"
    assert proposal["volume_multiplier"] == 1.0


def _stored_plan(tmp_path, monkeypatch):
    store = PhysiologyStore(tmp_path)
    monkeypatch.setattr(physiology, "_store", store)
    plan = build_training_block(
        race_date=_race_in_weeks(4),
        goal="base",
        sport="cycling",
        days_per_week=1,
    )
    row, created = persist_training_block(plan)
    assert created is True
    return store, plan, row


def _workout_client():
    client = Mock()
    client.query_garmin_graphql.return_value = {
        "data": {"workoutScheduleSummariesScalar": []}
    }
    uploaded = {}

    def upload(payload):
        workout_id = str(1000 + len(uploaded))
        uploaded[workout_id] = copy.deepcopy(payload)
        return {"workoutId": workout_id}

    client.upload_workout.side_effect = upload
    client.get_workout_by_id.side_effect = lambda workout_id: uploaded[str(workout_id)]
    schedule_ids = iter(range(2000, 3000))
    client.schedule_workout.side_effect = lambda *_args: {
        "scheduledWorkoutId": str(next(schedule_ids))
    }
    return client


def test_training_plan_apply_is_preview_first_and_idempotent(tmp_path, monkeypatch):
    store, plan, row = _stored_plan(tmp_path, monkeypatch)
    client = _workout_client()
    coaching.configure(client)

    preview = apply_training_plan(plan["plan_id"], dry_run=True)
    assert preview["status"] == "preview"
    assert preview["change_set"]["creates"] == 4
    client.upload_workout.assert_not_called()

    applied = apply_training_plan(plan["plan_id"], dry_run=False)
    assert applied["status"] == "applied"
    assert client.upload_workout.call_count == 4
    assert len(store.list_workout_links(plan_id=row["id"])) == 4

    repeated = apply_training_plan(plan["plan_id"], dry_run=False)
    assert repeated["status"] == "already_applied"
    assert client.upload_workout.call_count == 4


def test_training_plan_recovers_schedule_id_from_calendar(tmp_path, monkeypatch):
    store, plan, row = _stored_plan(tmp_path, monkeypatch)
    client = _workout_client()
    client.schedule_workout.side_effect = None
    client.schedule_workout.return_value = {}

    def calendar_result(query):
        scheduled_workout_id, scheduled_date = client.schedule_workout.call_args.args
        return {
            "data": {
                "workoutScheduleSummariesScalar": [
                    {
                        "workoutId": scheduled_workout_id,
                        "scheduleDate": scheduled_date,
                        "scheduledWorkoutId": f"schedule-{scheduled_workout_id}",
                    }
                ]
            }
        }

    client.query_garmin_graphql.side_effect = calendar_result
    coaching.configure(client)

    applied = apply_training_plan(plan["plan_id"], dry_run=False)

    assert applied["status"] == "applied"
    links = store.list_workout_links(plan_id=row["id"])
    assert all(link["external_schedule_id"] for link in links)


def test_training_plan_failure_compensates_only_new_objects(tmp_path, monkeypatch):
    store, plan, row = _stored_plan(tmp_path, monkeypatch)
    client = _workout_client()
    client.schedule_workout.side_effect = [
        {"scheduledWorkoutId": "2000"},
        RuntimeError("calendar unavailable"),
    ]
    coaching.configure(client)

    result = apply_training_plan(plan["plan_id"], dry_run=False)

    assert result["status"] == "failed_recovery_required"
    assert client.delete_workout.call_count == 1
    assert {link["state"] for link in store.list_workout_links(plan_id=row["id"])} == {
        "rolled_back",
        "recovery_required",
    }
    assert result["recovery_checklist"]


def test_training_plan_rollback_keeps_template_until_unschedule_is_read_back(
    monkeypatch,
):
    client = _workout_client()
    coaching.configure(client)
    monkeypatch.setattr(coaching, "_wait_scheduled_id_absent", lambda *_args: False)
    monkeypatch.setattr(coaching.time, "sleep", lambda _seconds: None)
    store = Mock()

    outcomes, recovery = coaching._rollback_created(
        store,
        [{
            "session_id": "session-1",
            "workout_id": "1000",
            "link_id": "link-1",
            "scheduled_id": "2000",
            "scheduled_date": "2026-09-10",
            "schedule_attempted": True,
        }],
    )

    assert outcomes[0]["state"] == "recovery_required"
    assert recovery
    assert client.unschedule_workout.call_count == 3
    client.unschedule_workout.assert_called_with("2000")
    client.delete_workout.assert_not_called()
    store.update_workout_link_state.assert_called_once_with(
        link_id="link-1", state="recovery_required"
    )


def test_training_plan_lost_upload_response_is_indeterminate_and_blocks_retry(
    tmp_path, monkeypatch
):
    store, plan, row = _stored_plan(tmp_path, monkeypatch)
    client = _workout_client()
    client.upload_workout.side_effect = RuntimeError("lost upload response")
    coaching.configure(client)

    first = apply_training_plan(plan["plan_id"], dry_run=False)
    second = apply_training_plan(plan["plan_id"], dry_run=False)

    assert first["status"] == "failed_recovery_required"
    assert first["recovery_checklist"]
    assert second["status"] == "blocked_recovery_required"
    assert client.upload_workout.call_count == 1
    links = store.list_workout_links(plan_id=row["id"])
    assert len(links) == 1
    assert links[0]["state"] == "indeterminate"


def test_week_adaptation_replaces_only_linked_generated_workouts(tmp_path, monkeypatch):
    store, plan, row = _stored_plan(tmp_path, monkeypatch)
    client = _workout_client()
    coaching.configure(client)
    assert apply_training_plan(plan["plan_id"], dry_run=False)["status"] == "applied"

    target_week = plan["weeks"][1]
    proposal = build_week_adaptation(
        plan,
        target_week["week_of"],
        readiness=25,
        hrv_status="low",
        tsb=-25,
        completion_rate=0.4,
    )
    adaptation, created = store.save_adaptation(
        plan_id=row["id"],
        week_of=target_week["week_of"],
        revision=2,
        patch=proposal,
        reasons=proposal["reasons"],
        input_hash="adaptation-input-hash",
        adaptation_id=proposal["adaptation_id"],
    )
    assert created is True

    applied = apply_week_adaptation_patch(adaptation["id"], dry_run=False)
    assert applied["status"] == "applied"
    assert len(applied["superseded"]) == 1
    client.unschedule_workout.assert_called_once()
    assert client.delete_workout.call_count == 1

    repeated = apply_week_adaptation_patch(adaptation["id"], dry_run=False)
    assert repeated["status"] == "already_applied"
    assert client.delete_workout.call_count == 1


def test_week_adaptation_preserves_locked_generated_workout_and_link(
    tmp_path, monkeypatch
):
    store = PhysiologyStore(tmp_path)
    monkeypatch.setattr(physiology, "_store", store)
    plan = build_training_block(
        race_date=_race_in_weeks(4),
        goal="road_race",
        sport="cycling",
        days_per_week=4,
    )
    row, created = persist_training_block(plan)
    assert created is True
    client = _workout_client()
    coaching.configure(client)
    assert apply_training_plan(plan["plan_id"], dry_run=False)["status"] == "applied"

    target_week = plan["weeks"][-1]
    locked_session = next(
        session for session in target_week["sessions"] if session["locked"]
    )
    locked_link = next(
        link
        for link in store.list_workout_links(plan_id=row["id"])
        if link["local_workout_key"] == locked_session["session_id"]
    )
    proposal = build_week_adaptation(
        plan,
        target_week["week_of"],
        readiness=20,
        hrv_status="low",
        tsb=-30,
        completion_rate=0.2,
        subjective_status="ill",
    )
    adaptation, created = store.save_adaptation(
        plan_id=row["id"],
        week_of=target_week["week_of"],
        revision=2,
        patch=proposal,
        reasons=proposal["reasons"],
        input_hash="locked-session-adaptation-input-hash",
        adaptation_id=proposal["adaptation_id"],
    )
    assert created is True

    applied = apply_week_adaptation_patch(adaptation["id"], dry_run=False)

    assert applied["status"] == "applied"
    assert [item["session_id"] for item in applied["locked_sessions_preserved"]] == [
        locked_session["session_id"]
    ]
    deleted_ids = {str(call.args[0]) for call in client.delete_workout.call_args_list}
    assert str(locked_link["external_workout_id"]) not in deleted_ids
    assert client.delete_workout.call_count == len(target_week["sessions"]) - 1
    new_links = store.list_workout_links(plan_id=applied["new_plan_revision_id"])
    preserved_link = next(
        link
        for link in new_links
        if link["local_workout_key"] == locked_session["session_id"]
    )
    assert preserved_link["external_workout_id"] == locked_link["external_workout_id"]
    assert preserved_link["external_schedule_id"] == locked_link["external_schedule_id"]
