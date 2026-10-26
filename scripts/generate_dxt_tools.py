#!/usr/bin/env python3
"""Regenerate dxt/manifest.json's "tools" array from the actual registered tools.

The manifest's tools list is for Desktop Extension UIs to display; it drifted
to 8 hand-picked entries while the server grew to ~150 tools (#340). This
builds the same FastMCP app main() builds (same module list and order, no
tool filter), against a mocked Garmin client, and writes every tool's name
and first docstring line into the manifest.
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

from mcp.server.fastmcp import FastMCP

from garmin_mcp import (
    activity_analysis,
    activity_management,
    calendar_events,
    challenges,
    courses,
    data_management,
    devices,
    exercise_catalog,
    gear_management,
    health_wellness,
    nutrition,
    training,
    user_profile,
    weight_management,
    womens_health,
    workout_builders,
    workouts,
)

# Same modules, same order, as the `configure`/`register_tools` calls in
# garmin_mcp.__init__.main(). Keep in sync with that list.
MODULES = [
    exercise_catalog,
    activity_management,
    health_wellness,
    user_profile,
    devices,
    gear_management,
    weight_management,
    challenges,
    training,
    workouts,
    data_management,
    womens_health,
    nutrition,
    workout_builders,
    courses,
    activity_analysis,
    calendar_events,
]

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "dxt" / "manifest.json"


def _first_line(description: str) -> str:
    return (description or "").strip().splitlines()[0].strip() if description else ""


async def _collect_tools() -> list[dict]:
    mock_client = MagicMock()
    app = FastMCP("Garmin Connect v1.0")
    for module in MODULES:
        module.configure(mock_client)
        module.register_tools(app)

    tools = await app.list_tools()
    return [
        {"name": tool.name, "description": _first_line(tool.description)}
        for tool in sorted(tools, key=lambda t: t.name)
    ]


def main() -> None:
    tools = asyncio.run(_collect_tools())
    manifest = json.loads(MANIFEST_PATH.read_text())
    manifest["tools"] = tools
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {len(tools)} tools to {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
