"""Interpret the user's local times for Garmin writes.

garminconnect turns naive timestamps into instants using the server host's
time zone. In Docker or a remote HTTP deployment the host usually runs in UTC
while the user doesn't, so a date-only weigh-in can land on the previous day.
GARMIN_TZ (an IANA name such as "America/Los_Angeles") says which zone the
user's local times are in; without it, write timestamps default to UTC.
"""

import datetime
import os
import time
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Noon reduces the chance that a date-only entry falls on an adjacent day
# when the user's time zone is unknown, but cannot guarantee it worldwide.
DATE_ONLY_TIME = datetime.time(12, 0)


def user_timezone() -> Optional[datetime.tzinfo]:
    """The zone named by GARMIN_TZ, or None when it is not configured."""
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


def resolve_local_time(value: Optional[str]) -> datetime.datetime:
    """Parse a user date or timestamp into an aware datetime.

    - "YYYY-MM-DD": that day at DATE_ONLY_TIME in GARMIN_TZ or UTC.
    - "YYYY-MM-DDTHH:MM[:SS]": wall-clock time in GARMIN_TZ or UTC.
    - A timestamp with an offset (or "Z") keeps its offset.
    - None or an empty string: now in GARMIN_TZ or UTC.
    """
    tz = user_timezone() or datetime.timezone.utc
    if value is None or not str(value).strip():
        return datetime.datetime.now(tz)

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
        dt = dt.replace(tzinfo=tz)
    return dt


def to_host_naive(dt: datetime.datetime) -> datetime.datetime:
    """The same instant as naive host-local time.

    garminconnect's FIT encoders read naive datetimes with time.mktime (the
    host's zone) and drop any tzinfo, so hand them host-local wall-clock time.
    """
    host_time = dt.astimezone().replace(tzinfo=None, microsecond=0)
    # The library reparses the ISO string, which loses datetime.fold. During
    # a host DST rollback, mktime may choose the other occurrence of the same
    # wall time. Refuse the write if it would encode a different instant.
    reparsed = datetime.datetime.fromisoformat(host_time.isoformat(timespec="seconds"))
    if time.mktime(reparsed.timetuple()) != dt.replace(microsecond=0).timestamp():
        raise ValueError(
            "Cannot encode this timestamp unambiguously in the server's time zone; "
            "run the server with TZ=UTC and retry"
        )
    return host_time


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
