"""Shared fail-closed capability guards for real-account contracts."""

from contextlib import contextmanager

import pytest


class NutritionCapabilityGuard:
    """Skip only when Garmin explicitly denies or omits a nutrition capability."""

    @staticmethod
    def _is_forbidden(exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        message = str(exc).lower()
        return status == 403 or "api error 403" in message or "http 403" in message

    @contextmanager
    def require(self, capability: str):
        try:
            yield
        except Exception as exc:
            if self._is_forbidden(exc):
                pytest.skip(
                    f"Garmin account capability unavailable: {capability} (HTTP 403)"
                )
            raise

    @staticmethod
    def meal_id(payload, meal_name: str):
        meals = payload.get("meals") if isinstance(payload, dict) else None
        if not isinstance(meals, list):
            pytest.skip(
                "Garmin account capability unavailable: nutrition meals response"
            )
        for meal in meals:
            if meal.get("mealName") == meal_name and meal.get("mealId") is not None:
                return meal["mealId"]
        pytest.skip(
            f"Garmin account capability unavailable: nutrition meal {meal_name}"
        )


@pytest.fixture
def nutrition_capability():
    return NutritionCapabilityGuard()
