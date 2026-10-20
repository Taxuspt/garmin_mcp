"""
Nutrition/food logging functions for Garmin Connect MCP Server
"""
import datetime
import json
import re
from copy import deepcopy
from typing import Optional

import requests
from garminconnect import GarminConnectConnectionError

from garmin_mcp.write_errors import WriteTracker

# The garmin_client will be set by the main file
garmin_client = None


def _num_to_str(value: float) -> str:
    """Format a number as string, dropping .0 for whole numbers.

    Garmin's API expects integer strings like "160" not "160.0".
    """
    return str(int(value)) if value == int(value) else str(value)


def resolve_weight_goal_target_date(stored_target, date, target_date=None):
    """Decide which ``targetDate`` a nutrition-settings update should carry.

    ``/nutrition-service/settings/{date}`` is a full-object PUT, and the object
    it returns includes ``targetDate`` -- the *weight goal's* target, not a
    nutrition field. Garmin validates it on write and rejects the entire
    request unless it is after the day being written::

        API Error 400 - Goal targetDate must be after the asOfDate provided.

    So once a user's weight-goal target date has passed, every nutrition
    update fails for every current or future date, with an error that names
    neither the field nor the weight goal.

    The stored value is deliberately never dropped or silently rewritten: it
    belongs to the user's weight goal, and changing it as a side effect of a
    calorie update would alter something they did not ask to change.

    Returns ``(target_date_to_set, error)``. At most one is not ``None``; both
    ``None`` means the stored value is valid and must be left untouched.
    """
    if target_date is not None:
        try:
            datetime.date.fromisoformat(target_date)
        except (ValueError, TypeError):
            return None, f"Invalid target_date {target_date!r}. Use YYYY-MM-DD."
        if target_date <= date:
            return None, (
                f"target_date ({target_date}) must be after date ({date}); Garmin "
                f"requires the weight goal's target to be later than the day being written."
            )
        return target_date, None

    if stored_target and str(stored_target) <= date:
        return None, (
            f"Cannot update nutrition settings for {date}: the weight goal's targetDate "
            f"({stored_target}) is not after {date}, and Garmin rejects the whole settings "
            f"update in that case.\n"
            f"Either set a new target date on the weight goal in Garmin Connect (or end the "
            f"goal) and retry, or pass target_date=YYYY-MM-DD (after {date}) to move the "
            f"weight goal's target date as part of this update."
        )

    return None, None


def _extract_http_status(exc: Exception) -> Optional[int]:
    """Return an HTTP status code from structured exception fields when available.

    The installed garminconnect client sometimes raises bare
    GarminConnectConnectionError instances with only a status-bearing message,
    so fall back to parsing the numeric code from that stable library format.
    """
    seen: set[int] = set()
    pending: list[BaseException | None] = [exc]

    while pending:
        current = pending.pop(0)
        if current is None:
            continue

        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        response = getattr(current, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)
            if isinstance(status, int):
                return status

        error = getattr(current, "error", None)
        error_response = getattr(error, "response", None)
        if error_response is not None:
            status = getattr(error_response, "status_code", None)
            if isinstance(status, int):
                return status

        pending.append(getattr(current, "__cause__", None))
        pending.append(getattr(current, "__context__", None))

    message = str(exc)
    match = re.search(r"\b(?:API Error|HTTP)\s+(\d{3})\b", message)
    if match:
        return int(match.group(1))
    return None


def _has_http_status(exc: Exception, status_code: int) -> bool:
    return _extract_http_status(exc) == status_code


def _iter_exception_texts(exc: Exception) -> list[str]:
    """Collect human-readable exception/response text for predicate matching."""
    texts: list[str] = []
    seen: set[int] = set()
    pending: list[BaseException | None] = [exc]

    while pending:
        current = pending.pop(0)
        if current is None:
            continue

        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        message = str(current)
        if message:
            texts.append(message)

        response = getattr(current, "response", None)
        response_text = getattr(response, "text", None)
        if isinstance(response_text, str) and response_text:
            texts.append(response_text)

        error = getattr(current, "error", None)
        error_response = getattr(error, "response", None)
        error_text = getattr(error_response, "text", None)
        if isinstance(error_text, str) and error_text:
            texts.append(error_text)

        pending.append(getattr(current, "__cause__", None))
        pending.append(getattr(current, "__context__", None))

    return texts


def _is_custom_food_unsupported_403(exc: Exception) -> bool:
    """Return True only for the known Garmin 403 unsupported-feature response."""
    if not _has_http_status(exc, 403):
        return False

    patterns = (
        "custom food unsupported",
        "custom foods unsupported",
        "custom food not supported",
        "custom foods not supported",
    )
    return any(
        any(pattern in text.lower() for pattern in patterns)
        for text in _iter_exception_texts(exc)
    )


_CUSTOM_FOOD_PAGE_SIZE = 20
_CUSTOM_FOOD_MAX_SCAN = 500


def _find_custom_food(food_id: str, food_name: str) -> Optional[dict]:
    """Return the stored custom food with ``food_id``, or None if not found.

    Garmin has no read-by-ID for custom foods, so search by ``food_name``
    first (the usual case) and fall back to paging through the unfiltered
    list: after a rename, the new name does not match the stored record.
    """
    for search in (food_name, ""):
        for start in range(0, _CUSTOM_FOOD_MAX_SCAN, _CUSTOM_FOOD_PAGE_SIZE):
            data = garmin_client.connectapi(
                "/nutrition-service/customFood",
                params={
                    "searchExpression": search,
                    "start": start,
                    "limit": _CUSTOM_FOOD_PAGE_SIZE,
                    "includeContent": "true",
                },
            )
            foods = (data.get("customFoods") or []) if isinstance(data, dict) else []
            for food in foods:
                meta = (food.get("foodMetaData") or {}) if isinstance(food, dict) else {}
                if str(meta.get("foodId", "")) == food_id:
                    return food
            if len(foods) < _CUSTOM_FOOD_PAGE_SIZE:
                break
    return None


def _find_custom_food_by_name(food_name: str) -> Optional[dict]:
    """Find an exact name across substring-search pages before creating a food.

    If the bounded scan is exhausted, refuse to guess that the food is absent:
    creating in that case could duplicate a record on a later page.
    """
    for start in range(0, _CUSTOM_FOOD_MAX_SCAN, _CUSTOM_FOOD_PAGE_SIZE):
        data = garmin_client.connectapi(
            "/nutrition-service/customFood",
            params={
                "searchExpression": food_name,
                "start": start,
                "limit": _CUSTOM_FOOD_PAGE_SIZE,
                "includeContent": "true",
            },
        )
        foods = (data.get("customFoods") or []) if isinstance(data, dict) else []
        for food in foods:
            if not isinstance(food, dict):
                continue
            meta = food.get("foodMetaData") or food
            if (meta.get("foodName") or "").lower() == food_name.lower():
                return food
        if len(foods) < _CUSTOM_FOOD_PAGE_SIZE:
            return None
    raise ValueError("Custom food search limit reached; cannot safely determine whether the food exists.")


def _custom_food_ids(food):
    """Read the food and first-serving IDs from a search or create response."""
    if not food:
        return None, None
    meta = food.get("foodMetaData") or food
    contents = food.get("nutritionContents") or []
    serving = (contents[0] or {}) if contents else {}
    food_id = meta.get("foodId") or food.get("foodId")
    serving_id = serving.get("servingId")
    return (
        str(food_id) if food_id is not None else None,
        str(serving_id) if serving_id is not None else None,
    )


def _validate_meal_datetime(meal_date: str, meal_time: str) -> None:
    """Validate the local clock fields before matching string meal windows."""
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", meal_date):
        raise ValueError("meal_date must be a valid date in YYYY-MM-DD format")
    try:
        datetime.date.fromisoformat(meal_date)
    except ValueError:
        raise ValueError("meal_date must be a valid date in YYYY-MM-DD format") from None
    if not re.fullmatch(r"[0-9]{2}:[0-9]{2}:[0-9]{2}", meal_time):
        raise ValueError("meal_time must be a valid time in HH:MM:SS format")
    try:
        datetime.time.fromisoformat(meal_time)
    except ValueError:
        raise ValueError("meal_time must be a valid time in HH:MM:SS format") from None


def _find_food_log_entry(data, log_id):
    """Find a logId in the daily log's nested JSON without assuming a meal layout."""
    if isinstance(data, dict):
        if data.get("logId") is not None and str(data["logId"]) == log_id:
            return data
        values = data.values()
    elif isinstance(data, list):
        values = data
    else:
        return None
    for value in values:
        entry = _find_food_log_entry(value, log_id)
        if entry is not None:
            return entry
    return None


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
    async def get_nutrition_summary_between_dates(start_date: str, end_date: str) -> str:
        """Get per-day nutrition totals for every day in a date range.

        Returns one lightweight entry per day (date, calories, carbs, protein,
        fat, item_count) instead of the full per-item food log that
        get_nutrition_daily_food_log returns for a single date. Use this for
        multi-day intake analysis (e.g. mean intake for a TDEE estimate)
        instead of calling get_nutrition_daily_food_log once per day.

        item_count is the number of logged food items that day. A day with
        item_count == 0 has no logged food at all, and a low but nonzero
        item_count may mean only part of the day was logged (e.g. breakfast
        only). Both cases read as low intake in the totals alone -- exclude
        low-item_count days explicitly before averaging intake or deriving
        TDEE; don't infer "unlogged" from a low calorie total.

        Maximum range: 61 days per call (Garmin's own limit for this endpoint).

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        MAX_DAYS = 61
        try:
            start = datetime.date.fromisoformat(start_date)
            end = datetime.date.fromisoformat(end_date)
        except ValueError as e:
            return f"Invalid date format: {e}. Use YYYY-MM-DD."

        days = (end - start).days + 1
        if days < 1:
            return "end_date must be on or after start_date."
        if days > MAX_DAYS:
            return f"Date range too large ({days} days). Maximum is {MAX_DAYS} days."

        try:
            data = garmin_client.connectapi(
                "/nutrition-service/food/logs/range",
                params={"startDate": start_date, "endDate": end_date},
            )
        except Exception as e:
            return f"Error retrieving nutrition summary: {str(e)}"

        summaries = (data or {}).get("dailyNutritionSummaries") or []
        if not summaries:
            return f"No nutrition data found between {start_date} and {end_date}."

        daily = []
        for day in summaries:
            content = day.get("dailyNutritionContent") or {}
            item_count = sum(
                len(meal.get("loggedFoods") or [])
                for meal in (day.get("mealDetails") or [])
            )
            daily.append({
                "date": day.get("mealDate"),
                "calories": content.get("calories"),
                "carbs": content.get("carbs"),
                "protein": content.get("protein"),
                "fat": content.get("fat"),
                "item_count": item_count,
            })

        return json.dumps({
            "start_date": start_date,
            "end_date": end_date,
            "days": daily,
        }, indent=2)

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
    async def set_nutrition_daily_settings(
        date: str,
        calorie_goal: Optional[int] = None,
        carbs_grams: Optional[int] = None,
        fat_grams: Optional[int] = None,
        protein_grams: Optional[int] = None,
        target_date: Optional[str] = None,
    ) -> str:
        """Update daily nutrition goals (calorie target and macronutrient targets).

        Reads the current settings for the date, applies the supplied overrides,
        and writes the merged result back.  Only the fields you provide are
        changed; omitted fields keep their existing values.

        Macro arguments retain their existing names and are passed through to
        Garmin's macroGoals without unit conversion. The endpoint's macro units
        have not been independently verified. Returned goals come from Garmin's
        response, or a settings read-back when the update has no response body.

        Args:
            date: Date in YYYY-MM-DD format (settings are typically set once and
                  inherited across days, but Garmin accepts per-day overrides)
            calorie_goal: Daily calorie target in kcal
            carbs_grams: Daily carbohydrate target in grams
            fat_grams: Daily fat target in grams
            protein_grams: Daily protein target in grams
            target_date: Optional new target date (YYYY-MM-DD) for the weight
                  goal that this endpoint carries. Only needed when the stored
                  target date has already passed, which otherwise makes Garmin
                  reject the update; must be after `date`.
        """
        if all(v is None for v in (calorie_goal, carbs_grams, fat_grams, protein_grams)):
            return "No fields to update — supply at least one of calorie_goal, carbs_grams, fat_grams, protein_grams."
        tracker = WriteTracker("set_nutrition_daily_settings", "Error updating nutrition settings")
        try:
            url = f"/nutrition-service/settings/{date}"
            current = garmin_client.connectapi(url)
            if not current:
                return f"Could not read current nutrition settings for {date} — cannot apply update."
            current = deepcopy(current)
            new_target, target_error = resolve_weight_goal_target_date(
                current.get("targetDate"), date, target_date
            )
            if target_error:
                return target_error
            if new_target:
                current["targetDate"] = new_target
            if calorie_goal is not None:
                current["calorieGoal"] = calorie_goal
            macro_overrides = {
                "carbs": carbs_grams,
                "fat": fat_grams,
                "protein": protein_grams,
            }
            if any(value is not None for value in macro_overrides.values()):
                macros = current.get("macroGoals")
                if macros is None:
                    macros = {}
                if not isinstance(macros, dict):
                    return "Could not read current macro goals — cannot apply update."
                macros.update({key: value for key, value in macro_overrides.items() if value is not None})
                current["macroGoals"] = macros
            tracker.sending()
            resp = garmin_client.client.put("connectapi", url, json=current, api=True)
            tracker.committed()
            result = resp
            if not result:
                try:
                    result = garmin_client.connectapi(url)
                except Exception as e:
                    return f"Nutrition update submitted, but could not verify stored settings: {e}"
            if not isinstance(result, dict) or not result:
                return "Nutrition update submitted, but could not verify stored settings."
            result_macros = result.get("macroGoals") or {}
            return json.dumps({
                "status": "updated",
                "date": date,
                "calorie_goal": result.get("calorieGoal"),
                "carbs_grams": result_macros.get("carbs"),
                "fat_grams": result_macros.get("fat"),
                "protein_grams": result_macros.get("protein"),
                "weight_goal_target_date": result.get("targetDate"),
            }, indent=2)
        except Exception as e:
            return tracker.error(e)

    @app.tool()
    async def search_foods(
        query: str,
        start: int = 0,
        limit: int = 20,
    ) -> str:
        """Search Garmin's general food catalog (FatSecret + Garmin custom foods)

        Searches across the entire food catalog including FatSecret-sourced
        branded and generic foods, not just the user's Garmin custom foods.
        Use this to find branded packaged foods by name before logging them.

        Returns food_id, source, name, brand, and all available servings with
        macros. The source field ("FATSECRET" or "GARMIN") and food_id together
        identify the right routing for log_custom_food — pass both to
        log_custom_food's food_id and source parameters respectively.

        For the user's own custom foods only, use get_custom_foods instead.

        Args:
            query: Food name or brand to search for (e.g. "Cheerios", "Greek yogurt")
            start: Starting index for pagination (default 0)
            limit: Maximum number of results per page (default 20)
        """
        try:
            data = garmin_client.connectapi(
                "/nutrition-service/food/search",
                params={"searchExpression": query, "start": start, "limit": limit},
            )
            if not data:
                return "No foods found."

            raw_results = (data.get("results") or []) if isinstance(data, dict) else []
            has_more = bool(data.get("moreDataAvailable", False)) if isinstance(data, dict) else False

            results = []
            for item in raw_results:
                if not isinstance(item, dict):
                    continue
                meta = item.get("foodMetaData")
                if not isinstance(meta, dict):
                    continue
                servings = []
                for s in (item.get("nutritionContents") or []):
                    if not isinstance(s, dict):
                        continue
                    serving: dict = {
                        "serving_id": s.get("servingId"),
                        "serving_unit": s.get("servingUnit"),
                        "number_of_units": s.get("numberOfUnits"),
                        "calories": s.get("calories"),
                        "carbs_g": s.get("carbs"),
                        "protein_g": s.get("protein"),
                        "fat_g": s.get("fat"),
                        "fiber_g": s.get("fiber"),
                        "sodium_mg": s.get("sodium"),
                    }
                    servings.append({k: v for k, v in serving.items() if v is not None})

                entry: dict = {
                    "food_id": meta.get("foodId"),
                    "name": meta.get("foodName"),
                    "food_type": meta.get("foodType"),
                    "source": meta.get("source"),
                    "region": meta.get("regionCode"),
                    "language": meta.get("languageCode"),
                    "servings": servings,
                }
                if meta.get("brandName"):
                    entry["brand"] = meta["brandName"]
                results.append({k: v for k, v in entry.items() if v is not None})

            return json.dumps({
                "count": len(results),
                "has_more": has_more,
                "results": results,
            }, indent=2)
        except Exception as e:
            return f"Error searching foods: {str(e)}"

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

        For branded catalog foods (FatSecret), use search_foods instead.

        Args:
            search: Search term to filter foods by name (default: list all)
            start: Starting index for pagination (default 0)
            limit: Maximum number of results (default 20)
        """
        try:
            data = garmin_client.connectapi(
                "/nutrition-service/customFood",
                params={
                    "searchExpression": search,
                    "start": start,
                    "limit": limit,
                    "includeContent": "true",
                },
            )
            if not data:
                return "No custom foods found."
            return json.dumps(data, indent=2)
        except Exception as e:
            if _is_custom_food_unsupported_403(e):
                return "No custom foods found; Garmin custom food API returned HTTP 403 Forbidden."
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
            if _is_custom_food_unsupported_403(e):
                return "No serving units found; Garmin custom food API returned HTTP 403 Forbidden."
            return f"Error retrieving serving units: {str(e)}"

    @app.tool()
    async def create_custom_food(
        food_name: str,
        calories: float,
        serving_unit: str = "G",
        number_of_units: float = 100,
        brand_name: Optional[str] = None,
        carbs: Optional[float] = None,
        protein: Optional[float] = None,
        fat: Optional[float] = None,
        fiber: Optional[float] = None,
        sugar: Optional[float] = None,
        saturated_fat: Optional[float] = None,
        sodium: Optional[float] = None,
        cholesterol: Optional[float] = None,
        potassium: Optional[float] = None,
        trans_fat: Optional[float] = None,
        calcium: Optional[float] = None,
        iron: Optional[float] = None,
        vitamin_d: Optional[float] = None,
    ) -> str:
        """Create a custom food in the user's Garmin nutrition library

        Creates a new food item with nutritional information per serving.
        On success the response includes foodId and servingId needed for
        log_custom_food. If the API returns no data (204), use
        get_custom_foods(search=food_name) to retrieve those IDs.

        All nutrient amounts are ABSOLUTE values per serving, not %DV.
        Nutrition labels often print %DV for calcium/iron/vitamin D —
        convert to absolute units before passing.

        Args:
            food_name: Name of the custom food (e.g. "Homemade Chocolate Cookies")
            calories: Calories per serving
            serving_unit: Unit for serving size (e.g. "G", "ML", "OZ"). Default "G"
            number_of_units: Serving size in the specified unit. Default 100
            brand_name: Brand or vendor name (e.g. "Three Bridges")
            carbs: Carbohydrates in grams per serving
            protein: Protein in grams per serving
            fat: Total fat in grams per serving
            fiber: Fiber in grams per serving
            sugar: Sugar in grams per serving
            saturated_fat: Saturated fat in grams per serving
            sodium: Sodium in mg per serving
            cholesterol: Cholesterol in mg per serving
            potassium: Potassium in mg per serving
            trans_fat: Trans fat in grams per serving
            calcium: Calcium in mg per serving (NOT %DV)
            iron: Iron in mg per serving (NOT %DV)
            vitamin_d: Vitamin D in mcg per serving (NOT %DV)
        """
        tracker = WriteTracker("create_custom_food", "Error creating custom food")
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
                "transFat": trans_fat,
                "calcium": calcium,
                "iron": iron,
                "vitaminD": vitamin_d,
            }
            for key, value in optional_fields.items():
                if value is not None:
                    nutrition[key] = _num_to_str(value)

            food_meta: dict = {
                "foodName": food_name,
                "foodType": "GENERIC",
                "source": "GARMIN",
                "regionCode": "US",
                "languageCode": "en",
            }
            if brand_name is not None:
                food_meta["brandName"] = brand_name

            payload = {
                "foodMetaData": food_meta,
                "nutritionContents": [nutrition],
            }
            url = "/nutrition-service/customFood"
            tracker.sending()
            resp = garmin_client.client.put(
                "connectapi", url, json=payload, api=True
            )
            tracker.committed()
            if not resp:
                return "Custom food created (no response data returned)."
            return json.dumps(resp, indent=2)
        except Exception as e:
            return tracker.error(e)

    @app.tool()
    async def update_custom_food(
        food_id: str,
        serving_id: str,
        food_name: str,
        calories: float,
        serving_unit: Optional[str] = None,
        number_of_units: Optional[float] = None,
        brand_name: Optional[str] = None,
        carbs: Optional[float] = None,
        protein: Optional[float] = None,
        fat: Optional[float] = None,
        fiber: Optional[float] = None,
        sugar: Optional[float] = None,
        saturated_fat: Optional[float] = None,
        sodium: Optional[float] = None,
        cholesterol: Optional[float] = None,
        potassium: Optional[float] = None,
        trans_fat: Optional[float] = None,
        calcium: Optional[float] = None,
        iron: Optional[float] = None,
        vitamin_d: Optional[float] = None,
    ) -> str:
        """Update an existing custom food in the user's Garmin nutrition library

        Fetches the food's current record (by food_id, so renaming is safe)
        before writing so that omitted optional fields (brand, carbs, protein,
        fat, micros, etc.) preserve their existing values rather than being
        cleared. Only the fields you explicitly pass are changed; everything
        else is carried forward from the current record.

        Garmin replaces the whole record on update. If the current record
        cannot be read, the update is refused unless serving_unit and
        number_of_units are given; the call then replaces the record in full,
        and any brand or nutrient you omit is cleared.

        Only future logs see the change. Each food-log entry stores its own
        copy of the food's nutrition from when it was logged, so entries
        already logged, and those days' summaries, keep the old values. To
        correct a past day, delete_food_log each affected entry and log it
        again after the update.

        All nutrient amounts are ABSOLUTE values per serving, not %DV.
        Nutrition labels often print %DV for calcium/iron/vitamin D —
        convert to absolute units before passing.

        Use get_custom_foods first to find the foodId and servingId.

        Args:
            food_id: ID of the custom food to update (from get_custom_foods)
            serving_id: Serving ID of the food (from get_custom_foods)
            food_name: Name of the custom food
            calories: Calories per serving
            serving_unit: Unit for serving size (e.g. "G", "ML", "OZ");
                omit to preserve the existing value
            number_of_units: Serving size in the specified unit;
                omit to preserve the existing value
            brand_name: Brand or vendor name; omit to preserve the existing value
            carbs: Carbohydrates in grams per serving
            protein: Protein in grams per serving
            fat: Total fat in grams per serving
            fiber: Fiber in grams per serving
            sugar: Sugar in grams per serving
            saturated_fat: Saturated fat in grams per serving
            sodium: Sodium in mg per serving
            cholesterol: Cholesterol in mg per serving
            potassium: Potassium in mg per serving
            trans_fat: Trans fat in grams per serving
            calcium: Calcium in mg per serving (NOT %DV)
            iron: Iron in mg per serving (NOT %DV)
            vitamin_d: Vitamin D in mcg per serving (NOT %DV)
        """
        tracker = WriteTracker("update_custom_food", "Error updating custom food")
        try:
            # Fetch current record so omitted fields are preserved (not wiped).
            try:
                existing = _find_custom_food(food_id, food_name)
            except Exception:
                existing = None
            if existing is None and (serving_unit is None or number_of_units is None):
                # The PUT is a full replace: writing without the stored record
                # would silently wipe the brand, nutrients and serving size.
                return (
                    f"Error updating custom food: could not read the current record "
                    f"for food_id {food_id}, so omitted fields cannot be preserved. "
                    "Check the food_id with get_custom_foods, or pass serving_unit "
                    "and number_of_units (plus every nutrient and brand to keep) to "
                    "replace the record in full."
                )
            existing_nutrition: dict = (
                ((existing or {}).get("nutritionContents") or [{}])[0] or {}
            )
            existing_brand: Optional[str] = (
                (existing or {}).get("foodMetaData") or {}
            ).get("brandName")

            # API field name → optional param value (None means "not supplied by caller")
            optional_updates = {
                "carbs": carbs,
                "protein": protein,
                "fat": fat,
                "fiber": fiber,
                "sugar": sugar,
                "saturatedFat": saturated_fat,
                "sodium": sodium,
                "cholesterol": cholesterol,
                "potassium": potassium,
                "transFat": trans_fat,
                "calcium": calcium,
                "iron": iron,
                "vitaminD": vitamin_d,
            }
            # Effective serving basis: caller-supplied wins, else preserve existing,
            # else fall back to the create-time defaults (stored record lacks them).
            effective_serving_unit = serving_unit
            if effective_serving_unit is None:
                effective_serving_unit = existing_nutrition.get("servingUnit", "G")
            effective_number_of_units = number_of_units
            if effective_number_of_units is None:
                effective_number_of_units = existing_nutrition.get("numberOfUnits", 100)

            nutrition: dict = {
                "servingId": serving_id,
                "servingUnit": effective_serving_unit,
                "numberOfUnits": _num_to_str(effective_number_of_units),
                "calories": _num_to_str(calories),
            }
            # Carry forward existing optional fields, then overlay caller-supplied values.
            preserved_keys = set(optional_updates.keys())
            for key, existing_val in existing_nutrition.items():
                if key in preserved_keys and existing_val is not None:
                    nutrition[key] = _num_to_str(existing_val)
            for key, value in optional_updates.items():
                if value is not None:
                    nutrition[key] = _num_to_str(value)

            # Effective brand: caller-supplied wins, else preserve existing, else omit.
            effective_brand = brand_name if brand_name is not None else existing_brand

            food_meta: dict = {
                "foodId": food_id,
                "foodName": food_name,
                "foodType": "GENERIC",
                "source": "GARMIN",
                "regionCode": "US",
                "languageCode": "en",
            }
            if effective_brand is not None:
                food_meta["brandName"] = effective_brand

            payload = {
                "foodMetaData": food_meta,
                "nutritionContents": [nutrition],
            }
            url = "/nutrition-service/customFood"
            tracker.sending()
            resp = garmin_client.client.put(
                "connectapi", url, json=payload, api=True
            )
            tracker.committed()
            if not resp:
                return "Custom food updated (no response data returned)."
            return json.dumps(resp, indent=2)
        except Exception as e:
            return tracker.error(e)

    @app.tool()
    async def delete_custom_food(food_id: str, confirm: bool = False) -> str:
        """Delete a custom food from the user's Garmin nutrition library

        Permanently removes a custom food entry — this cannot be undone and
        the deleted food's name/brand/nutrition data cannot be recovered from
        the API afterward. The default call previews the food without deleting
        it. Review that preview, then call again with confirm=true.

        Deleting a food does NOT require it to be unreferenced: existing food
        log entries that already reference this food are NOT blocked or
        removed — they remain in the log (their nutrition still counts toward
        daily totals) but are marked inactive (foodInactive: true) and can no
        longer be edited/re-logged from the library.

        Use get_custom_foods to find the foodId, and match on the exact
        foodId (not just a name search) — Garmin's search can return loosely
        related results for a given search term, not just exact matches.

        Args:
            food_id: ID of the custom food to delete — a 32-char hex string
                (from get_custom_foods or create_custom_food)
            confirm: Set true to delete after reviewing the default preview
        """
        tracker = WriteTracker("delete_custom_food", "Error deleting custom food")
        try:
            if not confirm:
                food = _find_custom_food(food_id, "")
                if food is None:
                    return json.dumps({"status": "not_found", "food_id": food_id})
                return json.dumps({
                    "status": "needs_confirmation", "food_id": food_id, "food": food,
                    "message": "Review this food, then call again with confirm=true to permanently delete it.",
                }, indent=2)
            url = f"/nutrition-service/customFood/{food_id}"
            tracker.sending()
            garmin_client.client.delete("connectapi", url, api=True)
            tracker.committed()
            return json.dumps(
                {"status": "success", "food_id": food_id,
                 "message": f"Custom food {food_id} deleted successfully."},
                indent=2,
            )
        except Exception as e:
            return tracker.error(e)

    @app.tool()
    async def log_custom_food(
        meal_date: str,
        meal_time: str,
        food_id: str,
        serving_id: str,
        serving_qty: float = 1,
        source: str = "GARMIN",
    ) -> str:
        """Log a food item to a meal on a date

        Adds a food entry to the nutrition log. The meal is determined
        automatically by matching meal_time against each meal's
        startTime/endTime window; falls back to SNACKS if no window matches.

        Food sources:
          - "GARMIN" (default): user's custom food library. Use
            get_custom_foods to find food_id and serving_id.
          - "FATSECRET": branded/catalog food from FatSecret. Use
            search_foods to find food_id and serving_id. Pass the source
            value from the search_foods result (e.g. "FATSECRET").

        Garmin custom food IDs are 32-char hex UUIDs; FatSecret IDs are
        numeric strings (e.g. "4132350"). Passing the wrong source for a
        given food_id returns a 400 from Garmin.

        Args:
            meal_date: Date in YYYY-MM-DD format
            meal_time: Time in HH:MM:SS format (e.g. "12:30:00", account timezone)
            food_id: Food ID from get_custom_foods (GARMIN) or search_foods (FATSECRET)
            serving_id: Serving ID from get_custom_foods or search_foods
            serving_qty: Number of servings (default 1)
            source: Food namespace — "GARMIN" (default) or "FATSECRET"
        """
        tracker = WriteTracker("log_custom_food", "Error logging food")
        try:
            from datetime import datetime, timezone

            _validate_meal_datetime(meal_date, meal_time)

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
                    return f"No nutrition meal windows found for {meal_date}; cannot log custom food at '{meal_time}'."
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
                        "source": source,
                        "regionCode": "US",
                        "languageCode": "en",
                        "servingQty": serving_qty,
                    }
                ],
            }
            url = "/nutrition-service/food/logs"
            tracker.sending()
            resp = garmin_client.client.put(
                "connectapi", url, json=payload, api=True
            )
            tracker.committed()
            if not resp:
                return "Food logged successfully."
            return json.dumps(resp, indent=2)
        except Exception as e:
            return tracker.error(e)

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
        tracker = WriteTracker("log_food", "Error logging food")
        try:
            from datetime import datetime, timezone

            _validate_meal_datetime(meal_date, meal_time)

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
                    return f"No nutrition meal windows found for {meal_date}; cannot log food at '{meal_time}'."
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
            tracker.sending()
            resp = garmin_client.client.put(
                "connectapi", url, json=payload, api=True
            )
            tracker.committed()
            if not resp:
                return "Food logged successfully."
            return json.dumps(resp, indent=2)
        except Exception as e:
            return tracker.error(e)

    @app.tool()
    async def delete_food_log(log_id: str, meal_date: str, confirm: bool = False) -> str:
        """Delete a food log entry

        Permanently removes a logged food item from the nutrition log.
        Works for both QUICK_ADD and REGULAR_LOG entry types.
        Use get_nutrition_daily_food_log to find the logId and date. The default
        call previews the matching entry; call again with confirm=true to delete.

        Args:
            log_id: Log entry ID to delete — a 32-char hex UUID
                (from get_nutrition_daily_food_log)
            meal_date: Date of the log entry in YYYY-MM-DD format
            confirm: Set true to delete after reviewing the default preview
        """
        tracker = WriteTracker("delete_food_log", "Error deleting food log")
        try:
            url = f"/nutrition-service/food/logs/{meal_date}"
            if not confirm:
                entry = _find_food_log_entry(garmin_client.connectapi(url), log_id)
                if entry is None:
                    return json.dumps({"status": "not_found", "log_id": log_id, "meal_date": meal_date})
                return json.dumps({
                    "status": "needs_confirmation", "log_id": log_id,
                    "meal_date": meal_date, "entry": entry,
                    "message": "Review this entry, then call again with confirm=true to permanently delete it.",
                }, indent=2)
            tracker.sending()
            garmin_client.client.delete("connectapi", url, json={"logIds": [log_id]}, api=True)
            tracker.committed()
            return json.dumps({"status": "success", "log_id": log_id, "message": f"Food log entry {log_id} deleted successfully."}, indent=2)
        except GarminConnectConnectionError as e:
            if _has_http_status(e, 404):
                return json.dumps(
                    {
                        "status": "not_found",
                        "log_id": log_id,
                        "message": f"Food log entry {log_id} was not found.",
                    },
                    indent=2,
                )
            return tracker.error(e)
        except Exception as e:
            return tracker.error(e)

    @app.tool()
    async def upsert_and_log(
        meal_date: str,
        meal_time: str,
        food_name: str,
        calories: float,
        carbs: Optional[float] = None,
        protein: Optional[float] = None,
        fat: Optional[float] = None,
        brand_name: Optional[str] = None,
        serving_unit: str = "G",
        number_of_units: float = 100,
        serving_qty: float = 1,
    ) -> str:
        """Find-or-create a custom food then log it in one step

        Searches the user's custom food library for food_name. If found, logs
        it immediately. If not found, creates it with the provided nutrition
        data and then logs it. This avoids duplicate food entries and removes
        the need for separate search → create → log round-trips.

        Matching is by food_name alone (case-insensitive) — brand_name never
        affects find-or-create lookup, so it can't cause a duplicate by missing
        an existing unbranded record. If an existing match is found, it is
        logged as stored, using its first serving: calories, carbs, protein,
        fat, brand_name, serving_unit and number_of_units are all ignored and
        the stored food is not changed; only serving_qty applies. Those values
        only take effect when creating a new food. To change an existing
        food's nutrition, serving size or brand, use update_custom_food.

        On an error after creation, custom_food_created is true and
        write_committed describes the food log, not the library food. A failed
        or exhausted lookup never triggers another create automatically.

        Args:
            meal_date: Date in YYYY-MM-DD format
            meal_time: Time in HH:MM:SS format (account timezone); used to
                determine the meal automatically
            food_name: Name of the food to find or create
            calories: Calories per serving (new food only)
            carbs: Carbohydrates in grams per serving (new food only)
            protein: Protein in grams per serving (new food only)
            fat: Total fat in grams per serving (new food only)
            brand_name: Brand or vendor name (new food only)
            serving_unit: Unit for serving size (e.g. "G", "ML", "OZ"). Default "G"
                (new food only)
            number_of_units: Serving size in the specified unit. Default 100
                (new food only)
            serving_qty: Number of servings to log (default 1)
        """
        tracker = WriteTracker("upsert_and_log", "Error in upsert_and_log")
        created = False
        food_id = None
        serving_id = None
        try:
            from datetime import datetime, timezone

            _validate_meal_datetime(meal_date, meal_time)

            # 1. Exhaust the bounded name search before deciding to create.
            existing = _find_custom_food_by_name(food_name)
            food_id, serving_id = _custom_food_ids(existing)
            if existing is not None and (not food_id or not serving_id):
                raise ValueError(
                    "Existing custom food has no usable foodId/servingId; not creating a duplicate."
                )

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
                food_meta: dict = {
                    "foodName": food_name,
                    "foodType": "GENERIC",
                    "source": "GARMIN",
                    "regionCode": "US",
                    "languageCode": "en",
                }
                if brand_name is not None:
                    food_meta["brandName"] = brand_name
                create_payload = {
                    "foodMetaData": food_meta,
                    "nutritionContents": [nutrition],
                }
                tracker.sending()
                try:
                    create_resp = garmin_client.client.put(
                        "connectapi", "/nutrition-service/customFood", json=create_payload, api=True
                    )
                except requests.exceptions.JSONDecodeError:
                    # Client.put(api=True) decodes only after Garmin accepts
                    # the create. The library food exists, but no log was sent.
                    created = True
                    tracker = WriteTracker(
                        "upsert_and_log", "Error in upsert_and_log (custom food was created)"
                    )
                    raise
                tracker.committed()
                created = True
                # Creation is committed, but the food log has not been sent.
                # Re-scope before parsing or looking up the create response.
                tracker = WriteTracker(
                    "upsert_and_log", "Error in upsert_and_log (custom food was created)"
                )
                food_id, serving_id = _custom_food_ids(create_resp)
                if not food_id or not serving_id:
                    found_id, found_serving = _custom_food_ids(
                        _find_custom_food_by_name(food_name)
                    )
                    food_id = found_id or food_id
                    serving_id = found_serving or serving_id
                if not food_id or not serving_id:
                    raise ValueError(f"could not retrieve foodId/servingId for '{food_name}' after creation")

            tracker = WriteTracker(
                "upsert_and_log",
                f"Error in upsert_and_log (custom food {food_id} was created)"
                if created
                else "Error in upsert_and_log",
            )

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
                    message = f"No nutrition meal windows found for {meal_date}; cannot log food at '{meal_time}'."
                    if created:
                        raise ValueError(message)
                    return message
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
            tracker.sending()
            log_resp = garmin_client.client.put(
                "connectapi", "/nutrition-service/food/logs", json=log_payload, api=True
            )
            tracker.committed()
            if not log_resp:
                return "Food logged successfully."
            return json.dumps(log_resp, indent=2)
        except Exception as e:
            error = json.loads(tracker.error(e))
            if created:
                error["custom_food_created"] = True
                if food_id:
                    error["food_id"] = food_id
                if error["write_committed"] is False:
                    error["hint"] = (
                        "The custom food was created but not logged. Check the custom "
                        "food library before retrying; reuse the existing food to log it."
                    )
            return json.dumps(error, indent=2)

    return app
