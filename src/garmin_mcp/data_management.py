"""
Data management functions for Garmin Connect MCP Server
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
    """Register all data management tools with the MCP server app"""
    
    @app.tool()
    async def add_body_composition(
        date: str,
        weight: float,
        percent_fat: Optional[float] = None,
        percent_hydration: Optional[float] = None,
        visceral_fat_mass: Optional[float] = None,
        bone_mass: Optional[float] = None,
        muscle_mass: Optional[float] = None,
        basal_met: Optional[float] = None,
        active_met: Optional[float] = None,
        physique_rating: Optional[int] = None,
        metabolic_age: Optional[float] = None,
        visceral_fat_rating: Optional[int] = None,
        bmi: Optional[float] = None
    ) -> str:
        """Add body composition data
        
        Args:
            date: Date in YYYY-MM-DD format
            weight: Weight in kg
            percent_fat: Body fat percentage
            percent_hydration: Hydration percentage
            visceral_fat_mass: Visceral fat mass
            bone_mass: Bone mass
            muscle_mass: Muscle mass
            basal_met: Basal metabolic rate
            active_met: Active metabolic rate
            physique_rating: Physique rating
            metabolic_age: Metabolic age
            visceral_fat_rating: Visceral fat rating
            bmi: Body Mass Index
        """
        try:
            result = garmin_client.add_body_composition(
                date,
                weight=weight,
                percent_fat=percent_fat,
                percent_hydration=percent_hydration,
                visceral_fat_mass=visceral_fat_mass,
                bone_mass=bone_mass,
                muscle_mass=muscle_mass,
                basal_met=basal_met,
                active_met=active_met,
                physique_rating=physique_rating,
                metabolic_age=metabolic_age,
                visceral_fat_rating=visceral_fat_rating,
                bmi=bmi
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error adding body composition data: {str(e)}"
    
    @app.tool()
    async def set_blood_pressure(
        systolic: int,
        diastolic: int,
        pulse: int,
        notes: Optional[str] = None
    ) -> str:
        """Set blood pressure values
        
        Args:
            systolic: Systolic pressure (top number)
            diastolic: Diastolic pressure (bottom number)
            pulse: Pulse rate
            notes: Optional notes
        """
        try:
            result = garmin_client.set_blood_pressure(
                systolic, diastolic, pulse, notes=notes
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error setting blood pressure values: {str(e)}"
    
    @app.tool()
    async def add_hydration_data(
        value_in_ml: int,
        cdate: str,
        timestamp: str
    ) -> str:
        """Add hydration data
        
        Args:
            value_in_ml: Amount of liquid in milliliters
            cdate: Date in YYYY-MM-DD format
            timestamp: Timestamp in YYYY-MM-DDThh:mm:ss.sss format
        """
        try:
            result = garmin_client.add_hydration_data(
                value_in_ml=value_in_ml,
                cdate=cdate,
                timestamp=timestamp
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error adding hydration data: {str(e)}"

    return app