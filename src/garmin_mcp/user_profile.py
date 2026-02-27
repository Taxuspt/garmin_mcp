"""
User Profile functions for Garmin Connect MCP Server
"""
import json
import datetime
from typing import Any, Dict, List, Optional, Union

from mcp.server.fastmcp import Context
from garmin_mcp.client_resolver import get_client

# The garmin_client will be set by the main file
garmin_client = None


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


def register_tools(app):
    """Register all user profile tools with the MCP server app"""

    @app.tool()
    async def get_full_name(ctx: Context) -> str:
        """Get user's full name from profile"""
        try:
            full_name = get_client(ctx).get_full_name()
            return json.dumps({"full_name": full_name}, indent=2)
        except Exception as e:
            return f"Error retrieving user's full name: {str(e)}"

    @app.tool()
    async def get_unit_system(ctx: Context) -> str:
        """Get user's preferred unit system from profile"""
        try:
            unit_system = get_client(ctx).get_unit_system()
            return json.dumps({"unit_system": unit_system}, indent=2)
        except Exception as e:
            return f"Error retrieving unit system: {str(e)}"

    @app.tool()
    async def get_user_profile(ctx: Context) -> str:
        """Get user profile information"""
        try:
            profile = get_client(ctx).get_user_profile()
            if not profile:
                return "No user profile information found."
            return json.dumps(profile, indent=2)
        except Exception as e:
            return f"Error retrieving user profile: {str(e)}"

    @app.tool()
    async def get_userprofile_settings(ctx: Context) -> str:
        """Get user profile settings"""
        try:
            settings = get_client(ctx).get_userprofile_settings()
            if not settings:
                return "No user profile settings found."
            return json.dumps(settings, indent=2)
        except Exception as e:
            return f"Error retrieving user profile settings: {str(e)}"

    return app