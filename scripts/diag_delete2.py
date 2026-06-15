#!/usr/bin/env python3
"""
Round 2: test Hypothesis B with required fields included.
Uses the throwaway entry efe0c60c6cd54e1485d5d96c16f724f4 on 2026-06-15.
Also tests REGULAR_LOG delete (creates throwaway via log_custom_food).
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from garminconnect import Garmin, GarminConnectConnectionError

tokenstore = os.path.expanduser("~/.garminconnect")
garmin = Garmin()
garmin.login(tokenstore)

# ── Helper ────────────────────────────────────────────────────────────────────
def find_entry(log_date, log_id):
    log = garmin.connectapi(f"/nutrition-service/food/logs/{log_date}")
    for meal in log.get("mealDetails", []):
        for food in meal.get("loggedFoods", []):
            if food.get("logId") == log_id:
                return food
    return None

# ── 1. Hypothesis B2: PUT /quickAdd with full required fields ─────────────────
TEST_DATE = "2026-06-15"
THROWAWAY_ID = "efe0c60c6cd54e1485d5d96c16f724f4"

entry = find_entry(TEST_DATE, THROWAWAY_ID)
if not entry:
    print(f"[WARN] Throwaway {THROWAWAY_ID} not found — creating a fresh one")
    meals_data = garmin.connectapi(f"/nutrition-service/meals/{TEST_DATE}")
    meals = (meals_data or {}).get("meals", [])
    snacks = next((m for m in meals if m.get("mealName") == "SNACKS"), None)
    meal_id = snacks["mealId"]
    log_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    garmin.client.put(
        "connectapi", "/nutrition-service/food/logs/quickAdd",
        json={
            "mealDate": TEST_DATE,
            "quickAddItems": [{
                "name": "ZZ Diag Delete Test",
                "logId": None,
                "logTimestamp": log_ts,
                "logSource": "GCW",
                "logCategory": "QUICK_ADD",
                "mealTime": "15:00:00",
                "mealId": meal_id,
                "action": "ADD",
                "calories": "1",
                "carbs": "0",
                "protein": "0",
                "fat": "0",
            }]
        }, api=True
    )
    time.sleep(1)
    log = garmin.connectapi(f"/nutrition-service/food/logs/{TEST_DATE}")
    for meal in log.get("mealDetails", []):
        for food in meal.get("loggedFoods", []):
            if (food.get("logCategory") == "QUICK_ADD"
                    and food.get("foodMetaData", {}).get("foodName") == "ZZ Diag Delete Test"):
                entry = food
                THROWAWAY_ID = food["logId"]
                break
    if not entry:
        print("ERROR: Could not create/find test entry")
        sys.exit(1)

print(f"\n[TARGET] logId={entry['logId']}")
print(f"         logTimestamp={entry['logTimestamp']}")
print(f"         logCategory={entry['logCategory']}")
print(f"         mealId={entry['mealId']}")
print(f"         mealTime={entry['mealTime']}")
print(f"         calories={entry['nutritionContent']['calories']}")

# Build delete payload with all required fields
delete_payload = {
    "mealDate": TEST_DATE,
    "quickAddItems": [{
        "logId": entry["logId"],
        "logTimestamp": entry["logTimestamp"],
        "logSource": entry.get("logSource", "GCW"),
        "logCategory": entry["logCategory"],
        "mealId": entry["mealId"],
        "mealTime": entry["mealTime"],
        "action": "DELETE",
        "calories": str(int(entry["nutritionContent"]["calories"])),
        "carbs": str(int(entry["nutritionContent"].get("carbs", 0))),
        "protein": str(int(entry["nutritionContent"].get("protein", 0))),
        "fat": str(int(entry["nutritionContent"].get("fat", 0))),
    }]
}

print(f"\n── Hypothesis B2: PUT /nutrition-service/food/logs/quickAdd action=DELETE (full fields)")
print(f"   Payload: {json.dumps(delete_payload, indent=2)}")

try:
    resp_b2 = garmin.client.put(
        "connectapi", "/nutrition-service/food/logs/quickAdd",
        json=delete_payload, api=True
    )
    print(f"\n   Response: {json.dumps(resp_b2, indent=2)}")
    time.sleep(1)
    still_there = find_entry(TEST_DATE, entry["logId"])
    if still_there:
        print("   RESULT: FAILED — entry still present")
    else:
        print(f"   RESULT: ✓ SUCCESS — quick-add deleted via PUT action=DELETE")

except GarminConnectConnectionError as e:
    print(f"   RESULT: FAILED with error: {e}")
    sys.exit(1)

# ── 2. Test REGULAR_LOG delete: create throwaway via log_custom_food ──────────
print(f"\n\n── Testing REGULAR_LOG delete ──")
# Create a throwaway custom food then log it
CUSTOM_FOOD_NAME = "ZZ Diag Regular Log Test"

# Create custom food
cf_payload = {
    "foodMetaData": {
        "foodName": CUSTOM_FOOD_NAME,
        "foodType": "GENERIC",
        "source": "GARMIN",
        "regionCode": "US",
        "languageCode": "en",
    },
    "nutritionContents": [{
        "servingUnit": "G",
        "numberOfUnits": "100",
        "calories": "1",
    }],
}
cf_resp = garmin.client.put("connectapi", "/nutrition-service/customFood", json=cf_payload, api=True)
print(f"\n[CREATE CUSTOM FOOD] Response: {json.dumps(cf_resp, indent=2)}")

# Get the foodId / servingId
food_id = None
serving_id = None
if cf_resp:
    meta = cf_resp.get("foodMetaData", cf_resp)
    food_id = str(meta.get("foodId", ""))
    contents = cf_resp.get("nutritionContents", [])
    if contents:
        serving_id = str(contents[0].get("servingId", ""))

if not food_id or not serving_id:
    # Look it up
    time.sleep(1)
    from urllib.parse import quote
    lookup = garmin.connectapi(
        f"/nutrition-service/customFood?searchExpression={quote(CUSTOM_FOOD_NAME)}&start=0&limit=10&includeContent=true"
    )
    for f in (lookup if isinstance(lookup, list) else []):
        m = f.get("foodMetaData", f)
        if m.get("foodName", "").lower() == CUSTOM_FOOD_NAME.lower():
            food_id = str(m.get("foodId") or f.get("foodId", ""))
            conts = f.get("nutritionContents", [])
            if conts:
                serving_id = str(conts[0].get("servingId", ""))
            break

if not food_id or not serving_id:
    print("ERROR: Could not get foodId/servingId — skipping REGULAR_LOG test")
    sys.exit(0)

print(f"\n[OK] Custom food created: foodId={food_id}, servingId={serving_id}")

# Log the custom food
meals_data2 = garmin.connectapi(f"/nutrition-service/meals/{TEST_DATE}")
meals2 = (meals_data2 or {}).get("meals", [])
snacks2 = next((m for m in meals2 if m.get("mealName") == "SNACKS"), None)
meal_id2 = snacks2["mealId"]

log_ts2 = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
log_payload = {
    "mealDate": TEST_DATE,
    "foodLogItems": [{
        "logTimestamp": log_ts2,
        "logSource": "GCW",
        "logCategory": "REGULAR_LOG",
        "mealTime": "15:00:00",
        "action": "ADD",
        "mealId": meal_id2,
        "foodId": food_id,
        "servingId": serving_id,
        "source": "GARMIN",
        "regionCode": "US",
        "languageCode": "en",
        "servingQty": 1.0,
    }]
}
log_resp = garmin.client.put("connectapi", "/nutrition-service/food/logs", json=log_payload, api=True)
print(f"\n[LOG REGULAR FOOD] Response keys: {list(log_resp.keys()) if log_resp else '{}'}")
time.sleep(1)

# Find the logId of the entry we just created
regular_entry = None
log15 = garmin.connectapi(f"/nutrition-service/food/logs/{TEST_DATE}")
for meal in log15.get("mealDetails", []):
    for food in meal.get("loggedFoods", []):
        if (food.get("logCategory") == "REGULAR_LOG"
                and food.get("foodMetaData", {}).get("foodId") == food_id):
            regular_entry = food
            break

if not regular_entry:
    print("ERROR: Could not find logged regular food entry")
    sys.exit(1)

reg_log_id = regular_entry["logId"]
print(f"\n[OK] Regular log entry created: logId={reg_log_id}")
print(f"     id={regular_entry.get('id', 'NOT PRESENT')}, logCategory={regular_entry['logCategory']}")

# Test A for REGULAR_LOG: DELETE /nutrition-service/food/logs/{logId}
print(f"\n── REGULAR_LOG Hypothesis A: DELETE /nutrition-service/food/logs/{reg_log_id}")
try:
    reg_resp_a = garmin.client.delete(
        "connectapi", f"/nutrition-service/food/logs/{reg_log_id}", api=True
    )
    print(f"    Response: {json.dumps(reg_resp_a, indent=2)}")
    time.sleep(1)
    if find_entry(TEST_DATE, reg_log_id):
        print("    RESULT: FAILED — entry still present")
        # Try hypothesis B for regular
        print(f"\n── REGULAR_LOG Hypothesis B: PUT /nutrition-service/food/logs action=DELETE")
        del_payload_reg = {
            "mealDate": TEST_DATE,
            "foodLogItems": [{
                "logId": reg_log_id,
                "logTimestamp": regular_entry["logTimestamp"],
                "logSource": regular_entry.get("logSource", "GCW"),
                "logCategory": regular_entry["logCategory"],
                "mealId": regular_entry["mealId"],
                "mealTime": regular_entry["mealTime"],
                "action": "DELETE",
                "mealDate": TEST_DATE,
                "foodId": food_id,
                "servingId": serving_id,
                "source": "GARMIN",
                "regionCode": "US",
                "languageCode": "en",
                "servingQty": regular_entry.get("servingQty", 1.0),
            }]
        }
        try:
            reg_resp_b = garmin.client.put(
                "connectapi", "/nutrition-service/food/logs",
                json=del_payload_reg, api=True
            )
            print(f"    Response: {json.dumps(reg_resp_b, indent=2)}")
            time.sleep(1)
            if find_entry(TEST_DATE, reg_log_id):
                print("    RESULT: FAILED — entry still present")
            else:
                print("    RESULT: ✓ SUCCESS — regular log deleted via PUT action=DELETE")
        except GarminConnectConnectionError as e:
            print(f"    RESULT: FAILED with error: {e}")
    else:
        print("    RESULT: ✓ SUCCESS — regular log deleted via DELETE /food/logs/{logId}")

except GarminConnectConnectionError as e:
    print(f"    RESULT: FAILED with error: {e}")

# Cleanup: delete custom food
print(f"\n── Cleanup: deleting custom food {food_id}")
try:
    garmin.client.delete("connectapi", f"/nutrition-service/customFood/{food_id}", api=True)
    print("    Custom food deleted")
except Exception as e:
    print(f"    Could not delete custom food: {e}")

print("\n[DONE]")
