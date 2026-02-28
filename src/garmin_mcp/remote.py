"""
Remote MCP server entry point with OAuth2 authentication.

Runs the Garmin MCP server in remote mode with:
- Streamable HTTP transport (network accessible)
- OAuth2 authentication (integrated authorization server)
- Multi-user support (each user links their own Garmin account)
"""

from __future__ import annotations

import sys

from pydantic import AnyHttpUrl
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from starlette.requests import Request
from starlette.responses import Response

from garmin_mcp.config import get_config
from garmin_mcp.oauth_provider import GarminOAuthProvider
from garmin_mcp.session_manager import SessionManager
from garmin_mcp.client_resolver import set_session_manager

# Import all tool modules
from garmin_mcp import activity_management
from garmin_mcp import health_wellness
from garmin_mcp import user_profile
from garmin_mcp import devices
from garmin_mcp import gear_management
from garmin_mcp import weight_management
from garmin_mcp import challenges
from garmin_mcp import training
from garmin_mcp import workouts
from garmin_mcp import workout_templates
from garmin_mcp import data_management
from garmin_mcp import womens_health


def main():
    """Start the remote MCP server with OAuth2 authentication."""
    # Load configuration
    try:
        config = get_config()
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Starting Garmin MCP remote server...", file=sys.stderr)
    print(f"  Server URL: {config.server_url}", file=sys.stderr)
    print(f"  Listen: {config.host}:{config.port}", file=sys.stderr)
    print(f"  MCP path: {config.path}", file=sys.stderr)
    print(f"  DB: {config.db_path}", file=sys.stderr)
    print(f"  Sessions: {config.session_storage_path}", file=sys.stderr)

    # Initialize session manager
    session_manager = SessionManager(config.session_storage_path)
    set_session_manager(session_manager)

    # Initialize OAuth provider
    oauth_provider = GarminOAuthProvider(
        db_path=config.db_path,
        server_url=config.server_url,
        session_manager=session_manager,
    )

    # Create the MCP app with OAuth2 authentication
    app = FastMCP(
        name="Garmin Connect v1.0",
        auth_server_provider=oauth_provider,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(config.server_url),
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=[config.scope],
                default_scopes=[config.scope],
            ),
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=[config.scope],
            resource_server_url=None,  # Combined AS+RS mode
        ),
        host=config.host,
        port=config.port,
        streamable_http_path=config.path,
    )

    # Register custom routes for login pages
    @app.custom_route("/login", methods=["GET"])
    async def login_page(request: Request) -> Response:
        state = request.query_params.get("state", "")
        return await oauth_provider.get_login_page(state)

    @app.custom_route("/login/callback", methods=["POST"])
    async def login_callback(request: Request) -> Response:
        return await oauth_provider.handle_login_callback(request)

    @app.custom_route("/login/mfa", methods=["GET"])
    async def mfa_page(request: Request) -> Response:
        state = request.query_params.get("state", "")
        return await oauth_provider.get_mfa_page(state)

    @app.custom_route("/login/mfa/callback", methods=["POST"])
    async def mfa_callback(request: Request) -> Response:
        return await oauth_provider.handle_mfa_callback(request)

    # Register tools from all modules
    app = activity_management.register_tools(app)
    app = health_wellness.register_tools(app)
    app = user_profile.register_tools(app)
    app = devices.register_tools(app)
    app = gear_management.register_tools(app)
    app = weight_management.register_tools(app)
    app = challenges.register_tools(app)
    app = training.register_tools(app)
    app = workouts.register_tools(app)
    app = data_management.register_tools(app)
    app = womens_health.register_tools(app)

    # Register resources (workout templates)
    app = workout_templates.register_resources(app)

    print("Server ready.", file=sys.stderr)

    # Run the server with streamable HTTP transport
    app.run(transport="streamable-http")


if __name__ == "__main__":
    main()
