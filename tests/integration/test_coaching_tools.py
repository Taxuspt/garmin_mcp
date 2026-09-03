import datetime as dt
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import coaching, physiology


def _race_in_weeks(weeks: int) -> str:
    today = dt.date.today()
    monday = today + dt.timedelta(days=(-today.weekday()) % 7)
    return (monday + dt.timedelta(weeks=weeks) - dt.timedelta(days=1)).isoformat()


@pytest.fixture
def coach_app(tmp_path):
    client = Mock()
    client.get_training_readiness.return_value = [{"score": 80}]
    client.get_hrv_data.return_value = {"hrvSummary": {"status": "BALANCED"}}
    client.get_training_status.return_value = {
        "mostRecentTrainingStatus": {
            "latestTrainingStatusData": {
                "device": {
                    "primaryTrainingDevice": True,
                    "acuteTrainingLoadDTO": {
                        "dailyTrainingLoadAcute": 300,
                        "dailyTrainingLoadChronic": 305,
                    },
                }
            }
        }
    }
    client.get_activities_by_date.return_value = [{"activityId": 1}, {"activityId": 2}]
    physiology.configure(client, str(tmp_path))
    coaching.configure(client)
    app = coaching.register_tools(FastMCP("coach test"))
    yield app, client
    physiology.configure(data_dir="")


@pytest.mark.asyncio
async def test_plan_and_adapt_are_structured_preview_first_tools(
    coach_app, monkeypatch
):
    app, client = coach_app
    _content, plan = await app.call_tool(
        "plan_training_block",
        {
            "race_date": _race_in_weeks(8),
            "goal": "gran_fondo",
            "sport": "cycling",
            "days_per_week": 3,
        },
    )
    assert plan["status"] == "draft"
    assert plan["storage"]["created"] is True

    _content, preview = await app.call_tool(
        "apply_training_block", {"plan_id": plan["plan_id"]}
    )
    assert preview["status"] == "preview"
    assert preview["dry_run"] is True
    client.upload_workout.assert_not_called()

    monkeypatch.setattr(
        coaching.write_confirmation,
        "confirm_garmin_write",
        AsyncMock(return_value=(False, "user declined")),
    )
    _content, refused = await app.call_tool(
        "apply_training_block", {"plan_id": plan["plan_id"], "dry_run": False}
    )
    assert refused["status"] == "needs_confirmation"
    assert refused["write_performed"] is False
    client.upload_workout.assert_not_called()
    summary = coaching.write_confirmation.confirm_garmin_write.call_args.kwargs[
        "summary"
    ]
    assert summary["workouts_to_create"] == len(summary["scheduled_workouts"])
    assert all(item["date"] for item in summary["scheduled_workouts"])
    assert all(item["workout_signature"] for item in summary["scheduled_workouts"])

    week_of = plan["weeks"][1]["week_of"]
    _content, adaptation = await app.call_tool(
        "adapt_week", {"week_of": week_of, "plan_id": plan["plan_id"]}
    )
    assert adaptation["status"] == "pending"
    assert adaptation["garmin_write_performed"] is False

    _content, adaptation_preview = await app.call_tool(
        "apply_week_adaptation", {"adaptation_id": adaptation["adaptation_id"]}
    )
    assert adaptation_preview["status"] == "preview"
    assert adaptation_preview["garmin_write_performed"] is False
    client.upload_workout.assert_not_called()
