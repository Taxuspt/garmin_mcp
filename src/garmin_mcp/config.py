"""
Configuration for Garmin MCP remote server via environment variables.
"""

import os


class RemoteConfig:
    """Configuration for the remote MCP server."""

    def __init__(self):
        self.host = os.getenv("GARMIN_MCP_HOST", "0.0.0.0")
        self.port = int(os.getenv("GARMIN_MCP_PORT", "8000"))
        self.path = os.getenv("GARMIN_MCP_PATH", "/mcp")
        self.server_url = os.getenv("GARMIN_MCP_SERVER_URL", "")
        self.scope = os.getenv("MCP_SCOPE", "garmin")
        self.db_path = os.getenv("DB_PATH", "/data/garmin_mcp.db")
        self.session_storage_path = os.getenv(
            "SESSION_STORAGE_PATH", "/data/garmin_sessions"
        )

    def validate(self):
        """Validate required configuration."""
        if not self.server_url:
            raise ValueError(
                "GARMIN_MCP_SERVER_URL is required. "
                "Set it to the public URL of your server (e.g., https://garmin-mcp.example.com)"
            )


def get_config() -> RemoteConfig:
    """Get and validate the remote server configuration."""
    config = RemoteConfig()
    config.validate()
    return config
