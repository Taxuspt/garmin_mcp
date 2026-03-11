"""
Shared pytest fixtures for Garmin MCP testing
"""
import pytest
from unittest.mock import Mock
from datetime import datetime, timedelta
from mcp.server.fastmcp import FastMCP


@pytest.fixture
def mock_garmin_client():
    """Create a mock Garmin client with common methods stubbed"""
    client = Mock()

    # Configure mock to have all the methods we need
    # By default, methods return None (can be overridden in tests)
    client.get_activities = Mock(return_value=[])

    return client


@pytest.fixture
def today_str():
    """Return today's date as YYYY-MM-DD string"""
    return datetime.now().strftime("%Y-%m-%d")


@pytest.fixture
def yesterday_str():
    """Return yesterday's date as YYYY-MM-DD string"""
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


@pytest.fixture
def date_range():
    """Return a tuple of (start_date, end_date) as strings"""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=7)
    return (start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))


@pytest.fixture
def sample_activity():
    """Sample activity data matching Garmin API response format"""
    return {
        "activityId": 12345678901,
        "activityName": "Morning Run",
        "activityType": {
            "typeKey": "running",
            "typeId": 1
        },
        "startTimeLocal": "2024-01-15 07:00:00",
        "distance": 5000.0,
        "duration": 1800.0,
        "averageHR": 145,
        "maxHR": 165,
        "calories": 350
    }


def create_test_app(module, mock_client):
    """
    Helper function to create a FastMCP app with a specific module registered

    Args:
        module: The module to register (e.g., activity_management, workouts)
        mock_client: Mock Garmin client to configure the module with

    Returns:
        FastMCP app instance with tools registered
    """
    # Configure the module with mock client
    module.configure(mock_client)

    # Create app and register tools
    app = FastMCP("Test Garmin MCP")
    app = module.register_tools(app)

    return app


@pytest.fixture
def app_factory(mock_garmin_client):
    """
    Factory fixture to create FastMCP apps with different modules

    Usage:
        app = app_factory(activity_management)
    """
    def _create_app(module):
        return create_test_app(module, mock_garmin_client)

    return _create_app
