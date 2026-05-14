"""
Test functions for the Garmin Connect API integration
This script allows you to test the connection and API functions without MCP
"""

import os
import datetime
from pathlib import Path
import json

import pytest
from dotenv import load_dotenv
from garminconnect import Garmin

pytestmark = pytest.mark.e2e

# Load environment variables from .env file
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

def test_garmin_login():
    """Test Garmin Connect login"""
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")

    if not email or not password:
        print("ERROR: GARMIN_EMAIL and GARMIN_PASSWORD environment variables must be set in .env file")
        return False

    print(f"Attempting to login with email: {email}")

    try:
        client = Garmin(email, password)
        client.login()
        print("SUCCESS: Login successful")
        return client
    except Exception as e:
        print(f"ERROR: Login failed: {str(e)}")
        print("\nNote: Garmin Connect might require additional verification.")
        print("If this is the first time using this API, try logging in through the official Garmin website first.")
        return False

def test_activities(client, limit=3):
    """Test retrieving activities"""
    if not client:
        return

    try:
        activities = client.get_activities(0, limit)
        print(f"\nRetrieved {len(activities)} activities:")

        for idx, activity in enumerate(activities, 1):
            print(f"\n--- Activity {idx} ---")
            print(f"Name: {activity.get('activityName', 'Unknown')}")
            print(f"Type: {activity.get('activityType', {}).get('typeKey', 'Unknown')}")
            print(f"Date: {activity.get('startTimeLocal', 'Unknown')}")
            print(f"ID: {activity.get('activityId', 'Unknown')}")

        if activities:
            # Save the first activity ID for testing get_activity_details
            return activities[0].get('activityId')
    except Exception as e:
        print(f"ERROR: Failed to retrieve activities: {str(e)}")

def test_activity_details(client, activity_id):
    """Test retrieving activity details"""
    if not client or not activity_id:
        return

    try:
        activity = client.get_activity_details(activity_id)
        print(f"\nActivity Details for ID {activity_id}:")
        print(json.dumps(activity, indent=2)[:1000] + "... (truncated)")
    except Exception as e:
        print(f"ERROR: Failed to retrieve activity details: {str(e)}")

def test_health_data(client):
    """Test retrieving health data for today"""
    if not client:
        return

    today = datetime.date.today().strftime("%Y-%m-%d")
    print(f"\nTesting health data for {today}:")

    # Test steps data
    try:
        steps_data = client.get_steps_data(today)
        print("\nSteps Data:")
        print(f"Steps: {steps_data.get('steps', 0)}")
        print(f"Goal: {steps_data.get('dailyStepGoal', 0)}")
    except Exception as e:
        print(f"ERROR: Failed to retrieve steps data: {str(e)}")

    # Test heart rate data
    try:
        hr_data = client.get_heart_rates(today)
        print("\nHeart Rate Data:")
        print(f"Resting HR: {hr_data.get('restingHeartRate', 0)} bpm")
    except Exception as e:
        print(f"ERROR: Failed to retrieve heart rate data: {str(e)}")

    # Test sleep data
    try:
        sleep_data = client.get_sleep_data(today)
        daily_sleep_data = sleep_data.get('dailySleepDTO', sleep_data)
        print("\nSleep Data:")
        sleep_score = daily_sleep_data.get('sleepScoreTotal', 0)
        print(f"Sleep Score: {sleep_score}")
    except Exception as e:
        print(f"ERROR: Failed to retrieve sleep data: {str(e)}")

if __name__ == "__main__":
    client = test_garmin_login()
    if client:
        activity_id = test_activities(client)
        if activity_id:
            test_activity_details(client, activity_id)
        test_health_data(client)
