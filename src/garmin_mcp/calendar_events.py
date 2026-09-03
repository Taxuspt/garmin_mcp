"""
Calendar event (race) functions for Garmin Connect MCP Server
"""
import json
import re
from datetime import date
from typing import Any, Dict, List, Optional

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The garmin_client will be set by the main file
garmin_client = None

# Garmin's calendar-service groups every calendar entry under an itemType.
# Races the user added or subscribed to arrive as "event"; workouts, weigh-ins
# and badges share the same feed and are ignored here.
EVENT_ITEM_TYPE = "event"


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


def _validate_date(value: str, field: str = "date") -> date:
    if not _DATE_RE.match(value):
        raise ValueError(f"Invalid {field} '{value}': expected YYYY-MM-DD")
    return date.fromisoformat(value)


def _month_range(start: date, end: date):
    """Yield (year, month) pairs covering start..end inclusive."""
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def _fetch_month(year: int, month: int) -> List[Dict[str, Any]]:
    """Return the raw calendar items for one month.

    Despite its name, the library's get_scheduled_workouts returns the whole
    calendar feed for a month, events included. It takes a 1-indexed month and
    converts it to the 0-indexed month Garmin's calendar-service expects.
    """
    data = garmin_client.get_scheduled_workouts(year, month)
    if not isinstance(data, dict):
        return []
    items = data.get("calendarItems")
    return items if isinstance(items, list) else []


def _as_dict(value: Any) -> Dict[str, Any]:
    """Return a dict for Garmin fields that may be null or another shape."""
    return value if isinstance(value, dict) else {}


def _target_distance_meters(item: Dict[str, Any]) -> Optional[float]:
    """Return the event's distance goal, if it is expressed as a distance."""
    target = _as_dict(item.get("completionTarget"))
    if target.get("unitType") != "distance":
        return None
    value = target.get("value")
    return value if isinstance(value, (int, float)) else None


def _clean_str(value: Any) -> Optional[str]:
    """Return a trimmed string, or None for the blanks Garmin sends."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _curate_event(item: Dict[str, Any]) -> Dict[str, Any]:
    """Curate one raw calendar event into a compact shape."""
    event_time = _as_dict(item.get("eventTimeLocal"))
    return {
        "title": item.get("title"),
        "date": item.get("date"),
        "is_race": bool(item.get("isRace")),
        "primary_event": bool(item.get("primaryEvent")),
        "subscribed": bool(item.get("subscribed")),
        "distance_meters": _target_distance_meters(item),
        "start_time_local": event_time.get("startTimeHhMm"),
        "time_zone": event_time.get("timeZoneId"),
        "location": _clean_str(item.get("location")),
        "url": item.get("url"),
        "event_uuid": item.get("shareableEventUuid"),
    }


def register_tools(app):
    """Register all calendar event tools with the MCP server app"""

    @app.tool()
    async def get_calendar_events(start_date: str, end_date: str) -> str:
        """Get races and events on the Garmin Connect calendar between two dates

        Returns calendar entries the user added or subscribed to in Garmin
        Connect, such as upcoming races. Use this to answer questions about
        which events or races are scheduled.

        These entries are not returned by get_scheduled_workouts, which covers
        only workouts, nor by get_goals. This is the only tool that exposes
        them.

        Each event reports its target distance in meters when Garmin stores one,
        the local start time when the organiser published it, and two flags:
        is_race marks the entry as a race rather than a general event, and
        primary_event marks the goal race that an active training plan targets.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            start = _validate_date(start_date, "start_date")
            end = _validate_date(end_date, "end_date")
            if start > end:
                return "start_date must be on or before end_date."

            events: List[Dict[str, Any]] = []
            seen = set()
            for year, month in _month_range(start, end):
                for item in _fetch_month(year, month):
                    if not isinstance(item, dict):
                        continue
                    if item.get("itemType") != EVENT_ITEM_TYPE:
                        continue
                    # The first and last month of the range extend past it.
                    day = item.get("date")
                    if not isinstance(day, str) or not start_date <= day <= end_date:
                        continue
                    # Multi-month responses can repeat an event at the seams.
                    key = (item.get("shareableEventUuid"), item.get("title"), day)
                    if key in seen:
                        continue
                    seen.add(key)
                    events.append(_curate_event(item))

            events.sort(key=lambda event: (event["date"], event["title"] or ""))

            curated = {
                "count": len(events),
                "date_range": {"start": start_date, "end": end_date},
                "events": events,
            }
            return json.dumps(curated, indent=2, ensure_ascii=False)
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Error retrieving calendar events: {str(e)}"

    return app
