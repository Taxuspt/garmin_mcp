#!/usr/bin/env python3
"""
Round 5: more creative approaches for quick-add delete.
First clean up the extra entry created by D3, then test:
  E1. DELETE /food/logs/quickAdd with JSON body (DELETE-with-body)
  E2. DELETE /food/logs with JSON body (quickAdd logId)
  E3. PUT /food/logs with foodLogItems=[] + quickAddItems=DELETE
  E4. PUT /food/logs/quickAdd omitting action (maybe server defaults to no-op on existing logId)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from garminconnect import Garmin, GarminConnectConnectionError

tokenstore = os.path.expanduser("~/.garminconnect")
garmin = Garmin()
garmin.login(tokenstore)

TEST_DATE = "2026-06-15"

def get_quick_adds(date):
    log = garmin.connectapi(f"/nutrition-service/food/logs/{date}")
    result = []
    for meal in log.get("mealDetails", []):
        for food in meal.get("loggedFoods", []):
            if food.get("logCategory") == "QUICK_ADD":
                result.append(food)
    return result

def find_entry(date, log_id):
    for qa in get_quick_adds(date):
        if qa["logId"] == log_id:
            return qa
    return None

def attempt(label, fn):
    print(f"\n── {label}")
    try:
        result = fn()
        if isinstance(result, dict):
            cal = result.get("dailyNutritionContent", {}).get("calories", "?")
            print(f"   HTTP OK — dailyCalories={cal}")
        else:
            print(f"   HTTP OK — response: {result}")
        return True
    except GarminConnectConnectionError as e:
        print(f"   ERROR: {e}")
        return False

# ── Audit current quick-adds ──────────────────────────────────────────────────
qas = get_quick_adds(TEST_DATE)
print(f"[STATE] Quick-adds on {TEST_DATE}: {len(qas)}")
for qa in qas:
    print(f"  logId={qa['logId']}, name={qa['foodMetaData'].get('foodName','?')}")

# Find our two test entries
test_entries = [qa for qa in qas if qa["foodMetaData"].get("foodName", "").startswith("ZZ Diag")]
if not test_entries:
    print("No test entries found — exiting")
    sys.exit(1)

# Use the first one as our primary target
target = test_entries[0]
print(f"\n[TARGET] logId={target['logId']}, name={target['foodMetaData']['foodName']}")

# Build fields
name = target["foodMetaData"]["foodName"]
cal  = str(int(target["nutritionContent"]["calories"]))
carbs = str(int(target["nutritionContent"].get("carbs", 0)))
prot  = str(int(target["nutritionContent"].get("protein", 0)))
fat   = str(int(target["nutritionContent"].get("fat", 0)))

# ── E1: DELETE /food/logs/quickAdd with JSON body ─────────────────────────────
ok = attempt(
    "E1: DELETE /food/logs/quickAdd with JSON body",
    lambda: garmin.client._run_request(
        "DELETE",
        "/nutrition-service/food/logs/quickAdd",
        json={
            "mealDate": TEST_DATE,
            "quickAddItems": [{
                "logId": target["logId"],
                "name": name,
                "logTimestamp": target["logTimestamp"],
                "logSource": target.get("logSource", "GCW"),
                "logCategory": "QUICK_ADD",
                "mealId": target["mealId"],
                "mealTime": target["mealTime"],
                "action": "DELETE",
                "calories": cal, "carbs": carbs, "protein": prot, "fat": fat,
            }]
        }
    ).json() if hasattr(
        garmin.client._run_request(
            "DELETE",
            "/nutrition-service/food/logs/quickAdd",
            json={
                "mealDate": TEST_DATE,
                "quickAddItems": [{
                    "logId": target["logId"],
                    "name": name,
                    "logTimestamp": target["logTimestamp"],
                    "logSource": target.get("logSource", "GCW"),
                    "logCategory": "QUICK_ADD",
                    "mealId": target["mealId"],
                    "mealTime": target["mealTime"],
                    "action": "DELETE",
                    "calories": cal, "carbs": carbs, "protein": prot, "fat": fat,
                }]
            }
        ), "json"
    ) else {}
)

# Simpler approach via _fresh_api_session:
print(f"\n── E1 (direct session): DELETE /food/logs/quickAdd with JSON body")
try:
    sess = garmin.client._fresh_api_session()
    url = "https://connectapi.garmin.com/nutrition-service/food/logs/quickAdd"
    headers = garmin.client.get_api_headers()
    r = sess.request("DELETE", url, headers=headers, json={
        "mealDate": TEST_DATE,
        "quickAddItems": [{
            "logId": target["logId"],
            "name": name,
            "logTimestamp": target["logTimestamp"],
            "logSource": target.get("logSource", "GCW"),
            "logCategory": "QUICK_ADD",
            "mealId": target["mealId"],
            "mealTime": target["mealTime"],
            "action": "DELETE",
            "calories": cal, "carbs": carbs, "protein": prot, "fat": fat,
        }]
    }, timeout=15)
    print(f"   HTTP {r.status_code}: {r.text[:300]}")
    time.sleep(1)
    if not find_entry(TEST_DATE, target["logId"]):
        print("   RESULT: ✓ DELETED")
        sys.exit(0)
    else:
        print("   RESULT: ✗ still present")
except Exception as e:
    print(f"   ERROR: {e}")

# ── E2: DELETE /food/logs with JSON body ──────────────────────────────────────
if find_entry(TEST_DATE, target["logId"]):
    print(f"\n── E2: DELETE /food/logs with JSON body")
    try:
        sess = garmin.client._fresh_api_session()
        url = "https://connectapi.garmin.com/nutrition-service/food/logs"
        headers = garmin.client.get_api_headers()
        r = sess.request("DELETE", url, headers=headers, json={
            "mealDate": TEST_DATE,
            "quickAddItems": [{"logId": target["logId"]}]
        }, timeout=15)
        print(f"   HTTP {r.status_code}: {r.text[:300]}")
        time.sleep(1)
        if not find_entry(TEST_DATE, target["logId"]):
            print("   RESULT: ✓ DELETED"); sys.exit(0)
        else:
            print("   RESULT: ✗ still present")
    except Exception as e:
        print(f"   ERROR: {e}")

# ── E3: PUT /food/logs with foodLogItems=[] + quickAddItems action=DELETE ─────
if find_entry(TEST_DATE, target["logId"]):
    ok3 = attempt(
        "E3: PUT /food/logs with foodLogItems=[] + quickAddItems action=DELETE",
        lambda: garmin.client.put("connectapi", "/nutrition-service/food/logs", json={
            "mealDate": TEST_DATE,
            "foodLogItems": [],
            "quickAddItems": [{
                "logId": target["logId"],
                "name": name,
                "logTimestamp": target["logTimestamp"],
                "logSource": target.get("logSource", "GCW"),
                "logCategory": "QUICK_ADD",
                "mealId": target["mealId"],
                "mealTime": target["mealTime"],
                "action": "DELETE",
                "calories": cal, "carbs": carbs, "protein": prot, "fat": fat,
            }]
        }, api=True)
    )
    time.sleep(1)
    if ok3:
        still3 = find_entry(TEST_DATE, target["logId"])
        print(f"   RESULT: {'✗ still present' if still3 else '✓ DELETED'}")
        if not still3:
            sys.exit(0)

# ── E4: Same as regular log delete but passing logId in URL query param ────────
if find_entry(TEST_DATE, target["logId"]):
    print(f"\n── E4: DELETE /food/logs with logId as query param")
    try:
        sess = garmin.client._fresh_api_session()
        url = "https://connectapi.garmin.com/nutrition-service/food/logs"
        headers = garmin.client.get_api_headers()
        r = sess.request("DELETE", url, headers=headers,
            params={"logId": target["logId"]}, timeout=15)
        print(f"   HTTP {r.status_code}: {r.text[:300]}")
        time.sleep(1)
        if not find_entry(TEST_DATE, target["logId"]):
            print("   RESULT: ✓ DELETED"); sys.exit(0)
        else:
            print("   RESULT: ✗ still present")
    except Exception as e:
        print(f"   ERROR: {e}")

# ── Summary ──────────────────────────────────────────────────────────────────
qas_final = get_quick_adds(TEST_DATE)
print(f"\n[FINAL STATE] Quick-adds on {TEST_DATE}: {len(qas_final)}")
for qa in qas_final:
    print(f"  logId={qa['logId']}, name={qa['foodMetaData'].get('foodName','?')}")
print("[DONE]")
