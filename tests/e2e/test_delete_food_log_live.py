"""
Live integration test for delete_food_log (Bug 2 fix).

Requires valid Garmin tokens at ~/.garminconnect/garmin_tokens.json.
Skipped automatically when tokens are absent.

Run with: pytest tests/e2e/test_delete_food_log_live.py -m e2e -s
"""
import os
import time
import uuid
import warnings
import pytest
from datetime import datetime, timezone
from urllib.parse import quote

TOKEN_PATH = os.path.expanduser("~/.garminconnect")

pytestmark = [pytest.mark.e2e, pytest.mark.live_write]


@pytest.fixture(scope="module")
def garmin():
    if not os.path.isdir(TOKEN_PATH):
        pytest.skip("No Garmin token store found")
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
    from garminconnect import Garmin
    g = Garmin()
    g.login(TOKEN_PATH)
    return g


def _find_entry(garmin, date, name):
    log = garmin.connectapi(f"/nutrition-service/food/logs/{date}")
    for meal in log.get("mealDetails", []):
        for food in meal.get("loggedFoods", []):
            if food.get("foodMetaData", {}).get("foodName") == name:
                return food
    return None


def _delete(garmin, date, log_id):
    """The exact call used by delete_food_log after the Bug 2 fix."""
    garmin.client.delete(
        "connectapi",
        f"/nutrition-service/food/logs/{date}",
        json={"logIds": [log_id]},
        api=True,
    )


def _search_foods(garmin, name):
    result = garmin.connectapi(
        f"/nutrition-service/customFood"
        f"?searchExpression={quote(name)}&start=0&limit=20&includeContent=true"
    )
    return result.get("customFoods", []) if isinstance(result, dict) else []


def _cleanup_log(garmin, date, name, known_log_id=None):
    """Delete only the UUID-named log created by this test invocation."""
    failures = []
    log_ids = {known_log_id} if known_log_id is not None else set()
    try:
        for attempt in range(3):
            entry = _find_entry(garmin, date, name)
            if entry is not None and entry.get("logId") is not None:
                log_ids.add(entry["logId"])
            if log_ids or attempt == 2:
                break
            time.sleep(0.5)
    except Exception as error:
        failures.append(f"log lookup failed: {error}")
    for log_id in log_ids:
        try:
            _delete(garmin, date, log_id)
        except Exception as error:
            failures.append(f"delete log {log_id} failed: {error}")
    if failures:
        warnings.warn("; ".join(failures), stacklevel=2)


def _cleanup_food(garmin, name, known_food_id=None):
    """Delete only the UUID-named custom food created by this invocation."""
    failures = []
    food_ids = {str(known_food_id)} if known_food_id is not None else set()
    try:
        for attempt in range(3):
            for food in _search_foods(garmin, name):
                meta = food.get("foodMetaData", {})
                if meta.get("foodName") == name and meta.get("foodId") is not None:
                    food_ids.add(str(meta["foodId"]))
            if food_ids or attempt == 2:
                break
            time.sleep(0.5)
    except Exception as error:
        failures.append(f"food lookup failed: {error}")
    for food_id in food_ids:
        try:
            garmin.client.delete(
                "connectapi",
                f"/nutrition-service/customFood/{food_id}",
                api=True,
            )
        except Exception as error:
            failures.append(f"delete food {food_id} failed: {error}")
    if failures:
        warnings.warn("; ".join(failures), stacklevel=2)


TEST_DATE = datetime.now().strftime("%Y-%m-%d")


def test_delete_quick_add_round_trip(garmin, nutrition_capability):
    """Create a QUICK_ADD entry, delete it, confirm it's gone."""
    name = f"ZZ Live Delete QA {uuid.uuid4().hex}"
    log_id = None
    try:
        meals = garmin.connectapi(f"/nutrition-service/meals/{TEST_DATE}")
        meal_id = nutrition_capability.meal_id(meals, "SNACKS")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        garmin.client.put(
            "connectapi",
            "/nutrition-service/food/logs/quickAdd",
            json={
                "mealDate": TEST_DATE,
                "quickAddItems": [
                    {
                        "name": name,
                        "logId": None,
                        "logTimestamp": ts,
                        "logSource": "GCW",
                        "logCategory": "QUICK_ADD",
                        "mealTime": "15:00:00",
                        "mealId": meal_id,
                        "action": "ADD",
                        "calories": "1",
                        "carbs": "0",
                        "protein": "0",
                        "fat": "0",
                    }
                ],
            },
            api=True,
        )
        time.sleep(1)

        entry = _find_entry(garmin, TEST_DATE, name)
        assert entry is not None, "Quick-add entry not found after create"
        log_id = entry["logId"]

        _delete(garmin, TEST_DATE, log_id)
        time.sleep(1)
        assert _find_entry(garmin, TEST_DATE, name) is None
        log_id = None
    finally:
        _cleanup_log(garmin, TEST_DATE, name, log_id)


def test_delete_regular_log_round_trip(garmin, nutrition_capability):
    """Create a REGULAR_LOG entry via custom food, delete it, confirm it's gone."""
    name = f"ZZ Live Delete Regular {uuid.uuid4().hex}"
    food_id = None
    log_id = None
    try:
        with nutrition_capability.require("custom-food write"):
            cf_resp = garmin.client.put(
                "connectapi",
                "/nutrition-service/customFood",
                json={
                    "foodMetaData": {
                        "foodName": name,
                        "foodType": "GENERIC",
                        "source": "GARMIN",
                        "regionCode": "US",
                        "languageCode": "en",
                    },
                    "nutritionContents": [
                        {"servingUnit": "G", "numberOfUnits": "100", "calories": "1"}
                    ],
                },
                api=True,
            )
        food_id = str(cf_resp["foodMetaData"]["foodId"])
        serving_id = str(cf_resp["nutritionContents"][0]["servingId"])

        meals = garmin.connectapi(f"/nutrition-service/meals/{TEST_DATE}")
        meal_id = nutrition_capability.meal_id(meals, "SNACKS")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        garmin.client.put(
            "connectapi",
            "/nutrition-service/food/logs",
            json={
                "mealDate": TEST_DATE,
                "foodLogItems": [
                    {
                        "logTimestamp": ts,
                        "logSource": "GCW",
                        "logCategory": "REGULAR_LOG",
                        "mealTime": "15:00:00",
                        "action": "ADD",
                        "mealId": meal_id,
                        "foodId": food_id,
                        "servingId": serving_id,
                        "source": "GARMIN",
                        "regionCode": "US",
                        "languageCode": "en",
                        "servingQty": 1.0,
                    }
                ],
            },
            api=True,
        )
        time.sleep(1)

        entry = _find_entry(garmin, TEST_DATE, name)
        assert entry is not None, "Regular log entry not found after create"
        log_id = entry["logId"]

        _delete(garmin, TEST_DATE, log_id)
        time.sleep(1)
        assert _find_entry(garmin, TEST_DATE, name) is None
        log_id = None
    finally:
        _cleanup_log(garmin, TEST_DATE, name, log_id)
        _cleanup_food(garmin, name, food_id)
