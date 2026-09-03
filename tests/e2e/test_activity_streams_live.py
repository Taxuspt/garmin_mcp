"""Read-only live contract for the canonical FIT timeline and analytics."""

import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _server_params() -> StdioServerParameters:
    env = os.environ.copy()
    src = Path(__file__).resolve().parents[2] / "src"
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(src), env.get("PYTHONPATH", "")])
    )
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "garmin_mcp"],
        env=env,
    )


def _payload(result):
    try:
        decoded = json.loads(result.content[0].text)
        if isinstance(decoded, dict):
            return decoded
    except (AttributeError, IndexError, json.JSONDecodeError, TypeError):
        pass
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    return {}


@pytest.mark.e2e
@pytest.mark.live_read
@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_live_cycling_streams_decoupling_and_reslice_close():
    today = dt.date.today()
    start = today - dt.timedelta(days=365)
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            activities = _payload(
                await session.call_tool(
                    "get_activities_by_date",
                    arguments={
                        "start_date": start.isoformat(),
                        "end_date": today.isoformat(),
                        "page_size": 200,
                    },
                )
            ).get("activities", [])
            cycling = [
                item
                for item in activities
                if any(
                    token in str(item.get("type", "")).lower()
                    for token in ("cycling", "biking", "bike")
                )
            ]
            if not cycling:
                pytest.skip("No cycling activity was available in the last 365 days")
            activity_id = cycling[0]["id"]

            cursor = None
            seen = 0
            expected_total = None
            while True:
                arguments = {
                    "activity_id": activity_id,
                    "fields": ["hr", "power", "speed", "cadence", "altitude"],
                    "resolution": "raw",
                    "time_basis": "active",
                    "page_size": 5000,
                }
                if cursor is not None:
                    arguments["cursor"] = cursor
                page = _payload(
                    await session.call_tool("get_activity_streams", arguments=arguments)
                )
                assert page["status"] == "ok"
                pagination = page["pagination"]
                expected_total = pagination["total_points"]
                seen += len(page["data"])
                cursor = pagination.get("next_cursor")
                if not cursor:
                    break
            assert seen == expected_total
            assert seen > 0

            decoupling = _payload(
                await session.call_tool(
                    "analyze_decoupling",
                    arguments={"activity_id": activity_id, "metric": "auto"},
                )
            )
            assert decoupling["status"] in {"ok", "insufficient_quality"}

            resliced = _payload(
                await session.call_tool(
                    "reslice_zones",
                    arguments={
                        "activity_id": activity_id,
                        "model": {
                            "sport": "cycling",
                            "metric": "hr",
                            "source": "live_contract",
                            "version": "1",
                            "zones": [
                                {"name": "z1", "lower_inclusive": 0, "upper_exclusive": 120},
                                {"name": "z2", "lower_inclusive": 120, "upper_exclusive": 150},
                                {"name": "z3", "lower_inclusive": 150, "upper_exclusive": 170},
                                {"name": "z4", "lower_inclusive": 170, "upper_exclusive": 190},
                                {"name": "z5", "lower_inclusive": 190, "upper_exclusive": None},
                            ],
                        },
                    },
                )
            )
            assert resliced["status"] == "ok"
            assert abs(
                resliced["classified_s"]
                + resliced["missing_s"]
                - resliced["total_active_s"]
            ) <= 1.0
