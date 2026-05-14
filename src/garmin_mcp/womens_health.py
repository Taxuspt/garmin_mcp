"""
Women's health functions for Garmin Connect MCP Server
"""
import json
from datetime import date, timedelta
from typing import Any, List

# The garmin_client will be set by the main file
garmin_client = None

MENSTRUAL_CALENDAR_MAX_DAYS = 92


def _stitch_menstrual_chunks(chunks: List[Any]) -> Any:
    """Combine chunked menstrual calendar responses without changing shape."""
    if len(chunks) == 1:
        return chunks[0]

    if all(isinstance(chunk, list) for chunk in chunks):
        stitched = []
        for chunk in chunks:
            stitched.extend(chunk)
        return stitched

    if all(isinstance(chunk, dict) for chunk in chunks) and all(
        isinstance(value, list)
        for chunk in chunks
        for value in chunk.values()
    ):
        stitched = {}
        for chunk in chunks:
            for key, value in chunk.items():
                stitched.setdefault(key, []).extend(value)
        return stitched

    return chunks


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


def register_tools(app):
    """Register all women's health tools with the MCP server app"""
    
    @app.tool()
    async def get_pregnancy_summary() -> str:
        """Get pregnancy summary data"""
        try:
            summary = garmin_client.get_pregnancy_summary()
            if not summary:
                return "No pregnancy summary data found."
            return json.dumps(summary, indent=2)
        except Exception as e:
            return f"Error retrieving pregnancy summary: {str(e)}"
    
    @app.tool()
    async def get_menstrual_data_for_date(date: str) -> str:
        """Get menstrual data for a specific date
        
        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            data = garmin_client.get_menstrual_data_for_date(date)
            if not data:
                return f"No menstrual data found for {date}."
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error retrieving menstrual data: {str(e)}"
    
    @app.tool()
    async def get_menstrual_calendar_data(start_date: str, end_date: str) -> str:
        """Get menstrual calendar data between specified dates

        Automatically chunks requests longer than 92 days, Garmin's
        server-side limit, and stitches the responses together.
        
        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
            if end < start:
                return f"end_date {end_date} is before start_date {start_date}."

            chunks = []
            cursor = start
            while cursor <= end:
                window_end = min(
                    cursor + timedelta(days=MENSTRUAL_CALENDAR_MAX_DAYS - 1),
                    end,
                )
                data = garmin_client.get_menstrual_calendar_data(
                    cursor.isoformat(),
                    window_end.isoformat(),
                )
                if data:
                    chunks.append(data)
                cursor = window_end + timedelta(days=1)

            if not chunks:
                return (
                    f"No menstrual calendar data found between {start_date} "
                    f"and {end_date}."
                )

            return json.dumps(_stitch_menstrual_chunks(chunks), indent=2)
        except Exception as e:
            return f"Error retrieving menstrual calendar data: {str(e)}"

    return app
