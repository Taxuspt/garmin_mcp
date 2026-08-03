import json
import os

import pytest

from garmin_mcp.workout_builders import (
    build_walk_run_json,
    build_z2_walk_json,
    build_strength_json,
    build_run_json,
)

SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "captured")


def test_build_walk_run_json_matches_poc_snapshot():
    """The walk/run builder must produce the exact JSON that Garmin accepted in the POC."""
    result = build_walk_run_json(
        name="POC Walk/Run 7x1m/3m Z3",
        run_seconds=60,
        walk_seconds=180,
        repeats=7,
        warmup_min=10,
        cooldown_min=8,
        hr_zone="Z3",
    )

    # Compare against the validated POC snapshot
    snapshot_path = os.path.join(SNAPSHOT_DIR, "poc_walk_run.json")
    with open(snapshot_path, "r", encoding="utf-8") as f:
        expected = json.load(f)

    assert result == expected


def test_build_z2_walk_json_structure():
    result = build_z2_walk_json(
        name="Z2 Walk 30m",
        duration_min=30,
        hr_min=110,
        hr_max=130,
    )
    assert result["workoutName"] == "Z2 Walk 30m"
    assert result["sportType"]["sportTypeKey"] == "walking"
    assert result["sportType"]["sportTypeId"] == 12
    steps = result["workoutSegments"][0]["workoutSteps"]
    assert len(steps) == 3
    assert steps[1]["zoneNumber"] == 2
    assert steps[1]["endConditionValue"] == 1800.0


def test_build_run_json_structure():
    result = build_run_json(
        name="Step 8 - 30min continuous",
        run_seconds=1800,
        warmup_min=5,
        cooldown_min=5,
        hr_zone="Z3",
    )
    assert result["workoutName"] == "Step 8 - 30min continuous"
    assert result["sportType"]["sportTypeKey"] == "running"
    assert result["sportType"]["sportTypeId"] == 1
    steps = result["workoutSegments"][0]["workoutSteps"]
    assert len(steps) == 3
    assert steps[0]["stepType"]["stepTypeKey"] == "warmup"
    assert steps[0]["endConditionValue"] == 300.0
    assert steps[1]["stepType"]["stepTypeKey"] == "interval"
    assert steps[1]["endConditionValue"] == 1800.0
    assert steps[1]["zoneNumber"] == 3
    assert steps[2]["stepType"]["stepTypeKey"] == "cooldown"
    assert steps[2]["endConditionValue"] == 300.0


def test_build_run_json_custom_hr_range():
    """hr_min/hr_max should produce a custom bpm-range target, not a zoneNumber."""
    result = build_run_json(
        name="Base run - custom range",
        run_seconds=1440,
        warmup_min=5,
        cooldown_min=5,
        hr_min=136,
        hr_max=148,
    )
    steps = result["workoutSegments"][0]["workoutSteps"]
    interval_step = steps[1]
    assert interval_step["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert interval_step["targetValueOne"] == 136.0
    assert interval_step["targetValueTwo"] == 148.0
    assert "zoneNumber" not in interval_step
    assert "136-148bpm" in result["description"]


def test_build_run_json_custom_hr_range_requires_both_bounds():
    with pytest.raises(ValueError, match="hr_min and hr_max must both be provided together"):
        build_run_json(
            name="Bad range",
            run_seconds=1440,
            warmup_min=5,
            cooldown_min=5,
            hr_min=136,
        )


def test_build_run_json_custom_hr_range_rejects_inverted_bounds():
    with pytest.raises(ValueError, match="must be less than"):
        build_run_json(
            name="Bad range",
            run_seconds=1440,
            warmup_min=5,
            cooldown_min=5,
            hr_min=148,
            hr_max=136,
        )


def test_build_strength_json_structure():
    result = build_strength_json(
        name="Full Body A",
        exercises=[
            {"name": "Sentadillas", "sets": 3, "reps": 12, "rest_seconds": 90},
            {"name": "Flexiones", "sets": 3, "reps": 15, "rest_seconds": 60},
        ],
    )
    assert result["workoutName"] == "Full Body A"
    assert result["sportType"]["sportTypeKey"] == "strength_training"
    assert result["sportType"]["sportTypeId"] == 5
    steps = result["workoutSegments"][0]["workoutSteps"]
    # 2 exercises + 1 rest between them = 3 steps
    assert len(steps) == 3
    assert steps[0]["exerciseName"] == "Sentadillas"
    assert steps[2]["exerciseName"] == "Flexiones"


# ---------------------------------------------------------------------------
# Strength step categories
#
# Garmin validates "category" against its own enum. A value outside it — the
# previously hardcoded "UNASSIGNED" — fails every upload with `400 - Invalid
# category`, while omitting the key is accepted.
# ---------------------------------------------------------------------------


def _work_steps(result):
    """Only the exercise steps; rest steps are recovery steps and carry no category."""
    steps = result["workoutSegments"][0]["workoutSteps"]
    return [s for s in steps if s["stepType"]["stepTypeKey"] == "interval"]


def test_strength_omits_category_when_not_supplied():
    result = build_strength_json(
        name="No categories",
        exercises=[
            {"name": "Back Squat", "sets": 3, "reps": 5, "rest_seconds": 120},
            {"name": "Zercher Whatever", "sets": 3, "reps": 8, "rest_seconds": 60},
        ],
    )
    for step in _work_steps(result):
        assert "category" not in step
    # The name survives the Garmin round trip in the description, not exerciseName.
    assert _work_steps(result)[0]["description"].startswith("Back Squat:")


def test_strength_passes_through_supplied_category():
    result = build_strength_json(
        name="Mixed",
        exercises=[
            {"name": "Farmers Carry 40m", "sets": 3, "reps": 1, "category": "carry"},
            {"name": "Back Squat", "sets": 3, "reps": 5},
        ],
    )
    first, second = _work_steps(result)
    assert first["category"] == "CARRY"
    assert first["exerciseName"] == "Farmers Carry 40m"
    assert "category" not in second


def test_strength_prefers_identifier_and_preserves_description():
    result = build_strength_json(
        name="Resolved",
        exercises=[{"name": "My carry", "exercise_name": "FARMERS_CARRY", "category": "CARRY"}],
    )
    step = _work_steps(result)[0]
    assert step["exerciseName"] == "FARMERS_CARRY"
    assert step["description"].startswith("My carry:")


def test_strength_humanizes_identifier_only_input():
    result = build_strength_json(
        name="Resolved", exercises=[{"exercise_name": "REVERSE_CRUNCH", "category": "CRUNCH"}]
    )
    assert _work_steps(result)[0]["description"].startswith("Reverse Crunch:")


def test_strength_rejects_empty_category():
    for bad in ("", "   ", 5):
        with pytest.raises(ValueError):
            build_strength_json(
                name="Bad",
                exercises=[{"name": "Back Squat", "sets": 1, "reps": 1, "category": bad}],
            )
