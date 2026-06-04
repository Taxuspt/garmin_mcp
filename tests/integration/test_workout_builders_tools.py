"""
Integration tests for high-level workout builder tools (workout_builders.py).
"""
import json

import pytest
from mcp.server.fastmcp import FastMCP
from unittest.mock import MagicMock

from garmin_mcp import workouts, workout_builders
from garmin_mcp.client_resolver import set_global_client


@pytest.fixture
def app_with_builders(mock_garmin_client):
    """FastMCP app with workout_builders registered.

    Also configures `workouts` because workout_builders.schedule_week reuses
    the `_is_already_scheduled` helper defined there; the per-request client
    resolved via get_client(ctx) is passed into it, so the resolver global
    must point at the same mock for the helper to see the right state.
    """
    # Default: pre-check finds no existing schedules so the POST path runs.
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {"workoutScheduleSummariesScalar": []}
    }
    workouts.configure(mock_garmin_client)
    workout_builders.configure(mock_garmin_client)
    set_global_client(mock_garmin_client)
    app = FastMCP("Test Workout Builders")
    app = workout_builders.register_tools(app)
    return app


@pytest.mark.asyncio
async def test_schedule_week_uses_client_post_not_garth(
    app_with_builders, mock_garmin_client
):
    """schedule_week must route through garmin_client.client.post

    Regression: garminconnect 0.3.2 removed the `.garth` attribute. The old
    code called `garmin_client.garth.post(...)` which raises AttributeError.
    This test pins the fix.
    """
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_garmin_client.client.post.return_value = mock_response

    result = await app_with_builders.call_tool(
        "schedule_week",
        {"week": [{"date": "2026-05-12", "workout_id": 1234567890}]},
    )

    assert result is not None
    payload = json.loads(result[0][0].text)
    assert payload["status"] == "complete"
    assert payload["scheduled"][0]["status"] == "scheduled"
    assert payload["scheduled"][0]["workout_id"] == 1234567890
    # Must call .client.post, never .garth.*
    mock_garmin_client.client.post.assert_called_once()


@pytest.mark.asyncio
async def test_schedule_week_is_idempotent(
    app_with_builders, mock_garmin_client
):
    """schedule_week skips the POST when the workout is already scheduled.

    Matches the idempotency behaviour of schedule_workout / schedule_workouts.
    """
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {
            "workoutScheduleSummariesScalar": [
                {
                    "workoutId": 1234567890,
                    "scheduleDate": "2026-05-12",
                    "workoutName": "Easy Run",
                }
            ]
        }
    }

    result = await app_with_builders.call_tool(
        "schedule_week",
        {"week": [{"date": "2026-05-12", "workout_id": 1234567890}]},
    )

    assert result is not None
    payload = json.loads(result[0][0].text)
    assert payload["status"] == "complete"
    assert payload["scheduled"][0]["status"] == "already_scheduled"
    assert payload["scheduled"][0]["idempotent"] is True
    # Critically: no POST happened
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_week_partial_idempotency(
    app_with_builders, mock_garmin_client
):
    """Mixed week: some entries already scheduled, others new.

    Verifies the pre-check runs per-item, not once for the whole batch.
    """
    def graphql_side_effect(query):
        # Return existing schedule only for 2026-05-12
        if "2026-05-12" in query["query"]:
            return {
                "data": {
                    "workoutScheduleSummariesScalar": [
                        {
                            "workoutId": 111,
                            "scheduleDate": "2026-05-12",
                            "workoutName": "Easy Run",
                        }
                    ]
                }
            }
        return {"data": {"workoutScheduleSummariesScalar": []}}

    mock_garmin_client.query_garmin_graphql.side_effect = graphql_side_effect

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_garmin_client.client.post.return_value = mock_response

    result = await app_with_builders.call_tool(
        "schedule_week",
        {
            "week": [
                {"date": "2026-05-12", "workout_id": 111},  # already scheduled
                {"date": "2026-05-14", "workout_id": 222},  # new
            ]
        },
    )

    payload = json.loads(result[0][0].text)
    scheduled = payload["scheduled"]
    assert scheduled[0]["status"] == "already_scheduled"
    assert scheduled[1]["status"] == "scheduled"
    # Only the new one triggered the POST
    assert mock_garmin_client.client.post.call_count == 1
