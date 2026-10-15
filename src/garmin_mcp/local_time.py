"""Interpret the user's local times for Garmin writes.

garminconnect turns naive timestamps into instants using the server host's
time zone. In Docker or a remote HTTP deployment the host usually runs in UTC
while the user doesn't, so a date-only weigh-in can land on the previous day.
GARMIN_TZ (an IANA name such as "America/Los_Angeles") says which zone the
user's local times are in; without it the host's zone is used, as before.
"""

import datetime
import os
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# A date given without a time is recorded at noon: far enough from midnight
# that the calendar day survives any zone mismatch up to +/-12 h.
DATE_ONLY_TIME = datetime.time(12, 0)


def user_timezone() -> Optional[datetime.tzinfo]:
    """The zone named by GARMIN_TZ, or None to use the host's zone."""
    name = (os.getenv("GARMIN_TZ") or "").strip()
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(
            f"GARMIN_TZ={name!r} is not a valid IANA time zone "
            "(e.g. 'America/Los_Angeles')"
        ) from e


def resolve_local_time(value: Optional[str]) -> Optional[datetime.datetime]:
    """Parse a user date or timestamp into an aware datetime.

    - "YYYY-MM-DD": that day at DATE_ONLY_TIME, local.
    - "YYYY-MM-DDTHH:MM[:SS]": local wall-clock time.
    - A timestamp with an offset (or "Z") keeps its offset.
    - None: now, if GARMIN_TZ is set; otherwise None (let garminconnect
      use the host's clock, as before).

    "Local" is GARMIN_TZ when set, else the host's zone.
    """
    tz = user_timezone()
    if value is None or not str(value).strip():
        return datetime.datetime.now(tz) if tz else None

    text = str(value).strip().replace("Z", "+00:00")
    try:
        if len(text) == 10:
            dt = datetime.datetime.combine(
                datetime.date.fromisoformat(text), DATE_ONLY_TIME
            )
        else:
            dt = datetime.datetime.fromisoformat(text)
    except ValueError as e:
        raise ValueError(
            f"Invalid date/time {value!r}: use YYYY-MM-DD or "
            "YYYY-MM-DDTHH:MM[:SS] (optionally with a UTC offset)"
        ) from e

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz) if tz else dt.astimezone()
    return dt


def to_host_naive(dt: datetime.datetime) -> datetime.datetime:
    """The same instant as naive host-local time.

    garminconnect's FIT encoders read naive datetimes with time.mktime (the
    host's zone) and drop any tzinfo, so hand them host-local wall-clock time.
    """
    return dt.astimezone().replace(tzinfo=None)


def format_epoch_ms(epoch_ms: float) -> str:
    """ISO 8601 with an explicit offset, in GARMIN_TZ or the host's zone."""
    try:
        tz = user_timezone()
    except ValueError:
        tz = None
    dt = datetime.datetime.fromtimestamp(epoch_ms / 1000.0, tz)
    if tz is None:
        dt = dt.astimezone()
    return dt.isoformat(timespec="seconds")
