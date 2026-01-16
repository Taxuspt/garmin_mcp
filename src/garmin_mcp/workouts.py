"""
Workout-related functions for Garmin Connect MCP Server
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
    """Register all workout-related tools with the MCP server app"""
    
    @app.tool()
    async def get_workouts() -> str:
        """Get all workouts"""
        try:
            workouts = garmin_client.get_workouts()
            if not workouts:
                return "No workouts found."
            return workouts
        except Exception as e:
            return f"Error retrieving workouts: {str(e)}"
    
    @app.tool()
    async def get_workout_by_id(workout_id: int) -> str:
        """Get details for a specific workout
        
        Args:
            workout_id: ID of the workout to retrieve
        """
        try:
            workout = garmin_client.get_workout_by_id(workout_id)
            if not workout:
                return f"No workout found with ID {workout_id}."
            return workout
        except Exception as e:
            return f"Error retrieving workout: {str(e)}"
    
    @app.tool()
    async def download_workout(workout_id: int) -> str:
        """Download a workout as a FIT file (this will return a message about how to access the file)
        
        Args:
            workout_id: ID of the workout to download
        """
        try:
            workout_data = garmin_client.download_workout(workout_id)
            if not workout_data:
                return f"No workout data found for workout with ID {workout_id}."
            
            # Since we can't return binary data directly, we'll inform the user
            return f"Workout data for ID {workout_id} is available. The data is in FIT format and would need to be saved to a file."
        except Exception as e:
            return f"Error downloading workout: {str(e)}"
    
    @app.tool()
    async def upload_workout(workout_data: dict) -> str:
        """Upload a workout from JSON data

        Args:
            workout_data: Dictionary containing workout data (will be converted to JSON)
        """
        try:
            import json
            workout_json = json.dumps(workout_data)
            result = garmin_client.upload_workout(workout_json)
            return result
        except Exception as e:
            return f"Error uploading workout: {str(e)}"
            
    @app.tool()
    async def upload_activity(file_path: str) -> str:
        """Upload an activity from a file (this is just a placeholder - file operations would need special handling)

        Args:
            file_path: Path to the activity file (.fit, .gpx, .tcx)
        """
        try:
            # This is a placeholder - actual implementation would need to handle file access
            return f"Activity upload from file path {file_path} is not supported in this MCP server implementation."
        except Exception as e:
            return f"Error uploading activity: {str(e)}"

    @app.tool()
    async def get_scheduled_workouts(start_date: str, end_date: str) -> str:
        """Get scheduled workouts between two dates.

        Returns workouts that have been scheduled on the Garmin Connect calendar,
        including their scheduled dates.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            # Query for scheduled workouts using GraphQL
            query = {
                "query": f'query{{workoutScheduleSummariesScalar(startDate:"{start_date}", endDate:"{end_date}")}}'
            }
            result = garmin_client.query_garmin_graphql(query)

            if not result or "data" not in result:
                return "No scheduled workouts found or error querying data."

            scheduled = result.get("data", {}).get("workoutScheduleSummariesScalar", [])

            if not scheduled:
                return f"No workouts scheduled between {start_date} and {end_date}."

            return scheduled
        except Exception as e:
            return f"Error retrieving scheduled workouts: {str(e)}"

    @app.tool()
    async def get_training_plan_workouts(calendar_date: str) -> str:
        """Get training plan workouts for a specific date.

        Returns workouts from your active training plan scheduled for the given date.

        Args:
            calendar_date: Date in YYYY-MM-DD format
        """
        try:
            # Query for training plan workouts using GraphQL
            query = {
                "query": f'query{{trainingPlanScalar(calendarDate:"{calendar_date}", lang:"en-US", firstDayOfWeek:"monday")}}'
            }
            result = garmin_client.query_garmin_graphql(query)

            if not result or "data" not in result:
                return "No training plan data found or error querying data."

            plan_data = result.get("data", {}).get("trainingPlanScalar", {})
            workouts = plan_data.get("trainingPlanWorkoutScheduleDTOS", [])

            if not workouts:
                return f"No training plan workouts scheduled for {calendar_date}."

            return plan_data
        except Exception as e:
            return f"Error retrieving training plan workouts: {str(e)}"

    @app.tool()
    async def schedule_workout(workout_id: int, calendar_date: str) -> str:
        """Schedule a workout to a specific calendar date.

        This adds an existing workout from your Garmin workout library
        to your Garmin Connect calendar on the specified date.

        Args:
            workout_id: ID of the workout to schedule (get IDs from get_workouts)
            calendar_date: Date to schedule the workout in YYYY-MM-DD format
        """
        try:
            url = f"workout-service/schedule/{workout_id}"
            response = garmin_client.garth.post("connectapi", url, json={"date": calendar_date})

            if response.status_code == 200:
                return f"Successfully scheduled workout {workout_id} for {calendar_date}"
            else:
                return f"Failed to schedule workout: HTTP {response.status_code}"
        except Exception as e:
            return f"Error scheduling workout: {str(e)}"

    return app