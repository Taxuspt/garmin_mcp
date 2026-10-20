"""Shared-secret bearer auth for the HTTP transports (#339).

GARMIN_MCP_TRANSPORT=streamable-http|sse serves full read/write access to the
Garmin account. With GARMIN_MCP_HTTP_TOKEN set, every request must carry
``Authorization: Bearer <token>``; the liveness and readiness probes stay open
because they reveal nothing beyond "up" and "logged in".

FastMCP builds its Starlette app inside streamable_http_app() / sse_app() and
offers no middleware hook short of the full OAuth resource-server setup (its
AuthSettings require an issuer URL), so the built app is wrapped instead.
"""

import hmac
import ipaddress
import os

from starlette.responses import JSONResponse

# Probes carry no Garmin data and are polled by orchestrators that usually
# cannot attach headers.
UNAUTHENTICATED_PATHS = frozenset({"/healthz", "/readyz"})


def resolve_http_token() -> str | None:
    """Read GARMIN_MCP_HTTP_TOKEN; None when unset or blank."""
    token = os.getenv("GARMIN_MCP_HTTP_TOKEN", "").strip()
    return token or None


def is_loopback(host: str) -> bool:
    """True when the bind address only accepts connections from this machine."""
    host = host.strip().strip("[]").lower()
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class BearerTokenGate:
    """ASGI wrapper that rejects HTTP requests without the shared bearer token."""

    def __init__(self, app, token: str):
        self._app = app
        self._token = token.encode("utf-8")

    def authorized(self, scope) -> bool:
        for name, value in scope.get("headers") or ():
            if name.lower() != b"authorization":
                continue
            scheme, _, credential = value.strip().partition(b" ")
            return scheme.lower() == b"bearer" and hmac.compare_digest(
                credential.strip(), self._token
            )
        return False

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and scope.get("path") not in UNAUTHENTICATED_PATHS
            and not self.authorized(scope)
        ):
            response = JSONResponse(
                {
                    "error": "unauthorized",
                    "detail": "Send 'Authorization: Bearer <GARMIN_MCP_HTTP_TOKEN>'.",
                },
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="garmin-mcp"'},
            )
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)


def install_http_bearer_auth(fastmcp, token: str) -> None:
    """Make fastmcp's HTTP apps require the bearer token on every request."""
    for builder_name in ("streamable_http_app", "sse_app"):
        builder = getattr(fastmcp, builder_name)

        def build(*args, _builder=builder, **kwargs):
            return BearerTokenGate(_builder(*args, **kwargs), token)

        setattr(fastmcp, builder_name, build)


def exposure_warning(host: str, port: int) -> str:
    """Startup warning for an HTTP bind beyond loopback with no token."""
    return (
        f"WARNING: GARMIN_MCP_HOST={host} accepts connections from other machines "
        f"and GARMIN_MCP_HTTP_TOKEN is not set: anyone who can reach port {port} "
        "has full read/write access to the Garmin account. Set "
        "GARMIN_MCP_HTTP_TOKEN, or front the server with an authenticating proxy."
    )
