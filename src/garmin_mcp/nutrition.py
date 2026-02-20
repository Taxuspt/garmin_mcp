"""
Nutrition/food logging functions for Garmin Connect MCP Server
"""
import json
from typing import Optional

from garth.exc import GarthHTTPError

# The garmin_client will be set by the main file
garmin_client = None


def _num_to_str(value: float) -> str:
    """Format a number as string, dropping .0 for whole numbers.

    Garmin's API expects integer strings like "160" not "160.0".
    """
    return str(int(value)) if value == int(value) else str(value)


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


def register_tools(app):
    """Register all nutrition tools with the MCP server app"""

    @app.tool()
    async def get_nutrition_daily_food_log(date: str) -> str:
        """Get daily food consumption records for a date

        Returns food items logged throughout the day including calories,
        macronutrients, and meal associations.

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            url = f"/nutrition-service/food/logs/{date}"
            data = garmin_client.connectapi(url)
            if not data:
                return f"No food log data found for {date}."
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error retrieving food log data: {str(e)}"

    @app.tool()
    async def get_nutrition_daily_meals(date: str) -> str:
        """Get daily meal summaries for a date

        Returns meal-level summaries (breakfast, lunch, dinner, snacks)
        with nutritional totals for each meal. Each meal includes a mealId
        needed for logging food items to that meal.

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            url = f"/nutrition-service/meals/{date}"
            data = garmin_client.connectapi(url)
            if not data:
                return f"No meal data found for {date}."
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error retrieving meal data: {str(e)}"

    @app.tool()
    async def get_nutrition_daily_settings(date: str) -> str:
        """Get nutrition plan/settings for a date

        Returns the user's nutrition goals and targets including
        calorie targets, macronutrient goals, and plan configuration.

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            url = f"/nutrition-service/settings/{date}"
            data = garmin_client.connectapi(url)
            if not data:
                return f"No nutrition settings found for {date}."
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error retrieving nutrition settings: {str(e)}"

    @app.tool()
    async def get_custom_foods(
        search: str = "",
        start: int = 0,
        limit: int = 20,
    ) -> str:
        """Search or list user's custom foods

        Returns custom foods the user has created, with optional search
        filtering. Use this to find foodId and servingId for logging.

        Args:
            search: Optional search expression to filter foods
            start: Starting index for pagination (default 0)
            limit: Maximum number of results (default 20)
        """
        try:
            url = (
                f"/nutrition-service/customFood"
                f"?searchExpression={search}"
                f"&start={start}&limit={limit}"
                f"&includeContent=true"
            )
            data = garmin_client.connectapi(url)
            if not data:
                return "No custom foods found."
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error retrieving custom foods: {str(e)}"

    @app.tool()
    async def get_custom_food_serving_units() -> str:
        """Get available serving units for custom foods

        Returns the list of valid serving units (e.g. G, ML, OZ)
        that can be used when creating custom foods.
        """
        try:
            url = "/nutrition-service/metadata/customFoodServingUnits"
            data = garmin_client.connectapi(url)
            if not data:
                return "No serving units found."
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error retrieving serving units: {str(e)}"

    @app.tool()
    async def create_custom_food(
        food_name: str,
        calories: float,
        serving_unit: str = "G",
        number_of_units: float = 100,
        carbs: Optional[float] = None,
        protein: Optional[float] = None,
        fat: Optional[float] = None,
        fiber: Optional[float] = None,
        sugar: Optional[float] = None,
        saturated_fat: Optional[float] = None,
        sodium: Optional[float] = None,
        cholesterol: Optional[float] = None,
        potassium: Optional[float] = None,
    ) -> str:
        """Create a custom food in the user's Garmin nutrition library

        Creates a new food item with nutritional information per serving.
        The response includes foodId and servingId needed for logging.

        Args:
            food_name: Name of the custom food (e.g. "Homemade Chocolate Cookies")
            calories: Calories per serving
            serving_unit: Unit for serving size (e.g. "G", "ML", "OZ"). Default "G"
            number_of_units: Serving size in the specified unit. Default 100
            carbs: Carbohydrates in grams per serving
            protein: Protein in grams per serving
            fat: Total fat in grams per serving
            fiber: Fiber in grams per serving
            sugar: Sugar in grams per serving
            saturated_fat: Saturated fat in grams per serving
            sodium: Sodium in mg per serving
            cholesterol: Cholesterol in mg per serving
            potassium: Potassium in mg per serving
        """
        try:
            nutrition = {
                "servingUnit": serving_unit,
                "numberOfUnits": _num_to_str(number_of_units),
                "calories": _num_to_str(calories),
            }
            # Only include optional fields that have values
            optional_fields = {
                "carbs": carbs,
                "protein": protein,
                "fat": fat,
                "fiber": fiber,
                "sugar": sugar,
                "saturatedFat": saturated_fat,
                "sodium": sodium,
                "cholesterol": cholesterol,
                "potassium": potassium,
            }
            for key, value in optional_fields.items():
                if value is not None:
                    nutrition[key] = _num_to_str(value)

            payload = {
                "foodMetaData": {
                    "foodName": food_name,
                    "foodType": "GENERIC",
                    "source": "GARMIN",
                    "regionCode": "US",
                    "languageCode": "en",
                },
                "nutritionContents": [nutrition],
            }
            url = "/nutrition-service/customFood"
            resp = garmin_client.garth.put(
                "connectapi", url, json=payload, api=True
            )
            if resp.status_code == 204:
                return "Custom food created (no response data returned)."
            return json.dumps(resp.json(), indent=2)
        except GarthHTTPError as e:
            body = ""
            if hasattr(e, "error") and hasattr(e.error, "response"):
                body = getattr(e.error.response, "text", "")
            return f"Error creating custom food: {e} | Response: {body}"
        except Exception as e:
            return f"Error creating custom food: {str(e)}"

    @app.tool()
    async def update_custom_food(
        food_id: str,
        serving_id: str,
        food_name: str,
        calories: float,
        serving_unit: str = "G",
        number_of_units: float = 100,
        carbs: Optional[float] = None,
        protein: Optional[float] = None,
        fat: Optional[float] = None,
        fiber: Optional[float] = None,
        sugar: Optional[float] = None,
        saturated_fat: Optional[float] = None,
        sodium: Optional[float] = None,
        cholesterol: Optional[float] = None,
        potassium: Optional[float] = None,
    ) -> str:
        """Update an existing custom food in the user's Garmin nutrition library

        Modifies a custom food's name and/or nutritional information.
        Use get_custom_foods first to find the foodId and servingId.

        Args:
            food_id: ID of the custom food to update (from get_custom_foods)
            serving_id: Serving ID of the food (from get_custom_foods)
            food_name: Name of the custom food
            calories: Calories per serving
            serving_unit: Unit for serving size (e.g. "G", "ML", "OZ"). Default "G"
            number_of_units: Serving size in the specified unit. Default 100
            carbs: Carbohydrates in grams per serving
            protein: Protein in grams per serving
            fat: Total fat in grams per serving
            fiber: Fiber in grams per serving
            sugar: Sugar in grams per serving
            saturated_fat: Saturated fat in grams per serving
            sodium: Sodium in mg per serving
            cholesterol: Cholesterol in mg per serving
            potassium: Potassium in mg per serving
        """
        try:
            nutrition = {
                "servingId": serving_id,
                "servingUnit": serving_unit,
                "numberOfUnits": _num_to_str(number_of_units),
                "calories": _num_to_str(calories),
            }
            optional_fields = {
                "carbs": carbs,
                "protein": protein,
                "fat": fat,
                "fiber": fiber,
                "sugar": sugar,
                "saturatedFat": saturated_fat,
                "sodium": sodium,
                "cholesterol": cholesterol,
                "potassium": potassium,
            }
            for key, value in optional_fields.items():
                if value is not None:
                    nutrition[key] = _num_to_str(value)

            payload = {
                "foodMetaData": {
                    "foodId": food_id,
                    "foodName": food_name,
                    "foodType": "GENERIC",
                    "source": "GARMIN",
                    "regionCode": "US",
                    "languageCode": "en",
                },
                "nutritionContents": [nutrition],
            }
            url = "/nutrition-service/customFood"
            resp = garmin_client.garth.put(
                "connectapi", url, json=payload, api=True
            )
            if resp.status_code == 204:
                return "Custom food updated (no response data returned)."
            return json.dumps(resp.json(), indent=2)
        except GarthHTTPError as e:
            body = ""
            if hasattr(e, "error") and hasattr(e.error, "response"):
                body = getattr(e.error.response, "text", "")
            return f"Error updating custom food: {e} | Response: {body}"
        except Exception as e:
            return f"Error updating custom food: {str(e)}"

    @app.tool()
    async def log_food(
        meal_date: str,
        meal_time: str,
        meal_id: int,
        food_id: str,
        serving_id: str,
        serving_qty: float = 1,
    ) -> str:
        """Log a food item to a specific meal on a date

        Adds a food entry to the user's nutrition log. Requires IDs
        from the meals endpoint (mealId) and from custom/searched
        foods (foodId, servingId).

        Args:
            meal_date: Date in YYYY-MM-DD format
            meal_time: Time in HH:MM:SS format (e.g. "12:30:00")
            meal_id: Meal ID from get_nutrition_daily_meals
            food_id: Food ID from create_custom_food or get_custom_foods
            serving_id: Serving ID from create_custom_food or get_custom_foods
            serving_qty: Number of servings (default 1)
        """
        try:
            from datetime import datetime, timezone

            log_timestamp = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"
            )
            payload = {
                "mealDate": meal_date,
                "foodLogItems": [
                    {
                        "logTimestamp": log_timestamp,
                        "logSource": "GCW",
                        "logCategory": "REGULAR_LOG",
                        "mealTime": meal_time,
                        "action": "ADD",
                        "mealId": meal_id,
                        "foodId": food_id,
                        "servingId": serving_id,
                        "source": "GARMIN",
                        "regionCode": "US",
                        "languageCode": "en",
                        "servingQty": serving_qty,
                    }
                ],
            }
            url = "/nutrition-service/food/logs"
            resp = garmin_client.garth.put(
                "connectapi", url, json=payload, api=True
            )
            if resp.status_code == 204:
                return "Food logged successfully."
            return json.dumps(resp.json(), indent=2)
        except GarthHTTPError as e:
            body = ""
            if hasattr(e, "error") and hasattr(e.error, "response"):
                body = getattr(e.error.response, "text", "")
            return f"Error logging food: {e} | Response: {body}"
        except Exception as e:
            return f"Error logging food: {str(e)}"

    return app
