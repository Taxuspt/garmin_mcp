"""
Gear management functions for Garmin Connect MCP Server
"""

import json
from typing import Any, Dict, List, Optional, Union

# The garmin_client will be set by the main file
garmin_client = None

# Activity type mappings for gear defaults
# This is extrapolated from data and might not be complete or 100% accurate
ACTIVITY_TYPE_MAPPING = {
    1: "Running",
    2: "Cycling",
    3: "Swimming",
    4: "Fitness",
    5: "Walking",
    6: "Hiking",
    7: "Strength",
    8: "Other",
}


def _parse_iso_date(iso_string: str) -> str:
    """Extract date from ISO datetime string"""
    if not iso_string:
        return None
    return iso_string.split("T")[0] if "T" in iso_string else iso_string


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


def register_tools(app):
    """Register all gear management tools with the MCP server app"""

    @app.tool()
    async def get_gear(include_stats: bool = True) -> str:
        """Get all gear registered with the user account

        Returns complete gear inventory including usage statistics and default
        activity associations. No parameters required - user profile is fetched automatically.

        Args:
            include_stats: Include usage statistics for each gear item (default True).
                           Set to False for faster response with large gear collections.
        """
        try:
            # 1. Get user_profile_id automatically from last used device
            device_info = garmin_client.get_device_last_used()
            if not device_info:
                return "Could not retrieve user profile. Please ensure you have a synced device."
            user_profile_id = device_info.get("userProfileNumber")

            # 2. Get all gear
            gear_list = garmin_client.get_gear(user_profile_id)
            if not gear_list:
                return "No gear found."

            # 3. Get defaults to map gear -> activity types
            defaults_list = garmin_client.get_gear_defaults(user_profile_id) or []
            defaults_by_uuid = {}
            for d in defaults_list:
                uuid = d.get("uuid")
                activity_pk = d.get("activityTypePk")
                activity_type = ACTIVITY_TYPE_MAPPING.get(
                    activity_pk, f"activity_{activity_pk}"
                )
                if uuid not in defaults_by_uuid:
                    defaults_by_uuid[uuid] = []
                defaults_by_uuid[uuid].append(activity_type)

            # 4. Build curated gear list
            curated_gear = []
            active_count = 0
            retired_count = 0

            for g in gear_list:
                uuid = g.get("uuid")
                status = g.get("gearStatusName", "").lower()

                if status == "active":
                    active_count += 1
                elif status == "retired":
                    retired_count += 1

                gear_item = {
                    "uuid": uuid,
                    "name": g.get("displayName"),
                    "full_name": g.get("customMakeModel"),
                    "type": g.get("gearTypeName"),
                    "status": status,
                    "date_begin": _parse_iso_date(g.get("dateBegin")),
                    "date_end": _parse_iso_date(g.get("dateEnd")),
                }

                # Add max distance in km if set
                max_meters = g.get("maximumMeters")
                if max_meters and max_meters > 0:
                    gear_item["max_distance_km"] = round(max_meters / 1000, 1)

                # Add default activity associations
                if uuid in defaults_by_uuid:
                    gear_item["is_default_for"] = defaults_by_uuid[uuid]

                # 5. Get stats if requested
                if include_stats:
                    try:
                        stats = garmin_client.get_gear_stats(uuid)
                        if stats:
                            gear_item["stats"] = {
                                "total_activities": stats.get("totalActivities"),
                                "total_distance_km": round(
                                    stats.get("totalDistance", 0) / 1000, 1
                                ),
                            }
                    except Exception:
                        pass  # Stats unavailable for this gear

                curated_gear.append(gear_item)

            # Sort: active first, then by date_begin descending
            curated_gear.sort(
                key=lambda x: (x["status"] != "active", x.get("date_begin") or ""),
                reverse=True,
            )
            # Re-sort to ensure active comes first
            curated_gear.sort(key=lambda x: x["status"] != "active")

            # Build defaults summary (activity -> gear name)
            defaults_summary = {}
            for uuid, activities in defaults_by_uuid.items():
                gear_name = next(
                    (g["name"] for g in curated_gear if g["uuid"] == uuid), None
                )
                for activity in activities:
                    defaults_summary[activity] = gear_name

            return json.dumps(
                {
                    "gear_count": len(curated_gear),
                    "active_count": active_count,
                    "retired_count": retired_count,
                    "defaults": defaults_summary,
                    "gear": curated_gear,
                },
                indent=2,
            )
        except Exception as e:
            return f"Error retrieving gear: {str(e)}"

    @app.tool()
    async def add_gear_to_activity(activity_id: int, gear_uuid: str) -> str:
        """Associate gear with an activity

        Links a specific piece of gear (like shoes, bike, etc.) to an activity.

        Args:
            activity_id: ID of the activity
            gear_uuid: UUID of the gear to add (get from get_gear)
        """
        try:
            garmin_client.add_gear_to_activity(activity_id, gear_uuid)

            return json.dumps(
                {
                    "success": True,
                    "activity_id": activity_id,
                    "gear_uuid": gear_uuid,
                    "message": "Gear successfully added to activity",
                },
                indent=2,
            )
        except Exception as e:
            return f"Error adding gear to activity: {str(e)}"

    @app.tool()
    async def remove_gear_from_activity(activity_id: int, gear_uuid: str) -> str:
        """Remove gear association from an activity

        Unlinks a specific piece of gear from an activity.

        Args:
            activity_id: ID of the activity
            gear_uuid: UUID of the gear to remove
        """
        try:
            garmin_client.remove_gear_from_activity(activity_id, gear_uuid)

            return json.dumps(
                {
                    "success": True,
                    "activity_id": activity_id,
                    "gear_uuid": gear_uuid,
                    "message": "Gear successfully removed from activity",
                },
                indent=2,
            )
        except Exception as e:
            return f"Error removing gear from activity: {str(e)}"

    return app
