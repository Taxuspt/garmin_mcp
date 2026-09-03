"""
Live integration tests for Bug A (upsert_and_log dedup) and Bug B (update_custom_food merge).

Requires valid Garmin tokens at ~/.garminconnect/garmin_tokens.json.
Skipped automatically when tokens are absent.

Run with: pytest tests/e2e/test_upsert_dedup_and_update_merge_live.py -m e2e -s
"""
import os
import sys
import time
import uuid
import warnings
import pytest
from datetime import datetime, timezone
from urllib.parse import quote

TOKEN_PATH = os.path.expanduser("~/.garminconnect")

pytestmark = [pytest.mark.e2e, pytest.mark.live_write]

TEST_DATE = datetime.now().strftime("%Y-%m-%d")


@pytest.fixture(scope="module")
def garmin():
    if not os.path.isdir(TOKEN_PATH):
        pytest.skip("No Garmin token store found")
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
    from garminconnect import Garmin
    g = Garmin()
    g.login(TOKEN_PATH)
    return g


# ── helpers ──────────────────────────────────────────────────────────────────

def _search_foods(garmin, name):
    r = garmin.connectapi(
        f"/nutrition-service/customFood"
        f"?searchExpression={quote(name)}&start=0&limit=20&includeContent=true"
    )
    return r.get("customFoods", []) if isinstance(r, dict) else []


def _find_by_id(garmin, name, food_id):
    for f in _search_foods(garmin, name):
        if str(f.get("foodMetaData", {}).get("foodId", "")) == food_id:
            return f
    return None


def _count_library(garmin, name):
    return sum(
        1 for f in _search_foods(garmin, name)
        if f.get("foodMetaData", {}).get("foodName") == name
    )


def _count_log_entries(garmin, food_id, date):
    log = garmin.connectapi(f"/nutrition-service/food/logs/{date}")
    return sum(
        1 for meal in log.get("mealDetails", [])
        for food in meal.get("loggedFoods", [])
        if food.get("foodMetaData", {}).get("foodId") == food_id
    )


def _get_log_entries(garmin, food_id, date):
    log = garmin.connectapi(f"/nutrition-service/food/logs/{date}")
    return [
        food for meal in log.get("mealDetails", [])
        for food in meal.get("loggedFoods", [])
        if food.get("foodMetaData", {}).get("foodId") == food_id
    ]


def _delete_food(garmin, food_id):
    garmin.client.delete("connectapi", f"/nutrition-service/customFood/{food_id}", api=True)


def _delete_log_entry(garmin, log_id, date):
    garmin.client.delete(
        "connectapi", f"/nutrition-service/food/logs/{date}",
        json={"logIds": [log_id]}, api=True,
    )


def _cleanup_unique_foods(garmin, name, known_food_ids=()):
    """Remove logs/foods scoped to this invocation's UUID-bearing name."""
    failures = []
    food_ids = {
        str(food_id)
        for food_id in known_food_ids
        if food_id is not None
    }
    try:
        for attempt in range(3):
            for food in _search_foods(garmin, name):
                meta = food.get("foodMetaData", {})
                if meta.get("foodName") == name and meta.get("foodId") is not None:
                    food_ids.add(str(meta["foodId"]))
            if attempt == 2:
                break
            time.sleep(0.5)
    except Exception as error:
        failures.append(f"food lookup failed: {error}")

    for food_id in food_ids:
        try:
            for entry in _get_log_entries(garmin, food_id, TEST_DATE):
                if entry.get("logId") is not None:
                    _delete_log_entry(garmin, entry["logId"], TEST_DATE)
                    time.sleep(0.3)
        except Exception as error:
            failures.append(f"delete logs for food {food_id} failed: {error}")
        try:
            _delete_food(garmin, food_id)
        except Exception as error:
            failures.append(f"delete food {food_id} failed: {error}")
    if failures:
        warnings.warn("; ".join(failures), stacklevel=2)


def _upsert_and_log(garmin, food_name, calories, meal_id):
    """Replicate the fixed upsert_and_log find-or-create-then-log logic."""
    r = garmin.connectapi(
        f"/nutrition-service/customFood"
        f"?searchExpression={quote(food_name)}&start=0&limit=10&includeContent=true"
    )
    foods = r.get("customFoods", []) if isinstance(r, dict) else []
    food_id = serving_id = None
    for f in foods:
        meta = f.get("foodMetaData", {})
        if meta.get("foodName", "").lower() == food_name.lower():
            food_id = str(meta.get("foodId", ""))
            contents = f.get("nutritionContents", [])
            serving_id = str(contents[0].get("servingId", "")) if contents else ""
            break

    created = False
    if not food_id:
        resp = garmin.client.put(
            "connectapi", "/nutrition-service/customFood",
            json={
                "foodMetaData": {
                    "foodName": food_name, "foodType": "GENERIC",
                    "source": "GARMIN", "regionCode": "US", "languageCode": "en",
                },
                "nutritionContents": [
                    {"servingUnit": "G", "numberOfUnits": "100",
                     "calories": str(int(calories))}
                ],
            },
            api=True,
        )
        food_id = str(resp["foodMetaData"]["foodId"])
        serving_id = str(resp["nutritionContents"][0]["servingId"])
        created = True

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    garmin.client.put(
        "connectapi", "/nutrition-service/food/logs",
        json={
            "mealDate": TEST_DATE,
            "foodLogItems": [
                {
                    "logTimestamp": ts, "logSource": "GCW", "logCategory": "REGULAR_LOG",
                    "mealTime": "15:00:00", "action": "ADD", "mealId": meal_id,
                    "foodId": food_id, "servingId": serving_id,
                    "source": "GARMIN", "regionCode": "US", "languageCode": "en",
                    "servingQty": 1.0,
                }
            ],
        },
        api=True,
    )
    return food_id, created


# ── Bug A: upsert dedup ───────────────────────────────────────────────────────

def test_upsert_dedup_same_name_reuses_food(garmin, nutrition_capability):
    """
    Calling upsert twice with the same food_name must reuse the same library
    entry (one food, two log rows) — not create a duplicate.

    Uses a name that sorts very late alphabetically to reproduce the original
    failure where page-1 results never included the food, so it was always created.
    """
    # The prefix still sorts late; UUID scoping prevents collisions with user data.
    food_name = f"ZZ Live Upsert Dedup ZZZZZ {uuid.uuid4().hex}"
    fid1 = None
    fid2 = None
    try:
        meals = garmin.connectapi(f"/nutrition-service/meals/{TEST_DATE}")
        meal_id = nutrition_capability.meal_id(meals, "SNACKS")

        with nutrition_capability.require("custom-food creation and food logging"):
            fid1, created1 = _upsert_and_log(garmin, food_name, 100, meal_id)
        assert created1, "First call should create the food"
        time.sleep(1)

        fid2, created2 = _upsert_and_log(garmin, food_name, 100, meal_id)
        assert not created2, "Second call must reuse, not create"
        time.sleep(1)

        assert fid1 == fid2, f"Got different food IDs: {fid1} vs {fid2}"
        assert _count_library(garmin, food_name) == 1
        assert _count_log_entries(garmin, fid1, TEST_DATE) == 2
    finally:
        _cleanup_unique_foods(garmin, food_name, (fid1, fid2))


# ── Bug B: update merge ───────────────────────────────────────────────────────

def test_update_custom_food_preserves_unset_fields(garmin, nutrition_capability):
    """
    Calling update_custom_food with only sodium (omitting carbs/protein/fat)
    must preserve the existing macro values, not wipe them.
    """
    food_name = f"ZZ Live Update Merge {uuid.uuid4().hex}"
    food_id = None
    try:
        with nutrition_capability.require("custom-food write"):
            resp = garmin.client.put(
                "connectapi", "/nutrition-service/customFood",
                json={
                    "foodMetaData": {
                        "foodName": food_name, "foodType": "GENERIC",
                        "source": "GARMIN", "regionCode": "US", "languageCode": "en",
                    },
                    "nutritionContents": [
                        {"servingUnit": "G", "numberOfUnits": "100", "calories": "200",
                         "carbs": "10", "protein": "20", "fat": "5"}
                    ],
                },
                api=True,
            )
        food_id = str(resp["foodMetaData"]["foodId"])
        serving_id = str(resp["nutritionContents"][0]["servingId"])
        time.sleep(1)

        before = _find_by_id(garmin, food_name, food_id)
        assert before is not None, "Food not found after create"
        nc_before = (before.get("nutritionContents") or [{}])[0]
        assert nc_before.get("carbs") == 10
        assert nc_before.get("protein") == 20
        assert nc_before.get("fat") == 5

        existing = nc_before
        optional_updates = {
            "carbs": None, "protein": None, "fat": None, "fiber": None,
            "sugar": None, "saturatedFat": None, "sodium": 500.0,
            "cholesterol": None, "potassium": None,
        }

        def _s(v):
            f = float(v)
            return str(int(f)) if f == int(f) else str(f)

        nutrition: dict = {
            "servingId": serving_id, "servingUnit": "G",
            "numberOfUnits": "100", "calories": "200",
        }
        preserved = set(optional_updates.keys())
        for key, val in existing.items():
            if key in preserved and val is not None:
                nutrition[key] = _s(val)
        for key, val in optional_updates.items():
            if val is not None:
                nutrition[key] = _s(val)

        garmin.client.put(
            "connectapi", "/nutrition-service/customFood",
            json={
                "foodMetaData": {
                    "foodId": food_id, "foodName": food_name, "foodType": "GENERIC",
                    "source": "GARMIN", "regionCode": "US", "languageCode": "en",
                },
                "nutritionContents": [nutrition],
            },
            api=True,
        )
        time.sleep(1)

        after = _find_by_id(garmin, food_name, food_id)
        assert after is not None, "Food not found after update"
        nc_after = (after.get("nutritionContents") or [{}])[0]

        assert nc_after.get("carbs") == 10
        assert nc_after.get("protein") == 20
        assert nc_after.get("fat") == 5
        assert nc_after.get("sodium") == 500
    finally:
        _cleanup_unique_foods(garmin, food_name, (food_id,))
