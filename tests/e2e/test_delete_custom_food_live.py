"""
Live integration test for delete_custom_food.

Requires valid Garmin tokens at ~/.garminconnect/garmin_tokens.json.
Skipped automatically when tokens are absent.

Run with: pytest tests/e2e/test_delete_custom_food_live.py -m e2e -s
"""
import os
import sys
import time
import pytest
from urllib.parse import quote

TOKEN_PATH = os.path.expanduser("~/.garminconnect")

pytestmark = pytest.mark.e2e


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


FOOD_NAME = "ZZ Live Delete Custom Food Test"


def test_delete_custom_food_round_trip(garmin):
    """Create a custom food, confirm it exists, delete it, confirm it's gone."""
    # Create
    resp = garmin.client.put(
        "connectapi",
        "/nutrition-service/customFood",
        json={
            "foodMetaData": {
                "foodName": FOOD_NAME,
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

    # Confirm exists
    foods = _search(garmin, FOOD_NAME)
    assert any(f["foodMetaData"]["foodId"] == food_id for f in foods), \
        f"Custom food {food_id} not found after create"

    # Delete — exact call used by delete_custom_food tool
    del_resp = garmin.client.delete(
        "connectapi", f"/nutrition-service/customFood/{food_id}", api=True
    )
    assert del_resp == {} or not del_resp, \
        f"Expected empty response (204), got: {del_resp!r}"
    time.sleep(1)

    # Confirm gone
    foods2 = _search(garmin, FOOD_NAME)
    assert not any(f["foodMetaData"]["foodId"] == food_id for f in foods2), \
        f"Custom food {food_id} still present after delete"
