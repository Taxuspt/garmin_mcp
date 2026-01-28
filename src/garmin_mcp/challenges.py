"""
Challenges and badges functions for Garmin Connect MCP Server
"""

import json
import datetime
from typing import Any, Dict, List, Optional, Union

# The garmin_client will be set by the main file
garmin_client = None

# Badge category mappings
# This list is estimated based on real data, the mapping might not be 100% accurate
BADGE_CATEGORY_MAPPING = {
    1: "Activity",
    2: "Running",
    3: "Cycling",
    4: "Challenge",
    5: "Steps",
    9: "Diving",
}

# Badge difficulty mappings
BADGE_DIFFICULTY_MAPPING = {
    1: "Easy",
    2: "Medium",
    3: "Hard",
}

# Badge unit mappings: unitId -> (name, value_type)
# value_type used to format the progress/target values
BADGE_UNIT_MAPPING = {
    1: ("distance", "distance"),  # meters
    2: ("elevation", "elevation"),  # meters
    3: ("activities", "count"),
    5: ("steps", "count"),
    7: ("time", "time"),  # seconds
}

# Challenge category mappings
CHALLENGE_CATEGORY_MAPPING = {
    1: "Running",
    2: "Cycling",
    3: "Fitness",
    4: "Steps",
    5: "Walking",
    6: "Yoga/Mindfulness",
    9: "Multi-Activity",
}

# Challenge status mappings
CHALLENGE_STATUS_MAPPING = {
    1: "Not Started",
    2: "In Progress",
    3: "Completed",
    4: "Ended",
}

# Adhoc challenge activity type mappings
ADHOC_ACTIVITY_TYPE_MAPPING = {
    1: "Running",
    2: "Cycling",
    3: "Swimming",
    4: "Steps",
    5: "Walking",
}

# Personal record type mappings: typeId -> (name, value_type)
# value_type: "time" (seconds), "distance" (meters), "count", "elevation" (meters), "days"
# This list is estimated based on real data, the type mapping might not be 100% accurate
PR_TYPE_MAPPING = {
    1: ("Fastest 1K", "time"),
    2: ("Fastest Mile", "time"),
    3: ("Fastest 5K", "time"),
    4: ("Fastest 10K", "time"),
    5: ("Fastest Half Marathon", "time"),
    6: ("Fastest Marathon", "time"),
    7: ("Longest Run", "distance"),
    8: ("Longest Ride", "distance"),
    9: ("Most Elevation Gain Cycling", "elevation"),
    10: ("Fastest 100K Cycling", "time"),
    11: ("Fastest 40K Cycling", "time"),
    12: ("Most Steps Day", "count"),
    13: ("Most Steps Week", "count"),
    14: ("Most Steps Month", "count"),
    15: ("Longest Daily Goal Streak", "days"),
    16: ("Longest Weekly Goal Streak", "days"),
    17: ("Longest Pool Swim", "distance"),
    18: ("Fastest 100m Pool Swim", "time"),
    19: ("Fastest 400m Pool Swim", "time"),
    20: ("Fastest 500m Pool Swim", "time"),
    21: ("Fastest 800m Pool Swim", "time"),
    22: ("Fastest 1500m Pool Swim", "time"),
    23: ("Fastest 1 Mile Pool Swim", "time"),
}


def _format_time(seconds: float) -> str:
    """Format seconds into human-readable time string"""
    if seconds is None:
        return None
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_distance(meters: float) -> str:
    """Format meters into human-readable distance string"""
    if meters is None:
        return None
    if meters >= 1000:
        return f"{meters / 1000:.2f} km"
    return f"{meters:.0f} m"


def _format_timestamp(timestamp_ms: int) -> str:
    """Convert millisecond timestamp to readable date string"""
    if timestamp_ms is None:
        return None
    dt = datetime.datetime.fromtimestamp(timestamp_ms / 1000.0)
    return dt.strftime("%Y-%m-%d")


def _parse_iso_date(iso_string: str) -> str:
    """Extract just the date part from an ISO datetime string"""
    if not iso_string:
        return None
    # Handle formats like "2024-03-31T18:55:47.534" or "2024-03-31T18:55:47.0"
    return iso_string.split("T")[0] if "T" in iso_string else iso_string


def _format_badge_value(value: float, unit_id: int) -> str:
    """Format a badge progress/target value based on its unit type"""
    if value is None:
        return None

    unit_info = BADGE_UNIT_MAPPING.get(unit_id)
    if not unit_info:
        return str(value)

    _, value_type = unit_info

    if value_type == "time":
        return _format_time(value)
    elif value_type == "distance":
        return _format_distance(value)
    elif value_type == "elevation":
        return f"{value:.0f} m"
    elif value_type == "count":
        return f"{int(value):,}"
    return str(value)


def _calculate_progress_percent(progress: float, target: float) -> str:
    """Calculate progress percentage"""
    if progress is None or target is None or target == 0:
        return None
    percent = (progress / target) * 100
    return f"{min(percent, 100):.1f}%"


def _curate_badge_challenge(challenge: dict) -> dict:
    """Curate a badge challenge into a clean format"""
    category_id = challenge.get("challengeCategoryId")
    status_id = challenge.get("badgeChallengeStatusId")
    unit_id = challenge.get("badgeUnitId")

    progress = challenge.get("badgeProgressValue")
    target = challenge.get("badgeTargetValue")

    curated = {
        "name": challenge.get("badgeChallengeName"),
        "uuid": challenge.get("uuid"),
        "category": CHALLENGE_CATEGORY_MAPPING.get(category_id, f"category_{category_id}"),
        "status": CHALLENGE_STATUS_MAPPING.get(status_id, f"status_{status_id}"),
        "points": challenge.get("badgePoints"),
        "start_date": _parse_iso_date(challenge.get("startDate")),
        "end_date": _parse_iso_date(challenge.get("endDate")),
        "joined": challenge.get("userJoined", False),
    }

    # Add progress info if available
    if target is not None and target > 0:
        curated["progress"] = _format_badge_value(progress, unit_id)
        curated["target"] = _format_badge_value(target, unit_id)
        curated["progress_percent"] = _calculate_progress_percent(progress, target)

    # Add earned date if completed
    earned_date = challenge.get("badgeEarnedDate")
    if earned_date:
        curated["earned_date"] = _parse_iso_date(earned_date)

    return curated


def _format_pr_value(value: float, value_type: str) -> str:
    """Format a PR value based on its type"""
    if value is None:
        return None
    if value_type == "time":
        return _format_time(value)
    elif value_type == "distance":
        return _format_distance(value)
    elif value_type == "elevation":
        return f"{value:.0f} m"
    elif value_type == "count":
        return f"{int(value):,}"
    elif value_type == "days":
        return f"{int(value)} days"
    return str(value)


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


def register_tools(app):
    """Register all challenges-related tools with the MCP server app"""

    @app.tool()
    async def get_goals(goal_type: str = "active") -> str:
        """Get Garmin Connect goals (active, future, or past)

        Args:
            goal_type: Type of goals to retrieve. Options: "active", "future", or "past"
        """
        try:
            goals = garmin_client.get_goals(goal_type)
            if not goals:
                return f"No {goal_type} goals found."
            return json.dumps(goals, indent=2)
        except Exception as e:
            return f"Error retrieving {goal_type} goals: {str(e)}"

    @app.tool()
    async def get_personal_record() -> str:
        """Get personal records for user"""
        try:
            records = garmin_client.get_personal_record()
            if not records:
                return "No personal records found."

            # Curate the output
            curated_records = []
            for record in records:
                type_id = record.get("typeId")
                pr_info = PR_TYPE_MAPPING.get(type_id)

                if pr_info:
                    pr_name, value_type = pr_info
                else:
                    pr_name = f"Unknown Record (typeId={type_id})"
                    value_type = "unknown"

                raw_value = record.get("value")
                formatted_value = _format_pr_value(raw_value, value_type)

                curated_record = {
                    "record_type": pr_name,
                    "type_id": type_id,
                    "value": formatted_value,
                    "raw_value": raw_value,
                    "date": _format_timestamp(record.get("prStartTimeGMT")),
                }

                # Add activity info if available
                activity_id = record.get("activityId")
                if activity_id:
                    curated_record["activity_id"] = activity_id

                curated_records.append(curated_record)

            # Sort by type_id for consistent ordering
            curated_records.sort(key=lambda x: x["type_id"])

            return json.dumps(curated_records, indent=2)
        except Exception as e:
            return f"Error retrieving personal records: {str(e)}"

    @app.tool()
    async def get_earned_badges() -> str:
        """Get earned badges for user"""
        try:
            badges = garmin_client.get_earned_badges()
            if not badges:
                return "No earned badges found."

            # Curate the output
            curated_badges = []
            for badge in badges:
                category_id = badge.get("badgeCategoryId")
                difficulty_id = badge.get("badgeDifficultyId")
                unit_id = badge.get("badgeUnitId")

                # Format progress/target values
                progress = badge.get("badgeProgressValue")
                target = badge.get("badgeTargetValue")
                formatted_progress = _format_badge_value(progress, unit_id)
                formatted_target = _format_badge_value(target, unit_id)

                curated_badge = {
                    "name": badge.get("badgeName"),
                    "category": BADGE_CATEGORY_MAPPING.get(
                        category_id, f"category_{category_id}"
                    ),
                    "difficulty": BADGE_DIFFICULTY_MAPPING.get(
                        difficulty_id, f"level_{difficulty_id}"
                    ),
                    "points": badge.get("badgePoints"),
                    "earned_date": _parse_iso_date(badge.get("badgeEarnedDate")),
                }

                # Add progress info if there's a target
                if target is not None and progress is not None:
                    curated_badge["progress"] = formatted_progress
                    curated_badge["target"] = formatted_target

                # Add challenge date range if applicable
                start_date = _parse_iso_date(badge.get("badgeStartDate"))
                end_date = _parse_iso_date(badge.get("badgeEndDate"))
                if start_date and end_date:
                    curated_badge["challenge_period"] = f"{start_date} to {end_date}"

                # Add linked activity if available
                if badge.get("badgeAssocType") == "activityId" and badge.get(
                    "badgeAssocDataId"
                ):
                    curated_badge["activity_id"] = badge.get("badgeAssocDataId")

                # Add badge series info if part of a series
                if badge.get("badgeSeriesId"):
                    curated_badge["series_id"] = badge.get("badgeSeriesId")

                curated_badges.append(curated_badge)

            # Sort by earned date (most recent first)
            curated_badges.sort(key=lambda x: x.get("earned_date") or "", reverse=True)

            return json.dumps(
                {"total_badges": len(curated_badges), "badges": curated_badges},
                indent=2,
            )
        except Exception as e:
            return f"Error retrieving earned badges: {str(e)}"

    @app.tool()
    async def get_adhoc_challenges(start: int = 0, limit: int = 20) -> str:
        """Get user-created social/group challenges (e.g., step competitions with friends)

        Returns challenges created by users to compete with connections. These are
        different from official Garmin badge challenges.

        Args:
            start: Starting index for pagination (default 0)
            limit: Maximum number of challenges to return (default 20, max 100)
        """
        try:
            challenges = garmin_client.get_adhoc_challenges(start, min(limit, 100))
            if not challenges:
                return "No adhoc challenges found."

            curated_challenges = []
            for challenge in challenges:
                status_id = challenge.get("socialChallengeStatusId")
                activity_type_id = challenge.get("socialChallengeActivityTypeId")

                curated = {
                    "name": challenge.get("adHocChallengeName"),
                    "description": challenge.get("adHocChallengeDesc"),
                    "uuid": challenge.get("uuid"),
                    "activity_type": ADHOC_ACTIVITY_TYPE_MAPPING.get(
                        activity_type_id, f"type_{activity_type_id}"
                    ),
                    "status": CHALLENGE_STATUS_MAPPING.get(status_id, f"status_{status_id}"),
                    "start_date": _parse_iso_date(challenge.get("startDate")),
                    "end_date": _parse_iso_date(challenge.get("endDate")),
                    "your_ranking": challenge.get("userRanking"),
                    "player_count": challenge.get("playerCount"),
                }

                curated_challenges.append(curated)

            # Sort by start date (most recent first)
            curated_challenges.sort(
                key=lambda x: x.get("start_date") or "", reverse=True
            )

            return json.dumps(
                {"total": len(curated_challenges), "challenges": curated_challenges},
                indent=2,
            )
        except Exception as e:
            return f"Error retrieving adhoc challenges: {str(e)}"

    @app.tool()
    async def get_available_badge_challenges(start: int = 1, limit: int = 20) -> str:
        """Get official Garmin badge challenges available to join

        Returns monthly/seasonal challenges from Garmin that the user can join.
        These challenges award badges and points upon completion.

        Args:
            start: Starting index for pagination (starts at 1)
            limit: Maximum number of challenges to return (default 20, max 100)
        """
        try:
            challenges = garmin_client.get_available_badge_challenges(start, min(limit, 100))
            if not challenges:
                return "No available badge challenges found."

            curated_challenges = []
            for challenge in challenges:
                curated = _curate_badge_challenge(challenge)
                # Add joinable status for available challenges
                curated["joinable"] = challenge.get("joinable", True)
                curated_challenges.append(curated)

            # Sort by start date (soonest first)
            curated_challenges.sort(key=lambda x: x.get("start_date") or "")

            return json.dumps(
                {"total": len(curated_challenges), "challenges": curated_challenges},
                indent=2,
            )
        except Exception as e:
            return f"Error retrieving available badge challenges: {str(e)}"

    @app.tool()
    async def get_badge_challenges(start: int = 1, limit: int = 20) -> str:
        """Get all badge challenges the user has joined (completed and in-progress)

        Returns the user's history of badge challenges including progress,
        completion status, and earned dates.

        Args:
            start: Starting index for pagination (starts at 1)
            limit: Maximum number of challenges to return (default 20, max 100)
        """
        try:
            challenges = garmin_client.get_badge_challenges(start, min(limit, 100))
            if not challenges:
                return "No badge challenges found."

            curated_challenges = []
            for challenge in challenges:
                curated = _curate_badge_challenge(challenge)
                curated_challenges.append(curated)

            # Sort by start date (most recent first)
            curated_challenges.sort(
                key=lambda x: x.get("start_date") or "", reverse=True
            )

            return json.dumps(
                {"total": len(curated_challenges), "challenges": curated_challenges},
                indent=2,
            )
        except Exception as e:
            return f"Error retrieving badge challenges: {str(e)}"

    @app.tool()
    async def get_non_completed_badge_challenges(
        start: int = 1, limit: int = 20
    ) -> str:
        """Get badge challenges currently in progress (not yet completed)

        Returns active challenges the user has joined but hasn't completed yet.
        Useful for tracking current progress toward badge goals.

        Args:
            start: Starting index for pagination (starts at 1)
            limit: Maximum number of challenges to return (default 20, max 100)
        """
        try:
            challenges = garmin_client.get_non_completed_badge_challenges(
                start, min(limit, 100)
            )
            if not challenges:
                return "No in-progress badge challenges found."

            curated_challenges = []
            for challenge in challenges:
                curated = _curate_badge_challenge(challenge)
                curated_challenges.append(curated)

            # Sort by end date (ending soonest first)
            curated_challenges.sort(key=lambda x: x.get("end_date") or "")

            return json.dumps(
                {"total": len(curated_challenges), "challenges": curated_challenges},
                indent=2,
            )
        except Exception as e:
            return f"Error retrieving in-progress badge challenges: {str(e)}"

    @app.tool()
    async def get_race_predictions() -> str:
        """Get predicted race times based on current fitness level

        Returns Garmin's predictions for 5K, 10K, half marathon, and marathon
        finish times based on the user's recent training data and VO2 max.
        """
        try:
            predictions = garmin_client.get_race_predictions()
            if not predictions:
                return "No race predictions found."

            # Format predictions with human-readable times
            curated = {
                "prediction_date": predictions.get("calendarDate"),
                "predictions": {
                    "5K": {
                        "time": _format_time(predictions.get("time5K")),
                        "time_seconds": predictions.get("time5K"),
                    },
                    "10K": {
                        "time": _format_time(predictions.get("time10K")),
                        "time_seconds": predictions.get("time10K"),
                    },
                    "half_marathon": {
                        "time": _format_time(predictions.get("timeHalfMarathon")),
                        "time_seconds": predictions.get("timeHalfMarathon"),
                    },
                    "marathon": {
                        "time": _format_time(predictions.get("timeMarathon")),
                        "time_seconds": predictions.get("timeMarathon"),
                    },
                },
            }

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving race predictions: {str(e)}"

    @app.tool()
    async def get_inprogress_virtual_challenges(start: int = 0, limit: int = 20) -> str:
        """Get in-progress virtual challenges/expeditions

        Returns virtual challenges (like walking expeditions on famous trails)
        that the user is currently participating in.

        Args:
            start: Starting index for pagination (default 0)
            limit: Maximum number of challenges to return (default 20, max 100)
        """
        try:
            challenges = garmin_client.get_inprogress_virtual_challenges(
                start, min(limit, 100)
            )
            if not challenges:
                return "No in-progress virtual challenges found."

            # If challenges is a dict with a list, extract it
            if isinstance(challenges, dict):
                challenge_list = challenges.get("challenges", [challenges])
            else:
                challenge_list = challenges if isinstance(challenges, list) else []

            curated_challenges = []
            for challenge in challenge_list:
                curated = {
                    "name": challenge.get("name") or challenge.get("challengeName"),
                    "uuid": challenge.get("uuid"),
                    "start_date": _parse_iso_date(challenge.get("startDate")),
                    "end_date": _parse_iso_date(challenge.get("endDate")),
                }

                # Add progress if available
                progress = challenge.get("progress") or challenge.get("progressValue")
                target = challenge.get("target") or challenge.get("targetValue")
                if progress is not None and target is not None:
                    curated["progress_meters"] = progress
                    curated["target_meters"] = target
                    curated["progress_km"] = f"{progress / 1000:.2f} km"
                    curated["target_km"] = f"{target / 1000:.2f} km"
                    curated["progress_percent"] = _calculate_progress_percent(
                        progress, target
                    )

                curated_challenges.append(curated)

            return json.dumps(
                {"total": len(curated_challenges), "challenges": curated_challenges},
                indent=2,
            )
        except Exception as e:
            return f"Error retrieving in-progress virtual challenges: {str(e)}"

    return app
