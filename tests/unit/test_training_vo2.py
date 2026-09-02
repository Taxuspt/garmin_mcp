import datetime
import math
from unittest.mock import Mock

import pytest

from garmin_mcp import training


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        [None],
        {},
        {"generic": None},
        {"vo2MaxValue": True},
        {"vo2MaxValue": "48.0"},
        {"vo2MaxValue": math.nan},
        {"vo2MaxValue": math.inf},
    ],
)
def test_extract_vo2_measurements_ignores_missing_or_invalid_values(payload):
    assert training._extract_vo2_measurements(payload) == {}


def test_extract_vo2_measurements_returns_both_sports_from_mixed_payload():
    payload = {
        "mostRecentVO2Max": {
            "generic": {"vo2MaxValue": 48.0},
            "cycling": {"vo2MaxValue": 55.0},
        }
    }

    assert training._extract_vo2_measurements(payload) == {
        "running": 48.0,
        "cycling": 55.0,
    }


def test_extract_vo2_measurements_merges_nested_list_payloads():
    payload = [
        [{"generic": {"vo2MaxValue": 48.0}}],
        {"cycling": {"vo2MaxPreciseValue": 55.12}},
    ]

    assert training._extract_vo2_measurements(payload) == {
        "running": 48.0,
        "cycling": 55.12,
    }


def test_extract_dated_vo2_measurements_indexes_each_sport_date():
    payload = [
        {
            "generic": {
                "calendarDate": "2024-01-14",
                "vo2MaxValue": 48.0,
            },
            "cycling": {
                "calendarDate": "2024-01-14",
                "vo2MaxValue": 55.0,
            },
        },
        [
            {
                "cycling": {
                    "calendarDate": "2024-01-15",
                    "vo2MaxValue": 55.5,
                }
            }
        ],
    ]

    assert training._extract_dated_vo2_measurements(payload) == {
        "2024-01-14": {"running": 48.0, "cycling": 55.0},
        "2024-01-15": {"cycling": 55.5},
    }


def test_get_max_metrics_range_uses_client_endpoint():
    client = Mock()
    client.garmin_connect_metrics_url = "/metrics-service/metrics/maxmet/daily"
    client.connectapi.return_value = [{"generic": {"vo2MaxValue": 48.0}}]

    supported, result = training._get_max_metrics_range(
        client, "2024-01-14", "2024-01-15"
    )

    assert supported is True
    assert result == [{"generic": {"vo2MaxValue": 48.0}}]
    client.connectapi.assert_called_once_with(
        "/metrics-service/metrics/maxmet/daily/2024-01-14/2024-01-15"
    )


def test_get_max_metrics_range_reports_unsupported_client():
    client = Mock()
    client.garmin_connect_metrics_url = None

    assert training._get_max_metrics_range(
        client, "2024-01-14", "2024-01-15"
    ) == (False, None)
    client.connectapi.assert_not_called()


def test_get_max_metrics_range_handles_missing_connectapi():
    client = Mock(spec=["garmin_connect_metrics_url"])
    client.garmin_connect_metrics_url = "/metrics-service/metrics/maxmet/daily"

    assert training._get_max_metrics_range(
        client, "2024-01-14", "2024-01-15"
    ) == (False, None)


def test_extract_vo2_measurements_prefers_precise_value():
    payload = {"generic": {"vo2MaxValue": 48.0, "vo2MaxPreciseValue": 47.6}}

    assert training._extract_vo2_measurements(payload) == {"running": 47.6}


def test_build_vo2_trend_series_carries_values_forward():
    history = [
        {"date": "2024-01-13", "vo2_max": 47.6, "source": "get_max_metrics"},
        {"date": "2024-01-15", "vo2_max": 48.7, "source": "get_training_status"},
    ]

    series = training._build_vo2_trend_series(history, datetime.date(2024, 1, 16))

    assert series == [
        {"date": "2024-01-13", "vo2_max": 47.6, "source": "get_max_metrics"},
        {
            "date": "2024-01-14",
            "vo2_max": 47.6,
            "source": "get_max_metrics",
            "carried_forward": True,
        },
        {"date": "2024-01-15", "vo2_max": 48.7, "source": "get_training_status"},
        {
            "date": "2024-01-16",
            "vo2_max": 48.7,
            "source": "get_training_status",
            "carried_forward": True,
        },
    ]


def test_build_vo2_trend_series_handles_empty_history_and_early_end():
    assert training._build_vo2_trend_series([], datetime.date(2024, 1, 16)) == []
    history = [{"date": "2024-01-20", "vo2_max": 48.0, "source": "get_max_metrics"}]

    assert training._build_vo2_trend_series(history, datetime.date(2024, 1, 16)) == []
