"""Capability-token file store for serving downloaded activity files over HTTP.

Only used when the server runs over an HTTP transport (streamable-http/sse).
Over stdio the client is on the same machine, so download_activity_file just
returns the local path and this module is never invoked.

The server has no authentication, so a guessable path like /files/{activity_id}
would let anyone who reaches the container read arbitrary activity files. Instead,
each downloaded file gets a random, short-lived token; only /files/{token} for a
token actually issued resolves to a file.
"""
import os
import re
import secrets
import time
from typing import Optional, Tuple

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# token -> (absolute file path, expiry epoch seconds)
_TOKENS: dict[str, Tuple[str, float]] = {}


def ttl_seconds() -> int:
    """How long a token (and its file) stays valid, in seconds."""
    return int(os.getenv("GARMIN_MCP_FILE_TOKEN_TTL_SECONDS", "900"))


def _purge_expired(now: float) -> None:
    """Drop expired tokens and delete their files, so the container doesn't
    accumulate FIT files indefinitely."""
    expired = [token for token, (_, expires_at) in _TOKENS.items() if expires_at <= now]
    for token in expired:
        file_path, _ = _TOKENS.pop(token)
        try:
            os.remove(file_path)
        except OSError:
            pass


def issue_token(file_path: str) -> str:
    """Register file_path under a fresh capability token and return the token."""
    now = time.time()
    _purge_expired(now)
    token = secrets.token_urlsafe(32)
    _TOKENS[token] = (file_path, now + ttl_seconds())
    return token


def resolve_token(token: str) -> Optional[str]:
    """Return the file path for a valid, unexpired token; None otherwise."""
    if not token or not _TOKEN_RE.match(token):
        return None
    now = time.time()
    _purge_expired(now)
    entry = _TOKENS.get(token)
    if entry is None:
        return None
    file_path, expires_at = entry
    if expires_at <= now:
        return None
    return file_path


def register_routes(fastmcp) -> None:
    """Register the GET /files/{token} download route on the FastMCP app."""
    from starlette.requests import Request
    from starlette.responses import FileResponse, PlainTextResponse

    @fastmcp.custom_route("/files/{token}", methods=["GET"])
    async def serve_file(request: "Request"):
        file_path = resolve_token(request.path_params["token"])
        if file_path is None or not os.path.isfile(file_path):
            return PlainTextResponse("Not found or expired", status_code=404)
        return FileResponse(file_path, filename=os.path.basename(file_path))
