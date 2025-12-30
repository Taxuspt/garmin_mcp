"""
Device-related functions for Garmin Connect MCP Server
"""

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
            return devices
        except Exception as e:
            return f"Error retrieving devices: {str(e)}"

    @app.tool()
    async def get_device_last_used() -> str:
        """Get information about the last used Garmin device"""
        try:
            device = garmin_client.get_device_last_used()
            if not device:
                return "No last used device found."
            return device
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
            return settings
        except Exception as e:
            return f"Error retrieving device settings: {str(e)}"

    @app.tool()
    async def get_primary_training_device() -> str:
        """Get information about the primary training device"""
        try:
            device = garmin_client.get_primary_training_device()
            if not device:
                return "No primary training device found."
            return device
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
            return solar_data
        except Exception as e:
            return f"Error retrieving solar data: {str(e)}"

    @app.tool()
    async def get_device_alarms() -> str:
        """Get alarms from all Garmin devices"""
        try:
            alarms = garmin_client.get_device_alarms()
            if not alarms:
                return "No device alarms found."
            return alarms
        except Exception as e:
            return f"Error retrieving device alarms: {str(e)}"

    return app
