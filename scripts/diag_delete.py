#!/usr/bin/env python3
"""
Diagnostic: find the correct Garmin API path to delete food log entries.
Creates a fresh throwaway quick-add entry, then tests candidate delete URLs.
Reports what worked and leaves nothing behind.
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

TEST_DATE = "2026-06-15"
TEST_NAME = "ZZ Diag Delete Test"

# ── 1. Confirm stranded fixture is still there ────────────────────────────────
STRANDED_ID = "70d19a839986435e901be3028dc96b9a"
log_14 = garmin.connectapi("/nutrition-service/food/logs/2026-06-14")
stranded = None
for meal in log_14.get("mealDetails", []):
    for food in meal.get("loggedFoods", []):
        if food.get("logId") == STRANDED_ID:
            stranded = food
if stranded:
    print(f"[OK] Stranded fixture still present: logId={STRANDED_ID}")
else:
    print("[WARN] Stranded fixture not found — may already be deleted")

# ── 2. Resolve SNACKS mealId for today ───────────────────────────────────────
meals_data = garmin.connectapi(f"/nutrition-service/meals/{TEST_DATE}")
meals = (meals_data or {}).get("meals", [])
snacks = next((m for m in meals if m.get("mealName") == "SNACKS"), None)
if not snacks:
    print("ERROR: No SNACKS meal for today — cannot create test entry")
    sys.exit(1)
meal_id = snacks["mealId"]
print(f"[OK] SNACKS mealId for {TEST_DATE}: {meal_id}")

# ── 3. Create throwaway quick-add on today ────────────────────────────────────
log_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
create_payload = {
    "mealDate": TEST_DATE,
    "quickAddItems": [{
        "name": TEST_NAME,
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
}
create_resp = garmin.client.put(
    "connectapi", "/nutrition-service/food/logs/quickAdd",
    json=create_payload, api=True
)
print(f"\n[CREATE] Response: {json.dumps(create_resp, indent=2)}")

time.sleep(1)

# ── 4. Read back to get logId ─────────────────────────────────────────────────
log_15 = garmin.connectapi(f"/nutrition-service/food/logs/{TEST_DATE}")
test_entry = None
for meal in log_15.get("mealDetails", []):
    for food in meal.get("loggedFoods", []):
        if (food.get("logCategory") == "QUICK_ADD"
                and food.get("foodMetaData", {}).get("foodName") == TEST_NAME):
            test_entry = food
            break

if not test_entry:
    print("ERROR: Could not find created test entry in food log — aborting")
    sys.exit(1)

test_log_id = test_entry["logId"]
print(f"\n[OK] Test entry created — logId: {test_log_id}")
print(f"     Fields: {list(test_entry.keys())}")

# ── 5a. Hypothesis A: DELETE /nutrition-service/food/logs/quickAdd/{logId} ────
print(f"\n── Hypothesis A: DELETE /nutrition-service/food/logs/quickAdd/{test_log_id}")
try:
    resp_a = garmin.client.delete(
        "connectapi", f"/nutrition-service/food/logs/quickAdd/{test_log_id}", api=True
    )
    print(f"    Response: {json.dumps(resp_a, indent=2)}")
    time.sleep(1)
    log_check = garmin.connectapi(f"/nutrition-service/food/logs/{TEST_DATE}")
    still_there = any(
        food.get("logId") == test_log_id
        for meal in log_check.get("mealDetails", [])
        for food in meal.get("loggedFoods", [])
    )
    if still_there:
        print("    RESULT: FAILED — entry still present")
    else:
        print("    RESULT: ✓ SUCCESS — entry deleted")
        print(f"\n[CONFIRMED] Correct delete URL: DELETE /nutrition-service/food/logs/quickAdd/{{logId}}")
        sys.exit(0)
except GarminConnectConnectionError as e:
    print(f"    RESULT: FAILED with error: {e}")

# ── 5b. Hypothesis B: PUT /nutrition-service/food/logs/quickAdd action=DELETE ─
print(f"\n── Hypothesis B: PUT /nutrition-service/food/logs/quickAdd action=DELETE")

# Need a new entry since A may have consumed it (or not)
# Re-check if entry is still there
log_check2 = garmin.connectapi(f"/nutrition-service/food/logs/{TEST_DATE}")
still_there_b = any(
    food.get("logId") == test_log_id
    for meal in log_check2.get("mealDetails", [])
    for food in meal.get("loggedFoods", [])
)
if not still_there_b:
    # A deleted it, create another one for B test
    log_ts2 = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    create_payload2 = {
        "mealDate": TEST_DATE,
        "quickAddItems": [{
            "name": TEST_NAME + " B",
            "logId": None,
            "logTimestamp": log_ts2,
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
    }
    garmin.client.put(
        "connectapi", "/nutrition-service/food/logs/quickAdd",
        json=create_payload2, api=True
    )
    time.sleep(1)
    log_fresh = garmin.connectapi(f"/nutrition-service/food/logs/{TEST_DATE}")
    for meal in log_fresh.get("mealDetails", []):
        for food in meal.get("loggedFoods", []):
            if (food.get("logCategory") == "QUICK_ADD"
                    and TEST_NAME in food.get("foodMetaData", {}).get("foodName", "")):
                test_log_id = food["logId"]
                break
    print(f"    Created fresh entry for B test — logId: {test_log_id}")

delete_payload = {
    "mealDate": TEST_DATE,
    "quickAddItems": [{
        "logId": test_log_id,
        "action": "DELETE",
    }]
}
try:
    resp_b = garmin.client.put(
        "connectapi", "/nutrition-service/food/logs/quickAdd",
        json=delete_payload, api=True
    )
    print(f"    Response: {json.dumps(resp_b, indent=2)}")
    time.sleep(1)
    log_check3 = garmin.connectapi(f"/nutrition-service/food/logs/{TEST_DATE}")
    still_there_b2 = any(
        food.get("logId") == test_log_id
        for meal in log_check3.get("mealDetails", [])
        for food in meal.get("loggedFoods", [])
    )
    if still_there_b2:
        print("    RESULT: FAILED — entry still present")
    else:
        print("    RESULT: ✓ SUCCESS — entry deleted")
        print(f"\n[CONFIRMED] Correct delete: PUT /nutrition-service/food/logs/quickAdd with action=DELETE")
        sys.exit(0)
except GarminConnectConnectionError as e:
    print(f"    RESULT: FAILED with error: {e}")

# ── 5c. Hypothesis C: DELETE /nutrition-service/food/logs/{logId} (baseline) ─
print(f"\n── Hypothesis C: DELETE /nutrition-service/food/logs/{test_log_id}  (baseline — expected 404)")
try:
    resp_c = garmin.client.delete(
        "connectapi", f"/nutrition-service/food/logs/{test_log_id}", api=True
    )
    print(f"    Response: {json.dumps(resp_c, indent=2)}")
except GarminConnectConnectionError as e:
    print(f"    Expected error: {e}")

print("\n[DONE] All hypotheses exhausted without confirmed success.")
sys.exit(1)
