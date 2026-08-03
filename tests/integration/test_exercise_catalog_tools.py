"""FastMCP integration tests for the public exercise catalog tools."""

import json
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import _ToolFilter, exercise_catalog


FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture
def sample_catalog():
    exercises = json.loads((FIXTURES / "exercises_catalog_sample.json").read_text())
    labels = exercise_catalog.parse_properties(
        (FIXTURES / "exercise_types_sample.properties").read_text()
    )
    return exercise_catalog.ExerciseCatalog(
        exercise_catalog.parse_exercises(exercises, labels)
    )


@pytest.fixture
def catalog_app(monkeypatch, sample_catalog):
    monkeypatch.setattr(exercise_catalog, "load_catalog", lambda: sample_catalog)
    app = FastMCP("Test Exercise Catalog")
    return exercise_catalog.register_tools(app)


@pytest.mark.asyncio
async def test_tools_registered_with_expected_schema(catalog_app):
    tools = await catalog_app.list_tools()
    by_name = {tool.name: tool for tool in tools}
    assert {"list_strength_exercises", "match_strength_exercise", "resolve_strength_exercises"} <= set(by_name)
    list_properties = by_name["list_strength_exercises"].inputSchema["properties"]
    match_properties = by_name["match_strength_exercise"].inputSchema["properties"]
    assert {"category", "search", "limit", "offset", "include_muscles"} <= set(list_properties)
    assert {"query", "category", "limit"} <= set(match_properties)
    assert "query" in by_name["match_strength_exercise"].inputSchema["required"]
    resolve_properties = by_name["resolve_strength_exercises"].inputSchema["properties"]
    assert {"exercises", "limit"} <= set(resolve_properties)


@pytest.mark.asyncio
async def test_list_tool_returns_identifiers_and_valid_json(catalog_app):
    result = await catalog_app.call_tool(
        "list_strength_exercises", {"search": "reverse crunch"}
    )
    payload = json.loads(result[0][0].text)
    assert payload["status"] == "success"
    assert payload["exercises"][0]["category"] == "CRUNCH"
    assert payload["exercises"][0]["exercise_name"] == "REVERSE_CRUNCH"


@pytest.mark.asyncio
async def test_match_tool_returns_reverse_crunch(catalog_app):
    result = await catalog_app.call_tool(
        "match_strength_exercise", {"query": "Reverse Crunch"}
    )
    payload = json.loads(result[0][0].text)
    assert payload["status"] == "exact"
    assert payload["match"]["category"] == "CRUNCH"
    assert payload["match"]["exercise_name"] == "REVERSE_CRUNCH"


@pytest.mark.asyncio
async def test_validation_responses_are_json(catalog_app):
    result = await catalog_app.call_tool("list_strength_exercises", {"limit": 201})
    assert json.loads(result[0][0].text)["status"] == "error"


@pytest.mark.asyncio
async def test_list_pagination_and_maximum_limit(catalog_app):
    first = await catalog_app.call_tool(
        "list_strength_exercises", {"limit": 1, "offset": 0}
    )
    page = json.loads(first[0][0].text)
    assert len(page["exercises"]) == 1
    assert page["has_more"] is True
    assert page["next_offset"] == 1

    maximum = await catalog_app.call_tool(
        "list_strength_exercises", {"limit": 200}
    )
    assert json.loads(maximum[0][0].text)["status"] == "success"


@pytest.mark.asyncio
async def test_tool_name_filtering(monkeypatch, sample_catalog):
    monkeypatch.setattr(exercise_catalog, "load_catalog", lambda: sample_catalog)
    inner = FastMCP("Filtered Exercise Catalog")
    filtered = _ToolFilter(inner, {"match_strength_exercise"}, set())
    exercise_catalog.register_tools(filtered)
    names = {tool.name for tool in await inner.list_tools()}
    assert "match_strength_exercise" in names
    assert "list_strength_exercises" not in names


@pytest.mark.asyncio
async def test_tools_do_not_use_authenticated_client(catalog_app, mock_garmin_client):
    await catalog_app.call_tool("list_strength_exercises", {"limit": 1})
    await catalog_app.call_tool("match_strength_exercise", {"query": "Reverse Crunch"})
    await catalog_app.call_tool("resolve_strength_exercises", {"exercises": [{"name": "Reverse Crunch"}]})
    assert mock_garmin_client.mock_calls == []
