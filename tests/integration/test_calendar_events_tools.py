"""
Integration tests for the calendar_events module MCP tools.

Covers get_calendar_events using FastMCP integration with a mocked Garmin
client. No real Garmin account or network access is used.
"""
import json

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import calendar_events


@pytest.fixture
def app_with_calendar_events(mock_garmin_client):
    """Create a FastMCP app with the calendar event tools registered."""
    calendar_events.configure(mock_garmin_client)
    app = FastMCP("Test Calendar Events")
    app = calendar_events.register_tools(app)
    return app


def _result_text(result):
    """Extract the text payload from a FastMCP call_tool result."""
    return result[0][0].text


def _race(date, title="Some Marathon", uuid="uuid-1", **overrides):
    """Build a raw calendar event item as Garmin returns it."""
    item = {
        "itemType": "event",
        "activityTypeId": 1,
        "title": title,
        "date": date,
        "url": "https://www.ahotu.com/event/some-marathon",
        "isRace": True,
        "shareableEventUuid": uuid,
        "completionTarget": {"value": 42195.0, "unit": "meter", "unitType": "distance"},
        "shareableEvent": True,
        "subscribed": True,
    }
    item.update(overrides)
    return item


def _month(*items):
    return {"calendarItems": list(items)}


# --- get_calendar_events --------------------------------------------------

@pytest.mark.asyncio
async def test_get_calendar_events_curates_fields(
    app_with_calendar_events, mock_garmin_client
):
    """A race is curated into a compact shape with its distance and start time."""
    mock_garmin_client.get_scheduled_workouts.return_value = _month(
        _race(
            "2026-10-17",
            title="Gyeongju Marathon",
            eventTimeLocal={"startTimeHhMm": "08:00", "timeZoneId": "Asia/Seoul"},
            primaryEvent=True,
            location="KR",
        )
    )

    result = await app_with_calendar_events.call_tool(
        "get_calendar_events", {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    )

    data = json.loads(_result_text(result))
    assert data["count"] == 1
    event = data["events"][0]
    assert event["title"] == "Gyeongju Marathon"
    assert event["date"] == "2026-10-17"
    assert event["is_race"] is True
    assert event["primary_event"] is True
    assert event["distance_meters"] == 42195.0
    assert event["start_time_local"] == "08:00"
    assert event["time_zone"] == "Asia/Seoul"
    assert event["location"] == "KR"
    assert event["event_uuid"] == "uuid-1"


@pytest.mark.asyncio
async def test_get_calendar_events_requests_the_month_one_indexed(
    app_with_calendar_events, mock_garmin_client
):
    """The library takes a 1-indexed month and handles Garmin's 0-indexing itself."""
    mock_garmin_client.get_scheduled_workouts.return_value = _month()

    await app_with_calendar_events.call_tool(
        "get_calendar_events", {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    )

    mock_garmin_client.get_scheduled_workouts.assert_called_once_with(2026, 10)


@pytest.mark.asyncio
async def test_get_calendar_events_walks_months_across_a_year_boundary(
    app_with_calendar_events, mock_garmin_client
):
    """A range spanning December to January requests both months, in order."""
    mock_garmin_client.get_scheduled_workouts.return_value = _month()

    await app_with_calendar_events.call_tool(
        "get_calendar_events", {"start_date": "2026-12-20", "end_date": "2027-01-10"}
    )

    requested = [
        call.args for call in mock_garmin_client.get_scheduled_workouts.call_args_list
    ]
    assert requested == [(2026, 12), (2027, 1)]


@pytest.mark.asyncio
async def test_get_calendar_events_clips_to_the_requested_range(
    app_with_calendar_events, mock_garmin_client
):
    """Events outside the range are dropped even though Garmin returns whole months."""
    mock_garmin_client.get_scheduled_workouts.return_value = _month(
        _race("2026-10-03", title="Too Early", uuid="uuid-early"),
        _race("2026-10-17", title="In Range", uuid="uuid-mid"),
        _race("2026-10-31", title="Too Late", uuid="uuid-late"),
    )

    result = await app_with_calendar_events.call_tool(
        "get_calendar_events", {"start_date": "2026-10-10", "end_date": "2026-10-20"}
    )

    data = json.loads(_result_text(result))
    assert [event["title"] for event in data["events"]] == ["In Range"]


@pytest.mark.asyncio
async def test_get_calendar_events_ignores_non_event_items(
    app_with_calendar_events, mock_garmin_client
):
    """Workouts, weigh-ins and training plans share the feed and are skipped."""
    mock_garmin_client.get_scheduled_workouts.return_value = _month(
        _race("2026-10-17", title="Gyeongju Marathon"),
        {"itemType": "fbtAdaptiveWorkout", "title": "Base", "date": "2026-10-15"},
        {"itemType": "weight", "date": "2026-10-16", "weight": 70100.0},
        {
            "itemType": "trainingPlan",
            "title": "Gyeongju Marathon Plan",
            "date": "2026-10-17",
        },
    )

    result = await app_with_calendar_events.call_tool(
        "get_calendar_events", {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    )

    data = json.loads(_result_text(result))
    assert data["count"] == 1
    assert data["events"][0]["title"] == "Gyeongju Marathon"


@pytest.mark.asyncio
async def test_get_calendar_events_deduplicates_across_months(
    app_with_calendar_events, mock_garmin_client
):
    """An event repeated in two monthly responses is reported once."""
    mock_garmin_client.get_scheduled_workouts.return_value = _month(
        _race("2026-11-15", title="MBN Seoul Marathon", uuid="uuid-seoul")
    )

    result = await app_with_calendar_events.call_tool(
        "get_calendar_events", {"start_date": "2026-11-01", "end_date": "2026-12-31"}
    )

    data = json.loads(_result_text(result))
    assert mock_garmin_client.get_scheduled_workouts.call_count == 2
    assert data["count"] == 1


@pytest.mark.asyncio
async def test_get_calendar_events_sorts_by_date(
    app_with_calendar_events, mock_garmin_client
):
    """Events are returned oldest first regardless of Garmin's ordering."""
    mock_garmin_client.get_scheduled_workouts.return_value = _month(
        _race("2026-10-17", title="Later", uuid="uuid-later"),
        _race("2026-10-03", title="Earlier", uuid="uuid-earlier"),
    )

    result = await app_with_calendar_events.call_tool(
        "get_calendar_events", {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    )

    data = json.loads(_result_text(result))
    assert [event["date"] for event in data["events"]] == ["2026-10-03", "2026-10-17"]


@pytest.mark.asyncio
async def test_get_calendar_events_handles_missing_completion_target(
    app_with_calendar_events, mock_garmin_client
):
    """An event without a distance goal reports None rather than failing."""
    mock_garmin_client.get_scheduled_workouts.return_value = _month(
        _race("2026-10-17", completionTarget=None)
    )

    result = await app_with_calendar_events.call_tool(
        "get_calendar_events", {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    )

    data = json.loads(_result_text(result))
    assert data["events"][0]["distance_meters"] is None


@pytest.mark.asyncio
async def test_get_calendar_events_ignores_non_distance_target(
    app_with_calendar_events, mock_garmin_client
):
    """A duration goal is not reported as a distance."""
    mock_garmin_client.get_scheduled_workouts.return_value = _month(
        _race(
            "2026-10-17",
            completionTarget={"value": 3600.0, "unit": "second", "unitType": "time"},
        )
    )

    result = await app_with_calendar_events.call_tool(
        "get_calendar_events", {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    )

    data = json.loads(_result_text(result))
    assert data["events"][0]["distance_meters"] is None


@pytest.mark.asyncio
async def test_get_calendar_events_normalises_blank_location(
    app_with_calendar_events, mock_garmin_client
):
    """Garmin sends an empty location string for many events; report None."""
    mock_garmin_client.get_scheduled_workouts.return_value = _month(_race("2026-10-17", location=""))

    result = await app_with_calendar_events.call_tool(
        "get_calendar_events", {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    )

    data = json.loads(_result_text(result))
    assert data["events"][0]["location"] is None


@pytest.mark.asyncio
async def test_get_calendar_events_empty(app_with_calendar_events, mock_garmin_client):
    """A month with no events returns count 0 and an empty list."""
    mock_garmin_client.get_scheduled_workouts.return_value = _month()

    result = await app_with_calendar_events.call_tool(
        "get_calendar_events", {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    )

    data = json.loads(_result_text(result))
    assert data["count"] == 0
    assert data["events"] == []


@pytest.mark.asyncio
async def test_get_calendar_events_handles_null_payload(
    app_with_calendar_events, mock_garmin_client
):
    """A null month response is treated as no events, not an error."""
    mock_garmin_client.get_scheduled_workouts.return_value = None

    result = await app_with_calendar_events.call_tool(
        "get_calendar_events", {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    )

    data = json.loads(_result_text(result))
    assert data["count"] == 0


@pytest.mark.asyncio
async def test_get_calendar_events_rejects_reversed_range(
    app_with_calendar_events, mock_garmin_client
):
    """An end date before the start date is refused without calling Garmin."""
    result = await app_with_calendar_events.call_tool(
        "get_calendar_events", {"start_date": "2026-10-31", "end_date": "2026-10-01"}
    )

    assert "start_date must be on or before end_date" in _result_text(result)
    mock_garmin_client.get_scheduled_workouts.assert_not_called()


@pytest.mark.asyncio
async def test_get_calendar_events_rejects_bad_date_format(
    app_with_calendar_events, mock_garmin_client
):
    """A malformed date is reported clearly without calling Garmin."""
    result = await app_with_calendar_events.call_tool(
        "get_calendar_events", {"start_date": "10/01/2026", "end_date": "2026-10-31"}
    )

    assert "Invalid start_date" in _result_text(result)
    mock_garmin_client.get_scheduled_workouts.assert_not_called()


@pytest.mark.asyncio
async def test_get_calendar_events_error_is_caught(
    app_with_calendar_events, mock_garmin_client
):
    """A client error is surfaced as a clean message, not a traceback."""
    mock_garmin_client.get_scheduled_workouts.side_effect = Exception("boom")

    result = await app_with_calendar_events.call_tool(
        "get_calendar_events", {"start_date": "2026-10-01", "end_date": "2026-10-31"}
    )

    assert "Error retrieving calendar events" in _result_text(result)
