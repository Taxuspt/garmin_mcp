"""
Live integration test for delete_custom_food.

Requires valid Garmin tokens at ~/.garminconnect/garmin_tokens.json.
Skipped automatically when tokens are absent.

Run with: pytest tests/e2e/test_delete_custom_food_live.py -m e2e -s
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


@pytest.fixture(scope="module")
def garmin():
    if not os.path.isdir(TOKEN_PATH):
        pytest.skip("No Garmin token store found")
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
    from garminconnect import Garmin
    g = Garmin()
    g.login(TOKEN_PATH)
    return g


def _search(garmin, name):
    r = garmin.connectapi(
        f"/nutrition-service/customFood"
        f"?searchExpression={quote(name)}&start=0&limit=10&includeContent=true"
    )
    return r.get("customFoods", [])


def _cleanup_unique_food(garmin, food_name, known_food_id=None):
    """Best-effort cleanup restricted to this test invocation's UUID name."""
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
            garmin.client.delete(
                "connectapi",
                f"/nutrition-service/customFood/{food_id}",
                api=True,
            )
        except Exception as error:
            failures.append(f"delete food {food_id} failed: {error}")
    if failures:
        warnings.warn("; ".join(failures), stacklevel=2)


def test_delete_custom_food_round_trip(garmin, nutrition_capability):
    """Create a custom food, confirm it exists, delete it, confirm it's gone."""
    food_name = f"ZZ Live Delete Custom Food {uuid.uuid4().hex}"
    food_id = None
    try:
        with nutrition_capability.require("custom-food write"):
            resp = garmin.client.put(
                "connectapi",
                "/nutrition-service/customFood",
                json={
                    "foodMetaData": {
                        "foodName": food_name,
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
        food_id = str(resp["foodMetaData"]["foodId"])
        time.sleep(1)

        foods = _search(garmin, food_name)
        assert any(
            str(food["foodMetaData"]["foodId"]) == food_id
            for food in foods
        ), f"Custom food {food_id} not found after create"

        del_resp = garmin.client.delete(
            "connectapi", f"/nutrition-service/customFood/{food_id}", api=True
        )
        assert del_resp == {} or not del_resp
        food_id = None
        time.sleep(1)

        foods2 = _search(garmin, food_name)
        assert not any(
            food.get("foodMetaData", {}).get("foodName") == food_name
            for food in foods2
        )
    finally:
        _cleanup_unique_food(garmin, food_name, food_id)
