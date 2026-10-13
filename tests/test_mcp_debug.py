"""
Debug version of the MCP server for direct testing
"""

import asyncio
import datetime
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from garminconnect import Garmin

# Logs in to the real account, so it only runs when e2e tests are requested.
pytestmark = pytest.mark.e2e

# Load environment variables from .env file
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

# Direct test function
async def test_direct():
    # Get credentials from environment
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")

    print(f"Logging in with email: {email}")

    try:
        # Create and initialize Garmin client
        client = Garmin(email, password)
        client.login()
        print("Login successful!")

        # Test activities
        print("\nGetting recent activities...")
        activities = client.get_activities(0, 2)

        if activities:
            print(f"Found {len(activities)} activities")
            for idx, activity in enumerate(activities, 1):
                print(f"\n--- Activity {idx} ---")
                print(f"Name: {activity.get('activityName', 'Unknown')}")
        else:
            print("No activities found")

        print("\nTest completed successfully!")

    except Exception as e:
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_direct())
