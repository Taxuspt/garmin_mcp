from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from garmin_mcp import activity_streams, physiology, user_profile


@pytest.fixture
def physiology_store(tmp_path):
    client = Mock()
    physiology.configure(client, str(tmp_path))
    user_profile.configure(client)
    yield physiology.require_store(), client
    physiology.configure(data_dir="")


def _now(days_ago=0):
    return (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).replace(microsecond=0).isoformat()


def _five_zone_model():
    return {
        "sport": "cycling",
        "metric": "heart_rate",
        "name": "Dual threshold",
        "zones": [
            {"name": "Z1", "lower_inclusive": 100, "upper_exclusive": 136},
            {"name": "Z2", "lower_inclusive": 136, "upper_exclusive": 150},
            {"name": "Z3", "lower_inclusive": 150, "upper_exclusive": 170},
            {"name": "Z4", "lower_inclusive": 170, "upper_exclusive": 188},
            {"name": "Z5", "lower_inclusive": 188, "upper_exclusive": 205},
        ],
        "vt1": 150,
        "vt2": 188,
        "source": "reconciled",
        "version": "1",
        "observed_at": _now(),
    }


def _steady_timeline(first_hr, second_hr=None, *, temperature_c=21):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    second_hr = first_hr if second_hr is None else second_hr
    records = [
        {
            "timestamp": start + timedelta(seconds=offset),
            "power": 200,
            "hr": first_hr if offset < 1200 else second_hr,
            "temperature": temperature_c,
        }
        for offset in range(2400)
    ]
    return activity_streams.build_timeline(
        records,
        session={
            "sport": "cycling",
            "start_time": start,
            "total_elapsed_time": 2400,
        },
    )


def test_store_is_opt_in(monkeypatch):
    monkeypatch.delenv("GARMIN_DATA_DIR", raising=False)
    physiology.configure(data_dir="")

    assert physiology.store_status()["enabled"] is False
    with pytest.raises(RuntimeError, match="GARMIN_DATA_DIR"):
        physiology.require_store()


def test_zone_model_rejects_gaps_and_activates_one_model(physiology_store):
    bad = _five_zone_model()
    bad["zones"][1]["lower_inclusive"] = 137
    with pytest.raises(ValueError, match="gap"):
        physiology.save_zone_model(bad)

    first = physiology.save_zone_model(_five_zone_model(), active=True)
    replacement_model = _five_zone_model()
    replacement_model["name"] = "Updated"
    second = physiology.save_zone_model(replacement_model, active=True)

    active = physiology.list_saved_zone_models(
        sport="cycling", metric="heart_rate", active_only=True
    )
    assert [item["id"] for item in active] == [second["id"]]
    assert first["id"] != second["id"]
    public_keys = {
        "id",
        "sport",
        "metric",
        "name",
        "zones",
        "vt1",
        "vt2",
        "source",
        "version",
        "timestamp",
        "active",
    }
    assert set(first) == public_keys
    assert set(second) == public_keys
    assert set(active[0]) == public_keys
    assert second["metric"] == "hr"
    assert second["zones"] == replacement_model["zones"]
    assert second["timestamp"] == replacement_model["observed_at"]
    assert "zones_json" not in second
    assert "athlete_id" not in second
    assert "created_at" not in second

    activated = physiology.activate_saved_zone_model(first["id"])
    assert set(activated) == public_keys
    assert activated["active"] is True
    assert activated["metric"] == "hr"
    # Public output can be validated again without translating timestamp or metric.
    round_trip = physiology.validate_zone_model(activated)
    assert round_trip["metric"] == "heart_rate"
    assert round_trip["observed_at"] == activated["timestamp"]


def test_inspect_and_import_csv_is_preview_first_and_idempotent(physiology_store, tmp_path):
    source = tmp_path / "cpet-summary.csv"
    source.write_text(
        "elapsed_seconds,heart_rate_bpm,VO2 (mL/min),VCO2 (mL/min),VT1 HR (bpm)\n"
        "0,90,1000,850,150\n"
        "60,120,1500,1400,150\n",
        encoding="utf-8",
    )

    inspected = physiology.inspect_test_file_data(str(source))
    assert inspected["row_count"] == 2
    assert inspected["inferred_test_type"] == "cpet"
    assert inspected["inferred_column_mapping"]["vt1_bpm"] == "VT1 HR (bpm)"
    assert "sample" in inspected["privacy"].lower()

    preview = physiology.import_test_file(path=str(source), dry_run=True)
    assert preview["status"] == "preview"
    assert preview["source_file_copied"] is False
    assert physiology.list_observation_records() == []

    imported = physiology.import_test_file(path=str(source), dry_run=False)
    duplicate = physiology.import_test_file(path=str(source), dry_run=False)
    assert imported["created"] is True
    assert duplicate["created"] is False
    observations = physiology.list_observation_records(metrics=["vt1"])
    assert len(observations) == 1
    assert observations[0]["value"] == 150
    assert observations[0]["provenance_json"]["file_sha256"] == inspected["sha256"]


def test_threshold_engine_never_blindly_averages_sources_and_flags_conflict(physiology_store):
    for value, days_ago in ((187, 10), (189, 3)):
        physiology.record_observation(
            metric="lthr",
            value=value,
            source="field",
            method="30_min_time_trial",
            confidence=0.8,
            observed_at=_now(days_ago),
            provenance={"activity_id": f"ride-{value}", "ambient_temperature_c": 32 if value == 189 else 20},
        )
    physiology.record_observation(
        metric="vt2",
        value=165,
        source="lab",
        method="cpet_convergent_markers",
        confidence=0.85,
        observed_at=_now(20),
    )
    # Configured Garmin values are metadata, not independent evidence.
    physiology.record_observation(
        metric="lthr",
        value=175,
        source="garmin_metadata",
        method="configured",
        confidence=1.0,
        observed_at=_now(1),
    )

    result = physiology.estimate_threshold_candidates(sport="cycling")
    lthr = next(item for item in result["candidates"] if item["metric"] == "lthr")

    assert lthr["value"] in {187, 189}
    assert lthr["value"] != pytest.approx((187 + 189 + 165) / 3)
    assert lthr["primary_source"] == "field"
    assert lthr["status"] == "conflict"
    assert lthr["conflicts_json"][0]["construct"] == "vt2"
    assert any(item["heat_flag"] for item in lthr["evidence_json"])
    assert next(item for item in result["candidates"] if item["metric"] == "vt2")["value"] == 165

    with pytest.raises(ValueError, match="acknowledge_conflicts"):
        physiology.accept_estimate(lthr["id"])
    accepted = physiology.accept_estimate(lthr["id"], acknowledge_conflicts=True)
    assert accepted["profile_observation"]["method"] == "accepted_estimate"


def test_field_threshold_requires_two_independent_observations(physiology_store):
    physiology.record_observation(
        metric="vt1",
        value=150,
        source="field",
        method="decoupling",
        confidence=0.8,
        observed_at=_now(),
    )
    result = physiology.estimate_threshold_candidates()
    assert not any(item["metric"] == "vt1" for item in result["candidates"])
    assert next(item for item in result["not_identifiable"] if item["metric"] == "vt1")[
        "field_evidence_count"
    ] == 1


def test_profile_freshness_and_preview_first_garmin_sync(physiology_store):
    _store, client = physiology_store
    physiology.set_profile_value(metric="max_hr", value=204, observed_at=_now())
    physiology.set_profile_value(metric="resting_hr", value=54, observed_at=_now())
    physiology.set_profile_value(metric="lthr", value=188, observed_at=_now())
    saved_model = physiology.save_zone_model(_five_zone_model(), active=True)
    profile = physiology.physiology_profile(sport="cycling")
    assert profile["active_zone_models"] == [saved_model]
    assert profile["active_zone_models"][0]["metric"] == "hr"
    assert "zones_json" not in profile["active_zone_models"][0]
    current = {
        "trainingMethod": "LACTATE_THRESHOLD",
        "restingHeartRateUsed": 55,
        "lactateThresholdHeartRateUsed": 185,
        "zone1Floor": 100,
        "zone2Floor": 130,
        "zone3Floor": 150,
        "zone4Floor": 170,
        "zone5Floor": 185,
        "maxHeartRateUsed": 202,
        "restingHrAutoUpdateUsed": True,
        "sport": "CYCLING",
        "changeState": "UNCHANGED",
    }
    client.connectapi.return_value = [current]

    preview = physiology.sync_profile(
        sport="cycling", fields=["max_hr", "resting_hr", "lthr", "zones"]
    )
    assert preview["status"] == "preview"
    assert preview["write_performed"] is False
    assert preview["payload"][0]["zone5Floor"] == 188
    assert preview["payload"][0]["maxHeartRateUsed"] == 204
    client.client.request.assert_not_called()

    confirmed = {**preview["payload"][0], "changeState": "UNCHANGED"}
    client.connectapi.side_effect = [[current], [confirmed]]
    applied = physiology.sync_profile(
        sport="cycling", fields=["max_hr", "resting_hr", "lthr", "zones"], dry_run=False
    )
    assert applied["write_performed"] is True
    assert applied["confirmed"] == confirmed
    client.client.request.assert_called_once()


def test_profile_refuses_stale_values_during_sync(physiology_store):
    _store, client = physiology_store
    physiology.set_profile_value(metric="lthr", value=188, observed_at=_now(days_ago=100))
    client.connectapi.return_value = []

    with pytest.raises(ValueError, match="stale"):
        physiology.sync_profile(sport="cycling", fields=["lthr"])


def test_profile_and_zone_model_validate_confidence_and_names(physiology_store):
    with pytest.raises(ValueError, match="confidence"):
        physiology.set_profile_value(metric="max_hr", value=200, confidence=1.1)

    model = _five_zone_model()
    model["zones"][1]["name"] = model["zones"][0]["name"]
    with pytest.raises(ValueError, match="unique"):
        physiology.save_zone_model(model)


def test_profile_sync_reports_readback_mismatch_without_retry(physiology_store):
    _store, client = physiology_store
    physiology.set_profile_value(metric="max_hr", value=204, observed_at=_now())
    current = {
        "trainingMethod": "HR_MAX",
        "restingHeartRateUsed": 54,
        "lactateThresholdHeartRateUsed": 188,
        "zone1Floor": 100,
        "zone2Floor": 136,
        "zone3Floor": 150,
        "zone4Floor": 170,
        "zone5Floor": 188,
        "maxHeartRateUsed": 203,
        "restingHrAutoUpdateUsed": False,
        "sport": "CYCLING",
        "changeState": "UNCHANGED",
    }
    client.connectapi.side_effect = [[current], [current]]

    result = physiology.sync_profile(
        sport="cycling", fields=["max_hr"], dry_run=False
    )

    assert result["status"] == "failed_readback_mismatch"
    assert result["write_performed"] is True
    assert result["mismatches"]["maxHeartRateUsed"] == {
        "target": 204,
        "confirmed": 203,
    }
    client.client.request.assert_called_once()


def test_profile_sync_lost_write_response_is_indeterminate(physiology_store):
    _store, client = physiology_store
    physiology.set_profile_value(metric="max_hr", value=204, observed_at=_now())
    current = {
        "trainingMethod": "HR_MAX",
        "restingHeartRateUsed": 54,
        "lactateThresholdHeartRateUsed": 188,
        "zone1Floor": 100,
        "zone2Floor": 136,
        "zone3Floor": 150,
        "zone4Floor": 170,
        "zone5Floor": 188,
        "maxHeartRateUsed": 203,
        "restingHrAutoUpdateUsed": False,
        "sport": "CYCLING",
        "changeState": "UNCHANGED",
    }
    client.connectapi.return_value = [current]
    client.client.request.side_effect = RuntimeError("lost response")

    result = physiology.sync_profile(
        sport="cycling", fields=["max_hr"], dry_run=False
    )

    assert result["status"] == "failed_write_outcome_unknown"
    assert result["write_performed"] is True
    assert result["recovery_checklist"]
    client.client.request.assert_called_once()


def test_activity_field_evidence_is_conservative_bracketed_and_idempotent(
    physiology_store, monkeypatch
):
    store, client = physiology_store
    timelines = {
        "1": _steady_timeline(140),
        "2": _steady_timeline(145, 160),
        "3": _steady_timeline(145),
        "4": _steady_timeline(155, 170),
    }
    monkeypatch.setattr(
        physiology,
        "_download_canonical_timeline",
        lambda activity_id: timelines[str(activity_id)],
    )
    client.get_activity.side_effect = lambda activity_id: {
        "activityId": activity_id,
        "activityName": f"FTP Test {activity_id}",
        "activityTypeDTO": {"typeKey": "cycling"},
        "summaryDTO": {
            "startTimeGMT": "2026-01-01T00:00:00Z",
            "trainingEffect": 4.2,
        },
    }
    client.get_activity_weather.side_effect = lambda activity_id: (
        {"temp": 86} if activity_id == 1 else None
    )

    first = physiology.estimate_threshold_candidates(activity_ids=[1, 2, 3, 4])
    second = physiology.estimate_threshold_candidates(activity_ids=[1, 2, 3, 4])

    assert first["activity_evidence"]["lthr"]["qualified_activity_count"] == 4
    assert len(first["activity_evidence"]["lthr"]["created_observation_ids"]) == 4
    assert second["activity_evidence"]["lthr"]["created_observation_ids"] == []
    assert len(second["activity_evidence"]["lthr"]["reused_observation_ids"]) == 4
    assert first["activity_evidence"]["vt1"]["status"] == "sufficient"
    assert first["activity_evidence"]["vt1"]["independent_bracket_count"] == 2
    assert len(first["activity_evidence"]["vt1"]["created_observation_ids"]) == 2
    assert second["activity_evidence"]["vt1"]["created_observation_ids"] == []

    observations = store.list_observations(
        athlete_id="garmin:local", sport="cycling", metrics=("lthr", "vt1")
    )
    assert sum(item["metric"] == "lthr" for item in observations) == 4
    assert sum(item["metric"] == "vt1" for item in observations) == 2
    weather_observation = next(
        item
        for item in observations
        if item["metric"] == "lthr"
        and item["provenance_json"].get("activity_id") == "1"
    )
    assert weather_observation["provenance_json"]["ambient_temperature_c"] == 30
    assert weather_observation["provenance_json"]["temperature_source"] == "garmin_activity_weather"
    assert any(item["metric"] == "lthr" for item in first["candidates"])
    assert any(item["metric"] == "vt1" for item in first["candidates"])


def test_activity_field_evidence_does_not_invent_vt1_or_unqualified_lthr(
    physiology_store, monkeypatch
):
    store, client = physiology_store
    timelines = {
        "10": _steady_timeline(140, temperature_c=24),
        "11": _steady_timeline(150, 165, temperature_c=25),
    }
    monkeypatch.setattr(
        physiology,
        "_download_canonical_timeline",
        lambda activity_id: timelines[str(activity_id)],
    )
    client.get_activity.side_effect = lambda activity_id: {
        "activityId": activity_id,
        "activityName": "Ordinary Endurance Ride",
        "activityTypeDTO": {"typeKey": "cycling"},
        "summaryDTO": {
            "startTimeGMT": "2026-01-01T00:00:00Z",
            "trainingEffect": 3.9,
        },
    }
    client.get_activity_weather.return_value = None

    result = physiology.estimate_threshold_candidates(activity_ids=[10, 11])

    assert result["activity_evidence"]["lthr"]["qualified_activity_count"] == 0
    assert result["activity_evidence"]["vt1"]["status"] == "insufficient"
    assert result["activity_evidence"]["vt1"]["independent_bracket_count"] == 1
    observations = store.list_observations(
        athlete_id="garmin:local", sport="cycling", metrics=("lthr", "vt1")
    )
    assert observations == []
    assert result["activity_evidence"]["activities"][0]["temperature"] == {
        "ambient_temperature_c": 24.0,
        "temperature_source": "fit_device_temperature_fallback",
        "weather_error": None,
    }
