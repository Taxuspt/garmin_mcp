"""
Integration tests for nutrition module MCP tools

Tests tools from:
- nutrition (9 tools: 6 read + 2 write + 1 metadata)
"""
import json
from copy import deepcopy
import pytest
from unittest.mock import Mock, call
from mcp.server.fastmcp import FastMCP
from garminconnect import GarminConnectConnectionError

from garmin_mcp import nutrition


@pytest.fixture
def app_with_nutrition(mock_garmin_client):
    """Create FastMCP app with nutrition tools registered"""
    nutrition.configure(mock_garmin_client)
    app = FastMCP("Test Nutrition")
    app = nutrition.register_tools(app)
    return app


def _connection_error_with_status(status_code: int, message: str = "API error"):
    exc = GarminConnectConnectionError(message)
    exc.response = Mock(status_code=status_code)
    return exc


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
    assert "No food log data found" in result[0][0].text


@pytest.mark.asyncio
async def test_get_nutrition_daily_food_log_error(app_with_nutrition, mock_garmin_client):
    """Test get_nutrition_daily_food_log tool handles errors"""
    mock_garmin_client.connectapi.side_effect = Exception("API error")
    result = await app_with_nutrition.call_tool(
        "get_nutrition_daily_food_log",
        {"date": "2024-01-15"}
    )
    assert "Error retrieving food log data" in result[0][0].text


# get_nutrition_summary_between_dates tests

@pytest.mark.asyncio
async def test_get_nutrition_summary_between_dates(app_with_nutrition, mock_garmin_client):
    """Curates per-day totals and derives item_count from mealDetails.

    Response shape mirrors what Garmin's /nutrition-service/food/logs/range
    actually returns: a logged day carries dailyNutritionContent and
    non-empty loggedFoods; a fully unlogged day has no dailyNutritionContent
    key at all (not null -- absent) and every meal's loggedFoods is [].
    """
    mock_garmin_client.connectapi.return_value = {
        "dailyNutritionSummaries": [
            {
                "mealDate": "2024-01-14",
                "mealDetails": [
                    {"mealName": "BREAKFAST", "loggedFoods": []},
                    {"mealName": "LUNCH", "loggedFoods": []},
                ],
            },
            {
                "mealDate": "2024-01-15",
                "dailyNutritionContent": {
                    "calories": 1499,
                    "carbs": 107.0,
                    "fat": 63.0,
                    "protein": 142.0,
                },
                "mealDetails": [
                    {"mealName": "BREAKFAST", "loggedFoods": [{"foodMetaData": {}}]},
                    {
                        "mealName": "LUNCH",
                        "loggedFoods": [{"foodMetaData": {}}, {"foodMetaData": {}}],
                    },
                ],
            },
        ]
    }

    result = await app_with_nutrition.call_tool(
        "get_nutrition_summary_between_dates",
        {"start_date": "2024-01-14", "end_date": "2024-01-15"},
    )

    mock_garmin_client.connectapi.assert_called_once_with(
        "/nutrition-service/food/logs/range",
        params={"startDate": "2024-01-14", "endDate": "2024-01-15"},
    )
    data = json.loads(result[0][0].text)
    assert data["start_date"] == "2024-01-14"
    assert data["end_date"] == "2024-01-15"

    unlogged, logged = data["days"]
    assert unlogged["date"] == "2024-01-14"
    assert unlogged["item_count"] == 0
    assert unlogged["calories"] is None

    assert logged["date"] == "2024-01-15"
    assert logged["item_count"] == 3
    assert logged["calories"] == 1499
    assert logged["carbs"] == 107.0
    assert logged["protein"] == 142.0
    assert logged["fat"] == 63.0


@pytest.mark.asyncio
async def test_get_nutrition_summary_between_dates_rejects_oversized_range(
    app_with_nutrition, mock_garmin_client
):
    """The tool enforces Garmin's own 61-day cap before making a request."""
    result = await app_with_nutrition.call_tool(
        "get_nutrition_summary_between_dates",
        {"start_date": "2024-01-01", "end_date": "2024-04-01"},
    )
    assert "too large" in result[0][0].text
    mock_garmin_client.connectapi.assert_not_called()


@pytest.mark.asyncio
async def test_get_nutrition_summary_between_dates_rejects_inverted_range(
    app_with_nutrition, mock_garmin_client
):
    result = await app_with_nutrition.call_tool(
        "get_nutrition_summary_between_dates",
        {"start_date": "2024-01-15", "end_date": "2024-01-01"},
    )
    assert "end_date must be on or after start_date" in result[0][0].text
    mock_garmin_client.connectapi.assert_not_called()


@pytest.mark.asyncio
async def test_get_nutrition_summary_between_dates_empty(app_with_nutrition, mock_garmin_client):
    mock_garmin_client.connectapi.return_value = {"dailyNutritionSummaries": []}
    result = await app_with_nutrition.call_tool(
        "get_nutrition_summary_between_dates",
        {"start_date": "2024-01-14", "end_date": "2024-01-15"},
    )
    assert "No nutrition data found" in result[0][0].text


@pytest.mark.asyncio
async def test_get_nutrition_summary_between_dates_error(app_with_nutrition, mock_garmin_client):
    mock_garmin_client.connectapi.side_effect = Exception("API error")
    result = await app_with_nutrition.call_tool(
        "get_nutrition_summary_between_dates",
        {"start_date": "2024-01-14", "end_date": "2024-01-15"},
    )
    assert "Error retrieving nutrition summary" in result[0][0].text


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
    assert "No meal data found" in result[0][0].text


@pytest.mark.asyncio
async def test_get_nutrition_daily_meals_error(app_with_nutrition, mock_garmin_client):
    """Test get_nutrition_daily_meals tool handles errors"""
    mock_garmin_client.connectapi.side_effect = Exception("API error")
    result = await app_with_nutrition.call_tool(
        "get_nutrition_daily_meals",
        {"date": "2024-01-15"}
    )
    assert "Error retrieving meal data" in result[0][0].text


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
    assert "No nutrition settings found" in result[0][0].text


@pytest.mark.asyncio
async def test_get_nutrition_daily_settings_error(app_with_nutrition, mock_garmin_client):
    """Test get_nutrition_daily_settings tool handles errors"""
    mock_garmin_client.connectapi.side_effect = Exception("API error")
    result = await app_with_nutrition.call_tool(
        "get_nutrition_daily_settings",
        {"date": "2024-01-15"}
    )
    assert "Error retrieving nutrition settings" in result[0][0].text


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
        "/nutrition-service/customFood",
        params={
            "searchExpression": "",
            "start": 0,
            "limit": 20,
            "includeContent": "true",
        },
    )


@pytest.mark.asyncio
async def test_get_custom_foods_with_search(app_with_nutrition, mock_garmin_client):
    """Test get_custom_foods tool with search filter"""
    mock_garmin_client.connectapi.return_value = []
    result = await app_with_nutrition.call_tool(
        "get_custom_foods",
        {"search": "cookie & cream/é", "start": 0, "limit": 10}
    )
    assert result is not None
    mock_garmin_client.connectapi.assert_called_once_with(
        "/nutrition-service/customFood",
        params={
            "searchExpression": "cookie & cream/é",
            "start": 0,
            "limit": 10,
            "includeContent": "true",
        },
    )


@pytest.mark.asyncio
async def test_get_custom_foods_empty(app_with_nutrition, mock_garmin_client):
    """Test get_custom_foods tool with no results"""
    mock_garmin_client.connectapi.return_value = None
    result = await app_with_nutrition.call_tool("get_custom_foods", {})
    assert "No custom foods found" in result[0][0].text


@pytest.mark.asyncio
async def test_get_custom_foods_forbidden_is_an_error(
    app_with_nutrition, mock_garmin_client
):
    """Test get_custom_foods preserves generic Garmin 403 failures"""
    mock_garmin_client.connectapi.side_effect = _connection_error_with_status(
        403, "localized or non-English message"
    )
    result = await app_with_nutrition.call_tool("get_custom_foods", {})
    assert result[0][0].text == "Error retrieving custom foods: localized or non-English message"


@pytest.mark.asyncio
async def test_get_custom_foods_unsupported_forbidden_is_unavailable(
    app_with_nutrition, mock_garmin_client
):
    """Test get_custom_foods downgrades only the unsupported-feature 403"""
    mock_garmin_client.connectapi.side_effect = _connection_error_with_status(
        403, "Custom foods not supported for this account"
    )
    result = await app_with_nutrition.call_tool("get_custom_foods", {})
    assert result[0][0].text == (
        "No custom foods found; Garmin custom food API returned HTTP 403 Forbidden."
    )


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
    assert "Error retrieving serving units" in result[0][0].text


@pytest.mark.asyncio
async def test_get_custom_food_serving_units_forbidden_is_an_error(
    app_with_nutrition, mock_garmin_client
):
    """Test get_custom_food_serving_units preserves generic Garmin 403 failures"""
    mock_garmin_client.connectapi.side_effect = _connection_error_with_status(
        403, "localized or non-English message"
    )
    result = await app_with_nutrition.call_tool("get_custom_food_serving_units", {})
    assert result[0][0].text == "Error retrieving serving units: localized or non-English message"


@pytest.mark.asyncio
async def test_get_custom_food_serving_units_unsupported_forbidden_is_unavailable(
    app_with_nutrition, mock_garmin_client
):
    """Test get_custom_food_serving_units downgrades only the unsupported-feature 403"""
    mock_garmin_client.connectapi.side_effect = _connection_error_with_status(
        403, "custom food unsupported in this region"
    )
    result = await app_with_nutrition.call_tool("get_custom_food_serving_units", {})
    assert result[0][0].text == (
        "No serving units found; Garmin custom food API returned HTTP 403 Forbidden."
    )


# create_custom_food tests

@pytest.mark.asyncio
async def test_create_custom_food(app_with_nutrition, mock_garmin_client):
    """Test create_custom_food creates a food item"""
    response_data = {
        "foodId": "abc123",
        "foodName": "Homemade Cookies",
        "servingId": "srv456",
    }
    mock_garmin_client.client.put.return_value = response_data
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
    call_args = mock_garmin_client.client.put.call_args
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
    mock_garmin_client.client.put.return_value = {}
    result = await app_with_nutrition.call_tool(
        "create_custom_food",
        {"food_name": "Simple Food", "calories": 100}
    )
    assert "Custom food created" in result[0][0].text
    call_args = mock_garmin_client.client.put.call_args
    payload = call_args[1]["json"]
    # Optional fields should NOT be present in the payload
    assert "carbs" not in payload["nutritionContents"][0]
    assert "protein" not in payload["nutritionContents"][0]
    assert "fat" not in payload["nutritionContents"][0]


@pytest.mark.asyncio
async def test_create_custom_food_error(app_with_nutrition, mock_garmin_client):
    """Test create_custom_food handles errors"""
    mock_garmin_client.client.put.side_effect = Exception("API error")
    result = await app_with_nutrition.call_tool(
        "create_custom_food",
        {"food_name": "Test", "calories": 100}
    )
    assert "Error creating custom food" in result[0][0].text


@pytest.mark.asyncio
async def test_create_custom_food_forbidden_is_an_error(
    app_with_nutrition, mock_garmin_client
):
    """Test create_custom_food preserves generic Garmin 403 failures"""
    mock_garmin_client.client.put.side_effect = _connection_error_with_status(
        403, "localized or non-English message"
    )
    result = await app_with_nutrition.call_tool(
        "create_custom_food",
        {"food_name": "Test", "calories": 100},
    )
    data = json.loads(result[0][0].text)
    assert data["message"] == "Error creating custom food: localized or non-English message"
    assert data["write_committed"] is False
    assert data["status"] == "error"


@pytest.mark.asyncio
async def test_create_custom_food_forbidden_falls_back_to_status_in_message(
    app_with_nutrition, mock_garmin_client
):
    """Test create_custom_food still surfaces 403s parsed from library text"""
    mock_garmin_client.client.put.side_effect = GarminConnectConnectionError(
        "API Error 403 - acceso denegado"
    )
    result = await app_with_nutrition.call_tool(
        "create_custom_food",
        {"food_name": "Test", "calories": 100},
    )
    data = json.loads(result[0][0].text)
    assert data["message"] == "Error creating custom food: API Error 403 - acceso denegado"
    assert data["upstream_status"] == 403
    assert data["write_committed"] is False


# update_custom_food tests

# The stored record update_custom_food reads before its full-replace PUT.
STORED_CUSTOM_FOOD = {
    "foodMetaData": {"foodId": "abc123", "foodName": "Simple Food"},
    "nutritionContents": [
        {"servingId": "srv456", "servingUnit": "G", "numberOfUnits": 100, "calories": 100}
    ],
}


@pytest.mark.asyncio
async def test_update_custom_food(app_with_nutrition, mock_garmin_client):
    """Test update_custom_food updates an existing food item"""
    response_data = {
        "foodId": "abc123",
        "foodName": "Homemade Cookies Updated",
        "servingId": "srv456",
    }
    mock_garmin_client.connectapi.return_value = {"customFoods": [STORED_CUSTOM_FOOD]}
    mock_garmin_client.client.put.return_value = response_data
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
    call_args = mock_garmin_client.client.put.call_args
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
    mock_garmin_client.connectapi.assert_called_once_with(
        "/nutrition-service/customFood",
        params={
            "searchExpression": "Homemade Cookies Updated",
            "start": 0,
            "limit": 20,
            "includeContent": "true",
        },
    )


@pytest.mark.asyncio
async def test_update_custom_food_handles_null_food_metadata(
    app_with_nutrition, mock_garmin_client
):
    """A search result with foodMetaData=null must not abort the update."""
    mock_garmin_client.connectapi.return_value = {
        "customFoods": [
            {"foodMetaData": None, "nutritionContents": None},
            STORED_CUSTOM_FOOD,
        ]
    }
    mock_garmin_client.client.put.return_value = {}

    result = await app_with_nutrition.call_tool(
        "update_custom_food",
        {
            "food_id": "abc123",
            "serving_id": "srv456",
            "food_name": "Simple Food",
            "calories": 100,
        },
    )

    assert "Error" not in result[0][0].text
    mock_garmin_client.client.put.assert_called_once()


@pytest.mark.asyncio
async def test_update_custom_food_204(app_with_nutrition, mock_garmin_client):
    """Test update_custom_food with 204 response"""
    mock_garmin_client.connectapi.return_value = {"customFoods": [STORED_CUSTOM_FOOD]}
    mock_garmin_client.client.put.return_value = {}
    result = await app_with_nutrition.call_tool(
        "update_custom_food",
        {
            "food_id": "abc123",
            "serving_id": "srv456",
            "food_name": "Simple Food",
            "calories": 100,
        }
    )
    assert "Custom food updated" in result[0][0].text


@pytest.mark.asyncio
async def test_update_custom_food_error(app_with_nutrition, mock_garmin_client):
    """Test update_custom_food handles errors"""
    mock_garmin_client.connectapi.return_value = {"customFoods": [STORED_CUSTOM_FOOD]}
    mock_garmin_client.client.put.side_effect = Exception("API error")
    result = await app_with_nutrition.call_tool(
        "update_custom_food",
        {
            "food_id": "abc123",
            "serving_id": "srv456",
            "food_name": "Test",
            "calories": 100,
        }
    )
    assert "Error updating custom food" in result[0][0].text


@pytest.mark.asyncio
async def test_update_custom_food_forbidden_is_an_error(
    app_with_nutrition, mock_garmin_client
):
    """Test update_custom_food preserves generic Garmin 403 failures"""
    mock_garmin_client.connectapi.return_value = {"customFoods": [STORED_CUSTOM_FOOD]}
    mock_garmin_client.client.put.side_effect = _connection_error_with_status(
        403, "localized or non-English message"
    )
    result = await app_with_nutrition.call_tool(
        "update_custom_food",
        {
            "food_id": "abc123",
            "serving_id": "srv456",
            "food_name": "Test",
            "calories": 100,
        },
    )
    data = json.loads(result[0][0].text)
    assert data["message"] == "Error updating custom food: localized or non-English message"
    assert data["write_committed"] is False
    assert data["status"] == "error"


@pytest.mark.asyncio
async def test_create_custom_food_with_brand_and_micros(app_with_nutrition, mock_garmin_client):
    """brand_name goes to foodMetaData.brandName; new micros go to nutritionContents"""
    mock_garmin_client.client.put.return_value = {"foodMetaData": {"foodId": "x"}}
    result = await app_with_nutrition.call_tool(
        "create_custom_food",
        {
            "food_name": "Branded Bar",
            "calories": 200,
            "brand_name": "ACME",
            "trans_fat": 0.5,
            "calcium": 130,
            "iron": 2,
            "vitamin_d": 2.5,
        },
    )
    assert result is not None
    payload = mock_garmin_client.client.put.call_args[1]["json"]
    assert payload["foodMetaData"]["brandName"] == "ACME"
    nc = payload["nutritionContents"][0]
    assert nc["transFat"] == "0.5"
    assert nc["calcium"] == "130"
    assert nc["iron"] == "2"
    assert nc["vitaminD"] == "2.5"


@pytest.mark.asyncio
async def test_create_custom_food_minimal_no_brand(app_with_nutrition, mock_garmin_client):
    """brand_name absent → brandName key must not appear in foodMetaData"""
    mock_garmin_client.client.put.return_value = {}
    await app_with_nutrition.call_tool(
        "create_custom_food",
        {"food_name": "Plain Food", "calories": 50},
    )
    payload = mock_garmin_client.client.put.call_args[1]["json"]
    assert "brandName" not in payload["foodMetaData"]
    nc = payload["nutritionContents"][0]
    for key in ("transFat", "calcium", "iron", "vitaminD"):
        assert key not in nc


@pytest.mark.asyncio
async def test_update_custom_food_with_brand_and_micros(app_with_nutrition, mock_garmin_client):
    """Caller-supplied brand and micros appear in the PUT payload"""
    mock_garmin_client.connectapi.return_value = {"customFoods": [STORED_CUSTOM_FOOD]}
    mock_garmin_client.client.put.return_value = {"foodMetaData": {"foodId": "abc123"}}
    result = await app_with_nutrition.call_tool(
        "update_custom_food",
        {
            "food_id": "abc123",
            "serving_id": "srv456",
            "food_name": "Branded Food",
            "calories": 300,
            "brand_name": "BigCo",
            "trans_fat": 1.0,
            "calcium": 260,
            "iron": 4,
            "vitamin_d": 5,
        },
    )
    assert result is not None
    payload = mock_garmin_client.client.put.call_args[1]["json"]
    assert payload["foodMetaData"]["brandName"] == "BigCo"
    nc = payload["nutritionContents"][0]
    assert nc["transFat"] == "1"
    assert nc["calcium"] == "260"
    assert nc["iron"] == "4"
    assert nc["vitaminD"] == "5"


@pytest.mark.asyncio
async def test_update_custom_food_preserves_brand_and_micros(app_with_nutrition, mock_garmin_client):
    """When brand and micros are omitted, the merge carries them from the existing record."""
    # Real API returns nutritionContents values as numbers (not strings).
    existing_food = {
        "customFoods": [
            {
                "foodMetaData": {
                    "foodId": "abc123",
                    "foodName": "Existing Food",
                    "brandName": "OriginalBrand",
                },
                "nutritionContents": [
                    {
                        "servingId": "srv456",
                        "calories": 200,
                        "transFat": 0.5,
                        "calcium": 100,
                        "iron": 3,
                        "vitaminD": 2,
                    }
                ],
            }
        ]
    }
    mock_garmin_client.connectapi.return_value = existing_food
    mock_garmin_client.client.put.return_value = {}

    await app_with_nutrition.call_tool(
        "update_custom_food",
        {
            "food_id": "abc123",
            "serving_id": "srv456",
            "food_name": "Existing Food",
            "calories": 200,
            # brand_name, trans_fat, calcium, iron, vitamin_d intentionally omitted
        },
    )
    payload = mock_garmin_client.client.put.call_args[1]["json"]
    # Brand preserved from existing record
    assert payload["foodMetaData"]["brandName"] == "OriginalBrand"
    nc = payload["nutritionContents"][0]
    assert nc["transFat"] == "0.5"
    assert nc["calcium"] == "100"
    assert nc["iron"] == "3"
    assert nc["vitaminD"] == "2"


@pytest.mark.asyncio
async def test_update_custom_food_brand_overrides_existing(app_with_nutrition, mock_garmin_client):
    """Caller-supplied brand_name replaces the existing one"""
    existing_food = {
        "customFoods": [
            {
                "foodMetaData": {"foodId": "abc123", "foodName": "Food", "brandName": "OldBrand"},
                "nutritionContents": [{"servingId": "srv456", "calories": 100}],
            }
        ]
    }
    mock_garmin_client.connectapi.return_value = existing_food
    mock_garmin_client.client.put.return_value = {}

    await app_with_nutrition.call_tool(
        "update_custom_food",
        {
            "food_id": "abc123",
            "serving_id": "srv456",
            "food_name": "Food",
            "calories": 100,
            "brand_name": "NewBrand",
        },
    )
    payload = mock_garmin_client.client.put.call_args[1]["json"]
    assert payload["foodMetaData"]["brandName"] == "NewBrand"


@pytest.mark.asyncio
async def test_update_custom_food_preserves_serving_basis(app_with_nutrition, mock_garmin_client):
    """serving_unit/number_of_units are omittable and preserved from the existing record.

    Regression: these params used to default to "G"/100, so omitting them on a
    non-default-basis food (e.g. a 355 ml beer) silently rewrote the serving
    basis while leaving the per-serving macros untouched.
    """
    existing_food = {
        "customFoods": [
            {
                "foodMetaData": {"foodId": "abc123", "foodName": "NA Beer"},
                "nutritionContents": [
                    {"servingId": "srv456", "servingUnit": "ML", "numberOfUnits": 355, "calories": 90}
                ],
            }
        ]
    }
    mock_garmin_client.connectapi.return_value = existing_food
    mock_garmin_client.client.put.return_value = {}

    await app_with_nutrition.call_tool(
        "update_custom_food",
        {
            "food_id": "abc123",
            "serving_id": "srv456",
            "food_name": "NA Beer",
            "calories": 90,
            # serving_unit, number_of_units intentionally omitted
        },
    )
    nc = mock_garmin_client.client.put.call_args[1]["json"]["nutritionContents"][0]
    assert nc["servingUnit"] == "ML"
    assert nc["numberOfUnits"] == "355"


@pytest.mark.asyncio
async def test_update_custom_food_overrides_serving_basis(app_with_nutrition, mock_garmin_client):
    """Caller-supplied serving_unit/number_of_units replace the existing basis."""
    existing_food = {
        "customFoods": [
            {
                "foodMetaData": {"foodId": "abc123", "foodName": "NA Beer"},
                "nutritionContents": [
                    {"servingId": "srv456", "servingUnit": "ML", "numberOfUnits": 355, "calories": 90}
                ],
            }
        ]
    }
    mock_garmin_client.connectapi.return_value = existing_food
    mock_garmin_client.client.put.return_value = {}

    await app_with_nutrition.call_tool(
        "update_custom_food",
        {
            "food_id": "abc123",
            "serving_id": "srv456",
            "food_name": "NA Beer",
            "calories": 90,
            "serving_unit": "OZ",
            "number_of_units": 12,
        },
    )
    nc = mock_garmin_client.client.put.call_args[1]["json"]["nutritionContents"][0]
    assert nc["servingUnit"] == "OZ"
    assert nc["numberOfUnits"] == "12"


@pytest.mark.asyncio
async def test_update_custom_food_partial_serving_override(app_with_nutrition, mock_garmin_client):
    """Passing only number_of_units preserves the existing servingUnit."""
    existing_food = {
        "customFoods": [
            {
                "foodMetaData": {"foodId": "abc123", "foodName": "NA Beer"},
                "nutritionContents": [
                    {"servingId": "srv456", "servingUnit": "ML", "numberOfUnits": 355, "calories": 90}
                ],
            }
        ]
    }
    mock_garmin_client.connectapi.return_value = existing_food
    mock_garmin_client.client.put.return_value = {}

    await app_with_nutrition.call_tool(
        "update_custom_food",
        {
            "food_id": "abc123",
            "serving_id": "srv456",
            "food_name": "NA Beer",
            "calories": 90,
            "number_of_units": 500,
        },
    )
    nc = mock_garmin_client.client.put.call_args[1]["json"]["nutritionContents"][0]
    assert nc["servingUnit"] == "ML"
    assert nc["numberOfUnits"] == "500"


@pytest.mark.asyncio
async def test_update_custom_food_refuses_without_existing_record(
    app_with_nutrition, mock_garmin_client
):
    """No existing record found and no serving basis supplied: refuse the write.

    Regression: the full-replace PUT used to go out anyway, wiping brand and
    nutrients and resetting the serving to 100 G.
    """
    mock_garmin_client.connectapi.return_value = {"customFoods": []}

    result = await app_with_nutrition.call_tool(
        "update_custom_food",
        {
            "food_id": "abc123",
            "serving_id": "srv456",
            "food_name": "Simple Food",
            "calories": 100,
        },
    )

    assert "could not read the current record for food_id abc123" in result[0][0].text
    mock_garmin_client.client.put.assert_not_called()
    # Searched by name, then fell back to the unfiltered list.
    searches = [c.kwargs["params"]["searchExpression"] for c in mock_garmin_client.connectapi.call_args_list]
    assert searches == ["Simple Food", ""]


@pytest.mark.asyncio
async def test_update_custom_food_refuses_when_lookup_fails(
    app_with_nutrition, mock_garmin_client
):
    """A failed lookup is treated like a missing record, not as license to wipe it."""
    mock_garmin_client.connectapi.side_effect = Exception("lookup failed")

    result = await app_with_nutrition.call_tool(
        "update_custom_food",
        {"food_id": "abc123", "serving_id": "srv456", "food_name": "Simple Food", "calories": 100},
    )

    assert "could not read the current record" in result[0][0].text
    mock_garmin_client.client.put.assert_not_called()


@pytest.mark.asyncio
async def test_update_custom_food_full_replace_without_existing_record(
    app_with_nutrition, mock_garmin_client
):
    """With the serving basis supplied, a missing record is replaced in full."""
    mock_garmin_client.connectapi.return_value = {"customFoods": []}
    mock_garmin_client.client.put.return_value = {}

    result = await app_with_nutrition.call_tool(
        "update_custom_food",
        {
            "food_id": "abc123",
            "serving_id": "srv456",
            "food_name": "Simple Food",
            "calories": 100,
            "serving_unit": "ML",
            "number_of_units": 250,
        },
    )

    assert "Custom food updated" in result[0][0].text
    nc = mock_garmin_client.client.put.call_args[1]["json"]["nutritionContents"][0]
    assert nc["servingUnit"] == "ML"
    assert nc["numberOfUnits"] == "250"


def _custom_food_catalog(*foods):
    """connectapi stand-in for /customFood: name search, unfiltered paging."""

    def connectapi(url, params=None):
        term = params["searchExpression"].lower()
        hits = [f for f in foods if term in f["foodMetaData"]["foodName"].lower()]
        return {"customFoods": hits[params["start"]:params["start"] + params["limit"]]}

    return connectapi


@pytest.mark.asyncio
async def test_update_custom_food_rename_preserves_omitted_fields(
    app_with_nutrition, mock_garmin_client
):
    """Renaming must still find the record by foodId and keep omitted fields.

    Regression: the lookup searched only for the NEW name, missed the record,
    and the full-replace PUT wiped brand, macros and serving size.
    """
    stored = {
        "foodMetaData": {"foodId": "abc123", "foodName": "Protein Shake", "brandName": "Acme"},
        "nutritionContents": [
            {
                "servingId": "srv456",
                "servingUnit": "ML",
                "numberOfUnits": 355,
                "calories": 160,
                "protein": 30,
                "carbs": 5,
                "sodium": 200,
            }
        ],
    }
    mock_garmin_client.connectapi.side_effect = _custom_food_catalog(stored)
    mock_garmin_client.client.put.return_value = {}

    result = await app_with_nutrition.call_tool(
        "update_custom_food",
        {"food_id": "abc123", "serving_id": "srv456", "food_name": "Vanilla Whey", "calories": 160},
    )

    assert "Custom food updated" in result[0][0].text
    payload = mock_garmin_client.client.put.call_args[1]["json"]
    assert payload["foodMetaData"]["foodName"] == "Vanilla Whey"
    assert payload["foodMetaData"]["brandName"] == "Acme"
    nc = payload["nutritionContents"][0]
    assert nc["servingUnit"] == "ML"
    assert nc["numberOfUnits"] == "355"
    assert nc["protein"] == "30"
    assert nc["carbs"] == "5"
    assert nc["sodium"] == "200"


@pytest.mark.asyncio
async def test_update_custom_food_finds_record_beyond_first_page(
    app_with_nutrition, mock_garmin_client
):
    """The unfiltered fallback pages past the first 20 foods."""
    others = [
        {
            "foodMetaData": {"foodId": f"other{i}", "foodName": f"Other {i}"},
            "nutritionContents": [{"servingId": f"s{i}", "calories": 1}],
        }
        for i in range(25)
    ]
    stored = {
        "foodMetaData": {"foodId": "abc123", "foodName": "Old Name", "brandName": "Acme"},
        "nutritionContents": [{"servingId": "srv456", "calories": 90, "fat": 3}],
    }
    mock_garmin_client.connectapi.side_effect = _custom_food_catalog(*others, stored)
    mock_garmin_client.client.put.return_value = {}

    await app_with_nutrition.call_tool(
        "update_custom_food",
        {"food_id": "abc123", "serving_id": "srv456", "food_name": "New Name", "calories": 90},
    )

    payload = mock_garmin_client.client.put.call_args[1]["json"]
    assert payload["foodMetaData"]["brandName"] == "Acme"
    assert payload["nutritionContents"][0]["fat"] == "3"


MOCK_MEALS = {
    "meals": [
        {"mealId": 20249, "mealName": "BREAKFAST", "startTime": "06:00:00", "endTime": "09:00:00"},
        {"mealId": 20250, "mealName": "LUNCH", "startTime": "11:00:00", "endTime": "14:00:00"},
        {"mealId": 20251, "mealName": "DINNER", "startTime": "18:00:00", "endTime": "21:00:00"},
        {"mealId": 20252, "mealName": "SNACKS"},
    ]
}


# log_food tests

@pytest.mark.asyncio
async def test_log_food(app_with_nutrition, mock_garmin_client):
    """Test log_food resolves meal ID and quick-adds a food entry"""
    mock_garmin_client.connectapi.return_value = MOCK_MEALS
    mock_garmin_client.client.put.return_value = {"status": "ok"}
    result = await app_with_nutrition.call_tool(
        "log_food",
        {
            "meal_date": "2024-01-15",
            "meal_time": "12:30:00",  # within LUNCH window 11:00-14:00
            "name": "Chicken Breast",
            "calories": 200,
            "carbs": 3,
            "protein": 40,
            "fat": 4,
        }
    )
    assert result is not None
    mock_garmin_client.connectapi.assert_called_once_with(
        "/nutrition-service/meals/2024-01-15"
    )
    call_args = mock_garmin_client.client.put.call_args
    assert call_args[0][0] == "connectapi"
    assert call_args[0][1] == "/nutrition-service/food/logs/quickAdd"
    payload = call_args[1]["json"]
    assert payload["mealDate"] == "2024-01-15"
    assert len(payload["quickAddItems"]) == 1
    item = payload["quickAddItems"][0]
    assert item["name"] == "Chicken Breast"
    assert item["mealId"] == 20250  # LUNCH
    assert item["calories"] == "200"
    assert item["carbs"] == "3"
    assert item["protein"] == "40"
    assert item["fat"] == "4"
    assert item["logCategory"] == "QUICK_ADD"
    assert item["action"] == "ADD"
    assert item["logId"] is None


@pytest.mark.asyncio
async def test_log_food_falls_back_to_snacks(app_with_nutrition, mock_garmin_client):
    """Test log_food falls back to SNACKS when time doesn't match any window"""
    mock_garmin_client.connectapi.return_value = MOCK_MEALS
    mock_garmin_client.client.put.return_value = {}
    result = await app_with_nutrition.call_tool(
        "log_food",
        {
            "meal_date": "2024-01-15",
            "meal_time": "10:00:00",  # between BREAKFAST and LUNCH, no match → SNACKS
            "name": "Oats",
            "calories": 150,
            "carbs": 27,
            "protein": 5,
            "fat": 3,
        }
    )
    assert "Food logged successfully" in result[0][0].text
    payload = mock_garmin_client.client.put.call_args[1]["json"]
    assert payload["quickAddItems"][0]["mealId"] == 20252  # SNACKS
    assert payload["quickAddItems"][0]["mealTime"] == "10:00:00"


@pytest.mark.asyncio
async def test_log_food_error(app_with_nutrition, mock_garmin_client):
    """Test log_food handles API errors"""
    mock_garmin_client.connectapi.return_value = MOCK_MEALS
    mock_garmin_client.client.put.side_effect = Exception("API error")
    result = await app_with_nutrition.call_tool(
        "log_food",
        {
            "meal_date": "2024-01-15",
            "meal_time": "12:00:00",
            "name": "Test",
            "calories": 100,
            "carbs": 10,
            "protein": 5,
            "fat": 2,
        }
    )
    assert "Error logging food" in result[0][0].text


@pytest.mark.asyncio
async def test_log_food_forbidden_is_an_error(
    app_with_nutrition, mock_garmin_client
):
    """Test log_food preserves generic Garmin 403 failures"""
    mock_garmin_client.connectapi.return_value = MOCK_MEALS
    mock_garmin_client.client.put.side_effect = GarminConnectConnectionError(
        "API Error 403 - localized or non-English message"
    )
    result = await app_with_nutrition.call_tool(
        "log_food",
        {
            "meal_date": "2024-01-15",
            "meal_time": "12:00:00",
            "name": "Test",
            "calories": 100,
            "carbs": 10,
            "protein": 5,
            "fat": 2,
        },
    )
    data = json.loads(result[0][0].text)
    assert data["status"] == "error"
    assert data["upstream_status"] == 403
    assert data["upstream_body"] == "localized or non-English message"
    assert data["write_committed"] is False


@pytest.mark.asyncio
async def test_log_food_without_meal_windows_is_unavailable(
    app_with_nutrition, mock_garmin_client
):
    """Test log_food reports missing meal windows as unavailable data"""
    mock_garmin_client.connectapi.return_value = {"meals": []}

    result = await app_with_nutrition.call_tool(
        "log_food",
        {
            "meal_date": "2024-01-15",
            "meal_time": "12:00:00",
            "name": "Test",
            "calories": 100,
            "carbs": 10,
            "protein": 5,
            "fat": 2,
        },
    )

    assert result[0][0].text == (
        "No nutrition meal windows found for 2024-01-15; "
        "cannot log food at '12:00:00'."
    )


# log_custom_food tests

@pytest.mark.asyncio
async def test_log_custom_food(app_with_nutrition, mock_garmin_client):
    """Test log_custom_food auto-resolves meal_id and logs using food_id/serving_id"""
    mock_garmin_client.connectapi.return_value = MOCK_MEALS
    mock_garmin_client.client.put.return_value = {"status": "ok"}
    result = await app_with_nutrition.call_tool(
        "log_custom_food",
        {
            "meal_date": "2024-01-15",
            "meal_time": "12:30:00",  # within LUNCH window 11:00-14:00
            "food_id": "abc123",
            "serving_id": "srv456",
            "serving_qty": 3,
        }
    )
    assert result is not None
    mock_garmin_client.connectapi.assert_called_once_with(
        "/nutrition-service/meals/2024-01-15"
    )
    call_args = mock_garmin_client.client.put.call_args
    assert call_args[0][0] == "connectapi"
    assert call_args[0][1] == "/nutrition-service/food/logs"
    payload = call_args[1]["json"]
    assert payload["mealDate"] == "2024-01-15"
    assert len(payload["foodLogItems"]) == 1
    item = payload["foodLogItems"][0]
    assert item["mealId"] == 20250  # LUNCH
    assert item["foodId"] == "abc123"
    assert item["servingId"] == "srv456"
    assert item["servingQty"] == 3
    assert item["logCategory"] == "REGULAR_LOG"
    assert item["action"] == "ADD"
    assert item["mealTime"] == "12:30:00"


@pytest.mark.asyncio
async def test_log_custom_food_falls_back_to_snacks(app_with_nutrition, mock_garmin_client):
    """Test log_custom_food falls back to SNACKS when time doesn't match any window"""
    mock_garmin_client.connectapi.return_value = MOCK_MEALS
    mock_garmin_client.client.put.return_value = {}
    result = await app_with_nutrition.call_tool(
        "log_custom_food",
        {
            "meal_date": "2024-01-15",
            "meal_time": "10:00:00",  # between BREAKFAST and LUNCH, no match → SNACKS
            "food_id": "abc123",
            "serving_id": "srv456",
        }
    )
    assert "Food logged successfully" in result[0][0].text
    payload = mock_garmin_client.client.put.call_args[1]["json"]
    assert payload["foodLogItems"][0]["mealId"] == 20252  # SNACKS
    assert payload["foodLogItems"][0]["servingQty"] == 1


@pytest.mark.asyncio
async def test_log_custom_food_error(app_with_nutrition, mock_garmin_client):
    """Test log_custom_food handles API errors"""
    mock_garmin_client.connectapi.return_value = MOCK_MEALS
    mock_garmin_client.client.put.side_effect = Exception("API error")
    result = await app_with_nutrition.call_tool(
        "log_custom_food",
        {
            "meal_date": "2024-01-15",
            "meal_time": "12:00:00",
            "food_id": "abc123",
            "serving_id": "srv456",
        }
    )
    assert "Error logging food" in result[0][0].text


@pytest.mark.asyncio
async def test_log_custom_food_forbidden_is_an_error(
    app_with_nutrition, mock_garmin_client
):
    """Test log_custom_food preserves generic Garmin 403 failures"""
    mock_garmin_client.connectapi.return_value = MOCK_MEALS
    mock_garmin_client.client.put.side_effect = GarminConnectConnectionError(
        "API Error 403 - localized or non-English message"
    )
    result = await app_with_nutrition.call_tool(
        "log_custom_food",
        {
            "meal_date": "2024-01-15",
            "meal_time": "12:00:00",
            "food_id": "abc123",
            "serving_id": "srv456",
        },
    )
    data = json.loads(result[0][0].text)
    assert data["status"] == "error"
    assert data["upstream_status"] == 403
    assert data["upstream_body"] == "localized or non-English message"
    assert data["write_committed"] is False


# delete_custom_food tests

@pytest.mark.asyncio
async def test_delete_custom_food(app_with_nutrition, mock_garmin_client):
    """Test delete_custom_food calls DELETE /customFood/{foodId} and returns success"""
    mock_garmin_client.client.delete.return_value = {}
    food_id = "08b27145e29d41479e36d8d3788fcccf"
    result = await app_with_nutrition.call_tool(
        "delete_custom_food",
        {"confirm": True, "food_id": food_id},
    )
    assert "success" in result[0][0].text
    assert food_id in result[0][0].text
    mock_garmin_client.client.delete.assert_called_once_with(
        "connectapi", f"/nutrition-service/customFood/{food_id}", api=True
    )


@pytest.mark.asyncio
async def test_delete_custom_food_error(app_with_nutrition, mock_garmin_client):
    """Test delete_custom_food handles API errors"""
    mock_garmin_client.client.delete.side_effect = Exception("API error")
    result = await app_with_nutrition.call_tool(
        "delete_custom_food",
        {"confirm": True, "food_id": "08b27145e29d41479e36d8d3788fcccf"},
    )
    assert "Error deleting custom food" in result[0][0].text


# delete_food_log tests

@pytest.mark.asyncio
async def test_delete_food_log(app_with_nutrition, mock_garmin_client):
    """Test delete_food_log removes a food log entry using date + hex UUID"""
    mock_garmin_client.client.delete.return_value = {}
    result = await app_with_nutrition.call_tool(
        "delete_food_log",
        {"confirm": True, "log_id": "581f7dc8797f421f8d7eea83e5d2c939", "meal_date": "2024-01-15"}
    )
    assert "success" in result[0][0].text
    assert "581f7dc8797f421f8d7eea83e5d2c939" in result[0][0].text
    mock_garmin_client.client.delete.assert_called_once_with(
        "connectapi", "/nutrition-service/food/logs/2024-01-15",
        json={"logIds": ["581f7dc8797f421f8d7eea83e5d2c939"]}, api=True
    )


@pytest.mark.asyncio
async def test_delete_food_log_error(app_with_nutrition, mock_garmin_client):
    """Test delete_food_log handles API errors"""
    mock_garmin_client.client.delete.side_effect = Exception("API error")
    result = await app_with_nutrition.call_tool(
        "delete_food_log",
        {"confirm": True, "log_id": "99001", "meal_date": "2024-01-15"}
    )
    assert "Error deleting food log" in result[0][0].text


@pytest.mark.asyncio
async def test_delete_food_log_not_found(app_with_nutrition, mock_garmin_client):
    """Test delete_food_log uses structured HTTP status for not_found data"""
    mock_garmin_client.client.delete.side_effect = _connection_error_with_status(
        404, "localized or non-English message"
    )
    result = await app_with_nutrition.call_tool(
        "delete_food_log",
        {"confirm": True, "log_id": "-1", "meal_date": "2024-01-15"},
    )

    data = json.loads(result[0][0].text)
    assert data == {
        "status": "not_found",
        "log_id": "-1",
        "message": "Food log entry -1 was not found.",
    }


@pytest.mark.asyncio
async def test_delete_food_log_not_found_falls_back_to_status_in_message(
    app_with_nutrition, mock_garmin_client
):
    """Test delete_food_log falls back to the library's stable status text"""
    mock_garmin_client.client.delete.side_effect = GarminConnectConnectionError(
        "API Error 404 - recurso inexistente"
    )
    result = await app_with_nutrition.call_tool(
        "delete_food_log",
        {"confirm": True, "log_id": "-1", "meal_date": "2024-01-15"},
    )

    data = json.loads(result[0][0].text)
    assert data == {
        "status": "not_found",
        "log_id": "-1",
        "message": "Food log entry -1 was not found.",
    }


# upsert_and_log tests

MOCK_CUSTOM_FOODS = {
    "customFoods": [
        {
            "foodMetaData": {"foodId": "food001", "foodName": "Greek Yogurt"},
            "nutritionContents": [{"servingId": "srv001", "calories": "100"}],
        }
    ]
}


@pytest.mark.asyncio
async def test_upsert_and_log_existing_food(app_with_nutrition, mock_garmin_client):
    """Test upsert_and_log finds existing food and logs it without creating"""
    mock_garmin_client.connectapi.side_effect = [
        MOCK_CUSTOM_FOODS,  # search
        MOCK_MEALS,         # meal resolution
    ]
    mock_garmin_client.client.put.return_value = {}
    result = await app_with_nutrition.call_tool(
        "upsert_and_log",
        {
            "meal_date": "2024-01-15",
            "meal_time": "08:30:00",  # within BREAKFAST window
            "food_name": "Greek Yogurt",
            "calories": 100,
        }
    )
    assert "Food logged successfully" in result[0][0].text
    # create_custom_food should NOT have been called
    mock_garmin_client.client.put.assert_called_once()
    payload = mock_garmin_client.client.put.call_args[1]["json"]
    item = payload["foodLogItems"][0]
    assert item["foodId"] == "food001"
    assert item["servingId"] == "srv001"
    assert item["mealId"] == 20249  # BREAKFAST
    assert mock_garmin_client.connectapi.call_args_list[0] == call(
        "/nutrition-service/customFood",
        params={
            "searchExpression": "Greek Yogurt",
            "start": 0,
            "limit": 20,
            "includeContent": "true",
        },
    )


@pytest.mark.asyncio
async def test_upsert_and_log_creates_new_food(app_with_nutrition, mock_garmin_client):
    """Test upsert_and_log creates food when not found then logs it"""
    created_food = {
        "foodMetaData": {"foodId": "food999", "foodName": "New Food"},
        "nutritionContents": [{"servingId": "srv999"}],
    }
    mock_garmin_client.connectapi.side_effect = [
        {"customFoods": []},  # search returns empty
        MOCK_MEALS,           # meal resolution
    ]
    mock_garmin_client.client.put.side_effect = [created_food, {}]
    result = await app_with_nutrition.call_tool(
        "upsert_and_log",
        {
            "meal_date": "2024-01-15",
            "meal_time": "12:00:00",
            "food_name": "New Food",
            "calories": 200,
            "protein": 20,
        }
    )
    assert "Food logged successfully" in result[0][0].text
    assert mock_garmin_client.client.put.call_count == 2
    log_payload = mock_garmin_client.client.put.call_args_list[1][1]["json"]
    assert log_payload["foodLogItems"][0]["foodId"] == "food999"
    assert log_payload["foodLogItems"][0]["servingId"] == "srv999"
    assert mock_garmin_client.connectapi.call_args_list[0] == call(
        "/nutrition-service/customFood",
        params={
            "searchExpression": "New Food",
            "start": 0,
            "limit": 20,
            "includeContent": "true",
        },
    )


@pytest.mark.asyncio
async def test_upsert_and_log_recovers_ids_after_bodyless_create(app_with_nutrition, mock_garmin_client):
    """Test upsert_and_log looks up IDs after a bodyless create response."""
    mock_garmin_client.connectapi.side_effect = [
        {"customFoods": []},  # search returns empty
        MOCK_CUSTOM_FOODS,      # post-create lookup
        MOCK_MEALS,              # meal resolution
    ]
    mock_garmin_client.client.put.side_effect = [{}, {}]
    result = await app_with_nutrition.call_tool(
        "upsert_and_log",
        {
            "meal_date": "2024-01-15",
            "meal_time": "12:00:00",
            "food_name": "Greek Yogurt",
            "calories": 200,
        }
    )
    assert "Food logged successfully" in result[0][0].text
    log_payload = mock_garmin_client.client.put.call_args_list[1][1]["json"]
    assert log_payload["foodLogItems"][0]["foodId"] == "food001"
    assert log_payload["foodLogItems"][0]["servingId"] == "srv001"
    expected_lookup = call(
        "/nutrition-service/customFood",
        params={
            "searchExpression": "Greek Yogurt",
            "start": 0,
            "limit": 20,
            "includeContent": "true",
        },
    )
    assert mock_garmin_client.connectapi.call_args_list[:2] == [expected_lookup, expected_lookup]


@pytest.mark.asyncio
async def test_upsert_and_log_with_brand_name_on_create(app_with_nutrition, mock_garmin_client):
    """brand_name is passed through to the create payload when the food is new."""
    created_food = {
        "foodMetaData": {"foodId": "food999", "foodName": "Branded Beer"},
        "nutritionContents": [{"servingId": "srv999"}],
    }
    mock_garmin_client.connectapi.side_effect = [
        {"customFoods": []},  # search returns empty
        MOCK_MEALS,           # meal resolution
    ]
    mock_garmin_client.client.put.side_effect = [created_food, {}]
    result = await app_with_nutrition.call_tool(
        "upsert_and_log",
        {
            "meal_date": "2024-01-15",
            "meal_time": "12:00:00",
            "food_name": "Branded Beer",
            "calories": 150,
            "brand_name": "Athletic Brewing",
        },
    )
    assert "Food logged successfully" in result[0][0].text
    create_payload = mock_garmin_client.client.put.call_args_list[0][1]["json"]
    assert create_payload["foodMetaData"]["brandName"] == "Athletic Brewing"


@pytest.mark.asyncio
async def test_upsert_and_log_no_brand_name_omits_key_on_create(app_with_nutrition, mock_garmin_client):
    """Omitting brand_name is byte-identical to current behavior: no brandName key."""
    created_food = {
        "foodMetaData": {"foodId": "food999", "foodName": "Plain Food"},
        "nutritionContents": [{"servingId": "srv999"}],
    }
    mock_garmin_client.connectapi.side_effect = [
        {"customFoods": []},
        MOCK_MEALS,
    ]
    mock_garmin_client.client.put.side_effect = [created_food, {}]
    await app_with_nutrition.call_tool(
        "upsert_and_log",
        {
            "meal_date": "2024-01-15",
            "meal_time": "12:00:00",
            "food_name": "Plain Food",
            "calories": 150,
        },
    )
    create_payload = mock_garmin_client.client.put.call_args_list[0][1]["json"]
    assert "brandName" not in create_payload["foodMetaData"]


@pytest.mark.asyncio
async def test_upsert_and_log_brand_name_ignored_on_existing_match(app_with_nutrition, mock_garmin_client):
    """brand_name never affects find-or-create matching and is ignored (log-only) on a hit.

    food_name alone remains the match key: a supplied brand must not cause a
    miss against an existing unbranded record (which would create a duplicate).
    """
    mock_garmin_client.connectapi.side_effect = [
        MOCK_CUSTOM_FOODS,  # search finds existing unbranded "Greek Yogurt"
        MOCK_MEALS,         # meal resolution
    ]
    mock_garmin_client.client.put.return_value = {}
    result = await app_with_nutrition.call_tool(
        "upsert_and_log",
        {
            "meal_date": "2024-01-15",
            "meal_time": "08:30:00",
            "food_name": "Greek Yogurt",
            "calories": 100,
            "brand_name": "Chobani",
        },
    )
    assert "Food logged successfully" in result[0][0].text
    # Only the log call happens -- no create/update call was made for the brand.
    mock_garmin_client.client.put.assert_called_once()
    payload = mock_garmin_client.client.put.call_args[1]["json"]
    assert payload["foodLogItems"][0]["foodId"] == "food001"


@pytest.mark.asyncio
async def test_upsert_and_log_error(app_with_nutrition, mock_garmin_client):
    """Test upsert_and_log handles errors"""
    mock_garmin_client.connectapi.side_effect = Exception("API error")
    result = await app_with_nutrition.call_tool(
        "upsert_and_log",
        {
            "meal_date": "2024-01-15",
            "meal_time": "12:00:00",
            "food_name": "Test Food",
            "calories": 100,
        }
    )
    assert "Error in upsert_and_log" in result[0][0].text


# Regression tests for Bug 1 and Bug 2

@pytest.mark.asyncio
async def test_log_food_no_attribute_error_on_success(app_with_nutrition, mock_garmin_client):
    """Regression for Bug 1: client.put(api=True) returns a plain dict, not a Response.
    Must not raise AttributeError: 'dict' object has no attribute 'status_code'."""
    mock_garmin_client.connectapi.return_value = MOCK_MEALS
    # Exact shape the real garminconnect client returns for a 200 response with body
    mock_garmin_client.client.put.return_value = {"logId": "abc123", "status": "logged"}
    result = await app_with_nutrition.call_tool(
        "log_food",
        {
            "meal_date": "2024-01-15",
            "meal_time": "12:30:00",
            "name": "Test Food",
            "calories": 100,
            "carbs": 10,
            "protein": 5,
            "fat": 3,
        }
    )
    assert "Error" not in result[0][0].text
    assert "logId" in result[0][0].text


@pytest.mark.asyncio
async def test_delete_food_log_accepts_hex_uuid(app_with_nutrition, mock_garmin_client):
    """Regression for Bug 2: delete_food_log must accept 32-char hex UUID log IDs
    and require meal_date; uses DELETE /food/logs/{date} with body logIds."""
    hex_log_id = "581f7dc8797f421f8d7eea83e5d2c939"
    mock_garmin_client.client.delete.return_value = {}
    result = await app_with_nutrition.call_tool(
        "delete_food_log",
        {"confirm": True, "log_id": hex_log_id, "meal_date": "2024-01-15"}
    )
    assert "success" in result[0][0].text
    assert hex_log_id in result[0][0].text
    mock_garmin_client.client.delete.assert_called_once_with(
        "connectapi", "/nutrition-service/food/logs/2024-01-15",
        json={"logIds": [hex_log_id]}, api=True
    )

# set_nutrition_daily_settings tests

# Field names follow the reporter's settings payload in issue #294.
# Numeric values are synthetic and do not establish Garmin's macro units.
_CURRENT_SETTINGS = {
    "weightChangeType": "LOSS",
    "targetDate": "2024-06-01",
    "weightChangeRate": 0.25,
    "activeDailyCalories": 400,
    "userDefinedActiveCalories": False,
    "calorieGoal": 2000,
    "macroGoals": {"carbs": 250, "fat": 65, "protein": 120},
    "autoCalorieAdjustment": False,
    "startingWeight": 80,
    "targetWeightGoal": 75,
    "nutritionStatus": "ACTIVE",
}


@pytest.mark.asyncio
async def test_set_nutrition_daily_settings_updates_all_fields(app_with_nutrition, mock_garmin_client):
    original = deepcopy(_CURRENT_SETTINGS)
    mock_garmin_client.connectapi.return_value = original
    # Server values can differ from the requested values.
    mock_garmin_client.client.put.return_value = {
        "calorieGoal": 1850,
        "macroGoals": {"carbs": 205, "fat": 60, "protein": 140},
    }
    result = await app_with_nutrition.call_tool(
        "set_nutrition_daily_settings",
        {"date": "2024-01-15", "calorie_goal": 1800, "carbs_grams": 200, "fat_grams": 60, "protein_grams": 140},
    )
    assert json.loads(result[0][0].text) == {
        "status": "updated", "date": "2024-01-15", "calorie_goal": 1850,
        "carbs_grams": 205, "fat_grams": 60, "protein_grams": 140,
        "weight_goal_target_date": None,
    }
    expected = deepcopy(_CURRENT_SETTINGS)
    expected.update(calorieGoal=1800, macroGoals={"carbs": 200, "fat": 60, "protein": 140})
    mock_garmin_client.client.put.assert_called_once_with(
        "connectapi", "/nutrition-service/settings/2024-01-15", json=expected, api=True
    )
    assert original == _CURRENT_SETTINGS


@pytest.mark.asyncio
async def test_set_nutrition_daily_settings_reports_passed_weight_goal_target(
    app_with_nutrition, mock_garmin_client
):
    """A weight-goal targetDate that is not after the date blocks the whole PUT.

    Garmin answers "Goal targetDate must be after the asOfDate provided" and
    names neither the field nor the weight goal, so explain it instead of
    letting the request through.
    """
    mock_garmin_client.connectapi.return_value = deepcopy(_CURRENT_SETTINGS)
    result = await app_with_nutrition.call_tool(
        "set_nutrition_daily_settings",
        {"date": "2024-09-01", "calorie_goal": 1800},
    )
    text = result[0][0].text
    assert "targetDate" in text and "2024-06-01" in text and "2024-09-01" in text
    mock_garmin_client.client.put.assert_not_called()


@pytest.mark.asyncio
async def test_set_nutrition_daily_settings_accepts_explicit_target_date(
    app_with_nutrition, mock_garmin_client
):
    """An explicit target_date moves the goal deliberately and unblocks the update."""
    mock_garmin_client.connectapi.return_value = deepcopy(_CURRENT_SETTINGS)
    mock_garmin_client.client.put.return_value = {
        "calorieGoal": 1800,
        "macroGoals": _CURRENT_SETTINGS["macroGoals"],
        "targetDate": "2024-12-31",
    }
    result = await app_with_nutrition.call_tool(
        "set_nutrition_daily_settings",
        {"date": "2024-09-01", "calorie_goal": 1800, "target_date": "2024-12-31"},
    )
    payload = json.loads(result[0][0].text)
    assert payload["status"] == "updated"
    assert payload["weight_goal_target_date"] == "2024-12-31"
    expected = deepcopy(_CURRENT_SETTINGS)
    expected.update(targetDate="2024-12-31", calorieGoal=1800)
    mock_garmin_client.client.put.assert_called_once_with(
        "connectapi", "/nutrition-service/settings/2024-09-01", json=expected, api=True
    )


@pytest.mark.asyncio
async def test_set_nutrition_daily_settings_rejects_target_date_not_after_date(
    app_with_nutrition, mock_garmin_client
):
    mock_garmin_client.connectapi.return_value = deepcopy(_CURRENT_SETTINGS)
    result = await app_with_nutrition.call_tool(
        "set_nutrition_daily_settings",
        {"date": "2024-09-01", "calorie_goal": 1800, "target_date": "2024-09-01"},
    )
    assert "must be after" in result[0][0].text
    mock_garmin_client.client.put.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides, key, value", [
    ({"calorie_goal": 1900}, "calorieGoal", 1900),
    ({"carbs_grams": 0}, "carbs", 0),
    ({"fat_grams": 55}, "fat", 55),
    ({"protein_grams": 130}, "protein", 130),
])
async def test_set_nutrition_daily_settings_partial_update(
    app_with_nutrition, mock_garmin_client, overrides, key, value
):
    expected = deepcopy(_CURRENT_SETTINGS)
    if key == "calorieGoal":
        expected[key] = value
    else:
        expected["macroGoals"][key] = value
    stored = deepcopy(expected)
    stored["calorieGoal"] = 1950
    mock_garmin_client.connectapi.side_effect = [deepcopy(_CURRENT_SETTINGS), stored]
    mock_garmin_client.client.put.return_value = None
    result = await app_with_nutrition.call_tool(
        "set_nutrition_daily_settings", {"date": "2024-01-15", **overrides}
    )
    data = json.loads(result[0][0].text)
    assert data["calorie_goal"] == 1950
    assert data["carbs_grams"] == stored["macroGoals"]["carbs"]
    assert mock_garmin_client.client.put.call_args.kwargs["json"] == expected
    assert mock_garmin_client.connectapi.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("readback", [None, Exception("read-back unavailable")])
async def test_set_nutrition_daily_settings_unverified_write(app_with_nutrition, mock_garmin_client, readback):
    mock_garmin_client.connectapi.side_effect = [deepcopy(_CURRENT_SETTINGS), readback]
    mock_garmin_client.client.put.return_value = None
    result = await app_with_nutrition.call_tool(
        "set_nutrition_daily_settings", {"date": "2024-01-15", "calorie_goal": 1900}
    )
    assert "could not verify" in result[0][0].text
    assert '"status": "updated"' not in result[0][0].text


@pytest.mark.asyncio
async def test_set_nutrition_daily_settings_no_fields_provided(app_with_nutrition, mock_garmin_client):
    """Returns a clear message when no fields are supplied."""
    result = await app_with_nutrition.call_tool(
        "set_nutrition_daily_settings",
        {"date": "2024-01-15"},
    )
    assert "No fields to update" in result[0][0].text
    mock_garmin_client.connectapi.assert_not_called()


@pytest.mark.asyncio
async def test_set_nutrition_daily_settings_no_current_data(app_with_nutrition, mock_garmin_client):
    """Returns a clear message when GET returns nothing (no baseline to merge into)."""
    mock_garmin_client.connectapi.return_value = None

    result = await app_with_nutrition.call_tool(
        "set_nutrition_daily_settings",
        {"date": "2024-01-15", "calorie_goal": 1800},
    )
    assert "Could not read current" in result[0][0].text
    mock_garmin_client.client.put.assert_not_called()


@pytest.mark.asyncio
async def test_set_nutrition_daily_settings_api_error(app_with_nutrition, mock_garmin_client):
    """API errors are surfaced as a clean message."""
    mock_garmin_client.connectapi.return_value = dict(_CURRENT_SETTINGS)
    mock_garmin_client.client.put.side_effect = Exception("403 Forbidden")

    result = await app_with_nutrition.call_tool(
        "set_nutrition_daily_settings",
        {"date": "2024-01-15", "calorie_goal": 1800},
    )
    assert "Error updating nutrition settings" in result[0][0].text
    assert "403" in result[0][0].text


# search_foods tests

_SEARCH_RESPONSE = {
    "results": [
        {
            "foodMetaData": {
                "foodId": "4132350",
                "foodName": "Cheerios",
                "foodType": "BRANDED",
                "source": "FATSECRET",
                "brandName": "General Mills",
                "regionCode": "US",
                "languageCode": "en",
            },
            "nutritionContents": [
                {
                    "servingId": "srv001",
                    "servingUnit": "cup",
                    "numberOfUnits": 1.0,
                    "calories": 100.0,
                    "carbs": 20.0,
                    "protein": 3.0,
                    "fat": 2.0,
                }
            ],
        }
    ],
    "moreDataAvailable": False,
}


@pytest.mark.asyncio
async def test_search_foods_returns_results(app_with_nutrition, mock_garmin_client):
    """search_foods returns formatted catalog results."""
    mock_garmin_client.connectapi.return_value = _SEARCH_RESPONSE

    result = await app_with_nutrition.call_tool(
        "search_foods",
        {"query": "Cheerios & Oats/é"},
    )
    data = json.loads(result[0][0].text)
    assert data["count"] == 1
    assert data["results"][0]["name"] == "Cheerios"
    assert data["results"][0]["source"] == "FATSECRET"
    assert data["results"][0]["brand"] == "General Mills"
    assert data["results"][0]["servings"][0]["calories"] == 100.0
    assert not data["has_more"]
    mock_garmin_client.connectapi.assert_called_once_with(
        "/nutrition-service/food/search",
        params={
            "searchExpression": "Cheerios & Oats/é",
            "start": 0,
            "limit": 20,
        },
    )


@pytest.mark.asyncio
async def test_search_foods_empty_response(app_with_nutrition, mock_garmin_client):
    """search_foods returns a clear message when no results are found."""
    mock_garmin_client.connectapi.return_value = None

    result = await app_with_nutrition.call_tool(
        "search_foods",
        {"query": "xyzunknownfood"},
    )
    assert "No foods found" in result[0][0].text


@pytest.mark.asyncio
async def test_search_foods_error(app_with_nutrition, mock_garmin_client):
    """search_foods surfaces API errors cleanly."""
    mock_garmin_client.connectapi.side_effect = Exception("503 Service Unavailable")

    result = await app_with_nutrition.call_tool(
        "search_foods",
        {"query": "Cheerios"},
    )
    assert "Error searching foods" in result[0][0].text


@pytest.mark.asyncio
async def test_log_custom_food_uses_fatsecret_source(app_with_nutrition, mock_garmin_client):
    """log_custom_food passes through the source parameter for FATSECRET foods."""
    mock_garmin_client.connectapi.return_value = MOCK_MEALS
    mock_garmin_client.client.put.return_value = {"success": True}

    await app_with_nutrition.call_tool(
        "log_custom_food",
        {
            "meal_date": "2024-01-15",
            "meal_time": "08:00:00",
            "food_id": "4132350",
            "serving_id": "srv001",
            "source": "FATSECRET",
        },
    )

    payload = mock_garmin_client.client.put.call_args[1]["json"]
    item = payload["foodLogItems"][0]
    assert item["source"] == "FATSECRET"


@pytest.mark.asyncio
async def test_log_custom_food_defaults_to_garmin_source(app_with_nutrition, mock_garmin_client):
    """log_custom_food uses GARMIN source by default (backwards compatible)."""
    mock_garmin_client.connectapi.return_value = MOCK_MEALS
    mock_garmin_client.client.put.return_value = {"success": True}

    await app_with_nutrition.call_tool(
        "log_custom_food",
        {
            "meal_date": "2024-01-15",
            "meal_time": "08:00:00",
            "food_id": "abc-uuid-123",
            "serving_id": "srv002",
        },
    )

    payload = mock_garmin_client.client.put.call_args[1]["json"]
    item = payload["foodLogItems"][0]
    assert item["source"] == "GARMIN"


@pytest.mark.asyncio
@pytest.mark.parametrize("macro_goals", [None, {}, {"fiber": 30}])
async def test_set_nutrition_settings_handles_sparse_macros(
    app_with_nutrition, mock_garmin_client, macro_goals
):
    current = deepcopy(_CURRENT_SETTINGS)
    current["macroGoals"] = macro_goals
    mock_garmin_client.connectapi.return_value = current
    mock_garmin_client.client.put.side_effect = lambda *args, **kwargs: kwargs["json"]
    result = await app_with_nutrition.call_tool(
        "set_nutrition_daily_settings", {"date": "2024-01-15", "protein_grams": 130}
    )
    assert json.loads(result[0][0].text)["protein_grams"] == 130
    expected = deepcopy(current)
    expected["macroGoals"] = {**(macro_goals or {}), "protein": 130}
    assert mock_garmin_client.client.put.call_args.kwargs["json"] == expected


@pytest.mark.asyncio
async def test_set_nutrition_settings_rejects_invalid_macros(app_with_nutrition, mock_garmin_client):
    current = deepcopy(_CURRENT_SETTINGS)
    current["macroGoals"] = [250, 65, 120]
    mock_garmin_client.connectapi.return_value = current
    result = await app_with_nutrition.call_tool(
        "set_nutrition_daily_settings", {"date": "2024-01-15", "protein_grams": 130}
    )
    assert "cannot apply update" in result[0][0].text
    mock_garmin_client.client.put.assert_not_called()


# structured write errors

_LOG_FOOD_ARGS = {
    "meal_date": "2024-01-15",
    "meal_time": "12:00:00",
    "name": "Test",
    "calories": 100,
    "carbs": 10,
    "protein": 5,
    "fat": 2,
}


@pytest.mark.asyncio
async def test_log_food_garmin_rejection_is_structured(app_with_nutrition, mock_garmin_client):
    """A Garmin 4xx on the write returns a structured, not-committed error"""
    from garminconnect import GarminConnectConnectionError

    mock_garmin_client.connectapi.return_value = MOCK_MEALS
    mock_garmin_client.client.put.side_effect = GarminConnectConnectionError(
        "API Error 401 - "
    )
    result = await app_with_nutrition.call_tool("log_food", _LOG_FOOD_ARGS)
    data = json.loads(result[0][0].text)
    assert data["status"] == "error"
    assert data["tool"] == "log_food"
    assert data["stage"] == "garmin_request"
    assert data["upstream_status"] == 401
    assert data["write_committed"] is False
    assert data["correlation_id"]


@pytest.mark.asyncio
async def test_log_food_5xx_after_send_is_unknown(app_with_nutrition, mock_garmin_client):
    """A gateway 5xx on the write may come after Garmin already logged the food"""
    from garminconnect import GarminConnectConnectionError

    mock_garmin_client.connectapi.return_value = MOCK_MEALS
    mock_garmin_client.client.put.side_effect = GarminConnectConnectionError(
        "API Error 504 - <html>Gateway Time-out</html>"
    )
    result = await app_with_nutrition.call_tool("log_food", _LOG_FOOD_ARGS)
    data = json.loads(result[0][0].text)
    assert data["stage"] == "garmin_request"
    assert data["upstream_status"] == 504
    assert data["write_committed"] == "unknown"
    mock_garmin_client.client.put.assert_called_once()  # never auto-retried


@pytest.mark.asyncio
async def test_log_food_timeout_after_send_is_unknown(app_with_nutrition, mock_garmin_client):
    """A read timeout on the write may have committed; say so rather than guess"""
    import requests

    mock_garmin_client.connectapi.return_value = MOCK_MEALS
    mock_garmin_client.client.put.side_effect = requests.exceptions.ReadTimeout("timed out")
    result = await app_with_nutrition.call_tool("log_food", _LOG_FOOD_ARGS)
    data = json.loads(result[0][0].text)
    assert data["write_committed"] == "unknown"
    mock_garmin_client.client.put.assert_called_once()  # never auto-retried


@pytest.mark.asyncio
async def test_log_food_meal_lookup_failure_is_not_committed(app_with_nutrition, mock_garmin_client):
    """A failure in the pre-write meal lookup never reaches the write"""
    import requests

    mock_garmin_client.connectapi.side_effect = requests.exceptions.ReadTimeout("timed out")
    result = await app_with_nutrition.call_tool("log_food", _LOG_FOOD_ARGS)
    data = json.loads(result[0][0].text)
    assert data["write_committed"] is False
    mock_garmin_client.client.put.assert_not_called()


@pytest.mark.asyncio
async def test_log_custom_food_error_is_structured(app_with_nutrition, mock_garmin_client):
    """log_custom_food returns the same structured error shape"""
    from garminconnect import GarminConnectConnectionError

    mock_garmin_client.connectapi.return_value = MOCK_MEALS
    mock_garmin_client.client.put.side_effect = GarminConnectConnectionError(
        "API Error 400 - Required Parameter meal id is null"
    )
    result = await app_with_nutrition.call_tool(
        "log_custom_food",
        {"meal_date": "2024-01-15", "meal_time": "12:00:00", "food_id": "abc", "serving_id": "s"},
    )
    data = json.loads(result[0][0].text)
    assert data["tool"] == "log_custom_food"
    assert data["upstream_status"] == 400
    assert data["upstream_body"] == "Required Parameter meal id is null"


@pytest.mark.asyncio
async def test_delete_food_log_error_is_structured(app_with_nutrition, mock_garmin_client):
    """delete_food_log returns the same structured error shape"""
    import requests

    mock_garmin_client.client.delete.side_effect = requests.exceptions.ConnectTimeout("no route")
    result = await app_with_nutrition.call_tool(
        "delete_food_log", {"confirm": True, "log_id": "581f7dc8797f421f8d7eea83e5d2c939", "meal_date": "2024-01-15"}
    )
    data = json.loads(result[0][0].text)
    assert data["tool"] == "delete_food_log"
    assert data["message"].startswith("Error deleting food log")
    assert data["write_committed"] is False


@pytest.mark.asyncio
async def test_log_food_success_shape_unchanged(app_with_nutrition, mock_garmin_client):
    """The success path still returns the plain message"""
    mock_garmin_client.connectapi.return_value = MOCK_MEALS
    mock_garmin_client.client.put.return_value = {}
    result = await app_with_nutrition.call_tool("log_food", _LOG_FOOD_ARGS)
    assert result[0][0].text == "Food logged successfully."


@pytest.mark.asyncio
async def test_upsert_and_log_forbidden_is_an_error(
    app_with_nutrition, mock_garmin_client
):
    """Test upsert_and_log preserves generic Garmin 403 failures"""
    mock_garmin_client.connectapi.side_effect = GarminConnectConnectionError(
        "API Error 403 - HTTP 403 Forbidden"
    )
    result = await app_with_nutrition.call_tool(
        "upsert_and_log",
        {
            "meal_date": "2024-01-15",
            "meal_time": "12:00:00",
            "food_name": "Test Food",
            "calories": 100,
        },
    )
    data = json.loads(result[0][0].text)
    assert data["message"] == "Error in upsert_and_log: API Error 403 - HTTP 403 Forbidden"
    assert data["upstream_status"] == 403
    assert data["write_committed"] is False
    mock_garmin_client.client.put.assert_not_called()


_UPSERT_ARGS = {
    "meal_date": "2024-01-15",
    "meal_time": "12:00:00",
    "food_name": "Greek Yogurt",
    "calories": 100,
}


@pytest.mark.asyncio
async def test_upsert_and_log_log_timeout_is_unknown(app_with_nutrition, mock_garmin_client):
    """A read timeout on the log PUT may have committed; a blind retry double-logs"""
    import requests

    mock_garmin_client.connectapi.side_effect = [MOCK_CUSTOM_FOODS, MOCK_MEALS]
    mock_garmin_client.client.put.side_effect = requests.exceptions.ReadTimeout("timed out")
    result = await app_with_nutrition.call_tool("upsert_and_log", _UPSERT_ARGS)
    data = json.loads(result[0][0].text)
    assert data["tool"] == "upsert_and_log"
    assert data["stage"] == "garmin_request"
    assert data["write_committed"] == "unknown"
    assert "hint" in data
    mock_garmin_client.client.put.assert_called_once()  # never auto-retried


@pytest.mark.asyncio
async def test_upsert_and_log_create_timeout_is_unknown(app_with_nutrition, mock_garmin_client):
    """A read timeout on the create PUT may have created the food"""
    import requests

    mock_garmin_client.connectapi.side_effect = [{"customFoods": []}]
    mock_garmin_client.client.put.side_effect = requests.exceptions.ReadTimeout("timed out")
    result = await app_with_nutrition.call_tool("upsert_and_log", _UPSERT_ARGS)
    data = json.loads(result[0][0].text)
    assert data["write_committed"] == "unknown"
    mock_garmin_client.client.put.assert_called_once()


@pytest.mark.asyncio
async def test_upsert_and_log_log_rejected_after_create(app_with_nutrition, mock_garmin_client):
    """A rejected log after a successful create says the food now exists"""
    created_food = {
        "foodMetaData": {"foodId": "food999", "foodName": "Greek Yogurt"},
        "nutritionContents": [{"servingId": "srv999"}],
    }
    mock_garmin_client.connectapi.side_effect = [{"customFoods": []}, MOCK_MEALS]
    mock_garmin_client.client.put.side_effect = [
        created_food,
        GarminConnectConnectionError("API Error 400 - bad meal"),
    ]
    result = await app_with_nutrition.call_tool("upsert_and_log", _UPSERT_ARGS)
    data = json.loads(result[0][0].text)
    assert data["write_committed"] is False
    assert data["upstream_status"] == 400
    assert "custom food food999 was created" in data["message"]
    assert data["custom_food_created"] is True
    assert "not logged" in data["hint"]
    assert "Nothing was written" not in data["hint"]


@pytest.mark.asyncio
async def test_upsert_and_log_skips_null_search_metadata(app_with_nutrition, mock_garmin_client):
    """A search hit with foodMetaData/foodName null must not abort the tool"""
    search = {
        "customFoods": [
            {"foodMetaData": None, "nutritionContents": None},
            {"foodMetaData": {"foodId": "x", "foodName": None}, "nutritionContents": []},
            *MOCK_CUSTOM_FOODS["customFoods"],
        ]
    }
    mock_garmin_client.connectapi.side_effect = [search, MOCK_MEALS]
    mock_garmin_client.client.put.return_value = {}
    result = await app_with_nutrition.call_tool("upsert_and_log", _UPSERT_ARGS)
    assert result[0][0].text == "Food logged successfully."
    mock_garmin_client.client.put.assert_called_once()
    item = mock_garmin_client.client.put.call_args[1]["json"]["foodLogItems"][0]
    assert item["foodId"] == "food001"
    assert item["servingId"] == "srv001"


@pytest.mark.asyncio
async def test_delete_custom_food_previews_without_deleting(app_with_nutrition, mock_garmin_client):
    mock_garmin_client.connectapi.return_value = MOCK_CUSTOM_FOODS
    result = await app_with_nutrition.call_tool("delete_custom_food", {"food_id": "food001"})
    data = json.loads(result[0][0].text)
    assert data["status"] == "needs_confirmation"
    assert data["food"]["foodMetaData"]["foodName"] == "Greek Yogurt"
    mock_garmin_client.client.delete.assert_not_called()


@pytest.mark.asyncio
async def test_delete_food_log_previews_matching_nested_entry(app_with_nutrition, mock_garmin_client):
    item = {"logId": "target", "foodName": "Oats", "calories": 100}
    mock_garmin_client.connectapi.return_value = {
        "mealDetails": [{"loggedFoods": [{"logId": "other", "foodName": "Rice"}, item]}],
    }
    result = await app_with_nutrition.call_tool("delete_food_log", {"log_id": "target", "meal_date": "2024-01-15"})
    data = json.loads(result[0][0].text)
    assert data["status"] == "needs_confirmation"
    assert data["entry"] == item
    mock_garmin_client.client.delete.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, args", [
    ("delete_custom_food", {"food_id": "missing"}),
    ("delete_food_log", {"log_id": "missing", "meal_date": "2024-01-15"}),
])
async def test_food_delete_preview_not_found_never_deletes(app_with_nutrition, mock_garmin_client, tool, args):
    mock_garmin_client.connectapi.return_value = {}
    result = await app_with_nutrition.call_tool(tool, args)
    assert json.loads(result[0][0].text)["status"] == "not_found"
    mock_garmin_client.client.delete.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, args", [
    ("delete_custom_food", {"food_id": "food001"}),
    ("delete_food_log", {"log_id": "target", "meal_date": "2024-01-15"}),
])
async def test_food_delete_preview_error_never_deletes(app_with_nutrition, mock_garmin_client, tool, args):
    mock_garmin_client.connectapi.side_effect = GarminConnectConnectionError("API Error 401 - expired")
    result = await app_with_nutrition.call_tool(tool, args)
    assert "Error deleting" in result[0][0].text
    mock_garmin_client.client.delete.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, arguments", [
    ("set_nutrition_daily_settings", {"date": "2024-01-15", "calorie_goal": 1800}),
    ("create_custom_food", {"food_name": "Oats", "calories": 100}),
    ("update_custom_food", {"food_id": "food001", "serving_id": "srv001", "food_name": "Greek Yogurt", "calories": 100}),
    ("delete_custom_food", {"food_id": "food001", "confirm": True}),
])
@pytest.mark.parametrize("error_kind", ["gateway", "read_timeout", "rejection", "parse"])
async def test_nutrition_library_writes_report_commit_status(
    app_with_nutrition, mock_garmin_client, tool, arguments, error_kind
):
    import requests

    errors = {
        "gateway": GarminConnectConnectionError("API Error 504 - Gateway Time-out"),
        "read_timeout": requests.exceptions.ReadTimeout("read timed out"),
        "rejection": GarminConnectConnectionError("API Error 400 - rejected"),
        "parse": requests.exceptions.JSONDecodeError("invalid JSON", "bad", 0),
    }
    mock_garmin_client.connectapi.return_value = (
        deepcopy(_CURRENT_SETTINGS) if tool == "set_nutrition_daily_settings" else MOCK_CUSTOM_FOODS
    )
    write = mock_garmin_client.client.delete if tool == "delete_custom_food" else mock_garmin_client.client.put
    write.side_effect = errors[error_kind]
    result = await app_with_nutrition.call_tool(tool, arguments)
    data = json.loads(result[0][0].text)
    assert data["tool"] == tool
    expected = True if error_kind == "parse" else False if error_kind == "rejection" else "unknown"
    assert data["write_committed"] == expected
    assert data["stage"] == ("response_parse" if error_kind == "parse" else "garmin_request")
    write.assert_called_once()
    if expected == "unknown":
        assert "before retrying" in data["hint"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_row", [None, {"foodMetaData": None}, {"foodMetaData": {"foodId": "incomplete"}, "nutritionContents": None}])
async def test_search_foods_retains_good_rows_with_null_optional_data(
    app_with_nutrition, mock_garmin_client, bad_row
):
    mock_garmin_client.connectapi.return_value = {
        "results": [bad_row, *_SEARCH_RESPONSE["results"]], "moreDataAvailable": False,
    }
    result = await app_with_nutrition.call_tool("search_foods", {"query": "Cheerios"})
    data = json.loads(result[0][0].text)
    good = next(row for row in data["results"] if row.get("name") == "Cheerios")
    assert good["servings"][0]["calories"] == 100


@pytest.mark.asyncio
async def test_search_foods_handles_null_results(app_with_nutrition, mock_garmin_client):
    mock_garmin_client.connectapi.return_value = {"results": None}
    result = await app_with_nutrition.call_tool("search_foods", {"query": "Cheerios"})
    assert json.loads(result[0][0].text)["results"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, extra", [
    ("log_food", {"name": "Oats", "calories": 100, "carbs": 10, "protein": 5, "fat": 2}),
    ("log_custom_food", {"food_id": "food001", "serving_id": "srv001"}),
    ("upsert_and_log", {"food_name": "Oats", "calories": 100}),
])
@pytest.mark.parametrize("meal_date, meal_time", [
    ("2024-01-15", "8:30:00"), ("2024-01-15", "noon"),
    ("2024-01-15", "25:00:00"), ("2024-01-15", "12:30 PM"),
    ("2024-01-15", "2024-01-15T08:30:00"), ("2024-02-30", "08:30:00"),
])
async def test_food_logging_rejects_invalid_meal_datetime_before_requests(
    app_with_nutrition, mock_garmin_client, tool, extra, meal_date, meal_time
):
    mock_garmin_client.connectapi.return_value = MOCK_MEALS
    mock_garmin_client.client.put.return_value = {}
    result = await app_with_nutrition.call_tool(tool, {"meal_date": meal_date, "meal_time": meal_time, **extra})
    data = json.loads(result[0][0].text)
    assert data["write_committed"] is False
    assert "meal_date" in data["message"] or "meal_time" in data["message"]
    mock_garmin_client.connectapi.assert_not_called()
    mock_garmin_client.client.put.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    GarminConnectConnectionError("HTTP error: API Error 500 - lookup failed"),
    GarminConnectConnectionError("HTTP error: API Error 401 - expired"),
    TimeoutError("lookup timed out"),
])
async def test_upsert_lookup_failure_after_create_does_not_commit_log(
    app_with_nutrition, mock_garmin_client, failure
):
    mock_garmin_client.connectapi.side_effect = [{"customFoods": []}, failure]
    mock_garmin_client.client.put.return_value = {}
    result = await app_with_nutrition.call_tool("upsert_and_log", _UPSERT_ARGS)
    data = json.loads(result[0][0].text)
    assert data["write_committed"] is False
    assert data["custom_food_created"] is True
    assert "not logged" in data["hint"]
    assert "Nothing was written" not in data["hint"]
    mock_garmin_client.client.put.assert_called_once()
    assert mock_garmin_client.client.put.call_args.args[1] == "/nutrition-service/customFood"


@pytest.mark.asyncio
async def test_upsert_missing_ids_after_create_is_structured(
    app_with_nutrition, mock_garmin_client
):
    mock_garmin_client.connectapi.return_value = {"customFoods": []}
    mock_garmin_client.client.put.return_value = {}
    result = await app_with_nutrition.call_tool("upsert_and_log", _UPSERT_ARGS)
    data = json.loads(result[0][0].text)
    assert data["status"] == "error"
    assert data["write_committed"] is False
    assert data["custom_food_created"] is True
    assert "not logged" in data["hint"]
    mock_garmin_client.client.put.assert_called_once()


@pytest.mark.asyncio
async def test_upsert_missing_meals_after_create_reports_partial_write(
    app_with_nutrition, mock_garmin_client
):
    mock_garmin_client.connectapi.side_effect = [{"customFoods": []}, {"meals": []}]
    mock_garmin_client.client.put.return_value = MOCK_CUSTOM_FOODS["customFoods"][0]
    result = await app_with_nutrition.call_tool("upsert_and_log", _UPSERT_ARGS)
    data = json.loads(result[0][0].text)
    assert data["write_committed"] is False
    assert data["custom_food_created"] is True
    assert data["food_id"] == "food001"
    assert "No nutrition meal windows" in data["message"]
    mock_garmin_client.client.put.assert_called_once()


@pytest.mark.asyncio
async def test_upsert_pages_exact_name_match_before_creating(
    app_with_nutrition, mock_garmin_client
):
    variants = [
        {"foodMetaData": {"foodId": f"variant{i}", "foodName": f"Greek Yogurt {i}"},
         "nutritionContents": [{"servingId": f"s{i}"}]}
        for i in range(20)
    ]
    catalog = variants + MOCK_CUSTOM_FOODS["customFoods"]

    def get_data(url, **kwargs):
        if url.endswith("customFood"):
            params = kwargs["params"]
            return {"customFoods": catalog[params["start"]:params["start"] + params["limit"]]}
        return MOCK_MEALS

    mock_garmin_client.connectapi.side_effect = get_data
    mock_garmin_client.client.put.return_value = {}
    result = await app_with_nutrition.call_tool("upsert_and_log", _UPSERT_ARGS)
    assert result[0][0].text == "Food logged successfully."
    mock_garmin_client.client.put.assert_called_once()
    assert mock_garmin_client.client.put.call_args.kwargs["json"]["foodLogItems"][0]["foodId"] == "food001"
    searches = [c.kwargs["params"]["start"] for c in mock_garmin_client.connectapi.call_args_list if "params" in c.kwargs]
    assert searches == [0, 20]


@pytest.mark.asyncio
async def test_upsert_refuses_creation_when_search_limit_is_exhausted(
    app_with_nutrition, mock_garmin_client
):
    mock_garmin_client.connectapi.return_value = {"customFoods": [
        {"foodMetaData": {"foodName": f"Greek Yogurt {i}"}} for i in range(20)
    ]}
    result = await app_with_nutrition.call_tool("upsert_and_log", _UPSERT_ARGS)
    data = json.loads(result[0][0].text)
    assert data["write_committed"] is False
    assert "search limit" in data["message"]
    mock_garmin_client.client.put.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_existing_food_without_serving_does_not_create_duplicate(
    app_with_nutrition, mock_garmin_client
):
    mock_garmin_client.connectapi.return_value = {"customFoods": [
        {"foodMetaData": {"foodId": "food001", "foodName": "Greek Yogurt"},
         "nutritionContents": None}
    ]}
    result = await app_with_nutrition.call_tool("upsert_and_log", _UPSERT_ARGS)
    data = json.loads(result[0][0].text)
    assert data["write_committed"] is False
    assert "serving" in data["message"]
    mock_garmin_client.client.put.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["create", "log"])
async def test_upsert_response_decode_commit_status_refers_to_food_log(
    app_with_nutrition, mock_garmin_client, phase
):
    import requests

    mock_garmin_client.connectapi.side_effect = (
        [{"customFoods": []}] if phase == "create" else [MOCK_CUSTOM_FOODS, MOCK_MEALS]
    )
    mock_garmin_client.client.put.side_effect = requests.exceptions.JSONDecodeError(
        "invalid JSON", "bad", 0
    )
    result = await app_with_nutrition.call_tool("upsert_and_log", _UPSERT_ARGS)
    data = json.loads(result[0][0].text)
    assert data["write_committed"] is (phase == "log")
    if phase == "create":
        assert data["custom_food_created"] is True
        assert "not logged" in data["hint"]
    mock_garmin_client.client.put.assert_called_once()
