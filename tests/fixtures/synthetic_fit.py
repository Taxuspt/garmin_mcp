"""Small, synthetic FIT file builder. Contains no account or location data."""

import struct
from datetime import datetime, timezone

from fitparse.records import Crc


def activity_fit(records=None, duration=10, sessions=1, events=()):
    """Record tuples: elapsed seconds, HR, watts. Include a single cycling session."""
    epoch = datetime(1989, 12, 31, tzinfo=timezone.utc)
    start = int((datetime(2024, 1, 1, tzinfo=timezone.utc) - epoch).total_seconds())
    records = (
        records
        if records is not None
        else [(0, 100, 100), (2, 120, 200), (4, 140, 300)]
    )
    body = bytearray()
    # Definition local 0: record (20), timestamp (253), heart_rate (3), power (7).
    body.extend(struct.pack("<BBBHB", 0x40, 0, 0, 20, 3))
    body.extend(bytes([253, 4, 0x86, 3, 1, 2, 7, 2, 0x84]))
    for second, hr, power in records:
        body.extend(struct.pack("<BIBH", 0, start + second, hr, power))
    # Definition local 2: timer event (21), timestamp, event, event_type.
    if events:
        body.extend(struct.pack("<BBBHB", 0x42, 0, 0, 21, 3))
        body.extend(bytes([253, 4, 0x86, 0, 1, 0, 1, 1, 0]))
        for second, action in events:
            body.extend(struct.pack("<BIBB", 2, start + second, 0, action))
    # Definition local 1: session (18), start_time (2), sport (5), total_elapsed_time (7).
    body.extend(struct.pack("<BBBHB", 0x41, 0, 0, 18, 3))
    body.extend(bytes([2, 4, 0x86, 5, 1, 0, 7, 4, 0x86]))
    for _ in range(sessions):
        body.extend(struct.pack("<BIBI", 1, start, 2, int(duration * 1000)))
    header = struct.pack("<BBHI4s", 12, 0x20, 2180, len(body), b".FIT")
    payload = header + bytes(body)
    return payload + struct.pack("<H", Crc.calculate(payload))
