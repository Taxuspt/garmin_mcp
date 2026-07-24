"""Unit tests for structured strength activity helpers."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from garmin_mcp import strength_training


CATALOG = (
    ("BENCH_PRESS", "BARBELL_BENCH_PRESS"),
    ("DEADLIFT", "BARBELL_DEADLIFT"),
    ("FLYE", "INCLINE_REVERSE_FLYE"),
    ("HIP_STABILITY", "DEAD_BUG"),
    ("PLANK", "SIDE_PLANK"),
    ("PULL_UP", "PULL_UP"),
    ("PUSH_UP", "PUSH_UP"),
    ("ROW", "BENT_OVER_ROW_WITH_DUMBELL"),
    ("SHOULDER_PRESS", "DUMBBELL_SHOULDER_PRESS"),
    ("SQUAT", "BARBELL_BACK_SQUAT"),
)


@pytest.fixture(autouse=True)
def use_test_catalog(monkeypatch):
    loader = strength_training._load_garmin_exercise_catalog
    loader.cache_clear()
    monkeypatch.setattr(
        strength_training,
        "_load_garmin_exercise_catalog",
        lambda: CATALOG,
    )
    yield
    loader.cache_clear()


def test_catalog_parser_extracts_category_name_pairs():
    payload = {
        "categories": {
            "PUSH_UP": {
                "primaryMuscles": ["CHEST"],
                "exercises": {
                    "PUSH_UP": {"primaryMuscles": ["CHEST"]},
                    "DIAMOND_PUSH_UP": {"primaryMuscles": ["TRICEPS"]},
                },
            }
        }
    }

    assert strength_training._catalog_pairs_from_payload(payload) == (
        ("PUSH_UP", "DIAMOND_PUSH_UP"),
        ("PUSH_UP", "PUSH_UP"),
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("push up", ("PUSH_UP", "PUSH_UP")),
        ("barbell bench press", ("BENCH_PRESS", "BARBELL_BENCH_PRESS")),
        ("dead bug", ("HIP_STABILITY", "DEAD_BUG")),
    ],
)
def test_english_names_are_matched_directly_from_catalog(query, expected):
    match = strength_training._resolve_exercise_query(query)

    assert (match["category"], match["name"]) == expected
    assert match["match_type"] == "catalog_exact"


def test_localized_names_are_not_maintained_as_manual_aliases():
    with pytest.raises(
        strength_training.StrengthTrainingError,
        match="No Garmin exercise matches",
    ):
        strength_training._resolve_exercise_query("pompki")


def test_catalog_exact_match(monkeypatch):
    monkeypatch.setattr(
        strength_training, "_load_garmin_exercise_catalog", lambda: CATALOG
    )

    match = strength_training._resolve_exercise_query(
        "BENT_OVER_ROW_WITH_DUMBELL"
    )

    assert match["category"] == "ROW"
    assert match["name"] == "BENT_OVER_ROW_WITH_DUMBELL"
    assert match["match_type"] == "catalog_exact"


def test_ambiguous_catalog_name_is_rejected(monkeypatch):
    monkeypatch.setattr(
        strength_training,
        "_load_garmin_exercise_catalog",
        lambda: (("ROW", "GENERIC_MOVEMENT"), ("CORE", "GENERIC_MOVEMENT")),
    )

    with pytest.raises(
        strength_training.StrengthTrainingError, match="ambiguous"
    ):
        strength_training._resolve_exercise_query("generic movement")


def test_low_confidence_match_returns_candidates_instead_of_guessing(monkeypatch):
    monkeypatch.setattr(
        strength_training, "_load_garmin_exercise_catalog", lambda: CATALOG
    )

    with pytest.raises(
        strength_training.StrengthTrainingError, match="No confident"
    ) as exc:
        strength_training._resolve_exercise_query("shoulder movement machine")

    assert "Closest candidates" in str(exc.value)


def test_exact_category_name_is_validated(monkeypatch):
    monkeypatch.setattr(
        strength_training, "_load_garmin_exercise_catalog", lambda: CATALOG
    )

    match = strength_training._resolve_exercise_spec(
        {"category": "push_up", "name": "push-up"},
        0,
    )

    assert match["category"] == "PUSH_UP"
    assert match["name"] == "PUSH_UP"
    assert match["match_type"] == "provided"


def test_exact_category_name_remains_available_when_catalog_is_down(monkeypatch):
    def unavailable():
        raise RuntimeError("network down")

    monkeypatch.setattr(
        strength_training, "_load_garmin_exercise_catalog", unavailable
    )

    match = strength_training._resolve_exercise_spec(
        {"category": "PUSH_UP", "name": "PUSH_UP"},
        0,
    )

    assert match["match_type"] == "provided_unverified"


def test_unknown_exact_identifier_is_rejected_with_category_suggestions(
    monkeypatch,
):
    monkeypatch.setattr(
        strength_training, "_load_garmin_exercise_catalog", lambda: CATALOG
    )

    with pytest.raises(
        strength_training.StrengthTrainingError, match="unknown Garmin exercise"
    ):
        strength_training._resolve_exercise_spec(
            {"category": "PUSH_UP", "name": "PUSH_DOWN"},
            0,
        )


def test_prepare_sets_expands_repeats_and_converts_kg_and_time_zone():
    activity_start = datetime(
        2026, 7, 23, 10, 0, tzinfo=ZoneInfo("Europe/Warsaw")
    )

    payload, matches = strength_training._prepare_strength_sets(
        [
            {
                "exercise": "push up",
                "sets": 2,
                "repetitions": 10,
                "weight_kg": 12.5,
                "duration_seconds": 40,
                "rest_seconds": 80,
            },
            {"set_type": "REST", "duration_seconds": 60},
        ],
        activity_start,
    )

    assert len(payload) == 3
    assert len(matches) == 1
    assert payload[0] == {
        "exercises": [
            {
                "category": "PUSH_UP",
                "name": "PUSH_UP",
                "probability": 100.0,
            }
        ],
        "duration": 40.0,
        "repetitionCount": 10,
        "weight": 12500.0,
        "setType": "ACTIVE",
        "startTime": "2026-07-23T08:00:00.0",
        "wktStepIndex": None,
        "messageIndex": None,
    }
    assert payload[1]["startTime"] == "2026-07-23T08:02:00.0"
    assert payload[2]["startTime"] == "2026-07-23T08:04:00.0"
    assert payload[2]["setType"] == "REST"
    assert payload[2]["exercises"] == []
    assert payload[2]["weight"] is None


def test_bodyweight_is_encoded_as_unset_weight():
    payload, _ = strength_training._prepare_strength_sets(
        [{"exercise": "push up", "repetitions": 12}],
        datetime(2026, 7, 23, 9, 0, tzinfo=ZoneInfo("UTC")),
    )

    assert payload[0]["weight"] == -1.0
    assert strength_training._display_exercise_sets(payload)[0]["weightKg"] is None


def test_clock_start_time_uses_activity_date_and_zone():
    payload, _ = strength_training._prepare_strength_sets(
        [{"exercise": "push up", "start_time": "18:30:15"}],
        datetime(
            2026, 12, 10, 9, 0, tzinfo=ZoneInfo("Europe/Warsaw")
        ),
    )

    assert payload[0]["startTime"] == "2026-12-10T17:30:15.0"


def test_out_of_order_or_overlapping_sets_are_rejected():
    with pytest.raises(
        strength_training.StrengthTrainingError, match="overlaps or precedes"
    ):
        strength_training._prepare_strength_sets(
            [
                {
                    "exercise": "push up",
                    "start_time": "10:05:00",
                    "duration_seconds": 60,
                },
                {
                    "exercise": "push up",
                    "start_time": "10:04:30",
                    "duration_seconds": 30,
                },
            ],
            datetime(
                2026, 7, 23, 10, 0, tzinfo=ZoneInfo("Europe/Warsaw")
            ),
        )


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"exercise": "push up", "sets": 0}, "positive integer"),
        ({"exercise": "push up", "repetitions": -1}, "non-negative"),
        ({"exercise": "push up", "weight_kg": -1}, "at least 0"),
        ({"exercise": "push up", "duration_seconds": 0}, "at least"),
        ({"exercise": "push up", "set_type": "WARMUP"}, "ACTIVE or REST"),
        (
            {"set_type": "REST", "exercise": "push up"},
            "must not specify an exercise",
        ),
        (
            {
                "exercise": "push up",
                "offset_seconds": 10,
                "offset_minutes": 1,
            },
            "cannot specify both",
        ),
    ],
)
def test_invalid_set_specs_are_rejected(spec, message):
    with pytest.raises(strength_training.StrengthTrainingError, match=message):
        strength_training._prepare_strength_sets(
            [spec],
            datetime(2026, 7, 23, 9, 0, tzinfo=ZoneInfo("UTC")),
        )


def test_readback_verification_accepts_garmin_bodyweight_normalisation():
    expected, _ = strength_training._prepare_strength_sets(
        [{"exercise": "push up", "repetitions": 12}],
        datetime(2026, 7, 23, 9, 0, tzinfo=ZoneInfo("UTC")),
    )
    readback = {
        "activityId": 123,
        "exerciseSets": [
            {
                **expected[0],
                "weight": 0.0,
                "messageIndex": 7,
                "exercises": [
                    {
                        "category": "PUSH_UP",
                        "name": "PUSH_UP",
                        "probability": 84.0,
                    },
                    {
                        "category": "TRICEPS_EXTENSION",
                        "name": "BENCH_DIP",
                        "probability": 40.0,
                    },
                ],
            }
        ],
    }

    verified, differences = strength_training._verify_exercise_sets(
        expected, readback
    )

    assert verified is True
    assert differences == []


@pytest.mark.parametrize(
    "set_spec",
    [
        {"exercise": "push up"},
        {"set_type": "REST"},
    ],
)
def test_readback_verification_accepts_zero_for_an_absent_repetition_count(
    set_spec,
):
    expected, _ = strength_training._prepare_strength_sets(
        [set_spec],
        datetime(2026, 7, 23, 9, 0, tzinfo=ZoneInfo("UTC")),
    )
    readback = {
        "exerciseSets": [
            {
                **expected[0],
                "repetitionCount": 0,
            }
        ]
    }

    verified, differences = strength_training._verify_exercise_sets(
        expected, readback
    )

    assert verified is True
    assert differences == []


def test_readback_verification_reports_material_differences():
    expected, _ = strength_training._prepare_strength_sets(
        [{"exercise": "push up", "repetitions": 12}],
        datetime(2026, 7, 23, 9, 0, tzinfo=ZoneInfo("UTC")),
    )
    readback = {
        "exerciseSets": [
            {
                **expected[0],
                "repetitionCount": 10,
                "exercises": [
                    {
                        "category": "PULL_UP",
                        "name": "PULL_UP",
                        "probability": 100,
                    }
                ],
            }
        ]
    }

    verified, differences = strength_training._verify_exercise_sets(
        expected, readback
    )

    assert verified is False
    assert any("repetitionCount" in difference for difference in differences)
    assert any("PUSH_UP/PUSH_UP" in difference for difference in differences)


def test_activity_start_prefers_gmt_and_represents_it_in_the_requested_zone():
    activity = {
        "summaryDTO": {
            "startTimeGMT": "2026-07-23T08:00:00.0",
            "startTimeLocal": "2026-07-23T10:00:00.0",
        }
    }

    start = strength_training._activity_start(
        activity, None, "Europe/Warsaw"
    )

    assert start.isoformat() == "2026-07-23T10:00:00+02:00"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"activityId": 123}, 123),
        ({"activity": {"activity_id": "456"}}, 456),
        ({"results": [{"activityId": 789}]}, 789),
        ({"activityId": 0}, None),
        ({"activityId": True}, None),
        ({"unrelatedId": 123}, None),
    ],
)
def test_find_activity_id_handles_known_response_shapes(response, expected):
    assert strength_training._find_activity_id(response) == expected


def test_local_garmin_timestamp_drops_offset_without_changing_wall_clock():
    value = datetime(
        2026, 7, 23, 18, 5, 7, tzinfo=ZoneInfo("Europe/Warsaw")
    )

    assert (
        strength_training._garmin_local_timestamp(value)
        == "2026-07-23T18:05:07.000"
    )


def test_sets_must_fit_inside_known_activity_duration():
    activity_start = datetime(2026, 7, 23, 8, 0, tzinfo=ZoneInfo("UTC"))
    exercise_sets, _ = strength_training._prepare_strength_sets(
        [
            {
                "exercise": "push up",
                "start_time": "08:09:50",
                "duration_seconds": 20,
            }
        ],
        activity_start,
    )

    with pytest.raises(
        strength_training.StrengthTrainingError, match="after the activity"
    ):
        strength_training._validate_sets_within_activity(
            {"summaryDTO": {"duration": 600}},
            activity_start,
            exercise_sets,
        )
