#!/usr/bin/env python3
"""
Round 3: probe remaining delete hypotheses.
Quick-add target: efe0c60c6cd54e1485d5d96c16f724f4 on 2026-06-15 (still present).
Regular-log: create new throwaway.
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
QA_LOG_ID = "efe0c60c6cd54e1485d5d96c16f724f4"

def find_entry(log_date, log_id):
    log = garmin.connectapi(f"/nutrition-service/food/logs/{log_date}")
    for meal in log.get("mealDetails", []):
        for food in meal.get("loggedFoods", []):
            if food.get("logId") == log_id:
                return food
    return None

def try_delete(label, fn):
    print(f"\n── {label}")
    try:
        result = fn()
        print(f"   HTTP OK — response: {json.dumps(result, indent=2)[:300]}...")
        return True
    except GarminConnectConnectionError as e:
        print(f"   ERROR: {e}")
        return False

# ── Confirm quick-add still present ──────────────────────────────────────────
qa = find_entry(TEST_DATE, QA_LOG_ID)
if not qa:
    print(f"[WARN] {QA_LOG_ID} already gone — need a fresh quick-add")
    meals_d = garmin.connectapi(f"/nutrition-service/meals/{TEST_DATE}")
    snacks = next(m for m in meals_d.get("meals", []) if m.get("mealName") == "SNACKS")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    garmin.client.put("connectapi", "/nutrition-service/food/logs/quickAdd", json={
        "mealDate": TEST_DATE,
        "quickAddItems": [{"name": "ZZ Diag Delete Test", "logId": None, "logTimestamp": ts,
                           "logSource": "GCW", "logCategory": "QUICK_ADD",
                           "mealTime": "15:00:00", "mealId": snacks["mealId"],
                           "action": "ADD", "calories": "1", "carbs": "0",
                           "protein": "0", "fat": "0"}]
    }, api=True)
    time.sleep(1)
    log = garmin.connectapi(f"/nutrition-service/food/logs/{TEST_DATE}")
    for meal in log.get("mealDetails", []):
        for food in meal.get("loggedFoods", []):
            if food.get("logCategory") == "QUICK_ADD" and \
               food.get("foodMetaData", {}).get("foodName") == "ZZ Diag Delete Test":
                qa = food
                QA_LOG_ID = food["logId"]
                break

print(f"[OK] Quick-add target: logId={qa['logId']}, name={qa['foodMetaData']['foodName']}")

# Build the full quick-add entry fields
qa_name = qa.get("foodMetaData", {}).get("foodName", "")
qa_cal = str(int(qa["nutritionContent"]["calories"]))
qa_carbs = str(int(qa["nutritionContent"].get("carbs", 0)))
qa_prot = str(int(qa["nutritionContent"].get("protein", 0)))
qa_fat  = str(int(qa["nutritionContent"].get("fat", 0)))

# ── C1: PUT quickAdd, action=DELETE + name ───────────────────────────────────
ok = try_delete(
    "C1: PUT /quickAdd action=DELETE with 'name' included",
    lambda: garmin.client.put("connectapi", "/nutrition-service/food/logs/quickAdd", json={
        "mealDate": TEST_DATE,
        "quickAddItems": [{
            "logId": qa["logId"],
            "name": qa_name,
            "logTimestamp": qa["logTimestamp"],
            "logSource": qa.get("logSource", "GCW"),
            "logCategory": "QUICK_ADD",
            "mealId": qa["mealId"],
            "mealTime": qa["mealTime"],
            "action": "DELETE",
            "calories": qa_cal, "carbs": qa_carbs,
            "protein": qa_prot, "fat": qa_fat,
        }]
    }, api=True)
)
time.sleep(1)
if ok:
    if not find_entry(TEST_DATE, qa["logId"]):
        print("   RESULT: ✓ SUCCESS")
        print("   CONFIRMED: PUT /quickAdd with action=DELETE AND name field works")
    else:
        print("   RESULT: ✗ still present")

# ── C2: PUT quickAdd, action=REMOVE ──────────────────────────────────────────
if find_entry(TEST_DATE, qa["logId"]):
    ok2 = try_delete(
        "C2: PUT /quickAdd action=REMOVE (with name)",
        lambda: garmin.client.put("connectapi", "/nutrition-service/food/logs/quickAdd", json={
            "mealDate": TEST_DATE,
            "quickAddItems": [{
                "logId": qa["logId"],
                "name": qa_name,
                "logTimestamp": qa["logTimestamp"],
                "logSource": qa.get("logSource", "GCW"),
                "logCategory": "QUICK_ADD",
                "mealId": qa["mealId"],
                "mealTime": qa["mealTime"],
                "action": "REMOVE",
                "calories": qa_cal, "carbs": qa_carbs,
                "protein": qa_prot, "fat": qa_fat,
            }]
        }, api=True)
    )
    time.sleep(1)
    if ok2 and not find_entry(TEST_DATE, qa["logId"]):
        print("   RESULT: ✓ SUCCESS — action=REMOVE works")
    elif ok2:
        print("   RESULT: ✗ still present")

# ── C3: DELETE /nutrition-service/food/logs/{date}/{logId} ───────────────────
if find_entry(TEST_DATE, qa["logId"]):
    try_delete(
        f"C3: DELETE /nutrition-service/food/logs/{TEST_DATE}/{qa['logId']}",
        lambda: garmin.client.delete("connectapi",
            f"/nutrition-service/food/logs/{TEST_DATE}/{qa['logId']}", api=True)
    )
    time.sleep(1)
    if not find_entry(TEST_DATE, qa["logId"]):
        print("   RESULT: ✓ SUCCESS")

# ── C4: REGULAR_LOG — try PUT /food/logs action=DELETE ───────────────────────
print(f"\n\n── REGULAR_LOG: creating new throwaway ──")
CUSTOM_FOOD_NAME = "ZZ Diag Regular Log Test 2"
cf_resp = garmin.client.put("connectapi", "/nutrition-service/customFood", json={
    "foodMetaData": {"foodName": CUSTOM_FOOD_NAME, "foodType": "GENERIC",
                     "source": "GARMIN", "regionCode": "US", "languageCode": "en"},
    "nutritionContents": [{"servingUnit": "G", "numberOfUnits": "100", "calories": "1"}],
}, api=True)
food_id = str(cf_resp.get("foodMetaData", {}).get("foodId", ""))
serving_id = str((cf_resp.get("nutritionContents") or [{}])[0].get("servingId", ""))
print(f"[OK] Custom food: foodId={food_id}")

meals_d2 = garmin.connectapi(f"/nutrition-service/meals/{TEST_DATE}")
snacks2 = next(m for m in meals_d2.get("meals", []) if m.get("mealName") == "SNACKS")
meal_id2 = snacks2["mealId"]

ts2 = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
garmin.client.put("connectapi", "/nutrition-service/food/logs", json={
    "mealDate": TEST_DATE,
    "foodLogItems": [{"logTimestamp": ts2, "logSource": "GCW", "logCategory": "REGULAR_LOG",
                      "mealTime": "15:00:00", "action": "ADD", "mealId": meal_id2,
                      "foodId": food_id, "servingId": serving_id,
                      "source": "GARMIN", "regionCode": "US", "languageCode": "en", "servingQty": 1.0}]
}, api=True)
time.sleep(1)

reg_entry = None
log15 = garmin.connectapi(f"/nutrition-service/food/logs/{TEST_DATE}")
for meal in log15.get("mealDetails", []):
    for food in meal.get("loggedFoods", []):
        if food.get("logCategory") == "REGULAR_LOG" and \
           food.get("foodMetaData", {}).get("foodId") == food_id:
            reg_entry = food; break
if not reg_entry:
    print("ERROR: Could not find regular log entry"); sys.exit(1)

reg_log_id = reg_entry["logId"]
print(f"[OK] Regular log entry: logId={reg_log_id}, id={reg_entry.get('id','N/A')}")

# C4a: DELETE /food/logs/{logId} (baseline — expected 404)
try_delete(
    f"C4a: DELETE /food/logs/{reg_log_id} (expected 404)",
    lambda: garmin.client.delete("connectapi", f"/nutrition-service/food/logs/{reg_log_id}", api=True)
)

# C4b: PUT /food/logs action=DELETE with required fields
if find_entry(TEST_DATE, reg_log_id):
    ok4b = try_delete(
        "C4b: PUT /food/logs action=DELETE (full fields)",
        lambda: garmin.client.put("connectapi", "/nutrition-service/food/logs", json={
            "mealDate": TEST_DATE,
            "foodLogItems": [{
                "logId": reg_log_id,
                "logTimestamp": reg_entry["logTimestamp"],
                "logSource": reg_entry.get("logSource", "GCW"),
                "logCategory": "REGULAR_LOG",
                "mealId": reg_entry["mealId"],
                "mealTime": reg_entry["mealTime"],
                "action": "DELETE",
                "foodId": food_id,
                "servingId": serving_id,
                "source": "GARMIN",
                "regionCode": "US",
                "languageCode": "en",
                "servingQty": reg_entry.get("servingQty", 1.0),
            }]
        }, api=True)
    )
    time.sleep(1)
    if ok4b:
        if not find_entry(TEST_DATE, reg_log_id):
            print("   RESULT: ✓ SUCCESS — regular log deleted via PUT action=DELETE")
        else:
            print("   RESULT: ✗ still present")

# C4c: DELETE /food/logs/{date}/{logId}
if find_entry(TEST_DATE, reg_log_id):
    try_delete(
        f"C4c: DELETE /food/logs/{TEST_DATE}/{reg_log_id}",
        lambda: garmin.client.delete("connectapi",
            f"/nutrition-service/food/logs/{TEST_DATE}/{reg_log_id}", api=True)
    )
    time.sleep(1)
    if not find_entry(TEST_DATE, reg_log_id):
        print("   RESULT: ✓ SUCCESS")
    else:
        print("   RESULT: ✗ still present")

# Cleanup custom food
try:
    garmin.client.delete("connectapi", f"/nutrition-service/customFood/{food_id}", api=True)
    print(f"\n[Cleanup] Custom food {food_id} deleted")
except Exception as e:
    print(f"\n[Cleanup] Could not delete custom food: {e}")

# Summary of quick-add
qa_final = find_entry(TEST_DATE, QA_LOG_ID)
print(f"\n[STATUS] Quick-add {QA_LOG_ID}: {'STILL PRESENT' if qa_final else 'DELETED'}")
print("[DONE]")
