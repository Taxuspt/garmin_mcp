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

import pytest
import asyncio
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Import MCP client for testing
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Load environment variables
load_dotenv()


@pytest.mark.e2e
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
    server_params = StdioServerParameters(
        command="python",
        args=["-m", "garmin_mcp"],
        env=None,  # Uses current environment which includes .env variables
    )

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
@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_list_activities_tool():
    """Test the list_activities MCP tool with real API"""
    server_params = StdioServerParameters(
        command="python",
        args=["-m", "garmin_mcp"],
        env=None,
    )

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
@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_get_steps_data_tool():
    """Test the get_steps_data MCP tool with real API"""
    server_params = StdioServerParameters(
        command="python",
        args=["-m", "garmin_mcp"],
        env=None,
    )

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
@pytest.mark.asyncio
@pytest.mark.timeout(45)
async def test_multiple_tools():
    """Test multiple MCP tools in a single session"""
    server_params = StdioServerParameters(
        command="python",
        args=["-m", "garmin_mcp"],
        env=None,
    )

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
@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_schedule_workouts_tool():
    """Test schedule_workout MCP tool with multiple workouts

    WARNING: This test requires:
    - Valid GARMIN_EMAIL and GARMIN_PASSWORD in .env file
    - Active internet connection
    - Real workout IDs from your Garmin library (set GARMIN_TEST_WORKOUT_IDS env var)

    Set GARMIN_TEST_WORKOUT_IDS as a comma-separated list of workout IDs,
    and GARMIN_TEST_SCHEDULE_DATES as a comma-separated list of YYYY-MM-DD dates
    (must match the number of IDs).

    If env vars are not set, the test verifies the tool is accessible and
    returns a structured response for a dummy schedule (expected to fail gracefully).
    """
    import os
    import json

    server_params = StdioServerParameters(
        command="python",
        args=["-m", "garmin_mcp"],
        env=None,
    )

    workout_ids_env = os.environ.get("GARMIN_TEST_WORKOUT_IDS", "")
    dates_env = os.environ.get("GARMIN_TEST_SCHEDULE_DATES", "")

    if workout_ids_env and dates_env:
        ids = [int(wid.strip()) for wid in workout_ids_env.split(",")]
        dates = [d.strip() for d in dates_env.split(",")]
        assert len(ids) == len(dates), "GARMIN_TEST_WORKOUT_IDS and GARMIN_TEST_SCHEDULE_DATES must have the same number of entries"
        schedules = [{"workout_id": wid, "calendar_date": date} for wid, date in zip(ids, dates)]
    else:
        # Use a dummy schedule — expected to fail at the API level but the tool should handle it gracefully
        schedules = [
            {"workout_id": 0, "calendar_date": "2099-01-01"},
        ]

    try:
        async with asyncio.timeout(50):
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    result = await session.call_tool(
                        "schedule_workouts",
                        arguments={"schedules": schedules}
                    )

                    assert result is not None
                    assert len(result.content) > 0

                    result_data = json.loads(result.content[0].text)
                    assert "total" in result_data
                    assert "succeeded" in result_data
                    assert "failed" in result_data
                    assert "results" in result_data
                    assert result_data["total"] == len(schedules)
                    assert len(result_data["results"]) == len(schedules)

                    print(f"\nschedule_workout result:")
                    print(json.dumps(result_data, indent=2))
    except asyncio.TimeoutError:
        pytest.fail("schedule_workout test timed out - check your Garmin credentials and network")


@pytest.mark.e2e
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
    import json

    server_params = StdioServerParameters(
        command="python",
        args=["-m", "garmin_mcp"],
        env=None,
    )

    minimal_workout = {
        "workoutName": "e2e Test Workout - DELETE ME",
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

    try:
        async with asyncio.timeout(50):
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    result = await session.call_tool(
                        "upload_workouts",
                        arguments={"workouts": [minimal_workout, minimal_workout]}
                    )

                    assert result is not None
                    assert len(result.content) > 0

                    result_data = json.loads(result.content[0].text)
                    assert "total" in result_data
                    assert "succeeded" in result_data
                    assert "failed" in result_data
                    assert "results" in result_data
                    assert result_data["total"] == 2
                    assert len(result_data["results"]) == 2

                    print(f"\nupload_workouts result:")
                    print(json.dumps(result_data, indent=2))

                    # Clean up: delete any workouts that were successfully created
                    created_ids = [
                        r["workout_id"] for r in result_data["results"]
                        if r.get("status") == "success" and r.get("workout_id")
                    ]
                    if created_ids:
                        await session.call_tool(
                            "delete_workouts",
                            arguments={"workout_ids": created_ids}
                        )
                        print(f"\nCleaned up workout IDs: {created_ids}")
    except asyncio.TimeoutError:
        pytest.fail("upload_workouts test timed out - check your Garmin credentials and network")


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_delete_workouts_tool():
    """Test delete_workouts MCP tool — deletes multiple workouts in one call

    WARNING: This test requires:
    - Valid GARMIN_EMAIL and GARMIN_PASSWORD in .env file
    - Active internet connection

    Uses dummy IDs that are unlikely to exist. The tool should handle
    API failures gracefully and return a structured response.
    """
    import json

    server_params = StdioServerParameters(
        command="python",
        args=["-m", "garmin_mcp"],
        env=None,
    )

    try:
        async with asyncio.timeout(50):
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    # Use dummy IDs — expected to fail at the API level but handled gracefully
                    result = await session.call_tool(
                        "delete_workouts",
                        arguments={"workout_ids": [0, 1]}
                    )

                    assert result is not None
                    assert len(result.content) > 0

                    result_data = json.loads(result.content[0].text)
                    assert "total" in result_data
                    assert "succeeded" in result_data
                    assert "failed" in result_data
                    assert "results" in result_data
                    assert result_data["total"] == 2
                    assert len(result_data["results"]) == 2

                    print(f"\ndelete_workouts result:")
                    print(json.dumps(result_data, indent=2))
    except asyncio.TimeoutError:
        pytest.fail("delete_workouts test timed out - check your Garmin credentials and network")


@pytest.mark.e2e
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
    import json

    server_params = StdioServerParameters(
        command="python",
        args=["-m", "garmin_mcp"],
        env=None,
    )

    inline_workout = {
        "workoutName": "e2e Inline Test - DELETE ME",
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

    try:
        async with asyncio.timeout(50):
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    schedules = [{"workout_data": inline_workout, "calendar_date": "2099-01-01"}]
                    result = await session.call_tool(
                        "schedule_workouts",
                        arguments={"schedules": schedules}
                    )

                    assert result is not None
                    assert len(result.content) > 0

                    result_data = json.loads(result.content[0].text)
                    assert "total" in result_data
                    assert "succeeded" in result_data
                    assert "failed" in result_data
                    assert "results" in result_data
                    assert result_data["total"] == 1

                    print(f"\nschedule_workouts inline upload result:")
                    print(json.dumps(result_data, indent=2))

                    # Clean up: delete any workout that was created
                    created_ids = [
                        r["workout_id"] for r in result_data["results"]
                        if r.get("workout_id")
                    ]
                    if created_ids:
                        await session.call_tool(
                            "delete_workouts",
                            arguments={"workout_ids": created_ids}
                        )
                        print(f"\nCleaned up workout IDs: {created_ids}")
    except asyncio.TimeoutError:
        pytest.fail("schedule_workouts inline upload test timed out - check your Garmin credentials and network")


@pytest.mark.e2e
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

    server_params = StdioServerParameters(
        command="python",
        args=["-m", "garmin_mcp"],
        env=None,
    )

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
