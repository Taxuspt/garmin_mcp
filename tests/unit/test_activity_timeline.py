"""Pause/gap-aware stream contracts extracted and narrowed from PR #295."""

import gzip
import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from garmin_mcp import activity_timeline as streams

BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def record(second, **values):
    return {"timestamp": BASE + timedelta(seconds=second), **values}


def timeline(records, duration, events=None):
    return streams.build_timeline(
        records,
        events,
        {"start_time": BASE, "total_elapsed_time": duration, "sport": "cycling"},
    )


def test_time_weighting_and_tail_values():
    value = timeline(
        [
            record(0, power=100, altitude=10),
            record(2, power=200, altitude=20),
            record(4, power=300, altitude=30),
        ],
        5,
    )
    result = streams.stream_response(42, value, ["power", "altitude"], "5s")
    point = result["data"][0]
    assert point["power"] == 180  # (100*2 + 200*2 + 300*1) / 5
    assert point["altitude"] == 30
    assert point["covered_s"] == 5
    assert result["summary"]["coverage"]["power"]["available_s"] == 5


def test_pauses_and_long_gaps_close_against_active_duration():
    value = timeline(
        [record(i, hr=120 + i) for i in (0, 1, 2, 12, 13, 20)],
        21,
        [
            {
                "timestamp": BASE + timedelta(seconds=3),
                "event": "timer",
                "event_type": "stop_all",
            },
            {
                "timestamp": BASE + timedelta(seconds=12),
                "event": "timer",
                "event_type": "start",
            },
        ],
    )
    result = streams.stream_response(42, value, ["hr"], "5s")
    summary = result["summary"]
    assert summary["total_elapsed_s"] == 21
    assert summary["total_active_s"] == 12
    assert summary["sampled_active_s"] == 6
    assert summary["missing_active_s"] == 6
    assert (
        summary["sampled_active_s"] + summary["missing_active_s"]
        == summary["total_active_s"]
    )
    assert summary["pause_count"] == 1


def test_paused_records_do_not_leak_into_active_bins():
    value = timeline(
        [record(0, hr=100), record(2, hr=250), record(4, hr=250), record(7, hr=120)],
        10,
        [
            {
                "timestamp": BASE + timedelta(seconds=2),
                "event": "timer",
                "event_type": "stop",
            },
            {
                "timestamp": BASE + timedelta(seconds=7),
                "event": "timer",
                "event_type": "start",
            },
        ],
    )
    active = streams.stream_response(42, value, ["hr"], "5s", "active")
    elapsed = streams.stream_response(42, value, ["hr"], "5s", "elapsed")
    assert active["data"][0]["hr"] == 112
    assert elapsed["data"][0]["hr"] == 100
    assert elapsed["data"][0]["covered_s"] == 2


def test_missing_fields_are_not_zero_filled():
    value = timeline([record(0, hr=100), record(1, power=0), record(2, hr=120)], 3)
    result = streams.stream_response(42, value, ["hr", "power"], "5s")
    assert result["data"][0]["hr"] == 110
    assert result["data"][0]["power"] == 0
    assert result["data"][0]["field_coverage_s"] == {"hr": 2, "power": 1}
    assert result["summary"]["coverage"]["hr"]["available_s"] == 2


def test_sort_and_merge_duplicate_timestamps():
    value = timeline(
        [
            record(2, hr=120),
            record(0, hr=100),
            record(0, power=200),
            {"timestamp": "not a date", "hr": 200},
        ],
        3,
    )
    assert len(value.samples) == 2
    assert value.samples[0]["values"]["hr"] == 100
    assert value.samples[0]["values"]["power"] == 200
    assert value.dropped_records == 1


@pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), 1e300, True, "bad"])
def test_invalid_timestamps_are_dropped(timestamp):
    value = timeline([{"timestamp": timestamp}, record(0, hr=100)], 1)
    assert value.dropped_records == 1


def test_pagination_reconstructs_bins_with_stable_source_digest():
    value = timeline([record(i, hr=100 + i) for i in range(25)], 25)
    whole = streams.stream_response(42, value, ["hr"], "5s")
    rows, offset = [], 0
    while True:
        page = streams.stream_response(
            42, value, ["hr"], "5s", offset=offset, page_size=2
        )
        rows.extend(page["data"])
        assert len(page["data"]) <= 2
        if not page["pagination"]["has_more"]:
            break
        offset = page["pagination"]["next_offset"]
    assert rows == whole["data"]
    assert page["pagination"]["next_offset"] is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"resolution": "raw"},
        {"resolution": "1s"},
        {"time_basis": "moving"},
        {"page_size": 0},
        {"page_size": 501},
        {"offset": -1},
        {"offset": True},
        {"fields": ["gps"]},
        {"fields": []},
    ],
)
def test_invalid_requests_are_rejected(kwargs):
    value = timeline([record(0, hr=100)], 1)
    args = {"fields": ["hr"], **kwargs}
    with pytest.raises(streams.ActivityStreamsError):
        streams.stream_response(42, value, **args)


def test_field_aliases_are_canonicalized():
    value = timeline([record(0, heart_rate=100)], 1)
    result = streams.stream_response(42, value, ["heart_rate", "hr"], "5s")
    assert result["fields"] == ["hr"]
    assert result["data"][0]["hr"] == 100


def test_no_sample_is_carried_across_a_long_gap():
    value = timeline([record(0, hr=100), record(1, hr=100), record(20, hr=200)], 21)
    points = streams.stream_response(42, value, ["hr"], "5s")["data"]
    assert points[0]["covered_s"] == 2
    assert "hr" not in points[1]
    assert points[1]["covered_s"] == 0


@pytest.mark.parametrize("kind", ["raw", "gzip", "zip"])
def test_fit_wrappers(kind):
    data = b"synthetic bytes"
    if kind == "gzip":
        wrapped = gzip.compress(data)
    elif kind == "zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("activity.fit", data)
        wrapped = buf.getvalue()
    else:
        wrapped = data
    assert streams._extract_fit_bytes(wrapped) == data


def test_decompression_is_bounded(monkeypatch):
    monkeypatch.setattr(streams, "MAX_FIT_BYTES", 100)
    with pytest.raises(streams.ActivityStreamsError, match="limit"):
        streams._extract_fit_bytes(gzip.compress(b"x" * 101))


def test_multiple_fit_files_are_not_silently_selected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("one.fit", b"one")
        archive.writestr("two.fit", b"two")
    with pytest.raises(streams.ActivityStreamsError, match="one FIT"):
        streams._extract_fit_bytes(buf.getvalue())


def test_pathological_activity_span_is_rejected():
    with pytest.raises(streams.ActivityStreamsError, match="duration"):
        timeline([record(0, hr=100), record(1, hr=101)], 1e9)


def test_randomized_timelines_conserve_duration_and_bound_coverage():
    import random

    rng = random.Random(295)
    for _ in range(100):
        seconds = sorted(rng.sample(range(100), 20))
        records = [
            record(t, hr=rng.choice([None, 0, 100, 150]), power=200) for t in seconds
        ]
        value = timeline(
            records,
            100,
            [
                {
                    "timestamp": BASE + timedelta(seconds=30),
                    "event": "timer",
                    "event_type": "stop",
                },
                {
                    "timestamp": BASE + timedelta(seconds=50),
                    "event": "timer",
                    "event_type": "start",
                },
            ],
        )
        for basis in ("active", "elapsed"):
            result = streams.stream_response(42, value, ["hr", "power"], "5s", basis)
            summary = result["summary"]
            assert summary["total_active_s"] == 80
            assert summary["sampled_active_s"] + summary[
                "missing_active_s"
            ] == pytest.approx(80)
            assert sum(row["covered_s"] for row in result["data"]) == pytest.approx(
                summary["sampled_active_s"]
            )
            for row in result["data"]:
                assert 0 <= row["covered_s"] <= row["duration_s"]
                for covered in row["field_coverage_s"].values():
                    assert 0 <= covered <= row["covered_s"]


def test_real_fit_crc_errors_are_not_accepted():
    from tests.fixtures.synthetic_fit import activity_fit

    payload = bytearray(activity_fit())
    payload[-1] ^= 0x01
    with pytest.raises(streams.ActivityStreamsError, match="CRC"):
        streams.decode_fit_timeline(bytes(payload))
