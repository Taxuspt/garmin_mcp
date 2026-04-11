"""
Nutrition/food logging functions for Garmin Connect MCP Server
"""
import json
from typing import Optional
from urllib.parse import quote

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

        Returns custom foods the user has created. Use the search parameter
        to find existing foods by name before creating duplicates — the
        response includes foodId and servingId needed for log_custom_food.

        Args:
            search: Search term to filter foods by name (default: list all)
            start: Starting index for pagination (default 0)
            limit: Maximum number of results (default 20)
        """
        try:
            url = (
                f"/nutrition-service/customFood"
                f"?searchExpression={quote(search)}"
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
        On success the response includes foodId and servingId needed for
        log_custom_food. If the API returns no data (204), use
        get_custom_foods(search=food_name) to retrieve those IDs.

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
    async def log_custom_food(
        meal_date: str,
        meal_time: str,
        food_id: str,
        serving_id: str,
        serving_qty: float = 1,
    ) -> str:
        """Log a custom food item to a meal on a date

        Adds a food entry from the user's custom food library to the nutrition
        log. The meal is determined automatically by matching meal_time against
        each meal's startTime/endTime window; falls back to SNACKS if no window
        matches.

        Use get_custom_foods (with the search parameter) to find existing foods
        and retrieve their foodId and servingId. Alternatively, create a new
        food with create_custom_food first.

        Args:
            meal_date: Date in YYYY-MM-DD format
            meal_time: Time in HH:MM:SS format (e.g. "12:30:00", account timezone)
            food_id: Food ID from get_custom_foods or create_custom_food
            serving_id: Serving ID from get_custom_foods or create_custom_food
            serving_qty: Number of servings (default 1)
        """
        try:
            from datetime import datetime, timezone

            meals_url = f"/nutrition-service/meals/{meal_date}"
            meals_data = garmin_client.connectapi(meals_url)
            meals = (meals_data or {}).get("meals", [])

            meal_id = None
            for m in meals:
                start = m.get("startTime")
                end = m.get("endTime")
                if start and end and start <= meal_time <= end:
                    meal_id = m["mealId"]
                    break
            if meal_id is None:
                snacks = next((m for m in meals if m.get("mealName") == "SNACKS"), None)
                if snacks is None:
                    return f"Error logging food: could not match meal for time '{meal_time}' and no SNACKS meal found."
                meal_id = snacks["mealId"]

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

    @app.tool()
    async def log_food(
        meal_date: str,
        meal_time: str,
        name: str,
        calories: float,
        carbs: float,
        protein: float,
        fat: float,
    ) -> str:
        """Quick-add a food entry with macro values to the nutrition log

        Logs food directly by name and macros without requiring a food ID.
        Uses Garmin's Quick Add feature. The meal is determined automatically
        by matching meal_time against each meal's startTime/endTime window;
        falls back to SNACKS if no window matches.

        Args:
            meal_date: Date in YYYY-MM-DD format
            name: Display name for the food entry
            calories: Calories (kcal)
            carbs: Carbohydrates in grams
            protein: Protein in grams
            fat: Fat in grams
            meal_time: Time in HH:MM:SS format (account timezone)
        """
        try:
            from datetime import datetime, timezone

            meals_url = f"/nutrition-service/meals/{meal_date}"
            meals_data = garmin_client.connectapi(meals_url)
            meals = (meals_data or {}).get("meals", [])

            # Match meal_time against startTime/endTime windows; fall back to SNACKS
            meal_id = None
            for m in meals:
                start = m.get("startTime")
                end = m.get("endTime")
                if start and end and start <= meal_time <= end:
                    meal_id = m["mealId"]
                    break
            if meal_id is None:
                snacks = next((m for m in meals if m.get("mealName") == "SNACKS"), None)
                if snacks is None:
                    return f"Error logging food: could not match meal for time '{meal_time}' and no SNACKS meal found."
                meal_id = snacks["mealId"]

            now = datetime.now(timezone.utc)
            log_timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            payload = {
                "mealDate": meal_date,
                "quickAddItems": [
                    {
                        "name": name,
                        "logId": None,
                        "logTimestamp": log_timestamp,
                        "logSource": "GCW",
                        "logCategory": "QUICK_ADD",
                        "mealTime": meal_time,
                        "mealId": meal_id,
                        "action": "ADD",
                        "calories": _num_to_str(calories),
                        "carbs": _num_to_str(carbs),
                        "protein": _num_to_str(protein),
                        "fat": _num_to_str(fat),
                    }
                ],
            }
            url = "/nutrition-service/food/logs/quickAdd"
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

    @app.tool()
    async def delete_food_log(log_id: int) -> str:
        """Delete a food log entry

        Permanently removes a logged food item from the nutrition log.
        Use get_nutrition_daily_food_log to find the logId of the entry
        to delete.

        Args:
            log_id: Log entry ID to delete (from get_nutrition_daily_food_log)
        """
        try:
            url = f"/nutrition-service/food/logs/{log_id}"
            resp = garmin_client.garth.delete("connectapi", url, api=True)
            if resp.status_code in (200, 204):
                return json.dumps({"status": "success", "log_id": log_id, "message": f"Food log entry {log_id} deleted successfully."}, indent=2)
            return json.dumps({"status": "failed", "log_id": log_id, "http_status": resp.status_code, "message": f"Failed to delete food log: HTTP {resp.status_code}"}, indent=2)
        except GarthHTTPError as e:
            body = ""
            if hasattr(e, "error") and hasattr(e.error, "response"):
                body = getattr(e.error.response, "text", "")
            return f"Error deleting food log: {e} | Response: {body}"
        except Exception as e:
            return f"Error deleting food log: {str(e)}"

    @app.tool()
    async def upsert_and_log(
        meal_date: str,
        meal_time: str,
        food_name: str,
        calories: float,
        carbs: Optional[float] = None,
        protein: Optional[float] = None,
        fat: Optional[float] = None,
        serving_unit: str = "G",
        number_of_units: float = 100,
        serving_qty: float = 1,
    ) -> str:
        """Find-or-create a custom food then log it in one step

        Searches the user's custom food library for food_name. If found, logs
        it immediately. If not found, creates it with the provided nutrition
        data and then logs it. This avoids duplicate food entries and removes
        the need for separate search → create → log round-trips.

        Args:
            meal_date: Date in YYYY-MM-DD format
            meal_time: Time in HH:MM:SS format (account timezone); used to
                determine the meal automatically
            food_name: Name of the food to find or create
            calories: Calories per serving
            carbs: Carbohydrates in grams per serving
            protein: Protein in grams per serving
            fat: Total fat in grams per serving
            serving_unit: Unit for serving size (e.g. "G", "ML", "OZ"). Default "G"
            number_of_units: Serving size in the specified unit. Default 100
            serving_qty: Number of servings to log (default 1)
        """
        try:
            from datetime import datetime, timezone

            # 1. Search for existing custom food
            search_url = (
                f"/nutrition-service/customFood"
                f"?searchExpression={quote(food_name)}"
                f"&start=0&limit=10&includeContent=true"
            )
            search_data = garmin_client.connectapi(search_url)
            foods = search_data if isinstance(search_data, list) else []

            food_id = None
            serving_id = None
            for f in foods:
                meta = f.get("foodMetaData", f)
                name_match = meta.get("foodName", "").lower() == food_name.lower()
                if name_match:
                    food_id = str(meta.get("foodId") or f.get("foodId", ""))
                    contents = f.get("nutritionContents", [])
                    if contents:
                        serving_id = str(contents[0].get("servingId", ""))
                    break

            # 2. Create if not found
            if not food_id or not serving_id:
                nutrition = {
                    "servingUnit": serving_unit,
                    "numberOfUnits": _num_to_str(number_of_units),
                    "calories": _num_to_str(calories),
                }
                optional_fields = {"carbs": carbs, "protein": protein, "fat": fat}
                for key, value in optional_fields.items():
                    if value is not None:
                        nutrition[key] = _num_to_str(value)
                create_payload = {
                    "foodMetaData": {
                        "foodName": food_name,
                        "foodType": "GENERIC",
                        "source": "GARMIN",
                        "regionCode": "US",
                        "languageCode": "en",
                    },
                    "nutritionContents": [nutrition],
                }
                create_resp = garmin_client.garth.put(
                    "connectapi", "/nutrition-service/customFood", json=create_payload, api=True
                )
                if create_resp.status_code not in (200, 201, 204):
                    return f"Error creating custom food: HTTP {create_resp.status_code}"
                if create_resp.status_code in (200, 201):
                    created = create_resp.json()
                    meta = created.get("foodMetaData", created)
                    food_id = str(meta.get("foodId", ""))
                    contents = created.get("nutritionContents", [])
                    if contents:
                        serving_id = str(contents[0].get("servingId", ""))
                # 204: no body — look up by name
                if not food_id or not serving_id:
                    lookup_url = (
                        f"/nutrition-service/customFood"
                        f"?searchExpression={quote(food_name)}"
                        f"&start=0&limit=10&includeContent=true"
                    )
                    lookup_data = garmin_client.connectapi(lookup_url)
                    lookup_foods = lookup_data if isinstance(lookup_data, list) else []
                    for f in lookup_foods:
                        meta = f.get("foodMetaData", f)
                        if meta.get("foodName", "").lower() == food_name.lower():
                            food_id = str(meta.get("foodId") or f.get("foodId", ""))
                            contents = f.get("nutritionContents", [])
                            if contents:
                                serving_id = str(contents[0].get("servingId", ""))
                            break
                if not food_id or not serving_id:
                    return f"Error: could not retrieve foodId/servingId for '{food_name}' after creation."

            # 3. Resolve meal_id from meal_time
            meals_data = garmin_client.connectapi(f"/nutrition-service/meals/{meal_date}")
            meals = (meals_data or {}).get("meals", [])
            meal_id = None
            for m in meals:
                start = m.get("startTime")
                end = m.get("endTime")
                if start and end and start <= meal_time <= end:
                    meal_id = m["mealId"]
                    break
            if meal_id is None:
                snacks = next((m for m in meals if m.get("mealName") == "SNACKS"), None)
                if snacks is None:
                    return f"Error logging food: could not match meal for time '{meal_time}' and no SNACKS meal found."
                meal_id = snacks["mealId"]

            # 4. Log
            log_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            log_payload = {
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
            log_resp = garmin_client.garth.put(
                "connectapi", "/nutrition-service/food/logs", json=log_payload, api=True
            )
            if log_resp.status_code == 204:
                return "Food logged successfully."
            return json.dumps(log_resp.json(), indent=2)
        except GarthHTTPError as e:
            body = ""
            if hasattr(e, "error") and hasattr(e.error, "response"):
                body = getattr(e.error.response, "text", "")
            return f"Error in upsert_and_log: {e} | Response: {body}"
        except Exception as e:
            return f"Error in upsert_and_log: {str(e)}"

    return app
