"""Prefect Horizon entrypoint for a READ-ONLY Garmin Connect MCP server.

Deploy on Horizon with entrypoint  main.py:mcp

Required env (set as Horizon secrets/vars):
  GARMIN_TOKEN_B64      base64 of your ~/.garminconnect/garmin_tokens.json
                        (run `garmin-mcp-auth` locally once, then
                         `base64 -w0 ~/.garminconnect/garmin_tokens.json`)
  GARMIN_DISABLED_TOOLS comma-separated write tools to hide (read-only policy)

This file only assembles the upstream tool modules onto a fastmcp v2 server;
it contains no Garmin logic of its own, so it stays trivial to rebase on
upstream.
"""

from __future__ import annotations

import base64
import importlib
import os
import pathlib

# The token must exist on disk BEFORE importing garmin_mcp, which reads
# GARMINTOKENS at import time. Materialize it from the env secret.
_b64 = os.getenv("GARMIN_TOKEN_B64")
if _b64:
    _dir = pathlib.Path(os.getenv("GARMINTOKENS") or "/tmp/garmintokens")
    _dir.mkdir(parents=True, exist_ok=True)
    (_dir / "garmin_tokens.json").write_text(
        base64.b64decode(_b64).decode("utf-8"), encoding="utf-8"
    )
    os.environ["GARMINTOKENS"] = str(_dir)

from fastmcp import FastMCP  # noqa: E402  (Horizon-native server type)
import garmin_mcp as gm  # noqa: E402
from garmin_mcp import _ToolFilter, workout_templates  # noqa: E402

_MODULE_NAMES = [
    "activity_management", "health_wellness", "user_profile", "devices",
    "gear_management", "weight_management", "challenges", "training",
    "workouts", "data_management", "womens_health", "nutrition",
    "workout_builders", "courses", "activity_analysis",
]
_MODULES = [importlib.import_module(f"garmin_mcp.{n}") for n in _MODULE_NAMES]


def _build() -> FastMCP:
    client = gm.init_api(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
    if client is None:
        raise RuntimeError(
            "Garmin authentication unavailable. Set GARMIN_TOKEN_B64 "
            "(run `garmin-mcp-auth` locally, then base64 the "
            "~/.garminconnect/garmin_tokens.json file)."
        )
    client = gm._GarminProxy(client)
    for module in _MODULES:
        module.configure(client)

    server = FastMCP("Garmin Connect")
    # _ToolFilter honours GARMIN_ENABLED_TOOLS / GARMIN_DISABLED_TOOLS (parsed
    # by garmin_mcp at import) and skips registration of filtered tools.
    app = _ToolFilter(server, gm.enabled_tools, gm.disabled_tools)
    for module in _MODULES:
        app = module.register_tools(app)
    workout_templates.register_resources(app)
    return server


mcp = _build()
