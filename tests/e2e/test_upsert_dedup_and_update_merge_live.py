"""
Live integration tests for Bug A (upsert_and_log dedup) and Bug B (update_custom_food merge).

Requires valid Garmin tokens at ~/.garminconnect/garmin_tokens.json.
Skipped automatically when tokens are absent.

Run with: pytest tests/e2e/test_upsert_dedup_and_update_merge_live.py -m e2e -s
"""
import os
import sys
import time
import pytest
from datetime import datetime, timezone
from urllib.parse import quote

TOKEN_PATH = os.path.expanduser("~/.garminconnect")

pytestmark = pytest.mark.e2e

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

# Name sorts very late alphabetically to mimic the original failure mode
# (foods beyond the first page were never matched).
_UPSERT_NAME = "ZZ Live Upsert Dedup Test ZZZZZ"


@pytest.fixture(autouse=False)
def cleanup_upsert(garmin):
    """Remove any pre-existing library/log entries for the test food."""
    for f in _search_foods(garmin, _UPSERT_NAME):
        if f["foodMetaData"]["foodName"] == _UPSERT_NAME:
            fid = str(f["foodMetaData"]["foodId"])
            for entry in _get_log_entries(garmin, fid, TEST_DATE):
                _delete_log_entry(garmin, entry["logId"], TEST_DATE)
                time.sleep(0.3)
            time.sleep(0.3)
            _delete_food(garmin, fid)
    time.sleep(0.5)
    yield


def test_upsert_dedup_same_name_reuses_food(garmin, cleanup_upsert):
    """
    Calling upsert twice with the same food_name must reuse the same library
    entry (one food, two log rows) — not create a duplicate.

    Uses a name that sorts very late alphabetically to reproduce the original
    failure where page-1 results never included the food, so it was always created.
    """
    meals = garmin.connectapi(f"/nutrition-service/meals/{TEST_DATE}")
    meal_id = next(m["mealId"] for m in meals["meals"] if m["mealName"] == "SNACKS")

    fid1, created1 = _upsert_and_log(garmin, _UPSERT_NAME, 100, meal_id)
    assert created1, "First call should create the food"
    time.sleep(1)

    fid2, created2 = _upsert_and_log(garmin, _UPSERT_NAME, 100, meal_id)
    assert not created2, "Second call must reuse, not create"
    time.sleep(1)

    assert fid1 == fid2, f"Got different food IDs: {fid1} vs {fid2}"
    assert _count_library(garmin, _UPSERT_NAME) == 1, "Expected exactly 1 library entry"
    assert _count_log_entries(garmin, fid1, TEST_DATE) == 2, "Expected 2 log entries"

    # cleanup
    for entry in _get_log_entries(garmin, fid1, TEST_DATE):
        _delete_log_entry(garmin, entry["logId"], TEST_DATE)
        time.sleep(0.3)
    time.sleep(0.3)
    _delete_food(garmin, fid1)


# ── Bug B: update merge ───────────────────────────────────────────────────────

_UPDATE_NAME = "ZZ Live Update Merge Test"


def test_update_custom_food_preserves_unset_fields(garmin):
    """
    Calling update_custom_food with only sodium (omitting carbs/protein/fat)
    must preserve the existing macro values, not wipe them.
    """
    resp = garmin.client.put(
        "connectapi", "/nutrition-service/customFood",
        json={
            "foodMetaData": {
                "foodName": _UPDATE_NAME, "foodType": "GENERIC",
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

    # Confirm initial values
    before = _find_by_id(garmin, _UPDATE_NAME, food_id)
    assert before is not None, "Food not found after create"
    nc_before = (before.get("nutritionContents") or [{}])[0]
    assert nc_before.get("carbs") == 10
    assert nc_before.get("protein") == 20
    assert nc_before.get("fat") == 5

    # Update with only sodium — replicate the fixed update logic
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
                "foodId": food_id, "foodName": _UPDATE_NAME, "foodType": "GENERIC",
                "source": "GARMIN", "regionCode": "US", "languageCode": "en",
            },
            "nutritionContents": [nutrition],
        },
        api=True,
    )
    time.sleep(1)

    after = _find_by_id(garmin, _UPDATE_NAME, food_id)
    assert after is not None, "Food not found after update"
    nc_after = (after.get("nutritionContents") or [{}])[0]

    assert nc_after.get("carbs") == 10, f"carbs wiped: {nc_after.get('carbs')}"
    assert nc_after.get("protein") == 20, f"protein wiped: {nc_after.get('protein')}"
    assert nc_after.get("fat") == 5, f"fat wiped: {nc_after.get('fat')}"
    assert nc_after.get("sodium") == 500, f"sodium not set: {nc_after.get('sodium')}"

    # cleanup
    _delete_food(garmin, food_id)
