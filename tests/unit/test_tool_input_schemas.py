"""Contract tests for the input schemas the server advertises in tools/list."""

import asyncio
from unittest.mock import Mock

from mcp.server.fastmcp import FastMCP

from scripts.generate_dxt_tools import MODULES


def _accepts_null(schema):
    if schema.get("type") == "null":
        return True
    if isinstance(schema.get("type"), list) and "null" in schema["type"]:
        return True
    return any(_accepts_null(option) for option in schema.get("anyOf", []))


def test_parameters_defaulting_to_none_accept_null():
    """A parameter advertised with "default": null must accept null.

    Otherwise a client that sends the advertised default explicitly gets a
    validation error before the tool runs.
    """
    app = FastMCP("input schema contract")
    for module in MODULES:
        module.configure(Mock())
        module.register_tools(app)

    rejects_null = []
    for tool in asyncio.run(app.list_tools()):
        for name, prop in tool.inputSchema.get("properties", {}).items():
            if "default" in prop and prop["default"] is None and not _accepts_null(prop):
                rejects_null.append(f"{tool.name}.{name}")

    assert rejects_null == []
