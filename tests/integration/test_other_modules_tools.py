"""
Integration tests for remaining module MCP tools

Tests tools from:
- devices (7 tools)
- weight_management (5 tools)
- user_profile (6 tools)
- data_management (3 tools)
- gear_management (3 tools)
- womens_health (3 tools)
Total: 26 tools. Recent activity listing is tested with activity_management.
"""
import json
import logging
import requests

import pytest
from unittest.mock import Mock
from mcp.server.fastmcp import FastMCP
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from garmin_mcp import (
    devices,
    weight_management,
    user_profile,
    data_management,
    gear_management,
    womens_health
)
from tests.fixtures.garmin_responses import (
    MOCK_DEVICES,
    MOCK_DEVICE_SETTINGS,
    MOCK_DEVICE_LAST_USED,
    MOCK_DEVICE_SOLAR_DATA,
    MOCK_WEIGH_INS,
    MOCK_DAILY_WEIGH_INS,
    MOCK_USER_PROFILE,
    MOCK_UNIT_SYSTEM,
    MOCK_GEAR,
    MOCK_GEAR_V2,
    MOCK_GEAR_DEFAULTS,
    MOCK_GEAR_STATS,
    MOCK_MENSTRUAL_DATA,
    MOCK_ACTIVITIES,
)


# Devices module tests
@pytest.fixture
def app_with_devices(mock_garmin_client):
    """Create FastMCP app with devices tools registered"""
    devices.configure(mock_garmin_client)
    app = FastMCP("Test Devices")
    app = devices.register_tools(app)
    return app


@pytest.mark.asyncio
async def test_get_devices_tool(app_with_devices, mock_garmin_client):
    """Test get_devices tool"""
    mock_garmin_client.get_devices.return_value = MOCK_DEVICES
    result = await app_with_devices.call_tool("get_devices", {})
    assert result is not None
    mock_garmin_client.get_devices.assert_called_once()


@pytest.mark.asyncio
async def test_get_device_last_used_tool(app_with_devices, mock_garmin_client):
    """Test get_device_last_used tool"""
    last_used = {"deviceId": 123456789, "lastUsed": "2024-01-15"}
    mock_garmin_client.get_device_last_used.return_value = last_used
    result = await app_with_devices.call_tool("get_device_last_used", {})
    assert result is not None
    mock_garmin_client.get_device_last_used.assert_called_once()


@pytest.mark.asyncio
async def test_get_device_settings_tool(app_with_devices, mock_garmin_client):
    """Test get_device_settings tool"""
    mock_garmin_client.get_device_settings.return_value = MOCK_DEVICE_SETTINGS
    result = await app_with_devices.call_tool(
        "get_device_settings",
        {"device_id": "abc123456789"}
    )
    assert result is not None
    mock_garmin_client.get_device_settings.assert_called_once_with("abc123456789")


@pytest.mark.asyncio
async def test_get_device_settings_default_device(app_with_devices, mock_garmin_client):
    """When device_id is omitted, falls back to get_device_last_used()."""
    mock_garmin_client.get_device_last_used.return_value = {
        "userDeviceId": "abc123456789"
    }
    mock_garmin_client.get_device_settings.return_value = MOCK_DEVICE_SETTINGS
    result = await app_with_devices.call_tool("get_device_settings", {})
    assert result is not None
    mock_garmin_client.get_device_last_used.assert_called_once()
    mock_garmin_client.get_device_settings.assert_called_once_with("abc123456789")


@pytest.mark.asyncio
async def test_get_device_settings_default_device_missing(app_with_devices, mock_garmin_client):
    """When no last-used device exists, returns a clear error string."""
    mock_garmin_client.get_device_last_used.return_value = None
    result = await app_with_devices.call_tool("get_device_settings", {})
    text = result[0][0].text
    assert "No default device found" in text
    mock_garmin_client.get_device_settings.assert_not_called()


@pytest.mark.asyncio
async def test_get_primary_training_device_tool(app_with_devices, mock_garmin_client):
    """Test get_primary_training_device tool"""
    primary_device = MOCK_DEVICES[0]
    mock_garmin_client.get_primary_training_device.return_value = primary_device
    result = await app_with_devices.call_tool("get_primary_training_device", {})
    assert result is not None
    mock_garmin_client.get_primary_training_device.assert_called_once()


@pytest.mark.asyncio
async def test_get_primary_training_device_handles_null_nested_sections(
    app_with_devices, mock_garmin_client
):
    """Optional device sections may be present as explicit nulls."""
    mock_garmin_client.get_primary_training_device.return_value = {
        "PrimaryTrainingDevice": None,
        "PrimaryTrainingDevices": None,
        "WearableDevices": None,
    }

    result = await app_with_devices.call_tool("get_primary_training_device", {})

    data = json.loads(result[0][0].text)
    assert data == {"primary_device_id": None}


@pytest.mark.asyncio
async def test_get_device_solar_data_tool(app_with_devices, mock_garmin_client):
    """Per-minute readings are summarised per day; an int deviceId (as get_devices returns it) is accepted"""
    mock_garmin_client.get_device_solar_data.return_value = MOCK_DEVICE_SOLAR_DATA
    result = await app_with_devices.call_tool(
        "get_device_solar_data",
        {"device_id": 123456789, "date": "2024-01-15"}
    )
    data = json.loads(result[0][0].text)
    assert data == {
        "device_id": 123456789,
        "solar_data": [
            {
                "date": "2024-01-15",
                "activity_time_gained_minutes": 30.0,
                "solar_utilization_avg_pct": 40.0,
                "solar_utilization_max_pct": 100.0,
                "minutes_with_solar_input": 3,
            }
        ],
    }
    mock_garmin_client.get_device_solar_data.assert_called_once_with(123456789, "2024-01-15")


@pytest.mark.asyncio
async def test_get_device_solar_data_non_solar_device(app_with_devices, mock_garmin_client):
    """The library's error for a response without solar input maps to a friendly message"""
    mock_garmin_client.get_device_solar_data.side_effect = GarminConnectConnectionError(
        "No device solar input data received"
    )
    result = await app_with_devices.call_tool(
        "get_device_solar_data",
        {"device_id": "123456789", "date": "2024-01-15"}
    )
    text = result[0][0].text
    assert "may not have solar capabilities" in text
    assert not text.startswith("Error")


@pytest.mark.asyncio
async def test_get_device_solar_data_other_error(app_with_devices, mock_garmin_client):
    """Other failures still surface as an error"""
    mock_garmin_client.get_device_solar_data.side_effect = GarminConnectConnectionError(
        "API Error 500 - "
    )
    result = await app_with_devices.call_tool(
        "get_device_solar_data",
        {"device_id": "123456789", "date": "2024-01-15"}
    )
    assert result[0][0].text == "Error retrieving solar data: API Error 500 - "


@pytest.mark.asyncio
async def test_get_device_alarms_tool(app_with_devices, mock_garmin_client):
    """Test get_device_alarms tool"""
    alarms = [{"alarmId": 1, "time": "07:00", "enabled": True}]
    mock_garmin_client.get_device_alarms.return_value = alarms
    result = await app_with_devices.call_tool(
        "get_device_alarms",
        {}
    )
    assert result is not None
    mock_garmin_client.get_device_alarms.assert_called_once()


@pytest.mark.asyncio
async def test_push_workout_to_device_explicit_device(app_with_devices, mock_garmin_client):
    """The workout is queued as a FIT message for the given device"""
    mock_garmin_client.get_workout_by_id.return_value = {
        "workoutId": 123456, "workoutName": "Upper Body A"
    }
    mock_garmin_client.client.post.return_value = Mock(status_code=200)

    result = await app_with_devices.call_tool(
        "push_workout_to_device", {"workout_id": 123456, "device_id": 3456789012}
    )

    data = json.loads(result[0][0].text)
    assert data["status"] == "success"
    assert data["device_id"] == 3456789012
    assert data["workout_name"] == "Upper Body A"
    mock_garmin_client.get_device_last_used.assert_not_called()
    args, kwargs = mock_garmin_client.client.post.call_args
    assert args == ("connectapi", "device-service/devicemessage/messages")
    assert kwargs["json"] == [{
        "deviceId": 3456789012,
        "messageUrl": "workout-service/workout/FIT/123456",
        "messageType": "workouts",
        "groupName": None,
        "messageName": "Upper Body A",
        "priority": 1,
        "fileType": "FIT",
        "metaDataId": 123456,
    }]


@pytest.mark.asyncio
async def test_push_workout_to_device_defaults_to_last_used(app_with_devices, mock_garmin_client):
    """Without device_id, the most recently used device is the target"""
    mock_garmin_client.get_device_last_used.return_value = MOCK_DEVICE_LAST_USED
    mock_garmin_client.get_workout_by_id.return_value = {"workoutName": "Easy Run"}
    mock_garmin_client.client.post.return_value = Mock(status_code=200)

    result = await app_with_devices.call_tool(
        "push_workout_to_device", {"workout_id": 123456}
    )

    data = json.loads(result[0][0].text)
    assert data["status"] == "success"
    assert data["device_id"] == MOCK_DEVICE_LAST_USED["userDeviceId"]
    payload = mock_garmin_client.client.post.call_args.kwargs["json"]
    assert payload[0]["deviceId"] == MOCK_DEVICE_LAST_USED["userDeviceId"]


@pytest.mark.asyncio
async def test_push_workout_to_device_no_default_device(app_with_devices, mock_garmin_client):
    """Nothing is sent when there is no device to default to"""
    mock_garmin_client.get_device_last_used.return_value = None

    result = await app_with_devices.call_tool(
        "push_workout_to_device", {"workout_id": 123456}
    )

    data = json.loads(result[0][0].text)
    assert data["status"] == "failed"
    assert "No default device" in data["message"]
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_push_workout_to_device_error(app_with_devices, mock_garmin_client):
    """A failed request is reported, not raised"""
    mock_garmin_client.get_workout_by_id.return_value = {"workoutName": "Easy Run"}
    mock_garmin_client.client.post.side_effect = GarminConnectConnectionError(
        "API Error 404"
    )

    result = await app_with_devices.call_tool(
        "push_workout_to_device", {"workout_id": 123456, "device_id": 3456789012}
    )

    data = json.loads(result[0][0].text)
    assert data["status"] == "failed"
    assert "404" in data["message"]


# Weight Management module tests
@pytest.fixture
def app_with_weight(mock_garmin_client):
    """Create FastMCP app with weight_management tools registered"""
    weight_management.configure(mock_garmin_client)
    app = FastMCP("Test Weight Management")
    app = weight_management.register_tools(app)
    return app


@pytest.mark.asyncio
async def test_get_weigh_ins_tool(app_with_weight, mock_garmin_client):
    """Test get_weigh_ins tool"""
    mock_garmin_client.get_weigh_ins.return_value = MOCK_WEIGH_INS
    result = await app_with_weight.call_tool(
        "get_weigh_ins",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"}
    )
    assert result is not None
    mock_garmin_client.get_weigh_ins.assert_called_once_with("2024-01-08", "2024-01-15")


@pytest.mark.asyncio
async def test_get_weigh_ins_handles_null_optional_sections(
    app_with_weight, mock_garmin_client
):
    """Weight summaries can contain null metric and average sections."""
    mock_garmin_client.get_weigh_ins.return_value = {
        "dailyWeightSummaries": [{"allWeightMetrics": None}],
        "totalAverage": None,
    }

    result = await app_with_weight.call_tool(
        "get_weigh_ins",
        {"start_date": "2024-01-08", "end_date": "2024-01-15"},
    )

    data = json.loads(result[0][0].text)
    assert data["measurement_count"] == 0
    assert data["measurements"] == []


@pytest.mark.asyncio
async def test_get_daily_weigh_ins_tool(app_with_weight, mock_garmin_client):
    """Test get_daily_weigh_ins tool"""
    mock_garmin_client.get_daily_weigh_ins.return_value = MOCK_DAILY_WEIGH_INS
    result = await app_with_weight.call_tool(
        "get_daily_weigh_ins",
        {"date": "2024-01-15"}
    )
    assert result is not None
    mock_garmin_client.get_daily_weigh_ins.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_delete_weigh_ins_tool(app_with_weight, mock_garmin_client):
    """Test delete_weigh_ins tool"""
    # API returns count of deleted entries
    mock_garmin_client.delete_weigh_ins.return_value = 1
    result = await app_with_weight.call_tool(
        "delete_weigh_ins",
        {"date": "2024-01-15", "delete_all": True, "confirm": True}
    )
    assert result is not None
    mock_garmin_client.delete_weigh_ins.assert_called_once_with("2024-01-15", delete_all=True)


@pytest.mark.asyncio
async def test_delete_weigh_ins_nothing_found(app_with_weight, mock_garmin_client):
    """garminconnect returns None, deleting nothing, when the day has no
    weigh-ins -- that must not be reported as a successful delete."""
    mock_garmin_client.delete_weigh_ins.return_value = None
    result = await app_with_weight.call_tool(
        "delete_weigh_ins",
        {"date": "2024-01-15", "confirm": True}
    )
    data = json.loads(result[0][0].text)
    assert data["status"] == "nothing_deleted"
    assert data["deleted_count"] == 0
    assert "No weight measurements found" in data["message"]


@pytest.mark.asyncio
async def test_delete_weigh_ins_several_without_delete_all(app_with_weight, mock_garmin_client):
    """garminconnect also returns None, deleting nothing, when the day has
    several weigh-ins and delete_all is false."""
    mock_garmin_client.delete_weigh_ins.return_value = None
    result = await app_with_weight.call_tool(
        "delete_weigh_ins",
        {"date": "2024-01-15", "delete_all": False, "confirm": True}
    )
    data = json.loads(result[0][0].text)
    assert data["status"] == "nothing_deleted"
    assert data["deleted_count"] == 0
    assert "delete_all=true" in data["message"]
    mock_garmin_client.delete_weigh_ins.assert_called_once_with("2024-01-15", delete_all=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 2])
async def test_delete_weigh_ins_previews_before_deleting(app_with_weight, mock_garmin_client, count):
    mock_garmin_client.get_daily_weigh_ins.return_value = {"dateWeightList": [
        {"samplePk": i, "weight": 70000 + i * 100, "sourceType": "MANUAL", "timestampGMT": 1705316400000 + i}
        for i in range(count)
    ]}
    mock_garmin_client.delete_weigh_ins.return_value = count

    result = await app_with_weight.call_tool("delete_weigh_ins", {"date": "2024-01-15"})

    data = json.loads(result[0][0].text)
    assert data["status"] == "needs_confirmation"
    assert data["measurement_count"] == count
    assert data["measurements"][0]["weight_kg"] == 70
    assert data["measurements"][0]["source_type"] == "MANUAL"
    assert "confirm=true" in data["message"]
    mock_garmin_client.delete_weigh_ins.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("weight_list", [None, []])
async def test_delete_weigh_ins_preview_empty_day_does_not_delete(app_with_weight, mock_garmin_client, weight_list):
    mock_garmin_client.get_daily_weigh_ins.return_value = {"dateWeightList": weight_list}
    mock_garmin_client.delete_weigh_ins.return_value = None

    result = await app_with_weight.call_tool("delete_weigh_ins", {"date": "2024-01-15"})

    data = json.loads(result[0][0].text)
    assert data["status"] == "nothing_deleted"
    mock_garmin_client.get_daily_weigh_ins.assert_called_once_with("2024-01-15")
    mock_garmin_client.delete_weigh_ins.assert_not_called()


@pytest.mark.asyncio
async def test_delete_weigh_ins_preview_respects_delete_all_false(app_with_weight, mock_garmin_client):
    mock_garmin_client.get_daily_weigh_ins.return_value = {"dateWeightList": [{"weight": 70000}, {"weight": 70100}]}
    mock_garmin_client.delete_weigh_ins.return_value = None

    result = await app_with_weight.call_tool("delete_weigh_ins", {"date": "2024-01-15", "delete_all": False})

    data = json.loads(result[0][0].text)
    assert data["status"] == "nothing_deleted"
    assert "delete_all=true" in data["message"]
    mock_garmin_client.delete_weigh_ins.assert_not_called()


@pytest.mark.asyncio
async def test_delete_weigh_ins_preview_failure_never_deletes(app_with_weight, mock_garmin_client):
    mock_garmin_client.get_daily_weigh_ins.side_effect = GarminConnectConnectionError("API Error 401")

    result = await app_with_weight.call_tool("delete_weigh_ins", {"date": "2024-01-15"})

    assert "API Error 401" in result[0][0].text
    mock_garmin_client.delete_weigh_ins.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["add_weigh_in", "add_weigh_in_with_timestamps"])
async def test_add_weigh_in_accepts_documented_lb_alias(app_with_weight, mock_garmin_client, tool_name):
    method = getattr(mock_garmin_client, tool_name)
    method.return_value = {}
    arguments = {"weight": 155, "unit_key": "lb"}
    if tool_name.endswith("timestamps"):
        arguments.update(date_timestamp="2024-01-15T07:30:00", gmt_timestamp="2024-01-15T15:30:00")

    result = await app_with_weight.call_tool(tool_name, arguments)

    assert json.loads(result[0][0].text)["unit"] == "lbs"
    assert method.call_args.kwargs["unitKey"] == "lbs"


@pytest.mark.asyncio
async def test_add_weigh_in_tool(app_with_weight, mock_garmin_client):
    """Test add_weigh_in tool"""
    add_response = {"status": "success", "weightPk": 12346}
    mock_garmin_client.add_weigh_in.return_value = add_response
    result = await app_with_weight.call_tool(
        "add_weigh_in",
        {"weight": 70.5, "unit_key": "kg"}
    )
    assert result is not None
    mock_garmin_client.add_weigh_in.assert_called_once_with(weight=70.5, unitKey="kg")


@pytest.mark.asyncio
async def test_add_weigh_in_with_timestamps_tool(app_with_weight, mock_garmin_client):
    """Test add_weigh_in_with_timestamps tool"""
    add_response = {"status": "success", "weightPk": 12347}
    mock_garmin_client.add_weigh_in_with_timestamps.return_value = add_response
    result = await app_with_weight.call_tool(
        "add_weigh_in_with_timestamps",
        {"weight": 70.5, "unit_key": "kg"}
    )
    assert result is not None
    # Note: function has optional date_timestamp and gmt_timestamp parameters


@pytest.mark.asyncio
async def test_add_weigh_in_with_timestamps_keeps_given_local_timestamp(
    app_with_weight, mock_garmin_client
):
    """A caller-supplied date_timestamp is passed through, not replaced with
    "now" just because gmt_timestamp was omitted -- garminconnect derives the
    GMT timestamp itself."""
    mock_garmin_client.add_weigh_in_with_timestamps.return_value = {}
    result = await app_with_weight.call_tool(
        "add_weigh_in_with_timestamps",
        {"weight": 70.5, "unit_key": "kg", "date_timestamp": "2024-01-15T07:30:00"}
    )
    data = json.loads(result[0][0].text)
    assert data["status"] == "success"
    assert data["timestamp_local"] == "2024-01-15T07:30:00"
    mock_garmin_client.add_weigh_in_with_timestamps.assert_called_once_with(
        weight=70.5,
        unitKey="kg",
        dateTimestamp="2024-01-15T07:30:00",
        gmtTimestamp="",
    )


@pytest.mark.asyncio
async def test_add_weigh_in_with_timestamps_passes_both_timestamps(
    app_with_weight, mock_garmin_client
):
    mock_garmin_client.add_weigh_in_with_timestamps.return_value = {}
    await app_with_weight.call_tool(
        "add_weigh_in_with_timestamps",
        {
            "weight": 70.5,
            "date_timestamp": "2024-01-15T07:30:00",
            "gmt_timestamp": "2024-01-15T15:30:00",
        }
    )
    mock_garmin_client.add_weigh_in_with_timestamps.assert_called_once_with(
        weight=70.5,
        unitKey="kg",
        dateTimestamp="2024-01-15T07:30:00",
        gmtTimestamp="2024-01-15T15:30:00",
    )


@pytest.mark.asyncio
async def test_add_weigh_in_with_timestamps_requires_local_with_gmt(
    app_with_weight, mock_garmin_client
):
    """With only gmt_timestamp, garminconnect would record the weigh-in at
    the current local time, so the tool refuses instead of guessing the day."""
    result = await app_with_weight.call_tool(
        "add_weigh_in_with_timestamps",
        {"weight": 70.5, "gmt_timestamp": "2024-01-15T15:30:00"}
    )
    assert "date_timestamp is required" in result[0][0].text
    mock_garmin_client.add_weigh_in_with_timestamps.assert_not_called()


# User Profile module tests
@pytest.fixture
def app_with_user_profile(mock_garmin_client):
    """Create FastMCP app with user_profile tools registered"""
    user_profile.configure(mock_garmin_client)
    app = FastMCP("Test User Profile")
    app = user_profile.register_tools(app)
    return app


@pytest.mark.asyncio
async def test_get_full_name_tool(app_with_user_profile, mock_garmin_client):
    """Test get_full_name tool"""
    mock_garmin_client.get_full_name.return_value = "Test User Full Name"
    result = await app_with_user_profile.call_tool("get_full_name", {})
    assert result is not None
    mock_garmin_client.get_full_name.assert_called_once()


@pytest.mark.asyncio
async def test_get_unit_system_tool(app_with_user_profile, mock_garmin_client):
    """Test get_unit_system tool"""
    mock_garmin_client.get_unit_system.return_value = MOCK_UNIT_SYSTEM
    result = await app_with_user_profile.call_tool("get_unit_system", {})
    assert result is not None
    mock_garmin_client.get_unit_system.assert_called_once()


@pytest.mark.asyncio
async def test_get_user_profile_tool(app_with_user_profile, mock_garmin_client):
    """Test get_user_profile tool"""
    mock_garmin_client.get_user_profile.return_value = MOCK_USER_PROFILE
    result = await app_with_user_profile.call_tool("get_user_profile", {})
    assert result is not None
    mock_garmin_client.get_user_profile.assert_called_once()


@pytest.mark.asyncio
async def test_get_userprofile_settings_tool(app_with_user_profile, mock_garmin_client):
    """Test get_userprofile_settings tool"""
    settings = {"emailNotifications": True, "privacySettings": "PUBLIC"}
    mock_garmin_client.get_userprofile_settings.return_value = settings
    result = await app_with_user_profile.call_tool("get_userprofile_settings", {})
    assert result is not None
    mock_garmin_client.get_userprofile_settings.assert_called_once()


HEART_RATE_ZONES = [
    {
        "trainingMethod": "LACTATE_THRESHOLD",
        "restingHeartRateUsed": 54,
        "lactateThresholdHeartRateUsed": 186,
        "zone1Floor": 105,
        "zone2Floor": 126,
        "zone3Floor": 147,
        "zone4Floor": 168,
        "zone5Floor": 189,
        "maxHeartRateUsed": 210,
        "restingHrAutoUpdateUsed": False,
        "sport": "DEFAULT",
        "changeState": "UNCHANGED",
    },
    {
        "trainingMethod": "LACTATE_THRESHOLD",
        "restingHeartRateUsed": 54,
        "lactateThresholdHeartRateUsed": 187,
        "zone1Floor": 135,
        "zone2Floor": 136,
        "zone3Floor": 166,
        "zone4Floor": 178,
        "zone5Floor": 198,
        "maxHeartRateUsed": 203,
        "restingHrAutoUpdateUsed": False,
        "sport": "CYCLING",
        "changeState": "UNCHANGED",
    },
]


@pytest.mark.asyncio
async def test_get_heart_rate_zones_for_sport(app_with_user_profile, mock_garmin_client):
    mock_garmin_client.connectapi.return_value = HEART_RATE_ZONES

    result = await app_with_user_profile.call_tool("get_heart_rate_zones", {"sport": "cycling"})

    data = json.loads(result[0][0].text)
    assert data == HEART_RATE_ZONES[1]
    mock_garmin_client.connectapi.assert_called_once_with("/biometric-service/heartRateZones")


@pytest.mark.asyncio
async def test_set_heart_rate_zones_custom_cycling_read_modify_write(
    app_with_user_profile, mock_garmin_client
):
    confirmed = {
        **HEART_RATE_ZONES[1],
        "trainingMethod": "HR_MAX",
        "maxHeartRateUsed": 204,
        "lactateThresholdHeartRateUsed": 188,
        "zone1Floor": 100,
        "zone2Floor": 136,
        "zone3Floor": 150,
        "zone4Floor": 170,
        "zone5Floor": 188,
    }
    mock_garmin_client.connectapi.side_effect = [HEART_RATE_ZONES, [HEART_RATE_ZONES[0], confirmed]]

    result = await app_with_user_profile.call_tool(
        "set_heart_rate_zones",
        {
            "sport": "cycling",
            "max_hr": 204,
            "resting_hr": 54,
            "lactate_threshold_hr": 188,
            "calculation_method": "custom_bpm",
            "zone_boundaries": [100, 136, 150, 170, 188],
        },
    )

    assert json.loads(result[0][0].text) == confirmed
    payload = mock_garmin_client.client.request.call_args.kwargs["json"]
    assert payload == [{**confirmed, "changeState": "CHANGED"}]
    assert payload[0]["sport"] == "CYCLING"
    mock_garmin_client.client.request.assert_called_once_with(
        "PUT",
        "connectapi",
        "/biometric-service/heartRateZones",
        json=payload,
        api=True,
    )
    assert mock_garmin_client.connectapi.call_count == 2


@pytest.mark.asyncio
async def test_set_heart_rate_zones_partial_update_preserves_other_fields(
    app_with_user_profile, mock_garmin_client
):
    confirmed = {**HEART_RATE_ZONES[1], "maxHeartRateUsed": 204}
    mock_garmin_client.connectapi.side_effect = [HEART_RATE_ZONES, [HEART_RATE_ZONES[0], confirmed]]

    result = await app_with_user_profile.call_tool(
        "set_heart_rate_zones", {"sport": "cycling", "max_hr": 204}
    )

    assert json.loads(result[0][0].text) == confirmed
    written = mock_garmin_client.client.request.call_args.kwargs["json"][0]
    assert written["zone1Floor"] == HEART_RATE_ZONES[1]["zone1Floor"]
    assert written["trainingMethod"] == HEART_RATE_ZONES[1]["trainingMethod"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments,error",
    [
        (
            {"calculation_method": "custom_bpm", "zone_boundaries": [100, 150, 140, 170, 188]},
            "strictly monotonically increasing",
        ),
        (
            {"max_hr": 180, "calculation_method": "custom_bpm", "zone_boundaries": [100, 136, 150, 170, 188]},
            "must not exceed max_hr",
        ),
        (
            {"zone_boundaries": [100, 136, 150, 170, 188]},
            "require calculation_method='custom_bpm'",
        ),
        (
            {"calculation_method": "custom_bpm"},
            "requires all five zone_boundaries",
        ),
        (
            {"max_hr": 301},
            "from 1 to 300",
        ),
    ],
)
async def test_set_heart_rate_zones_rejects_invalid_boundaries(
    app_with_user_profile, mock_garmin_client, arguments, error
):
    mock_garmin_client.connectapi.return_value = HEART_RATE_ZONES

    result = await app_with_user_profile.call_tool(
        "set_heart_rate_zones", {"sport": "cycling", **arguments}
    )

    assert error in result[0][0].text
    mock_garmin_client.client.request.assert_not_called()


@pytest.mark.asyncio
async def test_set_heart_rate_zones_new_sport_inherits_default(
    app_with_user_profile, mock_garmin_client
):
    confirmed = {**HEART_RATE_ZONES[0], "sport": "RUNNING", "maxHeartRateUsed": 205}
    mock_garmin_client.connectapi.side_effect = [
        [HEART_RATE_ZONES[0]],
        [HEART_RATE_ZONES[0], confirmed],
    ]

    result = await app_with_user_profile.call_tool(
        "set_heart_rate_zones", {"sport": "running", "max_hr": 205}
    )

    assert json.loads(result[0][0].text) == confirmed
    written = mock_garmin_client.client.request.call_args.kwargs["json"][0]
    assert written["sport"] == "RUNNING"
    assert written["zone1Floor"] == HEART_RATE_ZONES[0]["zone1Floor"]


@pytest.mark.asyncio
async def test_set_heart_rate_zones_requires_an_update(app_with_user_profile, mock_garmin_client):
    result = await app_with_user_profile.call_tool("set_heart_rate_zones", {"sport": "cycling"})

    assert "No fields to update" in result[0][0].text
    mock_garmin_client.connectapi.assert_not_called()


# Data Management module tests
@pytest.fixture
def app_with_data_management(mock_garmin_client):
    """Create FastMCP app with data_management tools registered"""
    data_management.configure(mock_garmin_client)
    app = FastMCP("Test Data Management")
    app = data_management.register_tools(app)
    return app


@pytest.mark.asyncio
async def test_add_body_composition_tool(app_with_data_management, mock_garmin_client):
    """Test add_body_composition tool"""
    add_response = {"status": "success", "message": "Body composition added"}
    mock_garmin_client.add_body_composition.return_value = add_response
    result = await app_with_data_management.call_tool(
        "add_body_composition",
        {"date": "2024-01-15", "weight": 70.0, "percent_fat": 15.0}
    )
    assert result is not None
    mock_garmin_client.add_body_composition.assert_called_once_with(
        "2024-01-15",
        weight=70.0,
        percent_fat=15.0,
        percent_hydration=None,
        visceral_fat_mass=None,
        bone_mass=None,
        muscle_mass=None,
        basal_met=None,
        active_met=None,
        physique_rating=None,
        metabolic_age=None,
        visceral_fat_rating=None,
        bmi=None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["weight", "visceral_fat_mass", "bone_mass", "muscle_mass"])
@pytest.mark.parametrize("value", [700, 655.35, -1, float("inf")])
async def test_body_composition_rejects_unencodable_mass_in_kg(
    app_with_data_management, mock_garmin_client, field, value
):
    mock_garmin_client.add_body_composition.return_value = {}

    result = await app_with_data_management.call_tool("add_body_composition", {
        "date": "2024-01-15", "weight": 70, field: value,
    })

    assert field in result[0][0].text
    assert "kg" in result[0][0].text
    assert "FIT" in result[0][0].text
    mock_garmin_client.add_body_composition.assert_not_called()


@pytest.mark.asyncio
async def test_body_composition_documents_mass_and_energy_units(app_with_data_management):
    tool = next(t for t in await app_with_data_management.list_tools() if t.name == "add_body_composition")
    for field in ["visceral_fat_mass", "bone_mass", "muscle_mass"]:
        line = next(line for line in tool.description.splitlines() if f"{field}:" in line)
        assert "kg" in line
    for field in ["basal_met", "active_met"]:
        line = next(line for line in tool.description.splitlines() if f"{field}:" in line)
        assert "kcal/day" in line


@pytest.mark.asyncio
async def test_set_blood_pressure_tool(app_with_data_management, mock_garmin_client):
    """Test set_blood_pressure tool"""
    add_response = {"status": "success", "message": "Blood pressure added"}
    mock_garmin_client.set_blood_pressure.return_value = add_response
    result = await app_with_data_management.call_tool(
        "set_blood_pressure",
        {"systolic": 120, "diastolic": 80, "pulse": 65}
    )
    assert result is not None
    mock_garmin_client.set_blood_pressure.assert_called_once_with(120, 80, 65, notes=None)


@pytest.mark.asyncio
async def test_add_hydration_data_tool(app_with_data_management, mock_garmin_client):
    """Test add_hydration_data tool"""
    add_response = {"status": "success", "message": "Hydration data added"}
    mock_garmin_client.add_hydration_data.return_value = add_response
    result = await app_with_data_management.call_tool(
        "add_hydration_data",
        {"value_in_ml": 500, "cdate": "2024-01-15", "timestamp": "2024-01-15T10:00:00"}
    )
    assert result is not None
    mock_garmin_client.add_hydration_data.assert_called_once_with(
        value_in_ml=500,
        cdate="2024-01-15",
        timestamp="2024-01-15T10:00:00"
    )


# Gear Management module tests
@pytest.fixture
def app_with_gear(mock_garmin_client):
    """Create FastMCP app with gear_management tools registered"""
    gear_management.configure(mock_garmin_client)
    app = FastMCP("Test Gear Management")
    app = gear_management.register_tools(app)
    return app


@pytest.mark.asyncio
async def test_get_gear_tool(app_with_gear, mock_garmin_client):
    """Test get_gear tool - fetches user_profile_id automatically"""
    # Setup mocks for all internal API calls
    mock_garmin_client.get_device_last_used.return_value = MOCK_DEVICE_LAST_USED
    mock_garmin_client.get_gear.return_value = MOCK_GEAR
    mock_garmin_client.connectapi.return_value = MOCK_GEAR_V2
    mock_garmin_client.get_gear_defaults.return_value = MOCK_GEAR_DEFAULTS
    mock_garmin_client.get_gear_stats.return_value = MOCK_GEAR_STATS

    # Call tool without user_profile_id (it's fetched automatically)
    result = await app_with_gear.call_tool("get_gear", {})

    assert result is not None
    payload = json.loads(result[0][0].text)
    gear_by_uuid = {item["uuid"]: item for item in payload["gear"]}
    assert (
        gear_by_uuid["8abfc40d71fb4860bce19072b6c79644"]["notes"]
        == "Rotation shoe; blue laces."
    )
    assert gear_by_uuid["6f27ed27397749ac9f6f450e039c2424"]["notes"] is None
    # Verify the chain of API calls
    mock_garmin_client.get_device_last_used.assert_called_once()
    mock_garmin_client.get_gear.assert_called_once_with(80653452)  # from MOCK_DEVICE_LAST_USED
    mock_garmin_client.connectapi.assert_called_once_with(
        "/gear-service/gear/v2/list"
    )
    mock_garmin_client.get_gear_defaults.assert_called_once_with(80653452)


@pytest.mark.asyncio
@pytest.mark.parametrize("total_activities", [0, 3])
async def test_get_gear_preserves_activity_count_when_distance_is_null(
    app_with_gear, mock_garmin_client, total_activities
):
    mock_garmin_client.get_device_last_used.return_value = MOCK_DEVICE_LAST_USED
    mock_garmin_client.get_gear.return_value = [MOCK_GEAR[0]]
    mock_garmin_client.connectapi.return_value = []
    mock_garmin_client.get_gear_defaults.return_value = []
    mock_garmin_client.get_gear_stats.return_value = {
        "totalActivities": total_activities, "totalDistance": None,
    }
    result = await app_with_gear.call_tool("get_gear", {})
    gear = json.loads(result[0][0].text)["gear"][0]
    assert gear["stats"] == {
        "total_activities": total_activities, "total_distance_km": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429, 503])
async def test_get_gear_reports_partial_stats_failures(
    app_with_gear, mock_garmin_client, status
):
    from garminconnect import GarminConnectConnectionError

    mock_garmin_client.get_device_last_used.return_value = MOCK_DEVICE_LAST_USED
    mock_garmin_client.get_gear.return_value = MOCK_GEAR[:2]
    mock_garmin_client.connectapi.return_value = []
    mock_garmin_client.get_gear_defaults.return_value = []
    error = f"API Error {status} - stats failed"
    mock_garmin_client.get_gear_stats.side_effect = [
        {"totalActivities": 10, "totalDistance": 1000},
        GarminConnectConnectionError(error),
    ]
    result = await app_with_gear.call_tool("get_gear", {})
    data = json.loads(result[0][0].text)
    gear = {item["uuid"]: item for item in data["gear"]}
    assert data["gear_count"] == 2
    assert gear[MOCK_GEAR[0]["uuid"]]["stats"]["total_activities"] == 10
    assert gear[MOCK_GEAR[1]["uuid"]]["stats_error"] == error


@pytest.mark.asyncio
async def test_get_gear_tool_default_activity_types(app_with_gear, mock_garmin_client):
    """Default activity types are labelled with Garmin's typeKey for the
    real typeId (3 is hiking, not swimming; 10 is road biking, ...)."""
    mock_garmin_client.get_device_last_used.return_value = MOCK_DEVICE_LAST_USED
    mock_garmin_client.get_gear.return_value = [MOCK_GEAR[0]]
    mock_garmin_client.connectapi.return_value = []
    mock_garmin_client.get_gear_defaults.return_value = [
        {"uuid": MOCK_GEAR[0]["uuid"], "activityTypePk": pk, "defaultGear": True}
        for pk in (1, 3, 6, 9, 10, 13, 26, 143, 999)
    ]

    result = await app_with_gear.call_tool("get_gear", {"include_stats": False})

    payload = json.loads(result[0][0].text)
    assert payload["gear"][0]["is_default_for"] == [
        "running",
        "hiking",
        "trail_running",
        "walking",
        "road_biking",
        "strength_training",
        "swimming",
        "gravel_cycling",
        "activity_999",
    ]


@pytest.mark.asyncio
async def test_get_gear_tool_defaults_summary(app_with_gear, mock_garmin_client):
    """Every default gear item of an activity type is listed, and gear
    without a nickname (displayName null) is named by its make/model."""
    unnamed_shoe = {**MOCK_GEAR[1], "gearStatusName": "active", "displayName": None}
    mock_garmin_client.get_device_last_used.return_value = MOCK_DEVICE_LAST_USED
    mock_garmin_client.get_gear.return_value = [MOCK_GEAR[0], unnamed_shoe]
    mock_garmin_client.connectapi.return_value = []
    mock_garmin_client.get_gear_defaults.return_value = [
        {"uuid": MOCK_GEAR[0]["uuid"], "activityTypePk": 1, "defaultGear": True},
        {"uuid": MOCK_GEAR[0]["uuid"], "activityTypePk": 6, "defaultGear": True},
        {"uuid": unnamed_shoe["uuid"], "activityTypePk": 1, "defaultGear": True},
    ]

    result = await app_with_gear.call_tool("get_gear", {"include_stats": False})

    payload = json.loads(result[0][0].text)
    assert payload["defaults"] == {
        "running": ["Nimbus 25", "ASICS Gel-Nimbus 24"],
        "trail_running": ["Nimbus 25"],
    }


@pytest.mark.parametrize("notes_value", ["", None], ids=["empty", "null"])
@pytest.mark.asyncio
async def test_get_gear_tool_preserves_empty_and_null_notes(
    app_with_gear, mock_garmin_client, notes_value
):
    """Explicit empty and null notes retain their distinct JSON values."""
    mock_garmin_client.get_device_last_used.return_value = MOCK_DEVICE_LAST_USED
    mock_garmin_client.get_gear.return_value = [MOCK_GEAR[0]]
    mock_garmin_client.connectapi.return_value = [
        {**MOCK_GEAR_V2[0], "notes": notes_value}
    ]
    mock_garmin_client.get_gear_defaults.return_value = []

    result = await app_with_gear.call_tool("get_gear", {"include_stats": False})

    payload = json.loads(result[0][0].text)
    assert payload["gear"][0]["notes"] == notes_value


@pytest.mark.asyncio
async def test_get_gear_tool_without_stats(
    app_with_gear, mock_garmin_client, caplog
):
    """Test get_gear tool with include_stats=False"""
    mock_garmin_client.get_device_last_used.return_value = MOCK_DEVICE_LAST_USED
    mock_garmin_client.get_gear.return_value = MOCK_GEAR
    mock_garmin_client.connectapi.side_effect = RuntimeError("v2 unavailable")
    mock_garmin_client.get_gear_defaults.return_value = MOCK_GEAR_DEFAULTS

    # Call with include_stats=False
    with caplog.at_level(logging.DEBUG, logger=gear_management.__name__):
        result = await app_with_gear.call_tool("get_gear", {"include_stats": False})

    assert result is not None
    payload = json.loads(result[0][0].text)
    assert payload["gear_count"] == len(MOCK_GEAR)
    assert all(item["notes"] is None for item in payload["gear"])
    assert "Garmin v2 gear list unavailable: v2 unavailable" in caplog.text
    # Stats should not be fetched
    mock_garmin_client.get_gear_stats.assert_not_called()


@pytest.mark.asyncio
async def test_add_gear_to_activity_tool(app_with_gear, mock_garmin_client):
    """Test add_gear_to_activity tool"""
    mock_garmin_client.add_gear_to_activity.return_value = {}
    result = await app_with_gear.call_tool(
        "add_gear_to_activity",
        {"activity_id": 12345678901, "gear_uuid": "abc123"}
    )
    assert result is not None
    mock_garmin_client.add_gear_to_activity.assert_called_once_with("abc123", 12345678901)


@pytest.mark.asyncio
async def test_add_gear_to_activity_missing_gear_is_unavailable(
    app_with_gear, mock_garmin_client
):
    """Test add_gear_to_activity reports missing gear as unavailable data"""
    mock_garmin_client.add_gear_to_activity.side_effect = GarminConnectConnectionError(
        "API Error 404 - Gear with uuid: missing doesn't exist."
    )

    result = await app_with_gear.call_tool(
        "add_gear_to_activity",
        {"activity_id": 12345678901, "gear_uuid": "missing"},
    )

    assert result[0][0].text == (
        "No gear association changed; gear missing was not found "
        "for activity 12345678901."
    )


@pytest.mark.asyncio
async def test_remove_gear_from_activity_tool(app_with_gear, mock_garmin_client):
    """Test remove_gear_from_activity tool"""
    mock_garmin_client.remove_gear_from_activity.return_value = {}
    result = await app_with_gear.call_tool(
        "remove_gear_from_activity",
        {"activity_id": 12345678901, "gear_uuid": "abc123"}
    )
    assert result is not None
    mock_garmin_client.remove_gear_from_activity.assert_called_once_with("abc123", 12345678901)


@pytest.mark.asyncio
async def test_remove_gear_from_activity_missing_gear_is_unavailable(
    app_with_gear, mock_garmin_client
):
    """Test remove_gear_from_activity reports missing gear as unavailable data"""
    mock_garmin_client.remove_gear_from_activity.side_effect = GarminConnectConnectionError(
        "API Error 404 - Gear with uuid: missing doesn't exist."
    )

    result = await app_with_gear.call_tool(
        "remove_gear_from_activity",
        {"activity_id": 12345678901, "gear_uuid": "missing"},
    )

    assert result[0][0].text == (
        "No gear association changed; gear missing was not found "
        "for activity 12345678901."
    )


# Women's Health module tests
@pytest.fixture
def app_with_womens_health(mock_garmin_client):
    """Create FastMCP app with womens_health tools registered"""
    womens_health.configure(mock_garmin_client)
    app = FastMCP("Test Womens Health")
    app = womens_health.register_tools(app)
    return app


@pytest.mark.asyncio
async def test_get_pregnancy_summary_tool(app_with_womens_health, mock_garmin_client):
    """Test get_pregnancy_summary tool"""
    pregnancy_summary = {"isPregnant": False}
    mock_garmin_client.get_pregnancy_summary.return_value = pregnancy_summary
    result = await app_with_womens_health.call_tool("get_pregnancy_summary", {})
    assert result is not None
    mock_garmin_client.get_pregnancy_summary.assert_called_once()


@pytest.mark.asyncio
async def test_get_menstrual_data_for_date_tool(app_with_womens_health, mock_garmin_client):
    """Test get_menstrual_data_for_date tool"""
    mock_garmin_client.get_menstrual_data_for_date.return_value = MOCK_MENSTRUAL_DATA
    result = await app_with_womens_health.call_tool(
        "get_menstrual_data_for_date",
        {"date": "2024-01-15"}
    )
    assert result is not None
    mock_garmin_client.get_menstrual_data_for_date.assert_called_once_with("2024-01-15")


@pytest.mark.asyncio
async def test_get_menstrual_calendar_data_tool(app_with_womens_health, mock_garmin_client):
    """Test get_menstrual_calendar_data tool"""
    calendar_data = [MOCK_MENSTRUAL_DATA]
    mock_garmin_client.get_menstrual_calendar_data.return_value = calendar_data
    result = await app_with_womens_health.call_tool(
        "get_menstrual_calendar_data",
        {"start_date": "2024-01-01", "end_date": "2024-01-31"}
    )
    assert result is not None
    mock_garmin_client.get_menstrual_calendar_data.assert_called_once_with("2024-01-01", "2024-01-31")


@pytest.mark.asyncio
async def test_get_menstrual_calendar_data_chunking(app_with_womens_health, mock_garmin_client):
    """Range >92 days is split into 92-day windows and stitched."""
    second_chunk = {
        **MOCK_MENSTRUAL_DATA,
        "calendarDate": "2026-04-03",
        "cycleDay": 1,
    }
    mock_garmin_client.get_menstrual_calendar_data.side_effect = [
        [MOCK_MENSTRUAL_DATA],
        [second_chunk],
    ]

    result = await app_with_womens_health.call_tool(
        "get_menstrual_calendar_data",
        {"start_date": "2026-01-01", "end_date": "2026-06-30"},
    )

    assert result is not None
    assert mock_garmin_client.get_menstrual_calendar_data.call_count == 2
    calls = mock_garmin_client.get_menstrual_calendar_data.call_args_list
    assert calls[0].args == ("2026-01-01", "2026-04-02")
    assert calls[1].args == ("2026-04-03", "2026-06-30")

    data = json.loads(result[0][0].text)
    assert data == [MOCK_MENSTRUAL_DATA, second_chunk]


@pytest.mark.asyncio
@pytest.mark.parametrize("readback", [
    GarminConnectTooManyRequestsError("rate limited"),
    ValueError("Garmin returned an unexpected heart-rate-zone response"),
    [],
])
async def test_heart_rate_zone_update_reports_committed_when_confirmation_fails(
    app_with_user_profile, mock_garmin_client, readback
):
    mock_garmin_client.connectapi.side_effect = [HEART_RATE_ZONES, readback]
    mock_garmin_client.client.request.return_value = Mock(status_code=204)
    result = await app_with_user_profile.call_tool(
        "set_heart_rate_zones", {"sport": "cycling", "max_hr": 204}
    )
    data = json.loads(result[0][0].text)
    assert data["status"] == "success"
    assert data["write_committed"] is True
    assert data["confirmation_error"]
    assert data["submitted_configuration"]["sport"] == "CYCLING"
    assert data["submitted_configuration"]["maxHeartRateUsed"] == 204
    mock_garmin_client.client.request.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("error, committed", [
    (GarminConnectConnectionError("API Error 503 - gateway failed"), "unknown"),
    (requests.exceptions.ReadTimeout("response timed out"), "unknown"),
    (GarminConnectConnectionError("API Error 400 - invalid profile"), False),
])
async def test_heart_rate_zone_update_tracks_failed_write(
    app_with_user_profile, mock_garmin_client, error, committed
):
    mock_garmin_client.connectapi.return_value = HEART_RATE_ZONES
    mock_garmin_client.client.request.side_effect = error
    result = await app_with_user_profile.call_tool(
        "set_heart_rate_zones", {"sport": "cycling", "max_hr": 204}
    )
    data = json.loads(result[0][0].text)
    assert data["write_committed"] == committed
    assert data["stage"] == "garmin_request"
    assert data["error_type"] == type(error).__name__
    assert str(error) in data["message"]
    assert data["correlation_id"]
    mock_garmin_client.connectapi.assert_called_once()


@pytest.mark.asyncio
async def test_heart_rate_zone_update_lookup_failure_reports_no_write(
    app_with_user_profile, mock_garmin_client
):
    mock_garmin_client.connectapi.side_effect = GarminConnectAuthenticationError("expired token")
    result = await app_with_user_profile.call_tool(
        "set_heart_rate_zones", {"sport": "cycling", "max_hr": 204}
    )
    data = json.loads(result[0][0].text)
    assert data["write_committed"] is False
    assert data["stage"] == "auth_refresh"
    mock_garmin_client.client.request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("dict_shape", [False, True])
async def test_menstrual_calendar_deduplicates_cycles_crossing_chunk_seam(
    app_with_womens_health, mock_garmin_client, dict_shape
):
    before = {"cycleId": 1, "startDate": "2026-02-20"}
    spanning = {"cycleId": 2, "startDate": "2026-03-20", "endDate": "2026-04-16"}
    after = {"cycleId": 3, "startDate": "2026-04-17"}
    wrap = (lambda items: {"cycleSummaries": items}) if dict_shape else (lambda items: items)
    mock_garmin_client.get_menstrual_calendar_data.side_effect = [
        wrap([before, spanning]), wrap([dict(reversed(list(spanning.items()))), after])
    ]

    result = await app_with_womens_health.call_tool("get_menstrual_calendar_data", {
        "start_date": "2026-01-01", "end_date": "2026-06-30"
    })

    assert json.loads(result[0][0].text) == wrap([before, spanning, after])
    assert mock_garmin_client.get_menstrual_calendar_data.call_count == 2
