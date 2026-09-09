"""
Integration tests for health_wellness module MCP tools

Tests all 22 health and wellness tools using FastMCP integration with mocked Garmin API responses.
"""
import datetime
import json

import pytest
from unittest.mock import Mock
from mcp.server.fastmcp import FastMCP

from garmin_mcp import health_wellness
from tests.fixtures.garmin_responses import (
    MOCK_STATS,
    MOCK_USER_SUMMARY,
    MOCK_BODY_COMPOSITION,
    MOCK_STEPS_DATA,
    MOCK_DAILY_STEPS,
    MOCK_TRAINING_READINESS,
    MOCK_BODY_BATTERY,
    MOCK_BODY_BATTERY_EVENTS,
    MOCK_BLOOD_PRESSURE,
    MOCK_FLOORS,
    MOCK_RHR_DAY,
    MOCK_HEART_RATES,
    MOCK_HYDRATION_DATA,
    MOCK_SLEEP_DATA,
    MOCK_STRESS_DATA,
    MOCK_RESPIRATION_DATA,
    MOCK_SPO2_DATA,
    MOCK_LIFESTYLE_LOGGING_DATA,
    MOCK_WEEKLY_STEPS,
    MOCK_WEEKLY_STRESS,
    MOCK_WEEKLY_INTENSITY_MINUTES,
    MOCK_MORNING_TRAINING_READINESS,
)


@pytest.fixture
def app_with_health_wellness(mock_garmin_client):
    """Create FastMCP app with health_wellness tools registered"""
    health_wellness.configure(mock_garmin_client)
    app = FastMCP("Test Health Wellness")
    app = health_wellness.register_tools(app)
    return app


@pytest.mark.asyncio
async def test_get_stats_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_stats tool returns daily activity stats"""
    # Setup mock
    mock_garmin_client.get_stats.return_value = MOCK_STATS

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_stats",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_stats.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_stats_range_tool(app_with_health_wellness, mock_garmin_client):
    """Curates calories + steps into one per-day entry and flags today as partial.

    Response shapes mirror what Garmin's /usersummary-service/stats/daily
    actually returns: one CALORIES call and one STEPS call, each with a
    "values" list keyed by calendarDate; a day with no device data (e.g. a
    future date) is simply absent from that list rather than null/zeroed.
    """
    today = datetime.date.today().isoformat()
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    calories_resp = {
        "values": [
            {
                "calendarDate": yesterday,
                "values": {"restingCalories": 2081, "totalCalories": 2656, "activeCalories": 575},
            },
            {
                "calendarDate": today,
                "values": {"restingCalories": 1393, "totalCalories": 1900, "activeCalories": 507},
            },
        ]
    }
    steps_resp = {
        "values": [
            {"calendarDate": yesterday, "values": {"totalSteps": 9238}},
            # today has no STEPS entry yet -- still has_data via calories
        ]
    }
    mock_garmin_client.connectapi.side_effect = [calories_resp, steps_resp]

    result = await app_with_health_wellness.call_tool(
        "get_stats_range",
        {"start_date": yesterday, "end_date": today},
    )

    data = json.loads(result[0][0].text)
    days = {d["date"]: d for d in data["days"]}

    past_day = days[yesterday]
    assert past_day["total_calories"] == 2656
    assert past_day["active_calories"] == 575
    assert past_day["resting_calories"] == 2081
    assert past_day["steps"] == 9238
    assert past_day["has_data"] is True
    assert past_day["is_partial"] is False

    today_entry = days[today]
    assert today_entry["total_calories"] == 1900
    assert today_entry["steps"] is None  # no STEPS entry for today yet
    assert today_entry["has_data"] is True
    assert today_entry["is_partial"] is True


@pytest.mark.asyncio
async def test_get_stats_range_no_data_day(app_with_health_wellness, mock_garmin_client):
    """A day absent from both CALORIES and STEPS responses is has_data: false with nulls."""
    mock_garmin_client.connectapi.side_effect = [
        {"values": [{"calendarDate": "2024-01-14", "values": {"restingCalories": 2081, "totalCalories": 2656, "activeCalories": 575}}]},
        {"values": [{"calendarDate": "2024-01-14", "values": {"totalSteps": 9238}}]},
    ]

    result = await app_with_health_wellness.call_tool(
        "get_stats_range",
        {"start_date": "2024-01-14", "end_date": "2024-01-15"},
    )

    data = json.loads(result[0][0].text)
    days = {d["date"]: d for d in data["days"]}
    missing_day = days["2024-01-15"]
    assert missing_day["has_data"] is False
    assert missing_day["total_calories"] is None
    assert missing_day["active_calories"] is None
    assert missing_day["resting_calories"] is None
    assert missing_day["steps"] is None


@pytest.mark.asyncio
async def test_get_stats_range_rejects_oversized_range(app_with_health_wellness, mock_garmin_client):
    """The tool enforces Garmin's own 28-day cap before making a request."""
    result = await app_with_health_wellness.call_tool(
        "get_stats_range",
        {"start_date": "2024-01-01", "end_date": "2024-03-01"},
    )
    assert "too large" in result[0][0].text
    mock_garmin_client.connectapi.assert_not_called()


@pytest.mark.asyncio
async def test_get_stats_range_rejects_inverted_range(app_with_health_wellness, mock_garmin_client):
    result = await app_with_health_wellness.call_tool(
        "get_stats_range",
        {"start_date": "2024-01-15", "end_date": "2024-01-01"},
    )
    assert "end_date must be on or after start_date" in result[0][0].text
    mock_garmin_client.connectapi.assert_not_called()


@pytest.mark.asyncio
async def test_get_stats_range_error(app_with_health_wellness, mock_garmin_client):
    mock_garmin_client.connectapi.side_effect = Exception("API error")
    result = await app_with_health_wellness.call_tool(
        "get_stats_range",
        {"start_date": "2024-01-14", "end_date": "2024-01-15"},
    )
    assert "Error retrieving stats range" in result[0][0].text


def _energy_balance_intake_resp(days):
    """Build a /nutrition-service/food/logs/range-shaped response.

    days: list of (mealDate, calories_or_None, item_count) -- calories_or_None
    is None for an unlogged day, matching Garmin's real behavior of omitting
    dailyNutritionContent entirely rather than sending a null/zero.
    """
    summaries = []
    for date_str, calories, item_count in days:
        entry = {"mealDate": date_str, "mealDetails": [{"loggedFoods": [{}] * item_count}]}
        if calories is not None:
            entry["dailyNutritionContent"] = {"calories": calories}
        summaries.append(entry)
    return {"dailyNutritionSummaries": summaries}


def _energy_balance_calories_resp(days):
    """Build a /usersummary-service/stats/daily-shaped CALORIES response.

    days: list of (calendarDate, totalCalories) tuples.
    """
    return {
        "values": [
            {"calendarDate": date_str, "values": {"totalCalories": total}}
            for date_str, total in days
        ]
    }


@pytest.mark.asyncio
async def test_get_energy_balance_derives_tdee(app_with_health_wellness, mock_garmin_client):
    """Full happy path: 2 qualifying body-comp readings yield a derived TDEE.

    Numbers are simplified round figures chosen so the deficit math is easy
    to hand-check: losing 1 kg fat and gaining 0 kg lean over 10 days at
    7700 kcal/kg implies a 770 kcal/day deficit; mean intake 2000 -> measured
    TDEE 2770.
    """
    intake_resp = _energy_balance_intake_resp([
        ("2024-01-01", None, 0),  # unlogged -- excluded from mean
        ("2024-01-02", 2000, 5),
        ("2024-01-03", 2000, 4),
    ])
    calories_resp = _energy_balance_calories_resp([
        ("2024-01-01", 2500),
        ("2024-01-02", 2500),
        # 2024-01-03 absent -- no device data that day, excluded from mean
    ])
    mock_garmin_client.connectapi.side_effect = [intake_resp, calories_resp]
    mock_garmin_client.get_body_composition.return_value = {
        "dateWeightList": [
            {"calendarDate": "2024-01-01", "weight": 80000.0, "bodyFat": 20.0},
            {"calendarDate": "2024-01-11", "weight": 79000.0, "bodyFat": 18.75},
            # weight-only manual entry -- skipped, no bodyFat
            {"calendarDate": "2024-01-05", "weight": 79500.0, "bodyFat": None},
        ]
    }

    result = await app_with_health_wellness.call_tool(
        "get_energy_balance",
        {"start_date": "2024-01-01", "end_date": "2024-01-03"},
    )
    data = json.loads(result[0][0].text)

    assert data["intake"]["days_included"] == 2
    assert data["intake"]["days_excluded_unlogged"] == 1
    assert data["intake"]["mean_calories_per_day"] == 2000.0
    assert data["intake"]["low_item_count_dates"] == []

    assert data["expenditure_garmin"]["days_included"] == 2
    assert data["expenditure_garmin"]["days_excluded"] == 1
    assert data["expenditure_garmin"]["mean_total_calories_per_day"] == 2500.0

    bc = data["body_composition"]
    assert bc["first_reading_date"] == "2024-01-01"
    assert bc["last_reading_date"] == "2024-01-11"
    assert bc["weight_change_kg"] == -1.0
    # fat: 80*0.20=16.0 -> 79*0.1875=14.8125, change -1.1875
    assert bc["fat_mass_change_kg"] == pytest.approx(-1.1875, abs=0.001)
    # lean: 64.0 -> 64.1875, change +0.1875
    assert bc["lean_mass_change_kg"] == pytest.approx(0.1875, abs=0.001)

    derived = data["derived"]
    # implied_deficit = -((-1.1875*7700) + (0.1875*1800)) / 10 = (9143.75 - 337.5)/10 = 880.625
    assert derived["implied_deficit_kcal_per_day"] == pytest.approx(880.625, abs=0.1)
    assert derived["composition_derived_tdee_kcal_per_day"] == pytest.approx(2880.625, abs=0.1)
    assert derived["garmin_tdee_kcal_per_day"] == 2500.0
    # Exactly 2 readings -- no residual variance to estimate uncertainty from.
    assert derived["implied_deficit_uncertainty_kcal_per_day"] is None
    assert "assumptions" in data
    assert data["assumptions"]["fat_kcal_per_kg"] == 7700


@pytest.mark.asyncio
async def test_get_energy_balance_excludes_partial_and_no_data_days(
    app_with_health_wellness, mock_garmin_client
):
    """The current (partial) day and a day with no device data are both
    excluded from the expenditure mean -- exercises the two branches of
    get_stats_range's has_data/is_partial exclusion logic that the earlier
    fixed-2024-date tests never touched (since "today" never equals a
    hardcoded past date).
    """
    today = datetime.date.today().isoformat()
    no_device_data_date = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    complete_date = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()

    mock_garmin_client.connectapi.side_effect = [
        _energy_balance_intake_resp([
            (complete_date, 2000, 5),
            (no_device_data_date, 2000, 5),
            (today, 2000, 5),
        ]),
        # no_device_data_date has no entry at all -- absent from Garmin's response
        _energy_balance_calories_resp([
            (complete_date, 2600),
            (today, 9999),  # partial-day accumulator -- must not enter the mean
        ]),
    ]
    mock_garmin_client.get_body_composition.return_value = {"dateWeightList": []}

    result = await app_with_health_wellness.call_tool(
        "get_energy_balance",
        {"start_date": complete_date, "end_date": today},
    )
    data = json.loads(result[0][0].text)

    assert data["expenditure_garmin"]["days_included"] == 1
    assert data["expenditure_garmin"]["days_excluded"] == 2
    assert data["expenditure_garmin"]["mean_total_calories_per_day"] == 2600.0


@pytest.mark.asyncio
async def test_get_energy_balance_surfaces_low_item_count_days(
    app_with_health_wellness, mock_garmin_client
):
    """Partially-logged days (nonzero but low item_count) stay in the intake
    mean but are called out in low_item_count_dates, per the tool's own
    documented contract -- they are not silently excluded or blended in
    without a trace.
    """
    mock_garmin_client.connectapi.side_effect = [
        _energy_balance_intake_resp([
            ("2024-01-01", 2000, 5),
            ("2024-01-02", 800, 1),  # breakfast-only -- low confidence, still counted
            ("2024-01-03", None, 0),  # fully unlogged -- excluded entirely
        ]),
        _energy_balance_calories_resp([]),
    ]
    mock_garmin_client.get_body_composition.return_value = {"dateWeightList": []}

    result = await app_with_health_wellness.call_tool(
        "get_energy_balance",
        {"start_date": "2024-01-01", "end_date": "2024-01-03"},
    )
    data = json.loads(result[0][0].text)

    assert data["intake"]["days_included"] == 2  # both logged days, including the low-count one
    assert data["intake"]["days_excluded_unlogged"] == 1
    assert data["intake"]["low_item_count_dates"] == ["2024-01-02"]
    assert data["intake"]["mean_calories_per_day"] == 1400.0  # (2000+800)/2, unaffected


@pytest.mark.asyncio
async def test_get_energy_balance_fits_trend_across_multiple_readings(
    app_with_health_wellness, mock_garmin_client
):
    """With more than 2 body-composition readings, the trend is fit by
    least-squares regression across all of them, and an uncertainty band on
    the deficit is returned -- both were previously impossible with the
    endpoint-difference approach.
    """
    mock_garmin_client.connectapi.side_effect = [
        _energy_balance_intake_resp([("2024-01-01", 2000, 5)]),
        _energy_balance_calories_resp([("2024-01-01", 2600)]),
    ]
    # Weight declines perfectly linearly across 5 readings 5 days apart, at a
    # constant body-fat % -- fat_kg (weight_kg * constant) is then also
    # exactly linear, so the fit has zero residual and the standard error
    # should come out as (numerically) zero, not just "small". A varying
    # body-fat % would make fat_kg a product of two linear terms -- which is
    # quadratic, not linear -- and give a nonzero residual on its own, which
    # would defeat the point of this specific check.
    mock_garmin_client.get_body_composition.return_value = {
        "dateWeightList": [
            {"calendarDate": "2024-01-01", "weight": 80000.0, "bodyFat": 20.0},
            {"calendarDate": "2024-01-06", "weight": 79500.0, "bodyFat": 20.0},
            {"calendarDate": "2024-01-11", "weight": 79000.0, "bodyFat": 20.0},
            {"calendarDate": "2024-01-16", "weight": 78500.0, "bodyFat": 20.0},
            {"calendarDate": "2024-01-21", "weight": 78000.0, "bodyFat": 20.0},
        ]
    }

    result = await app_with_health_wellness.call_tool(
        "get_energy_balance",
        {"start_date": "2024-01-01", "end_date": "2024-01-21"},
    )
    data = json.loads(result[0][0].text)

    bc = data["body_composition"]
    assert bc["readings_used"] == 5
    # weight declines exactly 500g / 5 days = 100 g/day -> 2.0 kg over 20 days
    assert bc["weight_change_kg"] == pytest.approx(-2.0, abs=0.01)

    derived = data["derived"]
    # A perfectly linear fit has zero residual, so the standard error is 0,
    # not None -- distinct from the exactly-2-readings case.
    assert derived["implied_deficit_uncertainty_kcal_per_day"] == pytest.approx(0.0, abs=0.01)
    assert "uncertainty_note" not in derived


@pytest.mark.asyncio
async def test_get_energy_balance_single_body_comp_reading(app_with_health_wellness, mock_garmin_client):
    """With only 1 qualifying reading, no TDEE is derived -- just a note."""
    mock_garmin_client.connectapi.side_effect = [
        _energy_balance_intake_resp([("2024-01-01", 2000, 3)]),
        _energy_balance_calories_resp([("2024-01-01", 2500)]),
    ]
    mock_garmin_client.get_body_composition.return_value = {
        "dateWeightList": [{"calendarDate": "2024-01-01", "weight": 80000.0, "bodyFat": 20.0}]
    }

    result = await app_with_health_wellness.call_tool(
        "get_energy_balance",
        {"start_date": "2024-01-01", "end_date": "2024-01-01"},
    )
    data = json.loads(result[0][0].text)
    assert "derived" not in data
    assert "body_composition_note" in data


@pytest.mark.asyncio
async def test_get_energy_balance_chunks_expenditure_over_28_days(
    app_with_health_wellness, mock_garmin_client
):
    """A range longer than the stats endpoint's 28-day cap is split into chunks."""
    mock_garmin_client.connectapi.side_effect = [
        _energy_balance_intake_resp([]),
        _energy_balance_calories_resp([("2024-01-01", 2500)]),  # chunk 1
        _energy_balance_calories_resp([("2024-02-05", 2600)]),  # chunk 2
    ]
    mock_garmin_client.get_body_composition.return_value = {"dateWeightList": []}

    result = await app_with_health_wellness.call_tool(
        "get_energy_balance",
        {"start_date": "2024-01-01", "end_date": "2024-02-05"},  # 36 days
    )
    data = json.loads(result[0][0].text)
    assert mock_garmin_client.connectapi.call_count == 3
    assert data["expenditure_garmin"]["days_included"] == 2
    assert data["expenditure_garmin"]["mean_total_calories_per_day"] == 2550.0


@pytest.mark.asyncio
async def test_get_energy_balance_rejects_oversized_range(app_with_health_wellness, mock_garmin_client):
    result = await app_with_health_wellness.call_tool(
        "get_energy_balance",
        {"start_date": "2024-01-01", "end_date": "2024-04-01"},
    )
    assert "too large" in result[0][0].text
    mock_garmin_client.connectapi.assert_not_called()


@pytest.mark.asyncio
async def test_get_energy_balance_rejects_inverted_range(app_with_health_wellness, mock_garmin_client):
    result = await app_with_health_wellness.call_tool(
        "get_energy_balance",
        {"start_date": "2024-01-15", "end_date": "2024-01-01"},
    )
    assert "end_date must be on or after start_date" in result[0][0].text
    mock_garmin_client.connectapi.assert_not_called()


@pytest.mark.asyncio
async def test_get_energy_balance_no_usable_data(app_with_health_wellness, mock_garmin_client):
    mock_garmin_client.connectapi.side_effect = [
        _energy_balance_intake_resp([]),
        _energy_balance_calories_resp([]),
    ]
    mock_garmin_client.get_body_composition.return_value = {"dateWeightList": []}

    result = await app_with_health_wellness.call_tool(
        "get_energy_balance",
        {"start_date": "2024-01-01", "end_date": "2024-01-03"},
    )
    assert "No usable data found" in result[0][0].text


@pytest.mark.asyncio
async def test_get_energy_balance_error(app_with_health_wellness, mock_garmin_client):
    mock_garmin_client.connectapi.side_effect = Exception("API error")
    result = await app_with_health_wellness.call_tool(
        "get_energy_balance",
        {"start_date": "2024-01-01", "end_date": "2024-01-03"},
    )
    assert "Error retrieving energy balance data" in result[0][0].text


@pytest.mark.asyncio
async def test_get_user_summary_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_user_summary tool returns user summary data"""
    # Setup mock
    mock_garmin_client.get_user_summary.return_value = MOCK_USER_SUMMARY

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_user_summary",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_user_summary.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_body_composition_single_date(app_with_health_wellness, mock_garmin_client):
    """Test get_body_composition tool with single date"""
    # Setup mock
    mock_garmin_client.get_body_composition.return_value = MOCK_BODY_COMPOSITION

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_body_composition",
        {"start_date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_body_composition.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_body_composition_date_range(app_with_health_wellness, mock_garmin_client):
    """Test get_body_composition tool with date range"""
    # Setup mock
    mock_garmin_client.get_body_composition.return_value = MOCK_BODY_COMPOSITION

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_body_composition",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_body_composition.assert_called_once_with("2024-01-08", "2024-01-15")


@pytest.mark.asyncio
async def test_get_stats_and_body_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_stats_and_body tool returns combined data"""
    # Setup mock
    combined_data = {**MOCK_STATS, **MOCK_BODY_COMPOSITION}
    mock_garmin_client.get_stats_and_body.return_value = combined_data

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_stats_and_body",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_stats_and_body.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_steps_data_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_steps_data tool returns steps data"""
    # Setup mock
    mock_garmin_client.get_steps_data.return_value = MOCK_STEPS_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_steps_data",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_steps_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_daily_steps_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_daily_steps tool returns steps for date range"""
    # Setup mock
    mock_garmin_client.get_daily_steps.return_value = MOCK_DAILY_STEPS

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_daily_steps",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_daily_steps.assert_called_once_with("2024-01-08", "2024-01-15")


@pytest.mark.asyncio
async def test_get_training_readiness_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_training_readiness tool returns readiness data"""
    # Setup mock
    mock_garmin_client.get_training_readiness.return_value = MOCK_TRAINING_READINESS

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_training_readiness",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_training_readiness.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_body_battery_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_body_battery tool returns battery data"""
    # Setup mock
    mock_garmin_client.get_body_battery.return_value = MOCK_BODY_BATTERY

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_body_battery",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_body_battery.assert_called_once_with("2024-01-08", "2024-01-15")


@pytest.mark.asyncio
async def test_get_body_battery_events_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_body_battery_events tool returns battery events"""
    # Setup mock
    mock_garmin_client.get_body_battery_events.return_value = MOCK_BODY_BATTERY_EVENTS

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_body_battery_events",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_body_battery_events.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_blood_pressure_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_blood_pressure tool returns blood pressure data"""
    # Setup mock
    mock_garmin_client.get_blood_pressure.return_value = MOCK_BLOOD_PRESSURE

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_blood_pressure",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_blood_pressure.assert_called_once_with("2024-01-08", "2024-01-15")


@pytest.mark.asyncio
async def test_get_floors_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_floors tool returns floors climbed data"""
    # Setup mock
    mock_garmin_client.get_floors.return_value = MOCK_FLOORS

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_floors",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_floors.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_rhr_day_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_rhr_day tool returns resting heart rate"""
    # Setup mock
    mock_garmin_client.get_rhr_day.return_value = MOCK_RHR_DAY

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_rhr_day",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_rhr_day.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_heart_rates_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_heart_rates tool returns heart rate data"""
    # Setup mock
    mock_garmin_client.get_heart_rates.return_value = MOCK_HEART_RATES

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_heart_rates",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_heart_rates.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_heart_rates_summary_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_heart_rates_summary tool returns lightweight heart rate summary"""
    # Setup mock
    mock_garmin_client.get_heart_rates.return_value = MOCK_HEART_RATES

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_heart_rates_summary",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    # Note: get_heart_rates_summary calls get_heart_rates internally
    mock_garmin_client.get_heart_rates.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_hydration_data_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_hydration_data tool returns hydration data"""
    # Setup mock
    mock_garmin_client.get_hydration_data.return_value = MOCK_HYDRATION_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_hydration_data",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_hydration_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_sleep_data_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_sleep_data tool returns sleep data"""
    # Setup mock
    mock_garmin_client.get_sleep_data.return_value = MOCK_SLEEP_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_sleep_data",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_sleep_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_sleep_summary_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_sleep_summary tool returns lightweight sleep summary"""
    # Setup mock
    mock_garmin_client.get_sleep_data.return_value = MOCK_SLEEP_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_sleep_summary",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    # Note: get_sleep_summary calls get_sleep_data internally
    mock_garmin_client.get_sleep_data.assert_called_once_with("2024-01-15")

    # Verify it's a summary (smaller than full sleep data)
    # The summary should contain key metrics but not the full time-series data


@pytest.mark.asyncio
async def test_get_sleep_summary_range_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_sleep_summary_range returns one curated summary per night"""
    # Setup mock: return sleep data for each of the 3 requested nights
    mock_garmin_client.get_sleep_data.side_effect = lambda date: {
        **MOCK_SLEEP_DATA,
        "dailySleepDTO": {**MOCK_SLEEP_DATA["dailySleepDTO"], "calendarDate": date},
    }

    result = await app_with_health_wellness.call_tool(
        "get_sleep_summary_range",
        {"start_date": "2024-01-13", "end_date": "2024-01-15"},
    )

    assert result is not None
    data = json.loads(result[0][0].text)
    assert data["start_date"] == "2024-01-13"
    assert data["end_date"] == "2024-01-15"
    assert data["nights_requested"] == 3
    assert data["nights_returned"] == 3
    assert [n["date"] for n in data["nights"]] == [
        "2024-01-13",
        "2024-01-14",
        "2024-01-15",
    ]
    # Each night should carry the same curated fields as get_sleep_summary
    assert data["nights"][0]["sleep_score"] == 85
    assert data["nights"][0]["sleep_hours"] == 8.0
    assert mock_garmin_client.get_sleep_data.call_count == 3


@pytest.mark.asyncio
async def test_get_sleep_summary_range_skips_nights_without_data(
    app_with_health_wellness, mock_garmin_client
):
    """Nights with no Garmin data (or a per-night error) are skipped, not fatal"""

    def side_effect(date):
        if date == "2024-01-14":
            return None
        if date == "2024-01-15":
            raise Exception("transient API error")
        return MOCK_SLEEP_DATA

    mock_garmin_client.get_sleep_data.side_effect = side_effect

    result = await app_with_health_wellness.call_tool(
        "get_sleep_summary_range",
        {"start_date": "2024-01-13", "end_date": "2024-01-15"},
    )

    data = json.loads(result[0][0].text)
    assert data["nights_requested"] == 3
    assert data["nights_returned"] == 1
    assert data["nights"][0]["date"] == "2024-01-13"


@pytest.mark.asyncio
async def test_get_sleep_summary_range_rejects_invalid_dates(
    app_with_health_wellness, mock_garmin_client
):
    """Malformed dates and inverted ranges return a clear error, no API calls"""
    result = await app_with_health_wellness.call_tool(
        "get_sleep_summary_range",
        {"start_date": "not-a-date", "end_date": "2024-01-15"},
    )
    assert "Invalid date format" in result[0][0].text

    result = await app_with_health_wellness.call_tool(
        "get_sleep_summary_range",
        {"start_date": "2024-01-15", "end_date": "2024-01-13"},
    )
    assert "end_date must be on or after start_date" in result[0][0].text

    mock_garmin_client.get_sleep_data.assert_not_called()


@pytest.mark.asyncio
async def test_get_sleep_summary_range_enforces_max_days(
    app_with_health_wellness, mock_garmin_client
):
    """Ranges over 90 days are rejected before making any API calls"""
    result = await app_with_health_wellness.call_tool(
        "get_sleep_summary_range",
        {"start_date": "2024-01-01", "end_date": "2024-04-15"},  # 106 days
    )
    assert "too large" in result[0][0].text
    mock_garmin_client.get_sleep_data.assert_not_called()


@pytest.mark.asyncio
async def test_get_stress_data_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_stress_data tool returns stress data"""
    # Setup mock
    mock_garmin_client.get_stress_data.return_value = MOCK_STRESS_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_stress_data",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_stress_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_stress_summary_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_stress_summary tool returns lightweight stress summary"""
    # Setup mock
    mock_garmin_client.get_stress_data.return_value = MOCK_STRESS_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_stress_summary",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    # Note: get_stress_summary calls get_stress_data internally
    mock_garmin_client.get_stress_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_respiration_data_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_respiration_data tool returns respiration data"""
    # Setup mock
    mock_garmin_client.get_respiration_data.return_value = MOCK_RESPIRATION_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_respiration_data",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_respiration_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_respiration_summary_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_respiration_summary tool returns lightweight respiration summary"""
    # Setup mock
    mock_garmin_client.get_respiration_data.return_value = MOCK_RESPIRATION_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_respiration_summary",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    # Note: get_respiration_summary calls get_respiration_data internally
    mock_garmin_client.get_respiration_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_spo2_data_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_spo2_data tool returns SpO2 data"""
    # Setup mock
    mock_garmin_client.get_spo2_data.return_value = MOCK_SPO2_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_spo2_data",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_spo2_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_all_day_stress_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_all_day_stress tool returns all-day stress data"""
    # Setup mock
    mock_garmin_client.get_all_day_stress.return_value = MOCK_STRESS_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_all_day_stress",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_all_day_stress.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_all_day_events_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_all_day_events tool returns daily wellness events"""
    # Setup mock
    mock_events = {"events": [{"type": "STRESS", "timestamp": 1705276800000}]}
    mock_garmin_client.get_all_day_events.return_value = mock_events

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_all_day_events",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_all_day_events.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_lifestyle_logging_data_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_lifestyle_logging_data tool returns lifestyle logging data"""
    # Setup mock
    mock_garmin_client.get_lifestyle_logging_data.return_value = MOCK_LIFESTYLE_LOGGING_DATA

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_lifestyle_logging_data",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_lifestyle_logging_data.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_weekly_steps_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_weekly_steps tool returns weekly step data"""
    # Setup mock
    mock_garmin_client.get_weekly_steps.return_value = MOCK_WEEKLY_STEPS

    # Call tool with end_date and weeks parameters
    result = await app_with_health_wellness.call_tool(
        "get_weekly_steps",
        {"end_date": "2024-01-10", "weeks": 4}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_weekly_steps.assert_called_once_with("2024-01-10", 4)


@pytest.mark.asyncio
async def test_get_weekly_steps_tool_default_weeks(app_with_health_wellness, mock_garmin_client):
    """Test get_weekly_steps tool with default weeks parameter"""
    mock_garmin_client.get_weekly_steps.return_value = MOCK_WEEKLY_STEPS

    result = await app_with_health_wellness.call_tool(
        "get_weekly_steps",
        {"end_date": "2024-01-10"}
    )

    assert result is not None
    mock_garmin_client.get_weekly_steps.assert_called_once_with("2024-01-10", 4)


@pytest.mark.asyncio
async def test_get_weekly_stress_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_weekly_stress tool returns weekly stress data"""
    # Setup mock
    mock_garmin_client.get_weekly_stress.return_value = MOCK_WEEKLY_STRESS

    # Call tool with end_date and weeks parameters
    result = await app_with_health_wellness.call_tool(
        "get_weekly_stress",
        {"end_date": "2024-01-10", "weeks": 4}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_weekly_stress.assert_called_once_with("2024-01-10", 4)


@pytest.mark.asyncio
async def test_get_weekly_intensity_minutes_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_weekly_intensity_minutes tool returns weekly intensity data"""
    # Setup mock
    mock_garmin_client.get_weekly_intensity_minutes.return_value = MOCK_WEEKLY_INTENSITY_MINUTES

    # Call tool with end_date and weeks parameters
    result = await app_with_health_wellness.call_tool(
        "get_weekly_intensity_minutes",
        {"end_date": "2024-01-10", "weeks": 2}
    )

    # Verify - weeks=2 means start_date is 13 days back (2*7-1=13)
    # From 2024-01-10, 13 days back is 2023-12-28
    assert result is not None
    mock_garmin_client.get_weekly_intensity_minutes.assert_called_once_with("2023-12-28", "2024-01-10")


@pytest.mark.asyncio
async def test_get_weekly_intensity_minutes_tool_default_weeks(app_with_health_wellness, mock_garmin_client):
    """Test get_weekly_intensity_minutes tool with default weeks parameter"""
    # Setup mock
    mock_garmin_client.get_weekly_intensity_minutes.return_value = MOCK_WEEKLY_INTENSITY_MINUTES

    # Call tool with only end_date (weeks defaults to 4)
    result = await app_with_health_wellness.call_tool(
        "get_weekly_intensity_minutes",
        {"end_date": "2024-01-10"}
    )

    # Verify - weeks=4 means start_date is 27 days back (4*7-1=27)
    # From 2024-01-10, 27 days back is 2023-12-14
    assert result is not None
    mock_garmin_client.get_weekly_intensity_minutes.assert_called_once_with("2023-12-14", "2024-01-10")


@pytest.mark.asyncio
async def test_get_morning_training_readiness_tool(app_with_health_wellness, mock_garmin_client):
    """Test get_morning_training_readiness tool returns morning readiness data"""
    # Setup mock
    mock_garmin_client.get_morning_training_readiness.return_value = MOCK_MORNING_TRAINING_READINESS

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_morning_training_readiness",
        {"date": "2024-01-15"}
    )

    # Verify
    assert result is not None
    mock_garmin_client.get_morning_training_readiness.assert_called_once_with("2024-01-15")


# Error handling tests
@pytest.mark.asyncio
async def test_get_steps_data_no_data(app_with_health_wellness, mock_garmin_client):
    """Test get_steps_data tool when no data is available"""
    # Setup mock to return None
    mock_garmin_client.get_steps_data.return_value = None

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_steps_data",
        {"date": "2024-01-15"}
    )

    # Verify error message is returned
    assert result is not None
    # The tool should return a helpful message when no data is found


@pytest.mark.asyncio
async def test_get_sleep_data_exception(app_with_health_wellness, mock_garmin_client):
    """Test get_sleep_data tool when API raises exception"""
    # Setup mock to raise exception
    mock_garmin_client.get_sleep_data.side_effect = Exception("API Error")

    # Call tool
    result = await app_with_health_wellness.call_tool(
        "get_sleep_data",
        {"date": "2024-01-15"}
    )

    # Verify error is handled gracefully
    assert result is not None
    # The tool should return an error message, not crash


@pytest.mark.asyncio
async def test_get_sleep_summary_handles_null_sleep_scores(app_with_health_wellness, mock_garmin_client):
    """A present sleep record with a null sleepScores block must not crash.

    `daily_sleep.get('sleepScores', {})` returns None when Garmin sends an
    explicit null, so the chained `.get('overall', {}).get('value')` raised
    "'NoneType' object has no attribute 'get'".
    """
    mock_garmin_client.get_sleep_data.return_value = {
        "dailySleepDTO": {
            "sleepTimeSeconds": 28800,
            "deepSleepSeconds": 7200,
            "lightSleepSeconds": 14400,
            "remSleepSeconds": 7200,
            "awakeSleepSeconds": 0,
            "sleepScores": None,
        },
    }

    result = await app_with_health_wellness.call_tool(
        "get_sleep_summary",
        {"date": "2024-01-15"},
    )
    text = result[0][0].text
    assert "NoneType" not in text
    assert "Error" not in text
    assert "28800" in text  # the rest of the summary still surfaces


@pytest.mark.asyncio
async def test_get_sleep_summary_handles_null_sleep_phases(app_with_health_wellness, mock_garmin_client):
    """A night with a total duration but null phase breakdowns must not crash.

    The phase keys are always written into `summary` (as None when Garmin omits
    the breakdown), so `summary.get('deep_sleep_seconds', 0)` never falls back
    to the 0 default and the percentage math raised
    "unsupported operand type(s) for /: 'NoneType' and 'int'".
    """
    mock_garmin_client.get_sleep_data.return_value = {
        "dailySleepDTO": {
            "sleepTimeSeconds": 28800,
            "deepSleepSeconds": None,
            "lightSleepSeconds": None,
            "remSleepSeconds": None,
            "awakeSleepSeconds": None,
            "sleepScores": {"overall": {"value": 85, "qualifierKey": "GOOD"}},
        },
    }

    result = await app_with_health_wellness.call_tool(
        "get_sleep_summary",
        {"date": "2024-01-15"},
    )
    text = result[0][0].text
    assert "NoneType" not in text
    assert "Error" not in text
    data = json.loads(text)
    # The night still reports what Garmin did send ...
    assert data["sleep_seconds"] == 28800
    assert data["sleep_hours"] == 8.0
    assert data["sleep_score"] == 85
    # ... and unmeasured phases are omitted rather than reported as 0%.
    assert "deep_sleep_percent" not in data
    assert "light_sleep_percent" not in data
    assert "rem_sleep_percent" not in data


@pytest.mark.asyncio
async def test_get_sleep_summary_handles_partial_sleep_phases(app_with_health_wellness, mock_garmin_client):
    """A night with only some phases measured reports percentages for those."""
    mock_garmin_client.get_sleep_data.return_value = {
        "dailySleepDTO": {
            "sleepTimeSeconds": 28800,
            "deepSleepSeconds": 7200,
            "lightSleepSeconds": None,
            "remSleepSeconds": None,
            "awakeSleepSeconds": 0,
        },
    }

    result = await app_with_health_wellness.call_tool(
        "get_sleep_summary",
        {"date": "2024-01-15"},
    )
    text = result[0][0].text
    assert "NoneType" not in text
    data = json.loads(text)
    assert data["deep_sleep_percent"] == 25.0
    assert "light_sleep_percent" not in data
    assert "rem_sleep_percent" not in data


@pytest.mark.asyncio
async def test_get_body_battery_handles_null_activity_events(app_with_health_wellness, mock_garmin_client):
    """A day whose bodyBatteryActivityEvent is an explicit null must not crash.

    `day.get('bodyBatteryActivityEvent', [])` returns None when the key is
    present but null, so `for event in ...` raised
    "'NoneType' object is not iterable".
    """
    mock_garmin_client.get_body_battery.return_value = [
        {
            "date": "2024-01-15",
            "charged": 100,
            "drained": 20,
            "bodyBatteryActivityEvent": None,
            "bodyBatteryDynamicFeedbackEvent": None,
        }
    ]

    result = await app_with_health_wellness.call_tool(
        "get_body_battery",
        {"start_date": "2024-01-15", "end_date": "2024-01-15"},
    )
    text = result[0][0].text
    assert "NoneType" not in text
    assert "Error" not in text
    assert "100" in text
