"""
End-to-end test for MCP server functionality

This test connects to the actual MCP server and makes real API calls.
It requires valid Garmin credentials in the .env file.

WARNING: These tests may hang if:
- Garmin credentials are invalid
- MFA is required and tokens are expired
- Network connection is unavailable

Run with: pytest tests/e2e/ -m e2e
Or skip with: pytest -m "not e2e"
"""

import os
import sys
import pytest
import asyncio
import json
import uuid
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# Import MCP client for testing
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Load environment variables
load_dotenv()


def _build_server_params():
    """Construct MCP server parameters using the active Python interpreter."""
    env = os.environ.copy()
    repo_root = Path(__file__).resolve().parents[2]
    src_path = repo_root / "src"
    if str(src_path) not in env.get("PYTHONPATH", ""):
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [str(src_path), env.get("PYTHONPATH", "")])
        )
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "garmin_mcp"],
        env=env,
    )


def _decoded_tool_result(result):
    """Decode a JSON tool result, returning an empty mapping for text errors."""
    try:
        return json.loads(result.content[0].text)
    except (AttributeError, IndexError, json.JSONDecodeError, TypeError):
        return {}


async def _cleanup_generated_workouts(
    session,
    names,
    *,
    calendar_date=None,
    known_workout_ids=(),
    known_scheduled_ids=(),
):
    """Best-effort cleanup scoped only to this invocation's unique names."""
    failures = []
    workout_ids = {int(workout_id) for workout_id in known_workout_ids}
    scheduled_ids = {int(value) for value in known_scheduled_ids}

    for attempt in range(12):
        try:
            listed = await session.call_tool("get_workouts", arguments={})
            for workout in _decoded_tool_result(listed).get("workouts", []):
                if workout.get("name") in names and workout.get("id") is not None:
                    workout_ids.add(int(workout["id"]))
        except Exception as error:
            if attempt == 11:
                failures.append(f"workout lookup failed: {error}")

        if calendar_date is not None:
            try:
                scheduled = await session.call_tool(
                    "get_scheduled_workouts",
                    arguments={"start_date": calendar_date, "end_date": calendar_date},
                )
                for item in _decoded_tool_result(scheduled).get(
                    "scheduled_workouts", []
                ):
                    item_workout_id = item.get("workout_id")
                    matches_id = (
                        item_workout_id is not None
                        and int(item_workout_id) in workout_ids
                    )
                    if item.get("name") not in names and not matches_id:
                        continue
                    if item_workout_id is not None:
                        workout_ids.add(int(item_workout_id))
                    if item.get("scheduled_workout_id") is not None:
                        scheduled_ids.add(item["scheduled_workout_id"])
            except Exception as error:
                if attempt == 11:
                    failures.append(f"calendar lookup failed: {error}")

        found_expected = workout_ids and (
            calendar_date is None or scheduled_ids
        )
        if found_expected or attempt == 11:
            break
        await asyncio.sleep(1)

    safely_unscheduled = calendar_date is None or bool(scheduled_ids)
    for scheduled_id in scheduled_ids:
        disappeared = calendar_date is None
        attempt_errors = []
        for delete_attempt in range(3):
            try:
                removed = await session.call_tool(
                    "unschedule_workout",
                    arguments={"scheduled_workout_id": scheduled_id},
                )
                removed_payload = _decoded_tool_result(removed)
                if removed_payload.get("status") != "success":
                    attempt_errors.append(str(removed_payload))
                elif removed_payload.get("read_back_absent") is True:
                    disappeared = True
            except Exception as error:
                attempt_errors.append(str(error))
            if calendar_date is not None and not disappeared:
                for attempt in range(5):
                    scheduled = await session.call_tool(
                        "get_scheduled_workouts",
                        arguments={
                            "start_date": calendar_date,
                            "end_date": calendar_date,
                        },
                    )
                    remaining = _decoded_tool_result(scheduled).get(
                        "scheduled_workouts", []
                    )
                    if not any(
                        str(item.get("scheduled_workout_id"))
                        == str(scheduled_id)
                        for item in remaining
                    ):
                        disappeared = True
                        break
                    if attempt < 4:
                        await asyncio.sleep(1)
            if disappeared:
                break
            if delete_attempt < 2:
                await asyncio.sleep(1)
        if not disappeared:
            safely_unscheduled = False
            failures.append(
                f"calendar entry {scheduled_id} still present after exact unschedule retries"
                + (f": {' | '.join(attempt_errors[-2:])}" if attempt_errors else "")
            )

    if calendar_date is not None and not scheduled_ids:
        failures.append(
            "calendar entry did not become visible; retained the uniquely named "
            "workout template to avoid creating an orphan schedule"
        )

    if workout_ids and safely_unscheduled:
        try:
            deleted = await session.call_tool(
                "delete_workouts",
                arguments={"workout_ids": sorted(workout_ids)},
            )
            deleted_payload = _decoded_tool_result(deleted)
            if deleted_payload.get("failed"):
                failures.append(f"workout cleanup failed: {deleted_payload}")
        except Exception as error:
            failures.append(f"workout cleanup failed: {error}")

    if failures:
        warnings.warn("; ".join(failures), stacklevel=2)
    return failures


@pytest.mark.e2e
@pytest.mark.live_read
@pytest.mark.asyncio
@pytest.mark.timeout(30)  # Pytest timeout
async def test_mcp_server_connection():
    """Test MCP server connection and initialization

    WARNING: This test requires:
    - Valid GARMIN_EMAIL and GARMIN_PASSWORD in .env file
    - Active internet connection
    - May require MFA code input if tokens are expired
    """
    # Use python module execution instead of direct script path
    server_params = _build_server_params()

    # Connect to server with timeout
    try:
        async with asyncio.timeout(20):  # AsyncIO timeout
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    # Initialize the connection
                    await session.initialize()

                    # List available tools
                    tools = await session.list_tools()

                    # Verify we have tools
                    assert len(tools.tools) > 0, "No tools found in MCP server"

                    # Print available tools for debugging
                    print(f"\nFound {len(tools.tools)} tools:")
                    for tool in tools.tools[:5]:  # Show first 5
                        print(f"  - {tool.name}: {tool.description}")
                    print(f"  ... and {len(tools.tools) - 5} more")
    except asyncio.TimeoutError:
        pytest.fail(
            "Server connection timed out after 20 seconds. "
            "Check your Garmin credentials in .env file and network connection. "
            "If MFA is required, run the server manually first to authenticate."
        )


@pytest.mark.e2e
@pytest.mark.live_read
@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_list_activities_tool():
    """Test the list_activities MCP tool with real API"""
    server_params = _build_server_params()

    try:
        async with asyncio.timeout(20):
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    # Test list_activities
                    result = await session.call_tool(
                        "list_activities",
                        arguments={"limit": 2}
                    )

                    # Verify result
                    assert result is not None
                    assert len(result.content) > 0

                    # Print result for debugging
                    print(f"\nlist_activities result preview:")
                    print(result.content[0].text[:500] + "...")
    except asyncio.TimeoutError:
        pytest.fail("Tool execution timed out - check your Garmin credentials and network")


@pytest.mark.e2e
@pytest.mark.live_read
@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_get_steps_data_tool():
    """Test the get_steps_data MCP tool with real API"""
    server_params = _build_server_params()

    try:
        async with asyncio.timeout(20):
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    # Test get_steps_data with today's date
                    today = datetime.now().strftime("%Y-%m-%d")

                    result = await session.call_tool(
                        "get_steps_data",
                        arguments={"date": today}
                    )

                    # Verify result
                    assert result is not None
                    assert len(result.content) > 0

                    # Print result for debugging
                    print(f"\nget_steps_data result preview:")
                    print(result.content[0].text[:500] + "...")
    except asyncio.TimeoutError:
        pytest.fail("Tool execution timed out - check your Garmin credentials and network")


@pytest.mark.e2e
@pytest.mark.live_read
@pytest.mark.asyncio
@pytest.mark.timeout(45)
async def test_multiple_tools():
    """Test multiple MCP tools in a single session"""
    server_params = _build_server_params()

    try:
        async with asyncio.timeout(40):
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    today = datetime.now().strftime("%Y-%m-%d")

                    # Test multiple tools
                    tools_to_test = [
                        ("list_activities", {"limit": 1}),
                        ("get_steps_data", {"date": today}),
                        ("get_user_profile", {}),
                    ]

                    for tool_name, args in tools_to_test:
                        try:
                            result = await session.call_tool(tool_name, arguments=args)
                            assert result is not None
                            print(f"\n✓ {tool_name} succeeded")
                        except Exception as e:
                            print(f"\n✗ {tool_name} failed: {str(e)}")
                            # Don't fail the test for individual tool failures
                            # Some tools may not have data available
    except asyncio.TimeoutError:
        pytest.fail("Multiple tools test timed out - check your Garmin credentials and network")


@pytest.mark.e2e
@pytest.mark.live_write
@pytest.mark.skip(
    reason=(
        "Quarantined: this legacy case schedules caller-owned workout IDs. "
        "Covered safely by test_schedule_workouts_inline_upload, which creates "
        "a uniquely named workout and removes its calendar entry in finally."
    )
)
@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_schedule_workouts_tool():
    """Quarantined permanently: never schedule caller-owned workouts in CI."""
    raise AssertionError("quarantine decorator must prevent this body from executing")


@pytest.mark.e2e
@pytest.mark.live_write
@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_upload_workouts_tool():
    """Test upload_workouts MCP tool — creates multiple workouts in one call

    WARNING: This test requires:
    - Valid GARMIN_EMAIL and GARMIN_PASSWORD in .env file
    - Active internet connection

    This test uploads real workouts to Garmin Connect. The created workouts are
    deleted at the end of the test to keep the library clean.
    """
    server_params = _build_server_params()
    run_id = uuid.uuid4().hex
    workout_names = {
        f"e2e upload contract {run_id} a",
        f"e2e upload contract {run_id} b",
    }

    def minimal_workout(name):
        return {
            "workoutName": name,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSegments": [{
                "segmentOrder": 1,
                "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
                "workoutSteps": [{
                    "type": "ExecutableStepDTO",
                    "stepOrder": 1,
                    "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": 600.0,
                    "targetType": {
                        "workoutTargetTypeId": 1,
                        "workoutTargetTypeKey": "no.target",
                    },
                }],
            }],
        }

    created_ids = set()
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            try:
                async with asyncio.timeout(35):
                    result = await session.call_tool(
                        "upload_workouts",
                        arguments={
                            "workouts": [
                                minimal_workout(name)
                                for name in sorted(workout_names)
                            ]
                        },
                    )

                assert result is not None
                assert len(result.content) > 0
                result_data = json.loads(result.content[0].text)
                assert result_data["total"] == 2
                assert len(result_data["results"]) == 2
                created_ids.update(
                    item["workout_id"]
                    for item in result_data["results"]
                    if item.get("workout_id") is not None
                )
                print("\nupload_workouts result:")
                print(json.dumps(result_data, indent=2))
            finally:
                try:
                    async with asyncio.timeout(20):
                        await _cleanup_generated_workouts(
                            session,
                            workout_names,
                            known_workout_ids=created_ids,
                        )
                except Exception as error:
                    warnings.warn(f"workout cleanup timed out/failed: {error}", stacklevel=2)


@pytest.mark.e2e
@pytest.mark.live_write
@pytest.mark.skip(
    reason=(
        "Quarantined: deleting guessed IDs is never safe on a live account. "
        "The delete_workouts contract is exercised by the finally cleanup in "
        "test_upload_workouts_tool using only IDs resolved from unique names."
    )
)
@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_delete_workouts_tool():
    """Quarantined permanently: guessed IDs must never reach a live account."""
    raise AssertionError("quarantine decorator must prevent this body from executing")


@pytest.mark.e2e
@pytest.mark.live_write
@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_schedule_workouts_inline_upload():
    """Test schedule_workouts with inline workout_data — uploads and schedules in one call

    WARNING: This test requires:
    - Valid GARMIN_EMAIL and GARMIN_PASSWORD in .env file
    - Active internet connection

    Uploads a minimal workout and schedules it for a far-future date.
    The created workout is deleted at the end of the test.
    """
    server_params = _build_server_params()
    workout_name = f"e2e inline schedule contract {uuid.uuid4().hex}"
    # Keep the contract within Garmin's normal calendar horizon. Very distant
    # dates can be indexed before the unschedule backend is ready, which makes
    # cleanup behavior unrepresentative of real use.
    calendar_date = (datetime.now().date() + timedelta(days=180)).isoformat()

    inline_workout = {
        "workoutName": workout_name,
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": [{
                "type": "ExecutableStepDTO",
                "stepOrder": 1,
                "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                "endConditionValue": 600.0,
                "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
            }]
        }]
    }

    created_ids = set()
    scheduled_ids = set()
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            try:
                async with asyncio.timeout(35):
                    schedules = [{
                        "workout_data": inline_workout,
                        "calendar_date": calendar_date,
                    }]
                    result = await session.call_tool(
                        "schedule_workouts",
                        arguments={"schedules": schedules},
                    )

                assert result is not None
                assert len(result.content) > 0
                result_data = json.loads(result.content[0].text)
                assert result_data["total"] == 1
                created_ids.update(
                    item["workout_id"]
                    for item in result_data["results"]
                    if item.get("workout_id") is not None
                )
                scheduled_ids.update(
                    item["scheduled_workout_id"]
                    for item in result_data["results"]
                    if item.get("scheduled_workout_id") is not None
                )
                print("\nschedule_workouts inline upload result:")
                print(json.dumps(result_data, indent=2))
            finally:
                async with asyncio.timeout(20):
                    cleanup_failures = await _cleanup_generated_workouts(
                        session,
                        {workout_name},
                        calendar_date=calendar_date,
                        known_workout_ids=created_ids,
                        known_scheduled_ids=scheduled_ids,
                    )
                assert not cleanup_failures, "; ".join(cleanup_failures)


@pytest.mark.e2e
@pytest.mark.live_read
@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_schedule_workouts_missing_required_fields():
    """Test schedule_workouts returns a structured failure when required fields are missing

    WARNING: This test requires:
    - Valid GARMIN_EMAIL and GARMIN_PASSWORD in .env file
    - Active internet connection

    Verifies that omitting both workout_id and workout_data yields a well-formed
    failure entry rather than an unhandled error.
    """
    import json

    server_params = _build_server_params()

    try:
        async with asyncio.timeout(50):
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    # Neither workout_id nor workout_data — should fail gracefully
                    result = await session.call_tool(
                        "schedule_workouts",
                        arguments={"schedules": [{"calendar_date": "2099-01-01"}]}
                    )

                    assert result is not None
                    assert len(result.content) > 0

                    result_data = json.loads(result.content[0].text)
                    assert "total" in result_data
                    assert "succeeded" in result_data
                    assert "failed" in result_data
                    assert "results" in result_data
                    assert result_data["total"] == 1
                    assert result_data["failed"] == 1
                    assert result_data["results"][0]["status"] == "failed"

                    print(f"\nschedule_workouts missing fields result:")
                    print(json.dumps(result_data, indent=2))
    except asyncio.TimeoutError:
        pytest.fail("schedule_workouts missing fields test timed out - check your Garmin credentials and network")
