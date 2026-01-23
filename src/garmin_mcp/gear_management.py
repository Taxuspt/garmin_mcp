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
            gear_list = garmin_client.get_gear(user_profile_id)
            if not gear_list:
                return "No gear found."

            # Curate the response
            curated = {
                "count": len(gear_list),
                "gear": []
            }

            for g in gear_list:
                gear_item = {
                    "uuid": g.get('uuid'),
                    "display_name": g.get('displayName'),
                    "model_name": g.get('modelName'),
                    "brand_name": g.get('brandName'),
                    "gear_type": g.get('gearTypePk'),
                    "maximum_distance_meters": g.get('maximumDistanceMeter'),
                    "current_distance_meters": g.get('gearStatusDTOList', [{}])[0].get('totalDistanceInMeters') if g.get('gearStatusDTOList') else None,
                    "date_begun": g.get('dateBegun'),
                    "date_retired": g.get('dateRetired'),
                    "notified": g.get('notified'),
                }
                # Remove None values
                gear_item = {k: v for k, v in gear_item.items() if v is not None}
                curated["gear"].append(gear_item)

            return json.dumps(curated, indent=2)
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

            # Curate the response - remove internal IDs
            curated = {}
            for key, value in defaults.items():
                if key not in ['userProfileNumber', 'userId']:
                    curated[key] = value

            return json.dumps(curated, indent=2)
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

            # Curate the stats
            curated = {
                "uuid": gear_uuid,
                "total_activities": stats.get('totalActivities'),
                "total_distance_meters": stats.get('totalDistance'),
                "total_duration_seconds": stats.get('totalDuration'),
                "total_ascent_meters": stats.get('totalAscent'),
                "total_descent_meters": stats.get('totalDescent'),
                "last_activity_date": stats.get('lastActivityDate'),
            }

            # Remove None values
            curated = {k: v for k, v in curated.items() if v is not None}

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving gear stats: {str(e)}"

    return app
