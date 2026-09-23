"""Weight-goal targetDate handling in nutrition settings updates.

`/nutrition-service/settings/{date}` is a full-object PUT whose payload carries
the weight goal's `targetDate`. Garmin rejects the whole request unless that
date is after the day being written, so a goal whose target has passed makes
every nutrition update fail with a message that names neither the field nor the
weight goal.
"""

from garmin_mcp.nutrition import resolve_weight_goal_target_date


def test_valid_stored_target_is_left_untouched():
    new_target, error = resolve_weight_goal_target_date("2030-01-01", "2024-02-01")
    assert new_target is None
    assert error is None


def test_absent_stored_target_is_left_untouched():
    for stored in (None, ""):
        new_target, error = resolve_weight_goal_target_date(stored, "2024-02-01")
        assert new_target is None
        assert error is None


def test_passed_target_is_reported_not_silently_changed():
    new_target, error = resolve_weight_goal_target_date("2024-03-01", "2024-07-15")
    assert new_target is None
    assert error is not None
    # The message must name the field, both dates, and how to resolve it.
    assert "targetDate" in error
    assert "2024-03-01" in error and "2024-07-15" in error
    assert "target_date" in error


def test_target_equal_to_date_is_rejected():
    # Garmin requires strictly after, not on or after.
    _, error = resolve_weight_goal_target_date("2024-07-15", "2024-07-15")
    assert error is not None


def test_explicit_target_date_overrides_a_passed_one():
    new_target, error = resolve_weight_goal_target_date(
        "2024-03-01", "2024-07-15", "2024-12-31"
    )
    assert new_target == "2024-12-31"
    assert error is None


def test_explicit_target_date_must_be_after_date():
    for candidate in ("2024-07-15", "2024-07-14"):
        new_target, error = resolve_weight_goal_target_date(
            "2024-03-01", "2024-07-15", candidate
        )
        assert new_target is None
        assert error is not None and "must be after" in error


def test_explicit_target_date_must_be_iso():
    for candidate in ("31-12-2026", "not-a-date", "2024-13-01"):
        new_target, error = resolve_weight_goal_target_date(
            "2024-03-01", "2024-07-15", candidate
        )
        assert new_target is None
        assert error is not None and "YYYY-MM-DD" in error
