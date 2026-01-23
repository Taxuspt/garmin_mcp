"""
Gear management functions for Garmin Connect MCP Server
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
    """Register all gear management tools with the MCP server app"""
    
    @app.tool()
    async def get_gear(user_profile_id: str) -> str:
        """Get all gear registered with the user account
        
        Args:
            user_profile_id: User profile ID (can be obtained from get_device_last_used)
        """
        try:
            gear = garmin_client.get_gear(user_profile_id)
            if not gear:
                return "No gear found."
            return json.dumps(gear, indent=2)
        except Exception as e:
            return f"Error retrieving gear: {str(e)}"

    @app.tool()
    async def get_gear_defaults(user_profile_id: str) -> str:
        """Get default gear settings
        
        Args:
            user_profile_id: User profile ID (can be obtained from get_device_last_used)
        """
        try:
            defaults = garmin_client.get_gear_defaults(user_profile_id)
            if not defaults:
                return "No gear defaults found."
            return json.dumps(defaults, indent=2)
        except Exception as e:
            return f"Error retrieving gear defaults: {str(e)}"
    
    @app.tool()
    async def get_gear_stats(gear_uuid: str) -> str:
        """Get statistics for specific gear
        
        Args:
            gear_uuid: UUID of the gear item
        """
        try:
            stats = garmin_client.get_gear_stats(gear_uuid)
            if not stats:
                return f"No stats found for gear with UUID {gear_uuid}."
            return json.dumps(stats, indent=2)
        except Exception as e:
            return f"Error retrieving gear stats: {str(e)}"

    return app