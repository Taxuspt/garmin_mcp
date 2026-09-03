import pytest

from tests.e2e.conftest import NutritionCapabilityGuard


class _ForbiddenError(Exception):
    def __init__(self):
        self.response = type("Response", (), {"status_code": 403})()
        super().__init__("forbidden")


def test_nutrition_guard_skips_only_explicit_forbidden():
    guard = NutritionCapabilityGuard()
    with pytest.raises(pytest.skip.Exception, match="custom-food write"):
        with guard.require("custom-food write"):
            raise _ForbiddenError()


def test_nutrition_guard_does_not_hide_other_failures():
    guard = NutritionCapabilityGuard()
    with pytest.raises(RuntimeError, match="schema changed"):
        with guard.require("custom-food write"):
            raise RuntimeError("schema changed")


def test_nutrition_guard_requires_named_meal():
    guard = NutritionCapabilityGuard()
    assert guard.meal_id(
        {"meals": [{"mealName": "SNACKS", "mealId": 42}]}, "SNACKS"
    ) == 42
    with pytest.raises(pytest.skip.Exception, match="SNACKS"):
        guard.meal_id({"meals": []}, "SNACKS")
