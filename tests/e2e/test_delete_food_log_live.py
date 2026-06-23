"""
Live integration test for delete_food_log (Bug 2 fix).

Requires valid Garmin tokens at ~/.garminconnect/garmin_tokens.json.
Skipped automatically when tokens are absent.

Run with: pytest tests/e2e/test_delete_food_log_live.py -m e2e -s
"""
import os
import time
import pytest
from datetime import datetime, timezone

TOKEN_PATH = os.path.expanduser("~/.garminconnect")

pytestmark = pytest.mark.e2e


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


TEST_DATE = datetime.now().strftime("%Y-%m-%d")
QUICK_ADD_NAME = "ZZ Live Delete Test QA"
REGULAR_NAME = "ZZ Live Delete Test Regular"


def test_delete_quick_add_round_trip(garmin):
    """Create a QUICK_ADD entry, delete it, confirm it's gone."""
    meals = garmin.connectapi(f"/nutrition-service/meals/{TEST_DATE}")
    snacks = next(m for m in meals["meals"] if m["mealName"] == "SNACKS")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    garmin.client.put(
        "connectapi",
        "/nutrition-service/food/logs/quickAdd",
        json={
            "mealDate": TEST_DATE,
            "quickAddItems": [
                {
                    "name": QUICK_ADD_NAME,
                    "logId": None,
                    "logTimestamp": ts,
                    "logSource": "GCW",
                    "logCategory": "QUICK_ADD",
                    "mealTime": "15:00:00",
                    "mealId": snacks["mealId"],
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

    entry = _find_entry(garmin, TEST_DATE, QUICK_ADD_NAME)
    assert entry is not None, "Quick-add entry not found after create"
    log_id = entry["logId"]

    _delete(garmin, TEST_DATE, log_id)
    time.sleep(1)

    assert _find_entry(garmin, TEST_DATE, QUICK_ADD_NAME) is None, \
        f"Quick-add entry {log_id} still present after delete"


def test_delete_regular_log_round_trip(garmin):
    """Create a REGULAR_LOG entry via custom food, delete it, confirm it's gone."""
    cf_resp = garmin.client.put(
        "connectapi",
        "/nutrition-service/customFood",
        json={
            "foodMetaData": {
                "foodName": REGULAR_NAME,
                "foodType": "GENERIC",
                "source": "GARMIN",
                "regionCode": "US",
                "languageCode": "en",
            },
            "nutritionContents": [{"servingUnit": "G", "numberOfUnits": "100", "calories": "1"}],
        },
        api=True,
    )
    food_id = str(cf_resp["foodMetaData"]["foodId"])
    serving_id = str(cf_resp["nutritionContents"][0]["servingId"])

    meals = garmin.connectapi(f"/nutrition-service/meals/{TEST_DATE}")
    snacks = next(m for m in meals["meals"] if m["mealName"] == "SNACKS")
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
                    "mealId": snacks["mealId"],
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

    entry = _find_entry(garmin, TEST_DATE, REGULAR_NAME)
    assert entry is not None, "Regular log entry not found after create"
    log_id = entry["logId"]

    _delete(garmin, TEST_DATE, log_id)
    time.sleep(1)

    assert _find_entry(garmin, TEST_DATE, REGULAR_NAME) is None, \
        f"Regular log entry {log_id} still present after delete"

    # Clean up custom food
    try:
        garmin.client.delete("connectapi", f"/nutrition-service/customFood/{food_id}", api=True)
    except Exception:
        pass
