"""
Weight management functions for Garmin Connect MCP Server
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
    """Register all weight management tools with the MCP server app"""

    @app.tool()
    async def get_weigh_ins(start_date: str, end_date: str) -> str:
        """Get weight measurements between specified dates

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            data = garmin_client.get_weigh_ins(start_date, end_date)
            if not data:
                return f"No weight measurements found between {start_date} and {end_date}."

            # API returns nested structure: {dailyWeightSummaries: [{allWeightMetrics: [...]}]}
            daily_summaries = data.get("dailyWeightSummaries", [])
            if not daily_summaries:
                return f"No weight measurements found between {start_date} and {end_date}."

            # Extract all measurements from daily summaries
            all_measurements = []
            for day in daily_summaries:
                metrics = day.get("allWeightMetrics", [])
                all_measurements.extend(metrics)

            # Curate the response
            curated = {
                "date_range": {"start": start_date, "end": end_date},
                "measurement_count": len(all_measurements),
                "days_with_data": len(daily_summaries),
                "measurements": []
            }

            for w in all_measurements:
                measurement = {
                    "date": w.get("calendarDate"),
                    "weight_grams": w.get("weight"),
                    "weight_kg": round(w.get("weight", 0) / 1000, 2) if w.get("weight") else None,
                    "bmi": w.get("bmi"),
                    "body_fat_percent": w.get("bodyFat"),
                    "body_water_percent": w.get("bodyWater"),
                    "bone_mass_grams": w.get("boneMass"),
                    "muscle_mass_grams": w.get("muscleMass"),
                    "source_type": w.get("sourceType"),
                    "timestamp_gmt": w.get("timestampGMT"),
                }
                # Remove None values
                measurement = {k: v for k, v in measurement.items() if v is not None}
                curated["measurements"].append(measurement)

            # Sort by date descending (most recent first)
            curated["measurements"].sort(
                key=lambda x: x.get("date") or "", reverse=True
            )

            # Include average if available
            total_avg = data.get("totalAverage", {})
            if total_avg.get("weight"):
                curated["average_weight_grams"] = total_avg["weight"]
                curated["average_weight_kg"] = round(total_avg["weight"] / 1000, 2)

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving weight measurements: {str(e)}"

    @app.tool()
    async def get_daily_weigh_ins(date: str) -> str:
        """Get weight measurements for a specific date

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            data = garmin_client.get_daily_weigh_ins(date)
            if not data:
                return f"No weight measurements found for {date}."

            # API returns nested structure: {dateWeightList: [...]}
            weight_list = data.get("dateWeightList", [])
            if not weight_list:
                return f"No weight measurements found for {date}."

            # Curate the response
            curated = {
                "date": date,
                "measurement_count": len(weight_list),
                "measurements": []
            }

            for w in weight_list:
                measurement = {
                    "weight_grams": w.get("weight"),
                    "weight_kg": round(w.get("weight", 0) / 1000, 2) if w.get("weight") else None,
                    "bmi": w.get("bmi"),
                    "body_fat_percent": w.get("bodyFat"),
                    "body_water_percent": w.get("bodyWater"),
                    "bone_mass_grams": w.get("boneMass"),
                    "muscle_mass_grams": w.get("muscleMass"),
                    "source_type": w.get("sourceType"),
                    "timestamp_gmt": w.get("timestampGMT"),
                }
                # Remove None values
                measurement = {k: v for k, v in measurement.items() if v is not None}
                curated["measurements"].append(measurement)

            # Include average if available
            total_avg = data.get("totalAverage", {})
            if total_avg.get("weight"):
                curated["average_weight_grams"] = total_avg["weight"]
                curated["average_weight_kg"] = round(total_avg["weight"] / 1000, 2)

            return json.dumps(curated, indent=2)
        except Exception as e:
            return f"Error retrieving daily weight measurements: {str(e)}"

    @app.tool()
    async def delete_weigh_ins(date: str, delete_all: bool = True) -> str:
        """Delete weight measurements for a specific date

        Args:
            date: Date in YYYY-MM-DD format
            delete_all: Whether to delete all measurements for the day
        """
        try:
            # API returns count of deleted entries
            deleted_count = garmin_client.delete_weigh_ins(date, delete_all=delete_all)
            return json.dumps({
                "status": "success",
                "date": date,
                "deleted_count": deleted_count if isinstance(deleted_count, int) else 0,
                "message": f"Weight measurements deleted for {date}"
            }, indent=2)
        except Exception as e:
            return f"Error deleting weight measurements: {str(e)}"

    @app.tool()
    async def add_weigh_in(weight: float, unit_key: str = "kg") -> str:
        """Add a new weight measurement

        Args:
            weight: Weight value
            unit_key: Unit of weight ('kg' or 'lb')
        """
        try:
            result = garmin_client.add_weigh_in(weight=weight, unitKey=unit_key)
            # Return structured response
            return json.dumps({
                "status": "success",
                "weight": weight,
                "unit": unit_key,
                "message": "Weight measurement added successfully"
            }, indent=2)
        except Exception as e:
            return f"Error adding weight measurement: {str(e)}"

    @app.tool()
    async def add_weigh_in_with_timestamps(
        weight: float,
        unit_key: str = "kg",
        date_timestamp: str = None,
        gmt_timestamp: str = None
    ) -> str:
        """Add a new weight measurement with specific timestamps

        Args:
            weight: Weight value
            unit_key: Unit of weight ('kg' or 'lb')
            date_timestamp: Local timestamp in format YYYY-MM-DDThh:mm:ss
            gmt_timestamp: GMT timestamp in format YYYY-MM-DDThh:mm:ss
        """
        try:
            if date_timestamp is None or gmt_timestamp is None:
                # Generate timestamps if not provided
                now = datetime.datetime.now()
                date_timestamp = now.strftime('%Y-%m-%dT%H:%M:%S')
                gmt_timestamp = now.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')

            result = garmin_client.add_weigh_in_with_timestamps(
                weight=weight,
                unitKey=unit_key,
                dateTimestamp=date_timestamp,
                gmtTimestamp=gmt_timestamp
            )
            # Return structured response
            return json.dumps({
                "status": "success",
                "weight": weight,
                "unit": unit_key,
                "timestamp_local": date_timestamp,
                "timestamp_gmt": gmt_timestamp,
                "message": "Weight measurement added successfully"
            }, indent=2)
        except Exception as e:
            return f"Error adding weight measurement with timestamps: {str(e)}"

    return app
