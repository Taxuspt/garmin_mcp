"""
Integration tests for nutrition module MCP tools

Tests tools from:
- nutrition (8 tools: 5 read + 2 write + 1 metadata)
"""
import json
import pytest
from unittest.mock import Mock, MagicMock
from mcp.server.fastmcp import FastMCP

from garmin_mcp import nutrition


@pytest.fixture
def app_with_nutrition(mock_garmin_client):
    """Create FastMCP app with nutrition tools registered"""
    nutrition.configure(mock_garmin_client)
    app = FastMCP("Test Nutrition")
    app = nutrition.register_tools(app)
    return app


# get_nutrition_daily_food_log tests

@pytest.mark.asyncio
async def test_get_nutrition_daily_food_log(app_with_nutrition, mock_garmin_client):
    """Test get_nutrition_daily_food_log tool returns food log data"""
    food_log = {
        "foodLogEntries": [
            {
                "foodName": "Banana",
                "calories": 105,
                "carbs": 27.0,
                "fat": 0.4,
                "protein": 1.3,
                "mealType": "BREAKFAST"
            }
        ],
        "totalCalories": 105
    }
    mock_garmin_client.connectapi.return_value = food_log
    result = await app_with_nutrition.call_tool(
        "get_nutrition_daily_food_log",
        {"date": "2024-01-15"}
    )
    assert result is not None
    mock_garmin_client.connectapi.assert_called_once_with("/nutrition-service/food/logs/2024-01-15")


@pytest.mark.asyncio
async def test_get_nutrition_daily_food_log_empty(app_with_nutrition, mock_garmin_client):
    """Test get_nutrition_daily_food_log tool with no data"""
    mock_garmin_client.connectapi.return_value = None
    result = await app_with_nutrition.call_tool(
        "get_nutrition_daily_food_log",
        {"date": "2024-01-15"}
    )
    assert "No food log data found" in result[0].text


@pytest.mark.asyncio
async def test_get_nutrition_daily_food_log_error(app_with_nutrition, mock_garmin_client):
    """Test get_nutrition_daily_food_log tool handles errors"""
    mock_garmin_client.connectapi.side_effect = Exception("API error")
    result = await app_with_nutrition.call_tool(
        "get_nutrition_daily_food_log",
        {"date": "2024-01-15"}
    )
    assert "Error retrieving food log data" in result[0].text


# get_nutrition_daily_meals tests

@pytest.mark.asyncio
async def test_get_nutrition_daily_meals(app_with_nutrition, mock_garmin_client):
    """Test get_nutrition_daily_meals tool returns meal data"""
    meals = {
        "meals": [
            {
                "mealId": 185360,
                "mealType": "BREAKFAST",
                "totalCalories": 450,
            }
        ]
    }
    mock_garmin_client.connectapi.return_value = meals
    result = await app_with_nutrition.call_tool(
        "get_nutrition_daily_meals",
        {"date": "2024-01-15"}
    )
    assert result is not None
    mock_garmin_client.connectapi.assert_called_once_with("/nutrition-service/meals/2024-01-15")


@pytest.mark.asyncio
async def test_get_nutrition_daily_meals_empty(app_with_nutrition, mock_garmin_client):
    """Test get_nutrition_daily_meals tool with no data"""
    mock_garmin_client.connectapi.return_value = None
    result = await app_with_nutrition.call_tool(
        "get_nutrition_daily_meals",
        {"date": "2024-01-15"}
    )
    assert "No meal data found" in result[0].text


@pytest.mark.asyncio
async def test_get_nutrition_daily_meals_error(app_with_nutrition, mock_garmin_client):
    """Test get_nutrition_daily_meals tool handles errors"""
    mock_garmin_client.connectapi.side_effect = Exception("API error")
    result = await app_with_nutrition.call_tool(
        "get_nutrition_daily_meals",
        {"date": "2024-01-15"}
    )
    assert "Error retrieving meal data" in result[0].text


# get_nutrition_daily_settings tests

@pytest.mark.asyncio
async def test_get_nutrition_daily_settings(app_with_nutrition, mock_garmin_client):
    """Test get_nutrition_daily_settings tool returns settings data"""
    settings = {
        "calorieGoal": 2000,
        "carbsGoalPercent": 50,
        "fatGoalPercent": 30,
        "proteinGoalPercent": 20
    }
    mock_garmin_client.connectapi.return_value = settings
    result = await app_with_nutrition.call_tool(
        "get_nutrition_daily_settings",
        {"date": "2024-01-15"}
    )
    assert result is not None
    mock_garmin_client.connectapi.assert_called_once_with("/nutrition-service/settings/2024-01-15")


@pytest.mark.asyncio
async def test_get_nutrition_daily_settings_empty(app_with_nutrition, mock_garmin_client):
    """Test get_nutrition_daily_settings tool with no data"""
    mock_garmin_client.connectapi.return_value = None
    result = await app_with_nutrition.call_tool(
        "get_nutrition_daily_settings",
        {"date": "2024-01-15"}
    )
    assert "No nutrition settings found" in result[0].text


@pytest.mark.asyncio
async def test_get_nutrition_daily_settings_error(app_with_nutrition, mock_garmin_client):
    """Test get_nutrition_daily_settings tool handles errors"""
    mock_garmin_client.connectapi.side_effect = Exception("API error")
    result = await app_with_nutrition.call_tool(
        "get_nutrition_daily_settings",
        {"date": "2024-01-15"}
    )
    assert "Error retrieving nutrition settings" in result[0].text


# get_custom_foods tests

@pytest.mark.asyncio
async def test_get_custom_foods(app_with_nutrition, mock_garmin_client):
    """Test get_custom_foods tool returns custom food list"""
    custom_foods = [
        {
            "foodId": "abc123",
            "foodName": "Homemade Cookies",
            "servingId": "srv456",
        }
    ]
    mock_garmin_client.connectapi.return_value = custom_foods
    result = await app_with_nutrition.call_tool("get_custom_foods", {})
    assert result is not None
    mock_garmin_client.connectapi.assert_called_once_with(
        "/nutrition-service/customFood?searchExpression=&start=0&limit=20&includeContent=true"
    )


@pytest.mark.asyncio
async def test_get_custom_foods_with_search(app_with_nutrition, mock_garmin_client):
    """Test get_custom_foods tool with search filter"""
    mock_garmin_client.connectapi.return_value = []
    result = await app_with_nutrition.call_tool(
        "get_custom_foods",
        {"search": "cookie", "start": 0, "limit": 10}
    )
    assert result is not None
    mock_garmin_client.connectapi.assert_called_once_with(
        "/nutrition-service/customFood?searchExpression=cookie&start=0&limit=10&includeContent=true"
    )


@pytest.mark.asyncio
async def test_get_custom_foods_empty(app_with_nutrition, mock_garmin_client):
    """Test get_custom_foods tool with no results"""
    mock_garmin_client.connectapi.return_value = None
    result = await app_with_nutrition.call_tool("get_custom_foods", {})
    assert "No custom foods found" in result[0].text


# get_custom_food_serving_units tests

@pytest.mark.asyncio
async def test_get_custom_food_serving_units(app_with_nutrition, mock_garmin_client):
    """Test get_custom_food_serving_units returns unit list"""
    units = [{"unitKey": "G", "unitName": "Grams"}, {"unitKey": "ML", "unitName": "Milliliters"}]
    mock_garmin_client.connectapi.return_value = units
    result = await app_with_nutrition.call_tool("get_custom_food_serving_units", {})
    assert result is not None
    mock_garmin_client.connectapi.assert_called_once_with(
        "/nutrition-service/metadata/customFoodServingUnits"
    )


@pytest.mark.asyncio
async def test_get_custom_food_serving_units_error(app_with_nutrition, mock_garmin_client):
    """Test get_custom_food_serving_units handles errors"""
    mock_garmin_client.connectapi.side_effect = Exception("API error")
    result = await app_with_nutrition.call_tool("get_custom_food_serving_units", {})
    assert "Error retrieving serving units" in result[0].text


# create_custom_food tests

@pytest.mark.asyncio
async def test_create_custom_food(app_with_nutrition, mock_garmin_client):
    """Test create_custom_food creates a food item"""
    response_data = {
        "foodId": "abc123",
        "foodName": "Homemade Cookies",
        "servingId": "srv456",
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = response_data
    mock_garmin_client.garth.put.return_value = mock_resp
    result = await app_with_nutrition.call_tool(
        "create_custom_food",
        {
            "food_name": "Homemade Cookies",
            "calories": 150,
            "serving_unit": "G",
            "number_of_units": 30,
            "carbs": 20,
            "protein": 2,
            "fat": 7,
        }
    )
    assert result is not None
    call_args = mock_garmin_client.garth.put.call_args
    assert call_args[0][0] == "connectapi"
    assert call_args[0][1] == "/nutrition-service/customFood"
    payload = call_args[1]["json"]
    assert payload["foodMetaData"]["foodName"] == "Homemade Cookies"
    assert payload["nutritionContents"][0]["calories"] == "150"
    assert payload["nutritionContents"][0]["carbs"] == "20"
    assert payload["nutritionContents"][0]["protein"] == "2"
    assert payload["nutritionContents"][0]["fat"] == "7"
    assert payload["nutritionContents"][0]["servingUnit"] == "G"
    assert payload["nutritionContents"][0]["numberOfUnits"] == "30"


@pytest.mark.asyncio
async def test_create_custom_food_minimal(app_with_nutrition, mock_garmin_client):
    """Test create_custom_food with only required fields"""
    mock_resp = MagicMock()
    mock_resp.status_code = 204
    mock_garmin_client.garth.put.return_value = mock_resp
    result = await app_with_nutrition.call_tool(
        "create_custom_food",
        {"food_name": "Simple Food", "calories": 100}
    )
    assert "Custom food created" in result[0].text
    call_args = mock_garmin_client.garth.put.call_args
    payload = call_args[1]["json"]
    # Optional fields should NOT be present in the payload
    assert "carbs" not in payload["nutritionContents"][0]
    assert "protein" not in payload["nutritionContents"][0]
    assert "fat" not in payload["nutritionContents"][0]


@pytest.mark.asyncio
async def test_create_custom_food_error(app_with_nutrition, mock_garmin_client):
    """Test create_custom_food handles errors"""
    mock_garmin_client.garth.put.side_effect = Exception("API error")
    result = await app_with_nutrition.call_tool(
        "create_custom_food",
        {"food_name": "Test", "calories": 100}
    )
    assert "Error creating custom food" in result[0].text


# update_custom_food tests

@pytest.mark.asyncio
async def test_update_custom_food(app_with_nutrition, mock_garmin_client):
    """Test update_custom_food updates an existing food item"""
    response_data = {
        "foodId": "abc123",
        "foodName": "Homemade Cookies Updated",
        "servingId": "srv456",
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = response_data
    mock_garmin_client.garth.put.return_value = mock_resp
    result = await app_with_nutrition.call_tool(
        "update_custom_food",
        {
            "food_id": "abc123",
            "serving_id": "srv456",
            "food_name": "Homemade Cookies Updated",
            "calories": 160,
            "serving_unit": "G",
            "number_of_units": 30,
            "carbs": 22,
            "protein": 3,
            "fat": 8,
        }
    )
    assert result is not None
    call_args = mock_garmin_client.garth.put.call_args
    assert call_args[0][0] == "connectapi"
    assert call_args[0][1] == "/nutrition-service/customFood"
    payload = call_args[1]["json"]
    assert payload["foodMetaData"]["foodId"] == "abc123"
    assert payload["foodMetaData"]["foodName"] == "Homemade Cookies Updated"
    assert payload["nutritionContents"][0]["servingId"] == "srv456"
    assert payload["nutritionContents"][0]["calories"] == "160"
    assert payload["nutritionContents"][0]["carbs"] == "22"
    assert payload["nutritionContents"][0]["protein"] == "3"
    assert payload["nutritionContents"][0]["fat"] == "8"


@pytest.mark.asyncio
async def test_update_custom_food_204(app_with_nutrition, mock_garmin_client):
    """Test update_custom_food with 204 response"""
    mock_resp = MagicMock()
    mock_resp.status_code = 204
    mock_garmin_client.garth.put.return_value = mock_resp
    result = await app_with_nutrition.call_tool(
        "update_custom_food",
        {
            "food_id": "abc123",
            "serving_id": "srv456",
            "food_name": "Simple Food",
            "calories": 100,
        }
    )
    assert "Custom food updated" in result[0].text


@pytest.mark.asyncio
async def test_update_custom_food_error(app_with_nutrition, mock_garmin_client):
    """Test update_custom_food handles errors"""
    mock_garmin_client.garth.put.side_effect = Exception("API error")
    result = await app_with_nutrition.call_tool(
        "update_custom_food",
        {
            "food_id": "abc123",
            "serving_id": "srv456",
            "food_name": "Test",
            "calories": 100,
        }
    )
    assert "Error updating custom food" in result[0].text


# log_food tests

@pytest.mark.asyncio
async def test_log_food(app_with_nutrition, mock_garmin_client):
    """Test log_food logs a food item to a meal"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "ok"}
    mock_garmin_client.garth.put.return_value = mock_resp
    result = await app_with_nutrition.call_tool(
        "log_food",
        {
            "meal_date": "2024-01-15",
            "meal_time": "12:30:00",
            "meal_id": 185360,
            "food_id": "abc123",
            "serving_id": "srv456",
            "serving_qty": 3,
        }
    )
    assert result is not None
    call_args = mock_garmin_client.garth.put.call_args
    assert call_args[0][0] == "connectapi"
    assert call_args[0][1] == "/nutrition-service/food/logs"
    payload = call_args[1]["json"]
    assert payload["mealDate"] == "2024-01-15"
    assert len(payload["foodLogItems"]) == 1
    item = payload["foodLogItems"][0]
    assert item["mealId"] == 185360
    assert item["foodId"] == "abc123"
    assert item["servingId"] == "srv456"
    assert item["servingQty"] == 3
    assert item["action"] == "ADD"
    assert item["mealTime"] == "12:30:00"


@pytest.mark.asyncio
async def test_log_food_default_qty(app_with_nutrition, mock_garmin_client):
    """Test log_food with default serving quantity of 1"""
    mock_resp = MagicMock()
    mock_resp.status_code = 204
    mock_garmin_client.garth.put.return_value = mock_resp
    result = await app_with_nutrition.call_tool(
        "log_food",
        {
            "meal_date": "2024-01-15",
            "meal_time": "08:00:00",
            "meal_id": 185360,
            "food_id": "abc123",
            "serving_id": "srv456",
        }
    )
    assert "Food logged successfully" in result[0].text
    payload = mock_garmin_client.garth.put.call_args[1]["json"]
    assert payload["foodLogItems"][0]["servingQty"] == 1


@pytest.mark.asyncio
async def test_log_food_error(app_with_nutrition, mock_garmin_client):
    """Test log_food handles errors"""
    mock_garmin_client.garth.put.side_effect = Exception("API error")
    result = await app_with_nutrition.call_tool(
        "log_food",
        {
            "meal_date": "2024-01-15",
            "meal_time": "12:00:00",
            "meal_id": 185360,
            "food_id": "abc123",
            "serving_id": "srv456",
        }
    )
    assert "Error logging food" in result[0].text
