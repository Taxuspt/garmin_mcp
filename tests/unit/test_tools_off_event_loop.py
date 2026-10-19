"""Tool bodies run on worker threads, so one Garmin call cannot stall the server (#338).

Tools are ``async def`` but call the synchronous garminconnect client, so
FastMCP awaiting them on its event loop meant every other request waited for
the slowest Garmin call.
"""

import asyncio
import threading
from typing import Optional

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import _ToolFilter


@pytest.fixture
def app():
    return _ToolFilter(FastMCP("off-loop-test"), set(), set())


async def test_blocking_tool_does_not_stall_other_tools(app):
    release = threading.Event()
    order = []

    @app.tool()
    async def slow_tool() -> str:
        """Block like a stalled Garmin call until released."""
        order.append("slow started")
        released = release.wait(30)
        order.append("slow finished" if released else "slow timed out")
        return "slow"

    @app.tool()
    async def fast_tool() -> str:
        """Return at once and release the slow tool."""
        order.append("fast ran")
        release.set()
        return "fast"

    # Before the fix slow_tool blocked the loop inside release.wait(), so
    # fast_tool could not run and nothing ever set the event.
    results = await asyncio.wait_for(
        asyncio.gather(app.call_tool("slow_tool", {}), app.call_tool("fast_tool", {})),
        timeout=10,
    )

    assert [block[0].text for block in results] == ["slow", "fast"]
    # Both bodies run on worker threads, so which one *starts* first is up to
    # the scheduler. What must hold: fast_tool ran at all, and slow_tool only
    # finished after it (the event is the only thing that releases it).
    assert sorted(order) == ["fast ran", "slow finished", "slow started"]
    assert order.index("fast ran") < order.index("slow finished")
    assert order[-1] == "slow finished"


async def test_tool_keeps_signature_docstring_and_return_value(app):
    @app.tool()
    async def echo_tool(activity_id: int, label: Optional[str] = None) -> str:
        """Echo the arguments back."""
        return f"{activity_id}:{label}"

    (tool,) = await app.list_tools()
    assert tool.name == "echo_tool"
    assert tool.description == "Echo the arguments back."
    assert set(tool.inputSchema["properties"]) == {"activity_id", "label"}
    assert tool.inputSchema["required"] == ["activity_id"]

    result = await app.call_tool("echo_tool", {"activity_id": 7, "label": "x"})
    assert result[0].text == "7:x"


async def test_tool_runs_on_a_worker_thread(app):
    main_thread = threading.current_thread()
    seen = {}

    @app.tool()
    async def where_am_i() -> str:
        """Record the thread the body runs on."""
        seen["thread"] = threading.current_thread()
        return "ok"

    await app.call_tool("where_am_i", {})

    assert seen["thread"] is not main_thread


async def test_tool_exceptions_still_surface(app):
    @app.tool()
    async def failing_tool() -> str:
        """Raise like a tool without its own try/except."""
        raise RuntimeError("boom")

    with pytest.raises(Exception, match="boom"):
        await app.call_tool("failing_tool", {})


def test_decorated_function_is_returned_unchanged(app):
    async def plain() -> str:
        """Keep the module-level name bound to the original function."""
        return "x"

    assert app.tool()(plain) is plain
