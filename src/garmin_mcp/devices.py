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
                    "device_id": device.get("deviceId"),
                    "device_name": device.get("displayName")
                    or device.get("productDisplayName"),
                    "model": device.get("partNumber"),
                    "manufacturer": device.get("manufacturerName"),
                    "serial_number": device.get("serialNumber"),
                    "software_version": device.get("softwareVersionString"),
                    "status": device.get("deviceStatusName"),
                    "last_sync_time": device.get("lastSyncTime"),
                    "battery_status": device.get("batteryStatus"),
                }

                # Add optional metadata if present
                if device.get("deviceType"):
                    device_info["device_type"] = device.get("deviceType")
                if device.get("primaryDevice") is not None:
                    device_info["is_primary"] = device.get("primaryDevice")

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
                "user_device_id": device.get("userDeviceId"),
                "device_name": device.get("lastUsedDeviceName"),
                "device_key": device.get("lastUsedDeviceApplicationKey"),
                "user_profile_id": device.get("userProfileNumber"),
            }

            # Format last upload time if available
            upload_time_ms = device.get("lastUsedDeviceUploadTime")
            if upload_time_ms:
                dt = datetime.datetime.fromtimestamp(upload_time_ms / 1000.0)
                curated["last_upload_time"] = dt.strftime("%Y-%m-%d %H:%M:%S")

            # Add image URL if available
            if device.get("imageUrl"):
                curated["image_url"] = device.get("imageUrl")

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving last used device: {str(e)}"

    @app.tool()
    async def get_device_settings(device_id: Union[int, str]) -> str:
        """Get settings for a specific Garmin device

        Returns device configuration including time/date format, units,
        activity tracking settings, and alarm information.

        Args:
            device_id: Device ID (can be obtained from get_devices or get_device_last_used)
        """
        try:
            settings = garmin_client.get_device_settings(device_id)
            if not settings:
                return f"No settings found for device ID {device_id}."

            # Curate device settings
            curated = {
                "device_id": settings.get("deviceId"),
                "time_format": settings.get("timeFormat"),
                "date_format": settings.get("dateFormat"),
                "measurement_units": settings.get("measurementUnits"),
            }

            # Sound/vibration settings
            if settings.get("keyTonesEnabled") is not None:
                curated["key_tones_enabled"] = settings.get("keyTonesEnabled")
            if settings.get("keyVibrationEnabled") is not None:
                curated["key_vibration_enabled"] = settings.get("keyVibrationEnabled")
            if settings.get("alertTonesEnabled") is not None:
                curated["alert_tones_enabled"] = settings.get("alertTonesEnabled")

            # Activity tracking settings
            activity_tracking = settings.get("activityTracking", {})
            if activity_tracking:
                tracking_info = {}
                if activity_tracking.get("moveAlertEnabled") is not None:
                    tracking_info["move_alert_enabled"] = activity_tracking.get(
                        "moveAlertEnabled"
                    )
                if activity_tracking.get("pulseOxSleepTrackingEnabled") is not None:
                    tracking_info["pulse_ox_sleep_tracking"] = activity_tracking.get(
                        "pulseOxSleepTrackingEnabled"
                    )
                if activity_tracking.get("highHrAlertEnabled") is not None:
                    tracking_info["high_hr_alert_enabled"] = activity_tracking.get(
                        "highHrAlertEnabled"
                    )
                if activity_tracking.get("lowHrAlertEnabled") is not None:
                    tracking_info["low_hr_alert_enabled"] = activity_tracking.get(
                        "lowHrAlertEnabled"
                    )
                if tracking_info:
                    curated["activity_tracking"] = tracking_info

            # Alarm count
            alarms = settings.get("alarms", [])
            if alarms:
                enabled_alarms = [a for a in alarms if a.get("alarmMode") == "ON"]
                curated["alarm_count"] = len(alarms)
                curated["enabled_alarm_count"] = len(enabled_alarms)

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving device settings: {str(e)}"

    @app.tool()
    async def get_primary_training_device() -> str:
        """Get information about the primary training device

        Returns details about the device designated as primary for training
        metrics, along with other wearable devices on the account.
        """
        try:
            data = garmin_client.get_primary_training_device()
            if not data:
                return "No primary training device found."

            # Extract primary device ID
            primary_device = data.get("PrimaryTrainingDevice", {})
            primary_device_id = primary_device.get("deviceId")

            # Get primary training devices list
            primary_devices = data.get("PrimaryTrainingDevices", {}).get(
                "deviceWeights", []
            )

            curated = {
                "primary_device_id": primary_device_id,
            }

            # Curate the list of training-capable devices
            if primary_devices:
                devices_list = []
                for device in primary_devices:
                    device_info = {
                        "device_id": device.get("deviceId"),
                        "display_name": device.get("displayName"),
                        "is_primary_wearable": device.get("primaryWearableDevice"),
                        "primary_training_capable": device.get("primaryTrainingCapable"),
                    }
                    if device.get("imageUrl"):
                        device_info["image_url"] = device.get("imageUrl")
                    devices_list.append(device_info)
                curated["training_devices"] = devices_list
                curated["training_device_count"] = len(devices_list)

            # Add wearable device count
            wearable_data = data.get("WearableDevices", {})
            if wearable_data.get("wearableDeviceCount"):
                curated["wearable_device_count"] = wearable_data.get(
                    "wearableDeviceCount"
                )

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving primary training device: {str(e)}"

    @app.tool()
    async def get_device_solar_data(device_id: str, date: str) -> str:
        """Get solar data for a specific device

        Returns solar charging data for devices with solar panels (e.g., Instinct Solar,
        Fenix Solar). Only applicable to solar-capable devices.

        Args:
            device_id: Device ID (can be obtained from get_devices)
            date: Date in YYYY-MM-DD format
        """
        try:
            solar_data = garmin_client.get_device_solar_data(device_id, date)
            if not solar_data:
                return f"No solar data found for device ID {device_id} on {date}."

            # Check if there's actual data in the response
            daily_data = solar_data.get("solarDailyDataDTOs", [])
            if not daily_data:
                return f"No solar data available for device ID {device_id} on {date}. This device may not have solar capabilities."

            # Curate solar data from the daily DTOs
            curated_days = []
            for day_data in daily_data:
                curated_day = {
                    "date": day_data.get("calendarDate"),
                    "solar_intensity_avg": day_data.get("solarIntensityAvg"),
                    "solar_intensity_max": day_data.get("solarIntensityMax"),
                    "battery_charged_percent": day_data.get("batteryCharged"),
                    "battery_used_percent": day_data.get("batteryUsed"),
                    "battery_net_percent": day_data.get("batteryNet"),
                }
                # Remove None values
                curated_day = {k: v for k, v in curated_day.items() if v is not None}
                curated_days.append(curated_day)

            return json.dumps(
                {"device_id": device_id, "solar_data": curated_days}, indent=2
            )
        except Exception as e:
            return f"Error retrieving solar data: {str(e)}"

    def _format_alarm_time(minutes: int) -> str:
        """Convert minutes from midnight to HH:MM format"""
        if minutes is None:
            return None
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours:02d}:{mins:02d}"

    @app.tool()
    async def get_device_alarms() -> str:
        """Get alarms from all Garmin devices

        Returns all configured alarms with their schedules, sounds, and enabled status.
        """
        try:
            alarms = garmin_client.get_device_alarms()
            if not alarms:
                return "No device alarms found."

            # Curate alarm data
            curated = []
            for alarm in alarms:
                # Convert time from minutes to HH:MM
                alarm_time_minutes = alarm.get("alarmTime")
                alarm_time = _format_alarm_time(alarm_time_minutes)

                alarm_info = {
                    "alarm_id": alarm.get("alarmId"),
                    "time": alarm_time,
                    "time_minutes": alarm_time_minutes,
                    "enabled": alarm.get("alarmMode") == "ON",
                    "days": alarm.get("alarmDays", []),
                    "sound": alarm.get("alarmSound"),
                }

                # Add backlight setting
                if alarm.get("backlight"):
                    alarm_info["backlight"] = alarm.get("backlight")

                # Add message if present
                if alarm.get("alarmMessage"):
                    alarm_info["message"] = alarm.get("alarmMessage")

                curated.append(alarm_info)

            # Sort by time
            curated.sort(key=lambda x: x.get("time_minutes") or 0)

            # Summary
            enabled_count = sum(1 for a in curated if a.get("enabled"))

            return json.dumps(
                {
                    "total_alarms": len(curated),
                    "enabled_alarms": enabled_count,
                    "alarms": curated,
                },
                indent=2,
            )
        except Exception as e:
            return f"Error retrieving device alarms: {str(e)}"

    return app
