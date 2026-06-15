#!/usr/bin/env python3
"""
Round 4: QUICK_ADD target still present (efe0c60c6cd54e1485d5d96c16f724f4).
The /quickAdd PUT action=DELETE silently no-ops. Try:
  D1. PUT /food/logs (non-quickAdd) with quickAddItems action=DELETE
  D2. PUT /food/logs (non-quickAdd) with foodLogItems + logCategory=QUICK_ADD action=DELETE
  D3. PUT /food/logs/quickAdd with just logId + action (no other fields — maybe server ignores empty)
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
QA_LOG_ID = "efe0c60c6cd54e1485d5d96c16f724f4"

def find_entry(date, log_id):
    log = garmin.connectapi(f"/nutrition-service/food/logs/{date}")
    for meal in log.get("mealDetails", []):
        for food in meal.get("loggedFoods", []):
            if food.get("logId") == log_id:
                return food
    return None

def attempt(label, fn):
    print(f"\n── {label}")
    try:
        result = fn()
        cal = result.get("dailyNutritionContent", {}).get("calories", "?") if isinstance(result, dict) else "?"
        print(f"   HTTP OK — dailyCalories={cal}")
        return True
    except GarminConnectConnectionError as e:
        print(f"   ERROR: {e}")
        return False

qa = find_entry(TEST_DATE, QA_LOG_ID)
if not qa:
    print(f"ERROR: target {QA_LOG_ID} not found — run diag_delete3.py first to create it")
    sys.exit(1)
print(f"[OK] Target logId={qa['logId']} still present (calories={qa['nutritionContent']['calories']})")

# ── D1: PUT /food/logs with quickAddItems ──────────────────────────────────
ok = attempt(
    "D1: PUT /food/logs (non-quickAdd endpoint) with quickAddItems action=DELETE",
    lambda: garmin.client.put("connectapi", "/nutrition-service/food/logs", json={
        "mealDate": TEST_DATE,
        "quickAddItems": [{
            "logId": qa["logId"],
            "name": qa["foodMetaData"]["foodName"],
            "logTimestamp": qa["logTimestamp"],
            "logSource": qa.get("logSource", "GCW"),
            "logCategory": "QUICK_ADD",
            "mealId": qa["mealId"],
            "mealTime": qa["mealTime"],
            "action": "DELETE",
            "calories": "1", "carbs": "0", "protein": "0", "fat": "0",
        }]
    }, api=True)
)
time.sleep(1)
if ok:
    still = find_entry(TEST_DATE, qa["logId"])
    print(f"   RESULT: {'✗ still present' if still else '✓ DELETED'}")
    if not still:
        print("   CONFIRMED: PUT /food/logs + quickAddItems + action=DELETE works!")
        sys.exit(0)

# ── D2: PUT /food/logs with foodLogItems + logCategory=QUICK_ADD ──────────
if find_entry(TEST_DATE, qa["logId"]):
    ok2 = attempt(
        "D2: PUT /food/logs with foodLogItems, logCategory=QUICK_ADD, action=DELETE",
        lambda: garmin.client.put("connectapi", "/nutrition-service/food/logs", json={
            "mealDate": TEST_DATE,
            "foodLogItems": [{
                "logId": qa["logId"],
                "logTimestamp": qa["logTimestamp"],
                "logSource": qa.get("logSource", "GCW"),
                "logCategory": "QUICK_ADD",
                "mealId": qa["mealId"],
                "mealTime": qa["mealTime"],
                "action": "DELETE",
            }]
        }, api=True)
    )
    time.sleep(1)
    if ok2:
        still2 = find_entry(TEST_DATE, qa["logId"])
        print(f"   RESULT: {'✗ still present' if still2 else '✓ DELETED'}")
        if not still2:
            print("   CONFIRMED: PUT /food/logs + foodLogItems(QUICK_ADD) + action=DELETE works!")
            sys.exit(0)

# ── D3: Try mealDate in the quickAdd item itself ──────────────────────────
if find_entry(TEST_DATE, qa["logId"]):
    ok3 = attempt(
        "D3: PUT /food/logs/quickAdd with mealDate in item + action=DELETE",
        lambda: garmin.client.put("connectapi", "/nutrition-service/food/logs/quickAdd", json={
            "mealDate": TEST_DATE,
            "quickAddItems": [{
                "logId": qa["logId"],
                "name": qa["foodMetaData"]["foodName"],
                "mealDate": TEST_DATE,
                "logTimestamp": qa["logTimestamp"],
                "logSource": qa.get("logSource", "GCW"),
                "logCategory": "QUICK_ADD",
                "mealId": qa["mealId"],
                "mealTime": qa["mealTime"],
                "action": "DELETE",
                "calories": "1", "carbs": "0", "protein": "0", "fat": "0",
            }]
        }, api=True)
    )
    time.sleep(1)
    if ok3:
        still3 = find_entry(TEST_DATE, qa["logId"])
        print(f"   RESULT: {'✗ still present' if still3 else '✓ DELETED'}")

# ── D4: Check if client has a post method — try POST /food/logs/quickAdd ─
if find_entry(TEST_DATE, qa["logId"]):
    print(f"\n── D4: Try client._run_request DELETE with body (custom)")
    try:
        # Use the underlying requests session to send DELETE with a body
        sess = garmin.client._session
        import requests
        url = "https://connectapi.garmin.com/nutrition-service/food/logs/quickAdd"
        headers = dict(sess.headers)
        headers["Content-Type"] = "application/json"
        r = sess.delete(url, json={
            "mealDate": TEST_DATE,
            "quickAddItems": [{"logId": qa["logId"]}]
        }, headers=headers)
        print(f"   HTTP {r.status_code}: {r.text[:200]}")
        time.sleep(1)
        if not find_entry(TEST_DATE, qa["logId"]):
            print("   RESULT: ✓ DELETED")
        else:
            print("   RESULT: ✗ still present")
    except Exception as e:
        print(f"   ERROR: {e}")

# ── Summary ───────────────────────────────────────────────────────────────
final = find_entry(TEST_DATE, qa["logId"])
print(f"\n[FINAL STATUS] Quick-add {qa['logId']}: {'STILL PRESENT' if final else 'DELETED'}")
print("[DONE]")
