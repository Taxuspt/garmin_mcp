import pytest

from garmin_mcp.workouts import _validate_target_type_steps


def _workout_with_target(target_type: dict, **target_values) -> dict:
    return {
        "sportType": {"sportTypeId": 2, "sportTypeKey": "cycling"},
        "workoutSegments": [
            {
                "segmentOrder": 1,
                "sportType": {"sportTypeId": 2, "sportTypeKey": "cycling"},
                "workoutSteps": [
                    {
                        "type": "ExecutableStepDTO",
                        "stepOrder": 1,
                        "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                        "endCondition": {
                            "conditionTypeId": 2,
                            "conditionTypeKey": "time",
                        },
                        "endConditionValue": 240,
                        "targetType": target_type,
                        **target_values,
                    }
                ],
            }
        ],
    }


def test_accepts_explicit_cycling_power_range() -> None:
    workout = _workout_with_target(
        {
            "workoutTargetTypeId": 9,
            "workoutTargetTypeKey": "power.lap",
        },
        targetValueOne=235,
        targetValueTwo=250,
    )

    _validate_target_type_steps(workout)


def test_accepts_single_cycling_power_target() -> None:
    workout = _workout_with_target(
        {
            "workoutTargetTypeId": 9,
            "workoutTargetTypeKey": "power.lap",
        },
        targetValueOne=160,
        targetValueTwo=160,
    )

    _validate_target_type_steps(workout)


def test_accepts_cycling_power_zone() -> None:
    workout = _workout_with_target(
        {
            "workoutTargetTypeId": 2,
            "workoutTargetTypeKey": "power.zone",
        },
        zoneNumber=4,
    )

    _validate_target_type_steps(workout)


def test_rejects_legacy_power_between_mapping() -> None:
    workout = _workout_with_target(
        {
            "workoutTargetTypeId": 6,
            "workoutTargetTypeKey": "power.between",
        },
        targetValueOne=235,
        targetValueTwo=250,
    )

    with pytest.raises(ValueError, match="mismatch"):
        _validate_target_type_steps(workout)


def test_rejects_power_lap_with_pace_id() -> None:
    workout = _workout_with_target(
        {
            "workoutTargetTypeId": 6,
            "workoutTargetTypeKey": "power.lap",
        },
        targetValueOne=235,
        targetValueTwo=250,
    )

    with pytest.raises(ValueError, match="mismatch"):
        _validate_target_type_steps(workout)


def test_power_lap_requires_both_bounds() -> None:
    workout = _workout_with_target(
        {
            "workoutTargetTypeId": 9,
            "workoutTargetTypeKey": "power.lap",
        },
        targetValueOne=235,
    )

    with pytest.raises(ValueError, match="requires"):
        _validate_target_type_steps(workout)


def test_power_lap_rejects_reversed_range() -> None:
    workout = _workout_with_target(
        {
            "workoutTargetTypeId": 9,
            "workoutTargetTypeKey": "power.lap",
        },
        targetValueOne=250,
        targetValueTwo=235,
    )

    with pytest.raises(ValueError, match="must not exceed"):
        _validate_target_type_steps(workout)


def test_power_lap_rejects_zone_number() -> None:
    workout = _workout_with_target(
        {
            "workoutTargetTypeId": 9,
            "workoutTargetTypeKey": "power.lap",
        },
        targetValueOne=235,
        targetValueTwo=250,
        zoneNumber=5,
    )

    with pytest.raises(ValueError, match="not zoneNumber"):
        _validate_target_type_steps(workout)


def test_power_zone_requires_zone_number() -> None:
    workout = _workout_with_target(
        {
            "workoutTargetTypeId": 2,
            "workoutTargetTypeKey": "power.zone",
        }
    )

    with pytest.raises(ValueError, match="requires zoneNumber"):
        _validate_target_type_steps(workout)


def test_power_zone_rejects_non_integer_zone_number() -> None:
    workout = _workout_with_target(
        {
            "workoutTargetTypeId": 2,
            "workoutTargetTypeKey": "power.zone",
        },
        zoneNumber=3.5,
    )

    with pytest.raises(ValueError, match="must be an integer"):
        _validate_target_type_steps(workout)


def test_power_zone_rejects_watt_bounds() -> None:
    workout = _workout_with_target(
        {
            "workoutTargetTypeId": 2,
            "workoutTargetTypeKey": "power.zone",
        },
        zoneNumber=4,
        targetValueOne=200,
        targetValueTwo=220,
    )

    with pytest.raises(ValueError, match="uses zoneNumber"):
        _validate_target_type_steps(workout)
