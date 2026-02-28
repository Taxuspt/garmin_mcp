"""
Client resolver for Garmin MCP server.

Resolves the Garmin client based on context:
- stdio mode: returns the global client (single-user)
- remote mode: extracts user_id from OAuth access token, resolves via SessionManager
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from garminconnect import Garmin

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

# Global client for stdio mode
_global_client: Optional[Garmin] = None

# Session manager for remote mode (set by remote.py)
_session_manager = None


def set_global_client(client: Garmin) -> None:
    """Set the global Garmin client for stdio mode."""
    global _global_client
    _global_client = client


def set_session_manager(manager) -> None:
    """Set the session manager for remote mode."""
    global _session_manager
    _session_manager = manager


def get_client(ctx: Optional[Context] = None) -> Garmin:
    """Resolve the Garmin client based on context.

    In stdio mode (no ctx or no auth token): returns the global client.
    In remote mode: extracts user_id from the OAuth access token and
    returns the per-user Garmin client from SessionManager.
    """
    # Try remote mode: extract user_id from OAuth token
    if ctx is not None and _session_manager is not None:
        try:
            from mcp.server.auth.middleware.auth_context import get_access_token

            access_token = get_access_token()
            if access_token is not None:
                user_id = _session_manager.get_user_id_for_token(access_token.token)
                if user_id:
                    client = _session_manager.get_client(user_id)
                    if client is not None:
                        return client
                    raise RuntimeError(
                        "Garmin session expired or not available. Please re-authenticate."
                    )
        except ImportError:
            pass

    # Fallback to global client (stdio mode)
    if _global_client is not None:
        return _global_client

    raise RuntimeError("Garmin client not available. Please authenticate first.")
