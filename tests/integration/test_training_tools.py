"""
Integration tests for training module MCP tools

Tests training tools using FastMCP integration with mocked Garmin API responses.
"""
import pytest
from unittest.mock import Mock
from mcp.server.fastmcp import FastMCP

import json

from garmin_mcp import training
from tests.fixtures.garmin_responses import (
    MOCK_PROGRESS_SUMMARY,
    MOCK_HRV_DATA,
    MOCK_TRAINING_STATUS,
    MOCK_LACTATE_THRESHOLD,
    MOCK_LACTATE_THRESHOLD_RANGE,
    MOCK_CYCLING_FTP,
    MOCK_ENDURANCE_SCORE,
    MOCK_ACTIVITY_TYPES,
    MOCK_RUNNING_TOLERANCE_DAILY,
    MOCK_RUNNING_TOLERANCE_DAILY_TREND,
    MOCK_RUNNING_TOLERANCE_WEEKLY,
)


@pytest.fixture
def app_with_training(mock_garmin_client):
    """Create FastMCP app with training tools registered"""
    training.configure(mock_garmin_client)
    app = FastMCP("Test Training")
    app = training.register_tools(app)
    return app


@pytest.mark.asyncio
async def test_get_progress_summary_between_dates_tool(app_with_training, mock_garmin_client):
    """Test get_progress_summary_between_dates tool"""
    # Setup mock
    mock_garmin_client.get_progress_summary_between_dates.return_value = MOCK_PROGRESS_SUMMARY

    # Call tool
    result = await app_with_training.call_tool(
        "get_progress_summary_between_dates",
        {
            "start_date": "2024-01-08",
            "end_date": "2024-01-15",
            "metric": "duration"
        }
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_progress_summary_between_dates.assert_called_once_with(
        "2024-01-08", "2024-01-15", "duration"
    )


@pytest.mark.asyncio
async def test_get_hill_score_tool(app_with_training, mock_garmin_client):
    """Test get_hill_score tool"""
    # Setup mock
    hill_score = {
        "hillScore": 75,
        "dateRange": {"start": "2024-01-08", "end": "2024-01-15"}
    }
    mock_garmin_client.get_hill_score.return_value = hill_score

    # Call tool
    result = await app_with_training.call_tool(
        "get_hill_score",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_hill_score.assert_called_once_with("2024-01-08", "2024-01-15")


@pytest.mark.asyncio
async def test_get_endurance_score_tool(app_with_training, mock_garmin_client):
    """Test get_endurance_score tool with realistic API response"""
    # Setup mocks
    mock_garmin_client.get_endurance_score.return_value = MOCK_ENDURANCE_SCORE
    mock_garmin_client.get_activity_types.return_value = MOCK_ACTIVITY_TYPES

    # Reset the activity type cache to ensure fresh lookup
    training._activity_type_cache = None

    # Call tool
    result = await app_with_training.call_tool(
        "get_endurance_score",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify API was called correctly
    assert result is not None
    mock_garmin_client.get_endurance_score.assert_called_once_with("2024-01-08", "2024-01-15")

    # Parse the result and verify content
    data = json.loads(result[0][0].text)

    # Check period summary
    assert data["period_avg_score"] == 5631
    assert data["period_max_score"] == 5740

    # Check current score
    assert data["current_score"] == 5712
    assert data["current_date"] == "2024-01-15"
    assert data["classification"] == "intermediate"
    assert data["classification_id"] == 2

    # Check thresholds
    assert "thresholds" in data
    assert data["thresholds"]["trained"] == 5800
    assert data["thresholds"]["well_trained"] == 6500

    # Check contributors have activity type names
    assert "contributors" in data
    contributors = data["contributors"]
    assert len(contributors) == 4

    # Find the hiking contributor
    hiking_contributor = next(
        (c for c in contributors if c.get("activity_type") == "hiking"), None
    )
    assert hiking_contributor is not None
    assert hiking_contributor["contribution_percent"] == 5.49
    assert hiking_contributor["activity_type_id"] == 3

    # Find the yoga contributor
    yoga_contributor = next(
        (c for c in contributors if c.get("activity_type") == "yoga"), None
    )
    assert yoga_contributor is not None
    assert yoga_contributor["contribution_percent"] == 3.13

    # Check weekly breakdown exists
    assert "weekly_breakdown" in data
    assert len(data["weekly_breakdown"]) == 1
    week = data["weekly_breakdown"][0]
    assert week["week_start"] == "2024-01-08"
    assert week["avg_score"] == 5548
    assert week["max_score"] == 5561


@pytest.mark.asyncio
async def test_get_training_effect_tool(app_with_training, mock_garmin_client):
    """Test get_training_effect tool"""
    # Setup mock - get_training_effect uses get_activity internally
    activity_data = {
        "summaryDTO": {
            "trainingEffect": 3.5,
            "anaerobicTrainingEffect": 2.0,
            "trainingEffectLabel": "Highly Improving",
            "activityTrainingLoad": 150,
            "recoveryTime": 720,  # 12 hours in minutes
            "performanceCondition": 95,
        }
    }
    mock_garmin_client.get_activity.return_value = activity_data

    # Call tool
    result = await app_with_training.call_tool(
        "get_training_effect",
        {"activity_id": 12345678901}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_activity.assert_called_once_with(12345678901)


@pytest.mark.asyncio
async def test_get_hrv_data_tool(app_with_training, mock_garmin_client):
    """Test get_hrv_data tool"""
    # Setup mock
    mock_garmin_client.get_hrv_data.return_value = MOCK_HRV_DATA

    # Call tool
    result = await app_with_training.call_tool(
        "get_hrv_data",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_hrv_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_fitnessage_data_tool(app_with_training, mock_garmin_client):
    """Test get_fitnessage_data tool"""
    # Setup mock
    fitness_age = {
        "fitnessAge": 25,
        "chronologicalAge": 30,
        "vo2Max": 52.5,
        "date": "2024-01-15"
    }
    mock_garmin_client.get_fitnessage_data.return_value = fitness_age

    # Call tool
    result = await app_with_training.call_tool(
        "get_fitnessage_data",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_fitnessage_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_cycling_ftp_tool(app_with_training, mock_garmin_client):
    """Test get_cycling_ftp tool returns latest FTP data"""
    mock_garmin_client.get_cycling_ftp.return_value = MOCK_CYCLING_FTP

    result = await app_with_training.call_tool("get_cycling_ftp", {})

    assert result is not None
    mock_garmin_client.get_cycling_ftp.assert_called_once_with()

    data = json.loads(result[0][0].text)
    assert data["sport"] == "CYCLING"
    assert data["functional_threshold_power_watts"] == 294
    assert data["calendar_date"] == "2024-03-15T10:30:00.000"
    assert data["is_stale"] is False
    assert data["biometric_source_type"] == "CHANGE_LOG"


@pytest.mark.asyncio
async def test_request_reload_tool(app_with_training, mock_garmin_client):
    """Test request_reload tool"""
    # Setup mock
    reload_response = {"status": "success", "message": "Data reload requested"}
    mock_garmin_client.request_reload.return_value = reload_response

    # Call tool
    result = await app_with_training.call_tool(
        "request_reload",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.request_reload.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_training_status_tool(app_with_training, mock_garmin_client):
    """Test get_training_status tool returns training status"""
    # Setup mock
    mock_garmin_client.get_training_status.return_value = MOCK_TRAINING_STATUS

    # Call tool
    result = await app_with_training.call_tool(
        "get_training_status",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_training_status.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_training_status_skips_null_device_entries(
    app_with_training, mock_garmin_client
):
    """Test null device entries are skipped when selecting training status data."""
    mock_garmin_client.get_training_status.return_value = {
        "mostRecentTrainingStatus": {
            "latestTrainingStatusData": {
                "device-null": None,
                "device-valid": {
                    "calendarDate": "2026-08-24",
                    "trainingStatus": "PRODUCTIVE",
                    "acuteTrainingLoadDTO": {
                        "dailyTrainingLoadAcute": 250,
                    },
                },
            }
        },
        "mostRecentVO2Max": {
            "generic": {"vo2MaxValue": 52.5},
            "cycling": None,
        },
        "mostRecentTrainingLoadBalance": {
            "metricsTrainingLoadBalanceDTOMap": {
                "device-null": None,
                "device-valid": {"monthlyLoadAerobicLow": 100},
            }
        },
    }

    result = await app_with_training.call_tool(
        "get_training_status",
        {"date": "2026-08-24"},
    )

    data = json.loads(result[0][0].text)
    assert data["date"] == "2026-08-24"
    assert data["training_status"] == "PRODUCTIVE"
    assert data["acute_load"] == 250
    assert data["vo2_max"] == 52.5
    assert data["monthly_load_aerobic_low"] == 100
    mock_garmin_client.get_training_status.assert_called_once_with("2026-08-24")


@pytest.mark.asyncio
async def test_get_training_status_skips_empty_device_entries(
    app_with_training, mock_garmin_client
):
    """Test empty device entries are skipped when selecting training status data."""
    mock_garmin_client.get_training_status.return_value = {
        "mostRecentTrainingStatus": {
            "latestTrainingStatusData": {
                "device-empty": {},
                "device-valid": {
                    "calendarDate": "2026-08-24",
                    "trainingStatus": "PRODUCTIVE",
                    "acuteTrainingLoadDTO": {
                        "dailyTrainingLoadAcute": 250,
                    },
                },
            }
        },
        "mostRecentTrainingLoadBalance": {
            "metricsTrainingLoadBalanceDTOMap": {
                "device-empty": {},
                "device-valid": {"monthlyLoadAerobicLow": 100},
            }
        },
    }

    result = await app_with_training.call_tool(
        "get_training_status",
        {"date": "2026-08-24"},
    )

    data = json.loads(result[0][0].text)
    assert data["training_status"] == "PRODUCTIVE"
    assert data["acute_load"] == 250
    assert data["monthly_load_aerobic_low"] == 100
    mock_garmin_client.get_training_status.assert_called_once_with("2026-08-24")


@pytest.mark.asyncio
async def test_get_training_status_tolerates_all_null_device_maps(
    app_with_training, mock_garmin_client
):
    """Test training status output contains only the requested date when maps are null."""
    mock_garmin_client.get_training_status.return_value = {
        "mostRecentTrainingStatus": {
            "latestTrainingStatusData": {"device-null": None},
        },
        "mostRecentVO2Max": {
            "generic": None,
            "cycling": None,
        },
        "mostRecentTrainingLoadBalance": {
            "metricsTrainingLoadBalanceDTOMap": {"device-null": None},
        },
    }

    result = await app_with_training.call_tool(
        "get_training_status",
        {"date": "2026-08-24"},
    )

    assert json.loads(result[0][0].text) == {"date": "2026-08-24"}
    mock_garmin_client.get_training_status.assert_called_once_with("2026-08-24")


@pytest.mark.asyncio
async def test_get_training_status_tolerates_non_mapping_response(
    app_with_training, mock_garmin_client
):
    """Test truthy non-mapping training status responses are treated as empty."""
    mock_garmin_client.get_training_status.return_value = [None]

    result = await app_with_training.call_tool(
        "get_training_status",
        {"date": "2026-08-24"},
    )

    assert result[0][0].text == "No training status data found for 2026-08-24."
    mock_garmin_client.get_training_status.assert_called_once_with("2026-08-24")


@pytest.mark.asyncio
async def test_get_vo2max_trend_falls_back_to_profile(
    app_with_training, mock_garmin_client
):
    """Test current profile estimate is separate from unavailable history"""
    mock_garmin_client.garmin_connect_metrics_url = (
        "/metrics-service/metrics/maxmet/daily"
    )
    mock_garmin_client.connectapi.return_value = []
    mock_garmin_client.get_training_status.return_value = {}
    mock_garmin_client.get_user_profile.return_value = {
        "userData": {"vo2MaxRunning": 28.0}
    }

    result = await app_with_training.call_tool(
        "get_vo2max_trend", {"start_date": "2024-01-14", "end_date": "2024-01-15"}
    )

    data = json.loads(result[0][0].text)
    assert data["data_points"] == 0
    assert data["first_vo2_max"] is None
    assert data["latest_vo2_max"] is None
    assert data["change"] is None
    assert data["trend"] == []
    assert data["current_vo2_max_estimate"] == {
        "vo2_max": 28.0,
        "sport": "running",
        "source": "get_user_profile",
    }
    assert "Historical VO2 max values were not available" in data["note"]
    assert mock_garmin_client.get_training_status.call_count == 2
    mock_garmin_client.connectapi.assert_called_once_with(
        "/metrics-service/metrics/maxmet/daily/2024-01-14/2024-01-15"
    )
    mock_garmin_client.get_max_metrics.assert_not_called()
    mock_garmin_client.get_fitnessage_data.assert_not_called()
    mock_garmin_client.get_user_profile.assert_called_once_with()


@pytest.mark.asyncio
async def test_get_vo2max_trend_uses_daily_metrics(
    app_with_training, mock_garmin_client
):
    """Test range metrics take precedence without daily training-status calls"""
    mock_garmin_client.garmin_connect_metrics_url = (
        "/metrics-service/metrics/maxmet/daily"
    )
    mock_garmin_client.get_training_status.return_value = {
        "mostRecentVO2Max": {"generic": {"vo2MaxValue": 99.0}}
    }
    mock_garmin_client.connectapi.return_value = [
        {
            "generic": {
                "calendarDate": "2024-01-14",
                "vo2MaxValue": 27.5,
            }
        },
        {
            "generic": {
                "calendarDate": "2024-01-15",
                "vo2MaxValue": 28.0,
            }
        },
    ]

    result = await app_with_training.call_tool(
        "get_vo2max_trend", {"start_date": "2024-01-14", "end_date": "2024-01-15"}
    )

    data = json.loads(result[0][0].text)
    assert data["latest_vo2_max"] == 28.0
    assert data["change"] == 0.5
    assert data["sport"] == "running"
    assert data["trend"] == [
        {
            "date": "2024-01-14",
            "vo2_max": 27.5,
            "source": "get_max_metrics",
        },
        {
            "date": "2024-01-15",
            "vo2_max": 28.0,
            "source": "get_max_metrics",
        },
    ]
    mock_garmin_client.connectapi.assert_called_once_with(
        "/metrics-service/metrics/maxmet/daily/2024-01-14/2024-01-15"
    )
    mock_garmin_client.get_training_status.assert_not_called()
    mock_garmin_client.get_max_metrics.assert_not_called()
    mock_garmin_client.get_fitnessage_data.assert_not_called()
    mock_garmin_client.get_user_profile.assert_not_called()


@pytest.mark.asyncio
async def test_get_vo2max_trend_carries_forward_activity_day_values(
    app_with_training, mock_garmin_client
):
    """Test sparse max-metrics days match the chart by carrying values forward"""
    mock_garmin_client.garmin_connect_metrics_url = (
        "/metrics-service/metrics/maxmet/daily"
    )
    # Garmin records max-metrics entries only on days with a VO2 max recompute.
    mock_garmin_client.connectapi.return_value = [
        {
            "generic": {
                "calendarDate": "2024-01-13",
                "vo2MaxValue": 48.0,
                "vo2MaxPreciseValue": 47.6,
            }
        },
        {
            "generic": {
                "calendarDate": "2024-01-15",
                "vo2MaxValue": 49.0,
                "vo2MaxPreciseValue": 48.7,
            }
        },
    ]
    mock_garmin_client.get_training_status.return_value = {}

    result = await app_with_training.call_tool(
        "get_vo2max_trend", {"start_date": "2024-01-12", "end_date": "2024-01-16"}
    )

    data = json.loads(result[0][0].text)
    assert data["sport"] == "running"
    assert data["data_points"] == 4
    assert data["first_vo2_max"] == 47.6
    assert data["latest_vo2_max"] == 48.7
    assert data["change"] == 1.1
    assert data["trend"] == [
        {"date": "2024-01-13", "vo2_max": 47.6, "source": "get_max_metrics"},
        {
            "date": "2024-01-14",
            "vo2_max": 47.6,
            "source": "get_max_metrics",
            "carried_forward": True,
        },
        {"date": "2024-01-15", "vo2_max": 48.7, "source": "get_max_metrics"},
        {
            "date": "2024-01-16",
            "vo2_max": 48.7,
            "source": "get_max_metrics",
            "carried_forward": True,
        },
    ]
    # Only the days missing from the range response trigger a status call.
    assert mock_garmin_client.get_training_status.call_count == 3


@pytest.mark.asyncio
async def test_get_vo2max_trend_prefers_training_status(
    app_with_training, mock_garmin_client
):
    """Test common historical data avoids extra fallback requests"""
    mock_garmin_client.get_training_status.side_effect = [
        {"mostRecentVO2Max": {"generic": {"vo2MaxValue": 48.0}}},
        {"mostRecentVO2Max": {"generic": {"vo2MaxValue": 48.5}}},
    ]

    result = await app_with_training.call_tool(
        "get_vo2max_trend", {"start_date": "2024-01-14", "end_date": "2024-01-15"}
    )

    data = json.loads(result[0][0].text)
    assert data["latest_vo2_max"] == 48.5
    assert data["sport"] == "running"
    assert all(point["source"] == "get_training_status" for point in data["trend"])
    assert mock_garmin_client.get_training_status.call_count == 2
    mock_garmin_client.get_max_metrics.assert_not_called()
    mock_garmin_client.get_fitnessage_data.assert_not_called()


@pytest.mark.asyncio
async def test_get_vo2max_trend_preserves_selected_cycling_from_mixed_payloads(
    app_with_training, mock_garmin_client
):
    """Test mixed payloads do not hide cycling after it becomes available first"""
    mock_garmin_client.garmin_connect_metrics_url = (
        "/metrics-service/metrics/maxmet/daily"
    )
    mock_garmin_client.connectapi.return_value = []
    mock_garmin_client.get_training_status.side_effect = [
        {"mostRecentVO2Max": {"cycling": {"vo2MaxValue": 55.0}}},
        {
            "mostRecentVO2Max": {
                "generic": {"vo2MaxValue": 48.0},
                "cycling": {"vo2MaxValue": 56.0},
            }
        },
        {
            "mostRecentVO2Max": {
                "generic": {"vo2MaxValue": 48.5},
                "cycling": {"vo2MaxValue": 57.0},
            }
        },
    ]

    result = await app_with_training.call_tool(
        "get_vo2max_trend", {"start_date": "2024-01-13", "end_date": "2024-01-15"}
    )

    data = json.loads(result[0][0].text)
    assert data["sport"] == "cycling"
    assert data["change"] == 2.0
    assert data["trend"] == [
        {
            "date": "2024-01-13",
            "vo2_max": 55.0,
            "source": "get_training_status",
        },
        {
            "date": "2024-01-14",
            "vo2_max": 56.0,
            "source": "get_training_status",
        },
        {
            "date": "2024-01-15",
            "vo2_max": 57.0,
            "source": "get_training_status",
        },
    ]
    mock_garmin_client.get_max_metrics.assert_not_called()


@pytest.mark.asyncio
async def test_get_vo2max_trend_selects_sport_with_most_history(
    app_with_training, mock_garmin_client
):
    """Test an isolated oldest point does not decide the sport for the interval"""
    mock_garmin_client.garmin_connect_metrics_url = (
        "/metrics-service/metrics/maxmet/daily"
    )
    mock_garmin_client.connectapi.return_value = []
    mock_garmin_client.get_training_status.side_effect = [
        {"mostRecentVO2Max": {"cycling": {"vo2MaxValue": 55.0}}},
        {"mostRecentVO2Max": {"generic": {"vo2MaxValue": 48.0}}},
        {"mostRecentVO2Max": {"generic": {"vo2MaxValue": 48.5}}},
    ]

    result = await app_with_training.call_tool(
        "get_vo2max_trend", {"start_date": "2024-01-13", "end_date": "2024-01-15"}
    )

    data = json.loads(result[0][0].text)
    assert data["sport"] == "running"
    assert data["data_points"] == 2
    assert [point["vo2_max"] for point in data["trend"]] == [48.0, 48.5]


@pytest.mark.asyncio
async def test_get_vo2max_trend_uses_cycling_range_metrics(
    app_with_training, mock_garmin_client
):
    """Test cycling-only max metrics retain their sport and date"""
    mock_garmin_client.garmin_connect_metrics_url = (
        "/metrics-service/metrics/maxmet/daily"
    )
    mock_garmin_client.connectapi.return_value = [
        {
            "cycling": {
                "calendarDate": "2024-01-15",
                "vo2MaxValue": 55.1,
            }
        }
    ]

    result = await app_with_training.call_tool(
        "get_vo2max_trend", {"start_date": "2024-01-15", "end_date": "2024-01-15"}
    )

    data = json.loads(result[0][0].text)
    assert data["sport"] == "cycling"
    assert data["trend"] == [
        {
            "date": "2024-01-15",
            "vo2_max": 55.1,
            "source": "get_max_metrics",
        }
    ]
    mock_garmin_client.get_training_status.assert_not_called()
    mock_garmin_client.get_max_metrics.assert_not_called()


@pytest.mark.asyncio
async def test_get_vo2max_trend_range_failure_does_not_retry_max_metrics_daily(
    app_with_training, mock_garmin_client
):
    """Test a failed range request falls back without amplifying that failure"""
    mock_garmin_client.garmin_connect_metrics_url = (
        "/metrics-service/metrics/maxmet/daily"
    )
    mock_garmin_client.connectapi.side_effect = RuntimeError("API unavailable")
    mock_garmin_client.get_training_status.return_value = {
        "mostRecentVO2Max": {"generic": {"vo2MaxValue": 48.0}}
    }

    result = await app_with_training.call_tool(
        "get_vo2max_trend", {"start_date": "2024-01-14", "end_date": "2024-01-15"}
    )

    data = json.loads(result[0][0].text)
    assert data["sport"] == "running"
    assert mock_garmin_client.get_training_status.call_count == 2
    mock_garmin_client.get_max_metrics.assert_not_called()


@pytest.mark.asyncio
async def test_get_vo2max_trend_uses_daily_max_metrics_for_older_clients(
    app_with_training, mock_garmin_client
):
    """Test clients without the range API keep the compatible daily fallback"""
    mock_garmin_client.garmin_connect_metrics_url = None
    mock_garmin_client.get_training_status.return_value = {}
    mock_garmin_client.get_max_metrics.return_value = [
        {"generic": {"vo2MaxValue": 48.0}}
    ]

    result = await app_with_training.call_tool(
        "get_vo2max_trend", {"start_date": "2024-01-15", "end_date": "2024-01-15"}
    )

    data = json.loads(result[0][0].text)
    assert data["latest_vo2_max"] == 48.0
    mock_garmin_client.connectapi.assert_not_called()
    mock_garmin_client.get_max_metrics.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_vo2max_trend_prefers_running_when_sport_coverage_is_tied(
    app_with_training, mock_garmin_client
):
    """Test equal sport coverage has a stable running-first tie break"""
    mock_garmin_client.garmin_connect_metrics_url = (
        "/metrics-service/metrics/maxmet/daily"
    )
    mock_garmin_client.connectapi.return_value = []
    mock_garmin_client.get_training_status.return_value = {
        "mostRecentVO2Max": {
            "generic": {"vo2MaxValue": 48.0},
            "cycling": {"vo2MaxValue": 55.0},
        }
    }

    result = await app_with_training.call_tool(
        "get_vo2max_trend", {"start_date": "2024-01-15", "end_date": "2024-01-15"}
    )

    data = json.loads(result[0][0].text)
    assert data["sport"] == "running"
    assert data["latest_vo2_max"] == 48.0


@pytest.mark.asyncio
async def test_get_vo2max_trend_no_history_avoids_daily_max_metrics_requests(
    app_with_training, mock_garmin_client
):
    """Test the 90-day no-history case uses one range max-metrics request"""
    mock_garmin_client.garmin_connect_metrics_url = (
        "/metrics-service/metrics/maxmet/daily"
    )
    mock_garmin_client.connectapi.return_value = []
    mock_garmin_client.get_training_status.return_value = {}
    mock_garmin_client.get_user_profile.return_value = {
        "userData": {"vo2MaxRunning": 48.0}
    }

    result = await app_with_training.call_tool(
        "get_vo2max_trend", {"start_date": "2024-01-01", "end_date": "2024-03-30"}
    )

    data = json.loads(result[0][0].text)
    assert data["data_points"] == 0
    assert mock_garmin_client.get_training_status.call_count == 90
    assert mock_garmin_client.connectapi.call_count == 1
    mock_garmin_client.get_max_metrics.assert_not_called()
    mock_garmin_client.get_user_profile.assert_called_once_with()


@pytest.mark.asyncio
async def test_get_vo2max_trend_handles_endpoint_exception_and_missing_method(
    app_with_training, mock_garmin_client
):
    """Test daily errors and an unavailable max-metrics method reach the profile"""
    mock_garmin_client.garmin_connect_metrics_url = None
    mock_garmin_client.get_training_status.side_effect = RuntimeError("API unavailable")
    mock_garmin_client.get_max_metrics = None
    mock_garmin_client.get_user_profile.return_value = {
        "userData": {"vo2MaxCycling": 55.0}
    }

    result = await app_with_training.call_tool(
        "get_vo2max_trend", {"start_date": "2024-01-14", "end_date": "2024-01-15"}
    )

    data = json.loads(result[0][0].text)
    assert data["current_vo2_max_estimate"]["sport"] == "cycling"
    assert mock_garmin_client.get_training_status.call_count == 2


@pytest.mark.asyncio
async def test_get_lactate_threshold_tool_latest(app_with_training, mock_garmin_client):
    """Test get_lactate_threshold tool returns latest lactate threshold data"""
    # Setup mock with latest=True response format
    mock_garmin_client.get_lactate_threshold.return_value = MOCK_LACTATE_THRESHOLD

    # Call tool with no dates (gets latest)
    result = await app_with_training.call_tool(
        "get_lactate_threshold",
        {}
    )

    # Verify API call
    assert result is not None
    mock_garmin_client.get_lactate_threshold.assert_called_once_with(latest=True)

    # Verify output structure
    data = json.loads(result[0][0].text)
    # Garmin returns speed as seconds/metre (inverse pace); the tool inverts it to m/s.
    assert abs(data["lactate_threshold_speed_mps"] - 1 / 0.32222132) < 1e-6
    assert data["lactate_threshold_heart_rate_bpm"] == 169
    assert data["functional_threshold_power_watts"] == 334
    assert data["sport"] == "RUNNING"
    assert data["power_to_weight"] == 4.575


@pytest.mark.asyncio
async def test_get_lactate_threshold_tool_range(app_with_training, mock_garmin_client):
    """Test get_lactate_threshold tool returns lactate threshold data for date range"""
    # Setup mock with date range response format
    mock_garmin_client.get_lactate_threshold.return_value = MOCK_LACTATE_THRESHOLD_RANGE

    # Call tool with date range
    result = await app_with_training.call_tool(
        "get_lactate_threshold",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify API call
    assert result is not None
    mock_garmin_client.get_lactate_threshold.assert_called_once_with(
        latest=False,
        start_date="2024-01-08",
        end_date="2024-01-15",
    )

    # Verify output structure
    data = json.loads(result[0][0].text)
    assert data["start_date"] == "2024-01-08"
    assert data["end_date"] == "2024-01-15"
    assert "speed_history" in data
    assert len(data["speed_history"]) == 3
    assert data["speed_history"][0]["date"] == "2024-01-08"
    # Garmin returns speed as seconds/metre (inverse pace); the tool inverts it to m/s.
    assert abs(data["speed_history"][0]["speed_mps"] - 1 / 0.29444) < 1e-4
    assert "heart_rate_history" in data
    assert len(data["heart_rate_history"]) == 3
    assert "power_history" in data


# Error handling tests
@pytest.mark.asyncio
async def test_get_hrv_data_no_data(app_with_training, mock_garmin_client):
    """Test get_hrv_data tool when no data available"""
    # Setup mock to return None
    mock_garmin_client.get_hrv_data.return_value = None

    # Call tool
    result = await app_with_training.call_tool(
        "get_hrv_data",
        {"date": "2024-01-15"}
    )

    # Verify error message is returned
    assert result is not None


@pytest.mark.asyncio
async def test_get_training_effect_exception(app_with_training, mock_garmin_client):
    """Test get_training_effect tool when API raises exception"""
    # Setup mock to raise exception - get_training_effect uses get_activity internally
    mock_garmin_client.get_activity.side_effect = Exception("API Error")

    # Call tool
    result = await app_with_training.call_tool(
        "get_training_effect",
        {"activity_id": 12345678901}
    )

    # Verify error is handled gracefully
    assert result is not None


@pytest.mark.asyncio
async def test_get_training_status_includes_cycling_vo2_max(app_with_training, mock_garmin_client):
    """Test that cycling VO2 max fields are surfaced when present in API response."""
    mock_garmin_client.get_training_status.return_value = MOCK_TRAINING_STATUS

    result = await app_with_training.call_tool(
        "get_training_status",
        {"date": "2024-01-15"},
    )

    assert result is not None
    import json
    text = result[0][0].text if result and result[0] else str(result)
    try:
        data = json.loads(text)
        assert data.get("cycling_vo2_max") == 55.0
        assert data.get("cycling_vo2_max_precise") == 55.12
    except (json.JSONDecodeError, AttributeError):
        # Tool may return raw text; just check the values appear in output
        assert "55.0" in text or "55.12" in text


@pytest.mark.asyncio
async def test_get_running_tolerance_tool(app_with_training, mock_garmin_client):
    """Test get_running_tolerance tool converts meters to km and computes load_ratio"""
    mock_garmin_client.get_running_tolerance.return_value = MOCK_RUNNING_TOLERANCE_DAILY

    result = await app_with_training.call_tool(
        "get_running_tolerance",
        {"date": "2024-01-15"}
    )

    assert result is not None
    mock_garmin_client.get_running_tolerance.assert_called_once_with(
        "2024-01-15", "2024-01-15", aggregation="daily"
    )

    data = json.loads(result[0][0].text)
    assert data["date"] == "2024-01-15"
    assert data["tolerance_km"] == 34.0
    assert data["acute_load_km"] == 25.0
    assert data["distance_km"] == 22.0
    assert data["load_ratio"] == round(25000 / 22000, 2)
    assert data["feedback_phrase"] == "MEDIUM_LOAD"


@pytest.mark.asyncio
async def test_get_running_tolerance_unsupported_device(app_with_training, mock_garmin_client):
    """Test get_running_tolerance tool when the account/device has no Running Tolerance data"""
    mock_garmin_client.get_running_tolerance.return_value = []

    result = await app_with_training.call_tool(
        "get_running_tolerance",
        {"date": "2024-01-15"}
    )

    assert result is not None
    assert result[0][0].text == "Your device does not support this metric."


@pytest.mark.asyncio
async def test_get_running_tolerance_error(app_with_training, mock_garmin_client):
    """Test get_running_tolerance tool when the API raises an exception"""
    mock_garmin_client.get_running_tolerance.side_effect = Exception("API Error")

    result = await app_with_training.call_tool(
        "get_running_tolerance",
        {"date": "2024-01-15"}
    )

    assert result is not None
    assert "Error retrieving running tolerance data" in result[0][0].text


@pytest.mark.asyncio
async def test_get_running_tolerance_trend_daily_sorts_by_date(app_with_training, mock_garmin_client):
    """Test get_running_tolerance_trend sorts the unordered daily response"""
    mock_garmin_client.get_running_tolerance.return_value = MOCK_RUNNING_TOLERANCE_DAILY_TREND

    result = await app_with_training.call_tool(
        "get_running_tolerance_trend",
        {"start_date": "2024-01-15", "end_date": "2024-01-16", "aggregation": "daily"}
    )

    assert result is not None
    mock_garmin_client.get_running_tolerance.assert_called_once_with(
        "2024-01-15", "2024-01-16", aggregation="daily"
    )

    data = json.loads(result[0][0].text)
    assert data["aggregation"] == "daily"
    assert data["data_points"] == 2
    assert [p["date"] for p in data["trend"]] == ["2024-01-15", "2024-01-16"]
    assert data["first_tolerance_km"] == 34.0
    assert data["latest_tolerance_km"] == 34.5
    assert data["tolerance_change_km"] == 0.5
    assert data["trend"][0]["feedback_phrase"] == "MEDIUM_LOAD"
    assert "start_of_week" not in data["trend"][0]


@pytest.mark.asyncio
async def test_get_running_tolerance_trend_weekly_default(app_with_training, mock_garmin_client):
    """Test get_running_tolerance_trend defaults to weekly aggregation"""
    mock_garmin_client.get_running_tolerance.return_value = MOCK_RUNNING_TOLERANCE_WEEKLY

    result = await app_with_training.call_tool(
        "get_running_tolerance_trend",
        {"start_date": "2024-01-02", "end_date": "2024-01-15"}
    )

    assert result is not None
    mock_garmin_client.get_running_tolerance.assert_called_once_with(
        "2024-01-02", "2024-01-15", aggregation="weekly"
    )

    data = json.loads(result[0][0].text)
    assert data["aggregation"] == "weekly"
    week = data["trend"][0]
    assert week["start_of_week"] == "2024-01-02"
    assert week["end_of_week"] == "2024-01-08"
    assert week["week_index"] == 1900
    assert week["tolerance_km"] == 33.0
    assert week["acute_load_km"] == 26.0
    assert week["distance_km"] == 24.0
    assert "feedback_phrase" not in week


@pytest.mark.asyncio
async def test_get_running_tolerance_trend_unsupported_device(app_with_training, mock_garmin_client):
    """Test get_running_tolerance_trend when the account/device has no data"""
    mock_garmin_client.get_running_tolerance.return_value = []

    result = await app_with_training.call_tool(
        "get_running_tolerance_trend",
        {"start_date": "2024-01-01", "end_date": "2024-01-07"}
    )

    assert result is not None
    assert result[0][0].text == "Your device does not support this metric."


@pytest.mark.asyncio
async def test_get_running_tolerance_trend_error(app_with_training, mock_garmin_client):
    """Test get_running_tolerance_trend when the API raises an exception"""
    mock_garmin_client.get_running_tolerance.side_effect = Exception("API Error")

    result = await app_with_training.call_tool(
        "get_running_tolerance_trend",
        {"start_date": "2024-01-01", "end_date": "2024-01-07"}
    )

    assert result is not None
    assert "Error retrieving running tolerance trend" in result[0][0].text


@pytest.mark.asyncio
async def test_get_running_tolerance_trend_invalid_range(app_with_training, mock_garmin_client):
    """Test get_running_tolerance_trend rejects end_date before start_date"""
    result = await app_with_training.call_tool(
        "get_running_tolerance_trend",
        {"start_date": "2024-01-15", "end_date": "2024-01-01"}
    )

    assert result is not None
    assert "end_date must be on or after start_date" in result[0][0].text
    mock_garmin_client.get_running_tolerance.assert_not_called()


@pytest.mark.asyncio
async def test_get_running_tolerance_trend_daily_range_too_large(app_with_training, mock_garmin_client):
    """Test get_running_tolerance_trend enforces the 90-day cap for daily aggregation"""
    result = await app_with_training.call_tool(
        "get_running_tolerance_trend",
        {"start_date": "2024-01-01", "end_date": "2024-04-15", "aggregation": "daily"}
    )

    assert result is not None
    assert "Date range too large" in result[0][0].text
    mock_garmin_client.get_running_tolerance.assert_not_called()


@pytest.mark.asyncio
async def test_get_training_status_no_cycling_vo2_when_absent(app_with_training, mock_garmin_client):
    """Test that cycling VO2 fields are omitted when the cycling subkey is missing."""
    status_without_cycling = {
        "mostRecentVO2Max": {
            "generic": {"vo2MaxValue": 52.5, "vo2MaxPreciseValue": 52.47},
        },
    }
    mock_garmin_client.get_training_status.return_value = status_without_cycling

    result = await app_with_training.call_tool(
        "get_training_status",
        {"date": "2024-01-15"},
    )

    assert result is not None
    import json
    text = result[0][0].text if result and result[0] else str(result)
    try:
        data = json.loads(text)
        assert "cycling_vo2_max" not in data
        assert "cycling_vo2_max_precise" not in data
    except (json.JSONDecodeError, AttributeError):
        assert "cycling_vo2_max" not in text


@pytest.mark.asyncio
async def test_get_hrv_data_handles_null_summary(app_with_training, mock_garmin_client):
    """A null hrvSummary must not crash the tool.

    Garmin returns an explicit null for sections the user has no data in.
    `hrv_data.get("hrvSummary", {})` returns None in that case (the default
    only applies when the key is absent), so `summary.get("baseline", {})`
    raised "'NoneType' object has no attribute 'get'".
    """
    mock_garmin_client.get_hrv_data.return_value = {
        "hrvSummary": None,
        "sleepStartTimestampLocal": None,
    }

    result = await app_with_training.call_tool(
        "get_hrv_data",
        {"date": "2024-01-15"},
    )
    text = result[0][0].text
    assert "NoneType" not in text
    assert "Error" not in text


@pytest.mark.asyncio
async def test_get_progress_summary_handles_null_stats(app_with_training, mock_garmin_client):
    """A null `stats` block must not crash the tool.

    An empty range can come back with `stats` as an explicit null;
    `data.get("stats", {})` then returns None and `.items()` raised
    "'NoneType' object has no attribute 'items'".
    """
    mock_garmin_client.get_progress_summary_between_dates.return_value = [
        {"date": "2024-01-15", "stats": None}
    ]

    result = await app_with_training.call_tool(
        "get_progress_summary_between_dates",
        {"start_date": "2024-01-08", "end_date": "2024-01-15", "metric": "duration"},
    )
    text = result[0][0].text
    assert "NoneType" not in text
    assert "Error" not in text


@pytest.mark.asyncio
async def test_get_acclimation_returns_heat_data(app_with_training, mock_garmin_client):
    """Test get_acclimation returns curated heat/altitude acclimation data."""
    mock_garmin_client.get_max_metrics.return_value = [
        {
            "heatAltitudeAcclimation": {
                "calendarDate": "2024-07-15",
                "heatAcclimationPercentage": 72.5,
                "previousHeatAcclimationPercentage": 65.0,
                "heatTrend": "ACCLIMATIZED",
                "heatAcclimationDate": "2024-07-15",
                "altitudeAcclimation": 1200,
                "altitudeTrend": "INCREASING",
                "currentAltitude": 850,
            }
        }
    ]

    result = await app_with_training.call_tool(
        "get_acclimation",
        {"date": "2024-07-15"},
    )

    data = json.loads(result[0][0].text)
    assert data["date"] == "2024-07-15"
    assert data["heat_acclimation_percent"] == 72.5
    assert data["previous_heat_acclimation_percent"] == 65.0
    assert data["heat_trend"] == "ACCLIMATIZED"
    assert data["heat_acclimation_change"] == 7.5
    assert data["altitude_acclimation_meters"] == 1200
    assert data["altitude_trend"] == "INCREASING"
    mock_garmin_client.get_max_metrics.assert_called_once_with("2024-07-15")


@pytest.mark.asyncio
async def test_get_acclimation_no_data(app_with_training, mock_garmin_client):
    """Test get_acclimation handles missing heatAltitudeAcclimation."""
    mock_garmin_client.get_max_metrics.return_value = [
        {"generic": {"calendarDate": "2024-07-15", "vo2MaxValue": 48.0}}
    ]

    result = await app_with_training.call_tool(
        "get_acclimation",
        {"date": "2024-07-15"},
    )

    assert "No acclimation data" in result[0][0].text


@pytest.mark.asyncio
async def test_get_acclimation_exception(app_with_training, mock_garmin_client):
    """Test get_acclimation error handling."""
    mock_garmin_client.get_max_metrics.side_effect = Exception("API Error")

    result = await app_with_training.call_tool(
        "get_acclimation",
        {"date": "2024-07-15"},
    )

    assert "Error" in result[0][0].text


@pytest.mark.asyncio
async def test_hrv_trend_uses_reported_nightly_averages(app_with_training, mock_garmin_client):
    """Garmin's lastNightAvg drives both daily values and the period mean."""
    mock_garmin_client.get_hrv_data.side_effect = [
        {"hrvSummary": {"lastNightAvg": 54, "lastNight": 999, "weeklyAvg": 60}},
        {"hrvSummary": {"lastNightAvg": 70, "lastNight5MinHigh": 107}},
    ]
    result = await app_with_training.call_tool(
        "get_hrv_trend", {"start_date": "2026-09-04", "end_date": "2026-09-05"}
    )
    data = json.loads(result[0][0].text)
    assert data["days_with_data"] == 2
    assert [day["last_night_avg_hrv_ms"] for day in data["trend"]] == [54, 70]
    assert data["period_avg_hrv_ms"] == 62
    assert data["trend"][0]["weekly_avg_hrv_ms"] == 60
    assert data["trend"][1]["last_night_5min_high_hrv_ms"] == 107


@pytest.mark.asyncio
async def test_hrv_trend_averages_only_available_nights(app_with_training, mock_garmin_client):
    mock_garmin_client.get_hrv_data.side_effect = [
        {"hrvSummary": {"lastNightAvg": 54}},
        None,
        {"hrvSummary": {"lastNightAvg": None, "weeklyAvg": 60}},
        Exception("unavailable"),
        {"hrvSummary": {"lastNightAvg": 71}},
    ]
    result = await app_with_training.call_tool(
        "get_hrv_trend", {"start_date": "2026-09-01", "end_date": "2026-09-05"}
    )
    data = json.loads(result[0][0].text)
    assert data["days_with_data"] == 3
    assert data["period_avg_hrv_ms"] == 62.5
    assert "last_night_avg_hrv_ms" not in data["trend"][1]


@pytest.mark.asyncio
async def test_hrv_trend_without_nightly_values(app_with_training, mock_garmin_client):
    mock_garmin_client.get_hrv_data.return_value = {"hrvSummary": {"weeklyAvg": 60}}
    result = await app_with_training.call_tool(
        "get_hrv_trend", {"start_date": "2026-09-05", "end_date": "2026-09-05"}
    )
    data = json.loads(result[0][0].text)
    assert data["period_avg_hrv_ms"] is None
    assert data["trend"] == [{"date": "2026-09-05", "weekly_avg_hrv_ms": 60}]
