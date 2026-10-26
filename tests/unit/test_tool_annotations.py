"""Coverage and contract tests for MCP tool effect annotations."""

import asyncio

from mcp.server.fastmcp import FastMCP

import garmin_mcp
from garmin_mcp import tool_annotations
from scripts.generate_dxt_tools import MODULES


def test_annotation_registry_is_exhaustive_and_disjoint():
    app = FastMCP("annotation coverage")
    proxy = garmin_mcp._ToolFilter(app, set(), set())
    for module in MODULES:
        module.configure(object())
        module.register_tools(proxy)

    tools = asyncio.run(app.list_tools())
    names = {tool.name for tool in tools}

    assert len(names) == 160
    assert tool_annotations.ALL_ANNOTATED_TOOLS == names
    assert not (
        tool_annotations.READ_ONLY_TOOLS
        & tool_annotations.ADDITIVE_TOOLS
    )
    assert not (
        tool_annotations.READ_ONLY_TOOLS
        & tool_annotations.DESTRUCTIVE_TOOLS
    )
    assert not (
        tool_annotations.ADDITIVE_TOOLS
        & tool_annotations.DESTRUCTIVE_TOOLS
    )
    assert proxy.unannotated_tool_names() == []


def test_annotations_use_conservative_open_world_defaults():
    assert tool_annotations.annotations_for("get_activities").model_dump() == {
        "title": None,
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": None,
        "openWorldHint": True,
    }
    assert (
        tool_annotations.annotations_for("set_fit_download_dir").openWorldHint
        is False
    )


def test_strength_catalog_tools_are_read_only():
    for name in (
        "list_strength_exercises",
        "match_strength_exercise",
        "resolve_strength_exercises",
    ):
        annotations = tool_annotations.annotations_for(name)
        assert annotations is not None
        assert annotations.readOnlyHint is True
        assert annotations.destructiveHint is False
