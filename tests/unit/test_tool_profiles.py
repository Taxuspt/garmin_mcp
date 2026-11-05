"""GARMIN_TOOL_PROFILE selects a built-in subset of tools (#336)."""

import asyncio

import pytest
from mcp.server.fastmcp import FastMCP

import garmin_mcp
from garmin_mcp import tool_profiles
from garmin_mcp.tool_profiles import TOOL_PROFILES, resolve_tool_profile
from scripts.generate_dxt_tools import MODULES


def _registered_tool_names():
    app = FastMCP("profile coverage")
    proxy = garmin_mcp._ToolFilter(app, set(), set())
    for module in MODULES:
        module.configure(object())
        module.register_tools(proxy)
    return {tool.name for tool in asyncio.run(app.list_tools())}


def test_every_profile_name_is_a_registered_tool():
    registered = _registered_tool_names()
    for name, tools in TOOL_PROFILES.items():
        if tools is None:
            continue
        assert tools <= registered, f"profile {name!r} lists unknown tools: {sorted(tools - registered)}"


def test_profiles_are_narrower_than_all_and_include_core():
    registered = _registered_tool_names()
    for name, tools in TOOL_PROFILES.items():
        if tools is None:
            continue
        assert tool_profiles.CORE_TOOLS <= tools, name
        assert len(tools) < len(registered), name
        assert len(tools) > len(tool_profiles.CORE_TOOLS), name


def test_all_is_the_only_profile_selecting_everything():
    assert TOOL_PROFILES["all"] is None
    assert resolve_tool_profile("all") is None
    assert resolve_tool_profile(None) is None
    assert resolve_tool_profile("   ") is None
    assert resolve_tool_profile("health,all") is None


def test_resolve_single_profile_is_case_insensitive():
    assert resolve_tool_profile(" Health ") == TOOL_PROFILES["health"]


def test_resolve_several_profiles_is_their_union():
    combined = resolve_tool_profile("training, workouts")

    assert combined == TOOL_PROFILES["training"] | TOOL_PROFILES["workouts"]
    assert "get_training_status" in combined
    assert "create_run_workout" in combined
    assert "log_food" not in combined


def test_unknown_profile_fails_loudly_with_known_names():
    with pytest.raises(ValueError) as exc_info:
        resolve_tool_profile("helth")

    message = str(exc_info.value)
    assert "Unknown GARMIN_TOOL_PROFILE 'helth'" in message
    assert "Known profiles: activities, all, health, minimal, nutrition, training, workouts" in message


def test_separators_without_names_are_rejected():
    with pytest.raises(ValueError, match="expected at least one profile name"):
        resolve_tool_profile(",, ,")
