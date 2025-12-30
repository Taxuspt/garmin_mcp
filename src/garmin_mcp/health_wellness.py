"""
Health & Wellness Data functions for Garmin Connect MCP Server
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
    """Register all health and wellness tools with the MCP server app"""
    
    @app.tool()
    async def get_stats(date: str) -> str:
        """Get daily activity stats
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            stats = garmin_client.get_stats(date)
            if not stats:
                return f"No stats found for {date}"
            
            return stats
        except Exception as e:
            return f"Error retrieving stats: {str(e)}"

    @app.tool()
    async def get_user_summary(date: str) -> str:
        """Get user summary data (compatible with garminconnect-ha)
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            summary = garmin_client.get_user_summary(date)
            if not summary:
                return f"No user summary found for {date}"
            
            return summary
        except Exception as e:
            return f"Error retrieving user summary: {str(e)}"

    @app.tool()
    async def get_body_composition(start_date: str, end_date: str = None) -> str:
        """Get body composition data for a single date or date range
        
        Args:
            start_date: Date in YYYY-MM-DD format or start date if end_date provided
            end_date: Optional end date in YYYY-MM-DD format for date range
        """
        try:
            if end_date:
                composition = garmin_client.get_body_composition(start_date, end_date)
                if not composition:
                    return f"No body composition data found between {start_date} and {end_date}"
            else:
                composition = garmin_client.get_body_composition(start_date)
                if not composition:
                    return f"No body composition data found for {start_date}"
            
            return composition
        except Exception as e:
            return f"Error retrieving body composition data: {str(e)}"

    @app.tool()
    async def get_stats_and_body(date: str) -> str:
        """Get stats and body composition data
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            data = garmin_client.get_stats_and_body(date)
            if not data:
                return f"No stats and body composition data found for {date}"
            
            return data
        except Exception as e:
            return f"Error retrieving stats and body composition data: {str(e)}"

    @app.tool()
    async def get_steps_data(date: str) -> str:
        """Get steps data
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            steps_data = garmin_client.get_steps_data(date)
            if not steps_data:
                return f"No steps data found for {date}"
            
            return steps_data
        except Exception as e:
            return f"Error retrieving steps data: {str(e)}"

    @app.tool()
    async def get_daily_steps(start_date: str, end_date: str) -> str:
        """Get steps data for a date range
        
        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            steps_data = garmin_client.get_daily_steps(start_date, end_date)
            if not steps_data:
                return f"No daily steps data found between {start_date} and {end_date}"
            
            return steps_data
        except Exception as e:
            return f"Error retrieving daily steps data: {str(e)}"

    @app.tool()
    async def get_training_readiness(date: str) -> str:
        """Get training readiness data
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            readiness = garmin_client.get_training_readiness(date)
            if not readiness:
                return f"No training readiness data found for {date}"
            
            return readiness
        except Exception as e:
            return f"Error retrieving training readiness data: {str(e)}"

    @app.tool()
    async def get_body_battery(start_date: str, end_date: str) -> str:
        """Get body battery data
        
        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            battery_data = garmin_client.get_body_battery(start_date, end_date)
            if not battery_data:
                return f"No body battery data found between {start_date} and {end_date}"
            
            return battery_data
        except Exception as e:
            return f"Error retrieving body battery data: {str(e)}"

    @app.tool()
    async def get_body_battery_events(date: str) -> str:
        """Get body battery events data
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            events = garmin_client.get_body_battery_events(date)
            if not events:
                return f"No body battery events found for {date}"
            
            return events
        except Exception as e:
            return f"Error retrieving body battery events: {str(e)}"

    @app.tool()
    async def get_blood_pressure(start_date: str, end_date: str) -> str:
        """Get blood pressure data
        
        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            bp_data = garmin_client.get_blood_pressure(start_date, end_date)
            if not bp_data:
                return f"No blood pressure data found between {start_date} and {end_date}"
            
            return bp_data
        except Exception as e:
            return f"Error retrieving blood pressure data: {str(e)}"

    @app.tool()
    async def get_floors(date: str) -> str:
        """Get floors climbed data
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            floors_data = garmin_client.get_floors(date)
            if not floors_data:
                return f"No floors data found for {date}"
            
            return floors_data
        except Exception as e:
            return f"Error retrieving floors data: {str(e)}"

    @app.tool()
    async def get_training_status(date: str) -> str:
        """Get training status data
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            status = garmin_client.get_training_status(date)
            if not status:
                return f"No training status data found for {date}"
            
            return status
        except Exception as e:
            return f"Error retrieving training status data: {str(e)}"

    @app.tool()
    async def get_rhr_day(date: str) -> str:
        """Get resting heart rate data
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            rhr_data = garmin_client.get_rhr_day(date)
            if not rhr_data:
                return f"No resting heart rate data found for {date}"
            
            return rhr_data
        except Exception as e:
            return f"Error retrieving resting heart rate data: {str(e)}"

    @app.tool()
    async def get_heart_rates(date: str) -> str:
        """Get heart rate data
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            hr_data = garmin_client.get_heart_rates(date)
            if not hr_data:
                return f"No heart rate data found for {date}"
            
            return hr_data
        except Exception as e:
            return f"Error retrieving heart rate data: {str(e)}"

    @app.tool()
    async def get_hydration_data(date: str) -> str:
        """Get hydration data
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            hydration_data = garmin_client.get_hydration_data(date)
            if not hydration_data:
                return f"No hydration data found for {date}"
            
            return hydration_data
        except Exception as e:
            return f"Error retrieving hydration data: {str(e)}"

    @app.tool()
    async def get_sleep_data(date: str) -> str:
        """Get sleep data

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            sleep_data = garmin_client.get_sleep_data(date)
            if not sleep_data:
                return f"No sleep data found for {date}"

            return sleep_data
        except Exception as e:
            return f"Error retrieving sleep data: {str(e)}"

    @app.tool()
    async def get_sleep_summary(date: str) -> str:
        """Get sleep summary with only essential metrics (lightweight version)

        This endpoint returns a compact summary of sleep data (~350 bytes) instead of
        the full granular data (~50KB). Ideal for daily health checkups and LLM integrations
        where the full time-series data would overwhelm the context window.

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            sleep_data = garmin_client.get_sleep_data(date)
            if not sleep_data:
                return f"No sleep summary found for {date}"

            # Extract only essential summary metrics
            summary = {}

            # Extract data from dailySleepDTO if available
            daily_sleep = sleep_data.get('dailySleepDTO', {})
            if daily_sleep:
                # Sleep duration and timing
                summary['sleepTimeSeconds'] = daily_sleep.get('sleepTimeSeconds')
                summary['napTimeSeconds'] = daily_sleep.get('napTimeSeconds')
                summary['sleepStartTimestampGMT'] = daily_sleep.get('sleepStartTimestampGMT')
                summary['sleepEndTimestampGMT'] = daily_sleep.get('sleepEndTimestampGMT')

                # Sleep score and quality
                summary['overallSleepScore'] = daily_sleep.get('sleepScores', {}).get('overall', {}).get('value')
                summary['sleepScoreQualifier'] = daily_sleep.get('sleepScores', {}).get('overall', {}).get('qualifierKey')
                summary['sleepScoreFeedback'] = daily_sleep.get('sleepScores', {}).get('overall', {}).get('optimalStart')

                # Sleep phases (in seconds)
                summary['deepSleepSeconds'] = daily_sleep.get('deepSleepSeconds')
                summary['lightSleepSeconds'] = daily_sleep.get('lightSleepSeconds')
                summary['remSleepSeconds'] = daily_sleep.get('remSleepSeconds')
                summary['awakeSleepSeconds'] = daily_sleep.get('awakeSleepSeconds')

                # Sleep disruptions
                summary['awakeCount'] = daily_sleep.get('awakeCount')
                summary['restlessMomentsCount'] = daily_sleep.get('restlessMomentsCount')

                # Average physiological metrics
                summary['avgSleepStress'] = daily_sleep.get('avgSleepStress')
                summary['restingHeartRate'] = daily_sleep.get('restingHeartRate')

            # Extract SpO2 summary if available
            spo2_summary = sleep_data.get('wellnessSpO2SleepSummaryDTO', {})
            if spo2_summary:
                summary['avgSpO2'] = spo2_summary.get('averageSpo2')
                summary['lowestSpO2'] = spo2_summary.get('lowestSpo2')

            # Add HRV data if available at top level
            if 'avgOvernightHrv' in sleep_data:
                summary['avgOvernightHrv'] = sleep_data.get('avgOvernightHrv')

            # Calculate sleep phase percentages if total sleep time is available
            total_sleep = summary.get('sleepTimeSeconds', 0)
            if total_sleep and total_sleep > 0:
                summary['deepSleepPercentage'] = round((summary.get('deepSleepSeconds', 0) / total_sleep) * 100, 1)
                summary['lightSleepPercentage'] = round((summary.get('lightSleepSeconds', 0) / total_sleep) * 100, 1)
                summary['remSleepPercentage'] = round((summary.get('remSleepSeconds', 0) / total_sleep) * 100, 1)

            # Convert sleep duration to hours for convenience
            if total_sleep:
                summary['sleepDurationHours'] = round(total_sleep / 3600, 2)

            return summary
        except Exception as e:
            return f"Error retrieving sleep summary: {str(e)}"

    @app.tool()
    async def get_stress_data(date: str) -> str:
        """Get stress data
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            stress_data = garmin_client.get_stress_data(date)
            if not stress_data:
                return f"No stress data found for {date}"
            
            return stress_data
        except Exception as e:
            return f"Error retrieving stress data: {str(e)}"

    @app.tool()
    async def get_respiration_data(date: str) -> str:
        """Get respiration data
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            respiration_data = garmin_client.get_respiration_data(date)
            if not respiration_data:
                return f"No respiration data found for {date}"
            
            return respiration_data
        except Exception as e:
            return f"Error retrieving respiration data: {str(e)}"

    @app.tool()
    async def get_spo2_data(date: str) -> str:
        """Get SpO2 (blood oxygen) data
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            spo2_data = garmin_client.get_spo2_data(date)
            if not spo2_data:
                return f"No SpO2 data found for {date}"
            
            return spo2_data
        except Exception as e:
            return f"Error retrieving SpO2 data: {str(e)}"

    @app.tool()
    async def get_all_day_stress(date: str) -> str:
        """Get all-day stress data
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            stress_data = garmin_client.get_all_day_stress(date)
            if not stress_data:
                return f"No all-day stress data found for {date}"
            
            return stress_data
        except Exception as e:
            return f"Error retrieving all-day stress data: {str(e)}"

    @app.tool()
    async def get_all_day_events(date: str) -> str:
        """Get daily wellness events data
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            events = garmin_client.get_all_day_events(date)
            if not events:
                return f"No daily wellness events found for {date}"
            
            return events
        except Exception as e:
            return f"Error retrieving daily wellness events: {str(e)}"

    return app