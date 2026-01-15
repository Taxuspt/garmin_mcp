"""
Challenges and badges functions for Garmin Connect MCP Server
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
            return json.dumps(records, indent=2)
        except Exception as e:
            return f"Error retrieving personal records: {str(e)}"

    @app.tool()
    async def get_earned_badges() -> str:
        """Get earned badges for user"""
        try:
            badges = garmin_client.get_earned_badges()
            if not badges:
                return "No earned badges found."
            return json.dumps(badges, indent=2)
        except Exception as e:
            return f"Error retrieving earned badges: {str(e)}"

    @app.tool()
    async def get_adhoc_challenges(start: int = 0, limit: int = 100) -> str:
        """Get adhoc challenges data

        Args:
            start: Starting index for challenges retrieval
            limit: Maximum number of challenges to retrieve
        """
        try:
            challenges = garmin_client.get_adhoc_challenges(start, limit)
            if not challenges:
                return "No adhoc challenges found."
            return json.dumps(challenges, indent=2)
        except Exception as e:
            return f"Error retrieving adhoc challenges: {str(e)}"

    @app.tool()
    async def get_available_badge_challenges(start: int = 1, limit: int = 100) -> str:
        """Get available badge challenges data

        Args:
            start: Starting index for challenges retrieval (starts at 1)
            limit: Maximum number of challenges to retrieve
        """
        try:
            challenges = garmin_client.get_available_badge_challenges(start, limit)
            if not challenges:
                return "No available badge challenges found."
            return json.dumps(challenges, indent=2)
        except Exception as e:
            return f"Error retrieving available badge challenges: {str(e)}"

    @app.tool()
    async def get_badge_challenges(start: int = 1, limit: int = 100) -> str:
        """Get badge challenges data

        Args:
            start: Starting index for challenges retrieval (starts at 1)
            limit: Maximum number of challenges to retrieve
        """
        try:
            challenges = garmin_client.get_badge_challenges(start, limit)
            if not challenges:
                return "No badge challenges found."
            return json.dumps(challenges, indent=2)
        except Exception as e:
            return f"Error retrieving badge challenges: {str(e)}"

    @app.tool()
    async def get_non_completed_badge_challenges(start: int = 1, limit: int = 100) -> str:
        """Get non-completed badge challenges data

        Args:
            start: Starting index for challenges retrieval (starts at 1)
            limit: Maximum number of challenges to retrieve
        """
        try:
            challenges = garmin_client.get_non_completed_badge_challenges(start, limit)
            if not challenges:
                return "No non-completed badge challenges found."
            return json.dumps(challenges, indent=2)
        except Exception as e:
            return f"Error retrieving non-completed badge challenges: {str(e)}"

    @app.tool()
    async def get_race_predictions() -> str:
        """Get race predictions for user"""
        try:
            predictions = garmin_client.get_race_predictions()
            if not predictions:
                return "No race predictions found."
            return json.dumps(predictions, indent=2)
        except Exception as e:
            return f"Error retrieving race predictions: {str(e)}"

    @app.tool()
    async def get_inprogress_virtual_challenges(start_date: str, end_date: str) -> str:
        """Get in-progress virtual challenges/expeditions between dates

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            challenges = garmin_client.get_inprogress_virtual_challenges(
                start_date, end_date
            )
            if not challenges:
                return f"No in-progress virtual challenges found between {start_date} and {end_date}."
            return json.dumps(challenges, indent=2)
        except Exception as e:
            return f"Error retrieving in-progress virtual challenges: {str(e)}"

    return app