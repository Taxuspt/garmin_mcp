"""
Training and performance functions for Garmin Connect MCP Server
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
    """Register all training-related tools with the MCP server app"""
    
    @app.tool()
    async def get_progress_summary_between_dates(
        start_date: str, end_date: str, metric: str
    ) -> str:
        """Get progress summary for a metric between dates

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            metric: Metric to get progress for (e.g., "elevationGain", "duration", "distance", "movingDuration")
        """
        try:
            summary = garmin_client.get_progress_summary_between_dates(
                start_date, end_date, metric
            )
            if not summary:
                return f"No progress summary found for {metric} between {start_date} and {end_date}."
            return json.dumps(summary, indent=2)
        except Exception as e:
            return f"Error retrieving progress summary: {str(e)}"
    
    @app.tool()
    async def get_hill_score(start_date: str, end_date: str) -> str:
        """Get hill score data between dates

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            hill_score = garmin_client.get_hill_score(start_date, end_date)
            if not hill_score:
                return f"No hill score data found between {start_date} and {end_date}."
            return json.dumps(hill_score, indent=2)
        except Exception as e:
            return f"Error retrieving hill score data: {str(e)}"
    
    @app.tool()
    async def get_endurance_score(start_date: str, end_date: str) -> str:
        """Get endurance score data between dates

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            endurance_score = garmin_client.get_endurance_score(start_date, end_date)
            if not endurance_score:
                return f"No endurance score data found between {start_date} and {end_date}."
            return json.dumps(endurance_score, indent=2)
        except Exception as e:
            return f"Error retrieving endurance score data: {str(e)}"
    
    @app.tool()
    async def get_training_effect(activity_id: int) -> str:
        """Get training effect data for a specific activity
        
        Args:
            activity_id: ID of the activity to retrieve training effect for
        """
        try:
            effect = garmin_client.get_training_effect(activity_id)
            if not effect:
                return f"No training effect data found for activity with ID {activity_id}."
            return json.dumps(effect, indent=2)
        except Exception as e:
            return f"Error retrieving training effect data: {str(e)}"
    
    @app.tool()
    async def get_max_metrics(date: str) -> str:
        """Get max metrics data (like VO2 Max and fitness age)
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            metrics = garmin_client.get_max_metrics(date)
            if not metrics:
                return f"No max metrics data found for {date}."
            return json.dumps(metrics, indent=2)
        except Exception as e:
            return f"Error retrieving max metrics data: {str(e)}"
    
    @app.tool()
    async def get_hrv_data(date: str) -> str:
        """Get Heart Rate Variability (HRV) data
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            hrv_data = garmin_client.get_hrv_data(date)
            if not hrv_data:
                return f"No HRV data found for {date}."
            return json.dumps(hrv_data, indent=2)
        except Exception as e:
            return f"Error retrieving HRV data: {str(e)}"
    
    @app.tool()
    async def get_fitnessage_data(date: str) -> str:
        """Get fitness age data
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            fitness_age = garmin_client.get_fitnessage_data(date)
            if not fitness_age:
                return f"No fitness age data found for {date}."
            return json.dumps(fitness_age, indent=2)
        except Exception as e:
            return f"Error retrieving fitness age data: {str(e)}"
    
    @app.tool()
    async def request_reload(date: str) -> str:
        """Request reload of epoch data
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            result = garmin_client.request_reload(date)
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error requesting data reload: {str(e)}"

    return app