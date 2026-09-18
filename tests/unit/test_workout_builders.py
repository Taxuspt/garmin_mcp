import json
import os

import pytest

from garmin_mcp.workout_builders import (
    build_walk_run_json,
    build_z2_walk_json,
    build_strength_json,
    build_run_json,
    build_run_interval_json,
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


def test_build_run_interval_json_structure():
    result = build_run_interval_json(
        name="VO2 5x3min",
        repeats=5,
        rep_seconds=180,
        recovery_seconds=120,
        warmup_min=10,
        cooldown_min=8,
        hr_zone="Z5",
    )
    assert result["workoutName"] == "VO2 5x3min"
    assert result["sportType"]["sportTypeKey"] == "running"
    warmup, group, cooldown = result["workoutSegments"][0]["workoutSteps"]

    assert warmup["stepType"]["stepTypeKey"] == "warmup"
    assert warmup["endConditionValue"] == 600.0

    # The group occupies stepOrder 2 and its children continue the numbering;
    # a mis-numbered child uploads cleanly but renders scrambled on the watch.
    assert group["type"] == "RepeatGroupDTO"
    assert group["stepOrder"] == 2
    assert group["numberOfIterations"] == 5
    assert group["endCondition"]["conditionTypeKey"] == "iterations"
    assert group["endConditionValue"] == 5.0
    work, recovery = group["workoutSteps"]
    assert work["stepOrder"] == 3
    assert work["stepType"]["stepTypeKey"] == "interval"
    assert work["endConditionValue"] == 180.0
    assert work["zoneNumber"] == 5
    assert recovery["stepOrder"] == 4
    assert recovery["stepType"]["stepTypeKey"] == "recovery"
    assert recovery["endConditionValue"] == 120.0
    assert recovery["targetType"]["workoutTargetTypeKey"] == "no.target"

    assert cooldown["stepOrder"] == 5
    assert cooldown["stepType"]["stepTypeKey"] == "cooldown"
    assert cooldown["endConditionValue"] == 480.0
    assert "5x(3m Z5 / 2m jog)" in result["description"]


def test_build_run_interval_json_custom_hr_range():
    """hr_min/hr_max should produce a custom bpm-range target, not a zoneNumber."""
    result = build_run_interval_json(
        name="Threshold 3x6min",
        repeats=3,
        rep_seconds=360,
        recovery_seconds=180,
        warmup_min=12,
        cooldown_min=8,
        hr_min=172,
        hr_max=180,
    )
    work = result["workoutSegments"][0]["workoutSteps"][1]["workoutSteps"][0]
    assert work["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert work["targetValueOne"] == 172.0
    assert work["targetValueTwo"] == 180.0
    assert "zoneNumber" not in work
    assert "172-180bpm" in result["description"]


def test_build_run_interval_json_recovery_hr_target():
    result = build_run_interval_json(
        name="Controlled recoveries",
        repeats=4,
        rep_seconds=240,
        recovery_seconds=120,
        warmup_min=10,
        cooldown_min=5,
        hr_min=165,
        hr_max=175,
        recovery_hr_min=120,
        recovery_hr_max=140,
    )
    recovery = result["workoutSegments"][0]["workoutSteps"][1]["workoutSteps"][1]
    assert recovery["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert recovery["targetValueOne"] == 120.0
    assert recovery["targetValueTwo"] == 140.0
    assert "120-140bpm" in recovery["description"]


def test_build_run_interval_json_zero_recovery_omits_step():
    result = build_run_interval_json(
        name="Strides",
        repeats=6,
        rep_seconds=20,
        recovery_seconds=0,
        warmup_min=10,
        cooldown_min=5,
    )
    _, group, cooldown = result["workoutSegments"][0]["workoutSteps"]
    assert len(group["workoutSteps"]) == 1
    assert group["workoutSteps"][0]["stepType"]["stepTypeKey"] == "interval"
    # Cooldown numbering shifts up when there is no recovery child.
    assert cooldown["stepOrder"] == 4


def test_build_run_interval_json_rejects_bad_inputs():
    common = dict(name="Bad", warmup_min=10, cooldown_min=5)
    with pytest.raises(ValueError, match="repeats"):
        build_run_interval_json(repeats=0, rep_seconds=60, recovery_seconds=60, **common)
    with pytest.raises(ValueError, match="rep_seconds"):
        build_run_interval_json(repeats=3, rep_seconds=0, recovery_seconds=60, **common)
    with pytest.raises(ValueError, match="recovery_seconds"):
        build_run_interval_json(repeats=3, rep_seconds=60, recovery_seconds=-1, **common)


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
    # Multi-set exercises each become one repeat group; rest lives inside the
    # group (after every set), so no separate inter-exercise rest step.
    assert len(steps) == 2
    assert [s["type"] for s in steps] == ["RepeatGroupDTO", "RepeatGroupDTO"]
    assert steps[0]["numberOfIterations"] == 3
    assert steps[0]["workoutSteps"][0]["exerciseName"] == "Sentadillas"
    assert steps[1]["workoutSteps"][0]["exerciseName"] == "Flexiones"


def test_strength_single_set_stays_flat():
    result = build_strength_json(
        name="Singles",
        exercises=[
            {"name": "Plank Hold", "sets": 1, "reps": 1, "rest_seconds": 60},
            {"name": "Dead Hang", "sets": 1, "reps": 1, "rest_seconds": 0},
        ],
    )
    steps = result["workoutSegments"][0]["workoutSteps"]
    # 2 flat work steps + 1 rest between them; no repeat group for a single set
    assert len(steps) == 3
    assert [s["type"] for s in steps] == ["ExecutableStepDTO"] * 3
    assert steps[0]["exerciseName"] == "Plank Hold"
    assert steps[1]["stepType"]["stepTypeKey"] == "recovery"


def test_strength_multi_set_repeat_group_shape():
    result = build_strength_json(
        name="Sets",
        exercises=[{"name": "Goblet Squat", "sets": 4, "reps": 8, "rest_seconds": 90}],
    )
    (group,) = result["workoutSegments"][0]["workoutSteps"]
    # The shape _sanitize_repeat_group enforces on upload: without a valid
    # iterations endCondition the Garmin API silently corrupts the group.
    assert group["type"] == "RepeatGroupDTO"
    assert group["stepType"] == {"stepTypeId": 6, "stepTypeKey": "repeat"}
    assert group["numberOfIterations"] == 4
    assert group["endCondition"]["conditionTypeKey"] == "iterations"
    work, rest = group["workoutSteps"]
    assert work["endCondition"]["conditionTypeKey"] == "reps"
    assert work["endConditionValue"] == 8.0
    assert "4 sets x 8 reps" in work["description"]
    assert rest["stepType"]["stepTypeKey"] == "recovery"
    assert rest["endConditionValue"] == 90.0


def test_strength_multi_set_without_rest_has_no_recovery_step():
    result = build_strength_json(
        name="No rest",
        exercises=[{"name": "Push Up", "sets": 3, "reps": 12, "rest_seconds": 0}],
    )
    (group,) = result["workoutSegments"][0]["workoutSteps"]
    assert group["numberOfIterations"] == 3
    assert len(group["workoutSteps"]) == 1
    assert group["workoutSteps"][0]["stepType"]["stepTypeKey"] == "interval"


# ---------------------------------------------------------------------------
# Strength step categories
#
# Garmin validates "category" against its own enum. A value outside it — the
# previously hardcoded "UNASSIGNED" — fails every upload with `400 - Invalid
# category`, while omitting the key is accepted.
# ---------------------------------------------------------------------------


def _work_steps(result):
    """Only the exercise steps; rest steps are recovery steps and carry no category.

    Multi-set exercises nest their work step inside a RepeatGroupDTO, so look
    through repeat groups as well as at top-level steps.
    """
    steps = result["workoutSegments"][0]["workoutSteps"]
    flat = []
    for s in steps:
        for child in s.get("workoutSteps", [s]):
            if child["stepType"]["stepTypeKey"] == "interval":
                flat.append(child)
    return flat


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


def test_strength_rejects_empty_category():
    for bad in ("", "   ", 5):
        with pytest.raises(ValueError):
            build_strength_json(
                name="Bad",
                exercises=[{"name": "Back Squat", "sets": 1, "reps": 1, "category": bad}],
            )
