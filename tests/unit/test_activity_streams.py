from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from garmin_mcp import activity_streams
from garmin_mcp.activity_streams import (
    ActivityStreamsError,
    ZoneModel,
    analyze_decoupling_timeline,
    build_timeline,
    decode_fit_timeline,
    polarization_audit_data,
    resample_timeline,
    reslice_timeline,
    stream_response,
)


BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _record(second, **values):
    return {"timestamp": BASE + timedelta(seconds=second), **values}


def _hr_model(**extra):
    return {
        "sport": "cycling",
        "metric": "hr",
        "zones": [
            {"name": "easy", "lower_inclusive": 0, "upper_exclusive": 150},
            {"name": "tempo", "lower_inclusive": 150, "upper_exclusive": 170},
            {"name": "hard", "lower_inclusive": 170, "upper_exclusive": None},
        ],
        **extra,
    }


def test_timeline_excludes_pauses_and_accounts_for_long_gaps():
    records = [
        _record(0, hr=120),
        _record(1, hr=121),
        _record(2, hr=122),
        _record(12, hr=123),
        _record(13, hr=124),
        _record(20, hr=125),
    ]
    events = [
        {"timestamp": BASE + timedelta(seconds=3), "event": "timer", "event_type": "stop_all"},
        {"timestamp": BASE + timedelta(seconds=12), "event": "timer", "event_type": "start"},
    ]

    timeline = build_timeline(
        records,
        events,
        {"start_time": BASE, "total_elapsed_time": 21, "sport": "cycling"},
    )

    assert timeline.total_elapsed_s == 21
    assert timeline.total_active_s == 12
    assert timeline.pause_intervals == [(3.0, 12.0)]
    assert sum(segment.duration_s for segment in timeline.value_segments) == 6
    assert sum(segment.duration_s for segment in timeline.missing_segments) == 6
    assert timeline.missing_segments[0].reason == "record_gap"


def test_fit_decoder_uses_timer_events_and_enhanced_fields(monkeypatch):
    class Message:
        def __init__(self, name, values):
            self.name = name
            self.values = values

        def get_value(self, name):
            return self.values.get(name)

    messages = [
        Message(
            "session",
            {"sport": "cycling", "start_time": BASE, "total_elapsed_time": 13},
        ),
        Message("record", {"timestamp": BASE, "heart_rate": 120, "enhanced_speed": 8.5}),
        Message("record", {"timestamp": BASE + timedelta(seconds=1), "heart_rate": 121}),
        Message(
            "event",
            {"timestamp": BASE + timedelta(seconds=2), "event": "timer", "event_type": "stop_all"},
        ),
        Message(
            "event",
            {"timestamp": BASE + timedelta(seconds=12), "event": "timer", "event_type": "start"},
        ),
        Message("record", {"timestamp": BASE + timedelta(seconds=12), "heart_rate": 122}),
    ]

    class FitFile:
        def __init__(self, _buffer):
            pass

        def get_messages(self):
            return iter(messages)

    monkeypatch.setattr(activity_streams.fitparse, "FitFile", FitFile)

    timeline = decode_fit_timeline(b"not-a-real-fit")

    assert timeline.pause_intervals == [(2.0, 12.0)]
    assert timeline.total_active_s == 3
    assert timeline.samples[0]["values"]["speed"] == 8.5


def test_resampling_uses_weighted_means_and_tail_altitude():
    timeline = build_timeline(
        [
            _record(0, power=100, altitude=10),
            _record(2, power=200, altitude=20),
            _record(4, power=300, altitude=30),
        ],
        session={"start_time": BASE, "total_elapsed_time": 5},
    )

    points = resample_timeline(timeline, ["power", "altitude"], "5s")

    assert points == [
        {
            "timestamp": "2024-01-01T00:00:00Z",
            "offset_s": 0,
            "duration_s": 5,
            "covered_s": 5,
            "power": 180,
            "altitude": 30,
        }
    ]


def test_raw_pagination_can_reconstruct_every_record_and_cursor_is_bound():
    timeline = build_timeline(
        [_record(i, hr=120 + i) for i in range(5)],
        session={"start_time": BASE, "total_elapsed_time": 5},
    )

    first = stream_response(42, timeline, ["hr"], "raw", page_size=2)
    second = stream_response(
        42,
        timeline,
        ["hr"],
        "raw",
        cursor=first["pagination"]["next_cursor"],
        page_size=2,
    )
    third = stream_response(
        42,
        timeline,
        ["hr"],
        "raw",
        cursor=second["pagination"]["next_cursor"],
        page_size=2,
    )

    assert [row["hr"] for row in first["data"] + second["data"] + third["data"]] == [
        120,
        121,
        122,
        123,
        124,
    ]
    assert third["pagination"]["has_more"] is False
    assert third["pagination"]["next_cursor"] is None

    with pytest.raises(ActivityStreamsError, match="different stream request"):
        stream_response(
            43,
            timeline,
            ["hr"],
            "raw",
            cursor=first["pagination"]["next_cursor"],
            page_size=2,
        )


def test_raw_active_excludes_pause_records_but_elapsed_keeps_them():
    timeline = build_timeline(
        [
            _record(0, hr=120),
            _record(1, hr=121),
            _record(2, hr=122),  # timer-stop boundary
            _record(3, hr=123),  # emitted while paused
            _record(5, hr=125),  # timer-start boundary
            _record(6, hr=126),
        ],
        timer_events=[
            {
                "timestamp": BASE + timedelta(seconds=2),
                "event": "timer",
                "event_type": "stop_all",
            },
            {
                "timestamp": BASE + timedelta(seconds=5),
                "event": "timer",
                "event_type": "start",
            },
        ],
        session={"start_time": BASE, "total_elapsed_time": 7},
    )

    active = resample_timeline(timeline, ["hr"], "raw", "active")
    elapsed = resample_timeline(timeline, ["hr"], "raw", "elapsed")

    assert [row["hr"] for row in active] == [120, 121, 125, 126]
    assert [row["hr"] for row in elapsed] == [120, 121, 122, 123, 125, 126]
    assert all(row["duration_s"] > 0 for row in active)
    assert [row["duration_s"] for row in elapsed[2:4]] == [0, 0]


def test_raw_active_and_elapsed_match_when_there_are_no_pauses():
    timeline = build_timeline(
        [_record(second, hr=120 + second) for second in range(5)],
        session={"start_time": BASE, "total_elapsed_time": 5},
    )

    active = resample_timeline(timeline, ["hr"], "raw", "active")
    elapsed = resample_timeline(timeline, ["hr"], "raw", "elapsed")

    assert active == elapsed


def test_decoupling_has_positive_sign_when_efficiency_declines():
    records = []
    for second in range(3600):
        records.append(
            _record(
                second,
                power=200,
                hr=140 if second < 1800 else 154,
                temperature=20 if second < 1800 else 23,
            )
        )
    timeline = build_timeline(
        records,
        session={"start_time": BASE, "total_elapsed_time": 3600, "sport": "cycling"},
    )

    result = analyze_decoupling_timeline(timeline)

    assert result["status"] == "ok"
    assert result["metric"] == "pw_hr"
    assert result["applicable"] is True
    assert result["window"]["duration_s"] == 3600
    assert result["decoupling_pct"] == pytest.approx(9.09, abs=0.01)
    assert result["temperature_delta_c"] == 3
    assert "Positive decoupling" in result["note"]


def test_explicit_short_decoupling_window_reports_metric_but_not_applicable():
    timeline = build_timeline(
        [
            _record(second, power=200, hr=140 if second < 900 else 150)
            for second in range(1800)
        ],
        session={"start_time": BASE, "total_elapsed_time": 1800},
    )

    result = analyze_decoupling_timeline(
        timeline, metric="pw_hr", start_offset_s=0, end_offset_s=1800
    )

    assert result["status"] == "insufficient_quality"
    assert result["applicable"] is False
    assert result["decoupling_pct"] > 0
    assert any("shorter" in warning for warning in result["warnings"])


def test_decoupling_does_not_treat_one_sample_as_full_bin_coverage():
    timeline = build_timeline(
        [_record(second, power=200, hr=140) for second in range(0, 3600, 30)],
        session={"start_time": BASE, "total_elapsed_time": 3600},
    )

    result = analyze_decoupling_timeline(timeline, metric="pw_hr")

    assert result["status"] == "insufficient_quality"
    assert result["applicable"] is False
    assert "No >=40 minute window" in result["reason"]


@pytest.mark.parametrize(
    "zones, message",
    [
        (
            [
                {"name": "z1", "lower_inclusive": 0, "upper_exclusive": 150},
                {"name": "z2", "lower_inclusive": 149, "upper_exclusive": None},
            ],
            "overlap",
        ),
        (
            [
                {"name": "z1", "lower_inclusive": 0, "upper_exclusive": 150},
                {"name": "z2", "lower_inclusive": 151, "upper_exclusive": None},
            ],
            "gap",
        ),
    ],
)
def test_zone_model_rejects_overlap_and_unexplained_gap(zones, message):
    with pytest.raises(ValidationError, match=message):
        ZoneModel.model_validate({"sport": "cycling", "metric": "hr", "zones": zones})


def test_reslice_zone_boundaries_and_missing_time_close_to_active_duration():
    records = []
    values = [140, 149, 150, 169, 170, None, 140, 150, 170, 170]
    for second, value in enumerate(values):
        row = _record(second)
        if value is not None:
            row["hr"] = value
        records.append(row)
    timeline = build_timeline(
        records, session={"start_time": BASE, "total_elapsed_time": 10}
    )

    result = reslice_timeline(timeline, _hr_model(), include_segments=True)

    totals = {zone["name"]: zone["seconds"] for zone in result["zones"]}
    assert totals == {"easy": 3.0, "tempo": 3.0, "hard": 3.0}
    assert result["missing_s"] == 1
    assert result["classified_s"] + result["missing_s"] == result["total_active_s"]
    assert any(segment["zone"] == "missing" for segment in result["segments"])


def test_reslice_accepts_physiology_store_row_shape():
    timeline = build_timeline(
        [_record(0, hr=140)],
        session={"start_time": BASE, "total_elapsed_time": 1},
    )
    stored_row = {
        "id": "model-1",
        "athlete_id": "athlete-1",
        "sport": "cycling",
        "metric": "heart_rate",
        "zones_json": [
            {"name": "easy", "lower_inclusive": 1, "upper_exclusive": 150},
            {"name": "hard", "lower_inclusive": 150, "upper_exclusive": None},
        ],
        "source": "field",
        "version": "2",
        "observed_at": "2024-01-01T00:00:00Z",
        "active": True,
    }

    result = reslice_timeline(timeline, stored_row)

    assert result["metric"] == "hr"
    assert result["zones"][0]["seconds"] == 1
    assert result["model"]["timestamp"] == "2024-01-01T00:00:00Z"


def test_polarization_reports_time_and_session_distributions_separately(monkeypatch):
    class Client:
        def get_activities_by_date(self, start, end, sport):
            assert (start, end, sport) == ("2024-01-01", "2024-01-07", "cycling")
            return [{"activityId": 1}, {"activityId": 2}]

    low = build_timeline(
        [_record(i, hr=130) for i in range(60)],
        session={"start_time": BASE, "total_elapsed_time": 60},
    )
    mixed = build_timeline(
        [_record(i, hr=160 if i < 40 else 180) for i in range(60)],
        session={"start_time": BASE, "total_elapsed_time": 60},
    )
    timelines = {1: low, 2: mixed}
    monkeypatch.setattr(activity_streams, "garmin_client", Client())
    monkeypatch.setattr(
        activity_streams, "_download_activity_timeline", lambda activity_id: timelines[activity_id]
    )

    result = polarization_audit_data(
        "2024-01-01", "2024-01-07", _hr_model(vt1=150, vt2=170)
    )

    assert result["time_distribution"]["seconds"] == {
        "low": 60.0,
        "black_hole": 40.0,
        "high": 20.0,
    }
    assert result["session_distribution"]["count"] == {
        "low": 1,
        "black_hole": 1,
        "high": 0,
    }
    assert "black_hole_time_above_10pct" in result["alerts"]


def test_analysis_sink_is_deterministic_and_failure_is_non_fatal(monkeypatch):
    timeline = build_timeline(
        [_record(second, power=200, hr=140) for second in range(2400)],
        session={"start_time": BASE, "total_elapsed_time": 2400, "sport": "cycling"},
    )
    monkeypatch.setattr(
        activity_streams, "_download_activity_timeline", lambda _activity_id: timeline
    )
    payloads = []

    def sink(payload):
        payloads.append(payload)
        return {"id": "analysis-1", "created": True}

    monkeypatch.setattr(activity_streams, "_analysis_sink", sink)
    first = activity_streams.analyze_activity_decoupling(42, metric="pw_hr")
    second = activity_streams.analyze_activity_decoupling(42, metric="pw_hr")

    assert first["status"] == "ok"
    assert first["storage"]["id"] == "analysis-1"
    assert first["storage"]["input_hash"] == second["storage"]["input_hash"]
    assert payloads[0]["analysis_type"] == "decoupling"
    assert payloads[0]["result_hash"] == payloads[1]["result_hash"]
    assert "storage" not in payloads[0]["result"]

    def failing_sink(_payload):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(activity_streams, "_analysis_sink", failing_sink)
    failed_storage = activity_streams.analyze_activity_decoupling(42, metric="pw_hr")
    assert failed_storage["status"] == "ok"
    assert failed_storage["storage"]["status"] == "error"
    assert any("persistence failed" in warning for warning in failed_storage["warnings"])
