"""Provider-neutral domain boundaries used by the coaching services."""

from __future__ import annotations

from typing import Any, Protocol, Sequence


class ActivityProvider(Protocol):
    def list_activities(
        self, start_date: str, end_date: str, sport: str | None = None
    ) -> Sequence[dict[str, Any]]: ...

    def download_activity(self, activity_id: str | int) -> bytes: ...


class HealthProvider(Protocol):
    def get_training_readiness(self, date: str) -> dict[str, Any] | list[Any]: ...

    def get_hrv(self, date: str) -> dict[str, Any] | None: ...

    def get_training_status(self, date: str) -> dict[str, Any]: ...


class WorkoutProvider(Protocol):
    def create_workout(self, workout: dict[str, Any]) -> dict[str, Any]: ...

    def schedule_workout(self, workout_id: int, date: str) -> Any: ...

    def delete_workout(self, workout_id: int) -> Any: ...
