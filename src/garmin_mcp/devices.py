"""
Device-related functions for Garmin Connect MCP Server
"""
import json
import datetime
from typing import Any, Dict, List, Optional, Union

# The garmin_client will be set by the main file
garmin_client = None


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


def _camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case"""
    import re
    name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', name).lower()


def register_tools(app):
    """Register all device-related tools with the MCP server app"""

    @app.tool()
    async def get_devices() -> str:
        """Get all Garmin devices associated with the user account"""
        try:
            devices = garmin_client.get_devices()
            if not devices:
                return "No devices found."

            # Curate device list - remove 200+ capability flags, keep only essential info
            curated = []
            for device in devices:
                # Extract only essential device information
                device_info = {
                    "device_id": device.get('deviceId'),
                    "device_name": device.get('displayName') or device.get('productDisplayName'),
                    "model": device.get('partNumber'),
                    "manufacturer": device.get('manufacturerName'),
                    "serial_number": device.get('serialNumber'),
                    "software_version": device.get('softwareVersionString'),
                    "status": device.get('deviceStatusName'),
                    "last_sync_time": device.get('lastSyncTime'),
                    "battery_status": device.get('batteryStatus'),
                }

                # Add optional metadata if present
                if device.get('deviceType'):
                    device_info["device_type"] = device.get('deviceType')
                if device.get('primaryDevice') is not None:
                    device_info["is_primary"] = device.get('primaryDevice')

                # Remove None values
                device_info = {k: v for k, v in device_info.items() if v is not None}
                curated.append(device_info)

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving devices: {str(e)}"

    @app.tool()
    async def get_device_last_used() -> str:
        """Get information about the last used Garmin device"""
        try:
            device = garmin_client.get_device_last_used()
            if not device:
                return "No last used device found."

            # Curate to essential device information
            curated = {
                "device_id": device.get('deviceId'),
                "device_name": device.get('displayName') or device.get('productDisplayName'),
                "model": device.get('partNumber'),
                "manufacturer": device.get('manufacturerName'),
                "serial_number": device.get('serialNumber'),
                "software_version": device.get('softwareVersionString'),
                "last_sync_time": device.get('lastSyncTime'),
                "battery_status": device.get('batteryStatus'),
                "device_type": device.get('deviceType'),
                "user_profile_id": device.get('userProfileId'),
            }

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving last used device: {str(e)}"

    @app.tool()
    async def get_device_settings(device_id: Union[int, str]) -> str:
        """Get settings for a specific Garmin device

        Args:
            device_id: Device ID
        """
        try:
            device_id = str(device_id)
            settings = garmin_client.get_device_settings(device_id)
            if not settings:
                return f"No settings found for device ID {device_id}."

            # Curate device settings to essential configuration
            curated = {
                "device_id": device_id,
            }

            # Time and location settings
            if settings.get('timeZoneOffsetInMilliseconds') is not None:
                curated["timezone_offset_hours"] = round(settings.get('timeZoneOffsetInMilliseconds') / 3600000, 1)
            if settings.get('timeMode'):
                curated["time_mode"] = settings.get('timeMode')

            # Display settings
            display_fields = ['displayOrientation', 'activityTrackingOn', 'backlightMode', 'backlightTimeout']
            for field in display_fields:
                if settings.get(field) is not None:
                    curated[_camel_to_snake(field)] = settings.get(field)

            # Heart rate settings
            hr_fields = ['heartRateMonitorMode', 'heartRateBroadcastMode', 'wristHeartRateEnabled']
            for field in hr_fields:
                if settings.get(field) is not None:
                    curated[_camel_to_snake(field)] = settings.get(field)

            # Fitness/Activity settings
            activity_fields = ['autoActivityDetect', 'moveBarEnabled', 'moveAlertEnabled',
                              'goalsSyncEnabled', 'intenseMinutesGoalEnabled']
            for field in activity_fields:
                if settings.get(field) is not None:
                    curated[_camel_to_snake(field)] = settings.get(field)

            # Smart features
            smart_fields = ['smartNotificationsEnabled', 'phoneCallsEnabled', 'textMessagesEnabled',
                           'weatherAlertsEnabled', 'weatherForecastEnabled']
            for field in smart_fields:
                if settings.get(field) is not None:
                    curated[_camel_to_snake(field)] = settings.get(field)

            # GPS settings
            if settings.get('gpsEnabled') is not None:
                curated["gps_enabled"] = settings.get('gpsEnabled')

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving device settings: {str(e)}"

    @app.tool()
    async def get_primary_training_device() -> str:
        """Get information about the primary training device"""
        try:
            device = garmin_client.get_primary_training_device()
            if not device:
                return "No primary training device found."

            # Curate to essential device information
            curated = {
                "device_id": device.get('deviceId'),
                "device_name": device.get('displayName') or device.get('productDisplayName'),
                "model": device.get('partNumber'),
                "manufacturer": device.get('manufacturerName'),
                "serial_number": device.get('serialNumber'),
                "software_version": device.get('softwareVersionString'),
                "last_sync_time": device.get('lastSyncTime'),
                "battery_status": device.get('batteryStatus'),
                "device_type": device.get('deviceType'),
            }

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving primary training device: {str(e)}"

    @app.tool()
    async def get_device_solar_data(device_id: str, date: str) -> str:
        """Get solar data for a specific device

        Args:
            device_id: Device ID
            date: Date in YYYY-MM-DD format
        """
        try:
            solar_data = garmin_client.get_device_solar_data(device_id, date)
            if not solar_data:
                return f"No solar data found for device ID {device_id} on {date}."

            # Curate solar data to essential metrics
            curated = {
                "device_id": device_id,
                "date": date,
                "solar_intensity_avg": solar_data.get('solarIntensityAvg'),
                "solar_intensity_max": solar_data.get('solarIntensityMax'),
                "battery_charged_percent": solar_data.get('batteryCharged'),
                "battery_used_percent": solar_data.get('batteryUsed'),
                "battery_net_percent": solar_data.get('batteryNet'),
            }

            # Add time ranges if available
            if solar_data.get('solarIntensityStartTimeGmt'):
                curated["start_time"] = solar_data.get('solarIntensityStartTimeGmt')
            if solar_data.get('solarIntensityEndTimeGmt'):
                curated["end_time"] = solar_data.get('solarIntensityEndTimeGmt')

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving solar data: {str(e)}"

    @app.tool()
    async def get_device_alarms() -> str:
        """Get alarms from all Garmin devices"""
        try:
            alarms = garmin_client.get_device_alarms()
            if not alarms:
                return "No device alarms found."

            # Curate alarm data to essential information
            curated = []
            for alarm in alarms:
                alarm_info = {
                    "alarm_id": alarm.get('alarmId'),
                    "device_id": alarm.get('deviceId'),
                    "time": alarm.get('time'),
                    "enabled": alarm.get('enabled'),
                    "repeat_days": [],
                }

                # Parse repeat days
                days_map = {
                    'monday': alarm.get('repeatMonday'),
                    'tuesday': alarm.get('repeatTuesday'),
                    'wednesday': alarm.get('repeatWednesday'),
                    'thursday': alarm.get('repeatThursday'),
                    'friday': alarm.get('repeatFriday'),
                    'saturday': alarm.get('repeatSaturday'),
                    'sunday': alarm.get('repeatSunday'),
                }
                alarm_info["repeat_days"] = [day for day, enabled in days_map.items() if enabled]

                # Add optional fields
                if alarm.get('alarmName'):
                    alarm_info["name"] = alarm.get('alarmName')
                if alarm.get('smartAlarmEnabled') is not None:
                    alarm_info["smart_alarm_enabled"] = alarm.get('smartAlarmEnabled')
                if alarm.get('backlight') is not None:
                    alarm_info["backlight"] = alarm.get('backlight')
                if alarm.get('vibration') is not None:
                    alarm_info["vibration"] = alarm.get('vibration')

                # Remove None and empty values
                alarm_info = {k: v for k, v in alarm_info.items() if v is not None and v != []}
                curated.append(alarm_info)

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving device alarms: {str(e)}"

    return app
