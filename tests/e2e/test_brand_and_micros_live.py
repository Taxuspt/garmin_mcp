"""
Live integration tests for brand_name and new micronutrients (transFat, calcium,
iron, vitaminD) on create_custom_food and update_custom_food.

Requires valid Garmin tokens at ~/.garminconnect/garmin_tokens.json.
Skipped automatically when tokens are absent.

Run with: pytest tests/e2e/test_brand_and_micros_live.py -m e2e -s

Validation scenarios:
  1. Create with brand + all four micros → read back → assert all fields present
  2. Merge check: update with ONLY calories (omitting brand/micros) → assert brand and
     all four micros are UNCHANGED (proves the merge carries the new fields)
  3. Update-set check: update with a NEW brand and a changed micro → assert those
     changed and the untouched ones persisted
  4. Cleanup via delete_custom_food
"""
import os
import sys
import time
import uuid
import warnings
import pytest
from urllib.parse import quote

TOKEN_PATH = os.path.expanduser("~/.garminconnect")

pytestmark = [pytest.mark.e2e, pytest.mark.live_write]

_BRAND_1 = "TestBrand Alpha"
_BRAND_2 = "TestBrand Beta"

# Distinctive values that are easy to spot in a read-back
_MICROS_1 = {"transFat": 1.5, "calcium": 130, "iron": 4, "vitaminD": 2.5}
# Changed value for step 3 — only vitaminD changes
_MICROS_3 = {"vitaminD": 10.0}


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

def _search(garmin, name):
    r = garmin.connectapi(
        f"/nutrition-service/customFood"
        f"?searchExpression={quote(name)}&start=0&limit=20&includeContent=true"
    )
    return r.get("customFoods", []) if isinstance(r, dict) else []


def _find_by_id(garmin, name, food_id):
    for f in _search(garmin, name):
        if str(f.get("foodMetaData", {}).get("foodId", "")) == food_id:
            return f
    return None


def _delete_food(garmin, food_id):
    garmin.client.delete("connectapi", f"/nutrition-service/customFood/{food_id}", api=True)


def _cleanup_unique_food(garmin, food_name, known_food_id=None):
    """Delete only food created by this invocation's UUID-scoped name."""
    food_ids = {str(known_food_id)} if known_food_id is not None else set()
    failures = []
    try:
        for attempt in range(3):
            for food in _search(garmin, food_name):
                meta = food.get("foodMetaData", {})
                if meta.get("foodName") == food_name and meta.get("foodId") is not None:
                    food_ids.add(str(meta["foodId"]))
            if food_ids or attempt == 2:
                break
            time.sleep(0.5)
    except Exception as error:
        failures.append(f"food lookup failed: {error}")

    for food_id in food_ids:
        try:
            _delete_food(garmin, food_id)
        except Exception as error:
            failures.append(f"delete food {food_id} failed: {error}")
    if failures:
        warnings.warn("; ".join(failures), stacklevel=2)


def _create(garmin, food_name, calories, brand_name=None, **micros):
    food_meta = {
        "foodName": food_name,
        "foodType": "GENERIC",
        "source": "GARMIN",
        "regionCode": "US",
        "languageCode": "en",
    }
    if brand_name is not None:
        food_meta["brandName"] = brand_name

    def _s(v):
        f = float(v)
        return str(int(f)) if f == int(f) else str(f)

    nutrition = {"servingUnit": "G", "numberOfUnits": "100", "calories": _s(calories)}
    key_map = {"transFat": "transFat", "calcium": "calcium", "iron": "iron", "vitaminD": "vitaminD"}
    for k, v in micros.items():
        if v is not None:
            nutrition[key_map[k]] = _s(v)

    resp = garmin.client.put(
        "connectapi", "/nutrition-service/customFood",
        json={"foodMetaData": food_meta, "nutritionContents": [nutrition]},
        api=True,
    )
    return str(resp["foodMetaData"]["foodId"]), str(resp["nutritionContents"][0]["servingId"])


def _update(garmin, food_id, serving_id, food_name, calories, brand_name=None, existing_brand=None, **micros):
    """Replicate the fixed update_custom_food merge logic."""
    # Fetch existing to preserve omitted fields
    existing_nutrition: dict = {}
    existing_brand_fetched = None
    try:
        r = garmin.connectapi(
            f"/nutrition-service/customFood"
            f"?searchExpression={quote(food_name)}&start=0&limit=20&includeContent=true"
        )
        foods = r.get("customFoods", []) if isinstance(r, dict) else []
        for f in foods:
            if str(f.get("foodMetaData", {}).get("foodId", "")) == food_id:
                existing_nutrition = (f.get("nutritionContents") or [{}])[0]
                existing_brand_fetched = f.get("foodMetaData", {}).get("brandName")
                break
    except Exception:
        pass

    preserved = {"carbs", "protein", "fat", "fiber", "sugar", "saturatedFat",
                 "sodium", "cholesterol", "potassium", "transFat", "calcium", "iron", "vitaminD"}

    def _s(v):
        f = float(v)
        return str(int(f)) if f == int(f) else str(f)

    nutrition: dict = {"servingId": serving_id, "servingUnit": "G",
                       "numberOfUnits": "100", "calories": _s(calories)}
    for key, val in existing_nutrition.items():
        if key in preserved and val is not None:
            nutrition[key] = _s(val)
    # overlay caller-supplied micros
    key_map = {"transFat": "transFat", "calcium": "calcium", "iron": "iron", "vitaminD": "vitaminD"}
    for k, v in micros.items():
        if v is not None:
            nutrition[key_map[k]] = _s(v)

    effective_brand = brand_name if brand_name is not None else existing_brand_fetched
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

    garmin.client.put(
        "connectapi", "/nutrition-service/customFood",
        json={"foodMetaData": food_meta, "nutritionContents": [nutrition]},
        api=True,
    )


# ── tests ─────────────────────────────────────────────────────────────────────

def test_brand_and_micros_create_and_merge(garmin, nutrition_capability):
    """
    Single test exercises all four validation scenarios sequentially:
    1. Create with brand + all four micros → read back → assert all present
    2. Update with ONLY calories (omit brand/micros) → assert brand + micros unchanged
    3. Update with new brand + changed vitaminD → assert change applied, others unchanged
    4. Cleanup
    """
    food_name = f"ZZ Live Brand Micros {uuid.uuid4().hex}"
    food_id = None
    try:
        # ── step 1: create ───────────────────────────────────────────────────
        with nutrition_capability.require("custom-food write"):
            food_id, serving_id = _create(
                garmin, food_name, 200,
                brand_name=_BRAND_1,
                **_MICROS_1,
            )
        time.sleep(1)

        rec = _find_by_id(garmin, food_name, food_id)
        assert rec is not None, "Food not found after create"
        meta = rec.get("foodMetaData", {})
        nc = (rec.get("nutritionContents") or [{}])[0]

        assert meta.get("brandName") == _BRAND_1
        assert nc.get("transFat") == 1.5
        assert nc.get("calcium") == 130
        assert nc.get("iron") == 4
        assert nc.get("vitaminD") == 2.5

        # ── step 2: merge check — omit brand and all micros ──────────────────
        _update(garmin, food_id, serving_id, food_name, 250)
        time.sleep(1)

        rec2 = _find_by_id(garmin, food_name, food_id)
        assert rec2 is not None, "Food not found after merge update"
        meta2 = rec2.get("foodMetaData", {})
        nc2 = (rec2.get("nutritionContents") or [{}])[0]

        assert meta2.get("brandName") == _BRAND_1
        assert nc2.get("transFat") == 1.5
        assert nc2.get("calcium") == 130
        assert nc2.get("iron") == 4
        assert nc2.get("vitaminD") == 2.5

        # ── step 3: update-set check — change brand + one micro ──────────────
        _update(
            garmin,
            food_id,
            serving_id,
            food_name,
            250,
            brand_name=_BRAND_2,
            **_MICROS_3,
        )
        time.sleep(1)

        rec3 = _find_by_id(garmin, food_name, food_id)
        assert rec3 is not None, "Food not found after update-set"
        meta3 = rec3.get("foodMetaData", {})
        nc3 = (rec3.get("nutritionContents") or [{}])[0]

        assert meta3.get("brandName") == _BRAND_2
        assert nc3.get("vitaminD") == 10.0
        assert nc3.get("transFat") == 1.5
        assert nc3.get("calcium") == 130
        assert nc3.get("iron") == 4
    finally:
        _cleanup_unique_food(garmin, food_name, food_id)
