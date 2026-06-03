"""
Claude.ai OAuth interoperability shims for the Garmin MCP remote server.

Claude.ai's MCP connector follows RFC 9728 (OAuth 2.0 Protected Resource
Metadata) and registers as a PKCE *public* client using a fixed ``client_id``.
In combined Authorization-Server + Resource-Server mode (``resource_server_url``
is ``None``) the MCP Python SDK (>= 1.26) does not advertise the pieces Claude.ai
needs, so discovery fails before it ever reaches ``/authorize`` or ``/token``.

This module fills four gaps without forking the SDK:

1. ``/.well-known/oauth-protected-resource`` endpoint (RFC 9728). Claude.ai
   refuses to start OAuth discovery without it. See
   :func:`protected_resource_metadata` / :func:`protected_resource_response`.
2. ``WWW-Authenticate`` on 401s must carry ``resource_metadata`` so the client
   knows where discovery begins. Per RFC 6750 the ``error="invalid_token"``
   form must only appear when a token *is* present but invalid — a request with
   no credentials should get a bare challenge. See
   :func:`patch_require_auth_middleware`.
3. ``token_endpoint_auth_methods_supported`` must include ``"none"`` so the
   PKCE public client sees its auth method as supported and calls ``/token``.
   See :func:`patch_token_endpoint_auth_methods`.
4. Claude.ai skips dynamic client registration and goes straight to
   ``/authorize`` with ``client_id=https://claude.ai``, so a matching client
   must be pre-registered. See :func:`build_static_clients`.
"""

from __future__ import annotations

import functools
import json
import logging
from typing import Any

from mcp.shared.auth import OAuthClientInformationFull
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# Claude.ai's static, pre-known public-client identity.
CLAUDE_AI_CLIENT_ID = "https://claude.ai"
CLAUDE_AI_REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"

# RFC 9728 well-known location for protected resource metadata.
WELL_KNOWN_PROTECTED_RESOURCE = "/.well-known/oauth-protected-resource"

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
}


# ─── (1) Protected resource metadata ──────────────────────────────────────


def resource_metadata_url(server_url: str) -> str:
    """Return the absolute URL of the protected-resource metadata document."""
    return f"{server_url.rstrip('/')}{WELL_KNOWN_PROTECTED_RESOURCE}"


def protected_resource_metadata(
    server_url: str, scopes: list[str] | None = None
) -> dict[str, Any]:
    """Build the RFC 9728 protected resource metadata document.

    In combined AS+RS mode the resource and its authorization server are the
    same origin, so both point at ``server_url``.
    """
    base = server_url.rstrip("/")
    data: dict[str, Any] = {
        "resource": base,
        "authorization_servers": [base],
    }
    if scopes:
        data["scopes_supported"] = list(scopes)
        data["bearer_methods_supported"] = ["header"]
    return data


def protected_resource_response(
    request: Request, server_url: str, scopes: list[str] | None = None
) -> Response:
    """Starlette handler body for the protected-resource metadata route.

    Handles the CORS preflight (``OPTIONS``) and the ``GET`` document. The
    document is unauthenticated by design — it is what tells a client how to
    authenticate in the first place.
    """
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_CORS_HEADERS)
    return JSONResponse(
        protected_resource_metadata(server_url, scopes),
        headers=_CORS_HEADERS,
    )


# ─── (2) WWW-Authenticate header with resource_metadata ────────────────────


def _has_bearer_token(scope: dict[str, Any]) -> bool:
    """True if the request carried an ``Authorization: Bearer ...`` header."""
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            return value[:7].lower() == b"bearer "
    return False


def _make_resource_aware_middleware(metadata_url: str):
    """Build a ``RequireAuthMiddleware`` subclass bound to ``metadata_url``.

    FastMCP constructs the middleware as
    ``RequireAuthMiddleware(app, required_scopes, resource_metadata_url)`` and,
    in combined AS+RS mode, passes ``resource_metadata_url=None``. We ignore
    that argument and inject our own URL so every 401/403 advertises where to
    begin discovery, and we split the no-credentials case (a bare challenge)
    from the invalid-token case (RFC 6750 ``error="invalid_token"``).
    """
    from mcp.server.auth.middleware.bearer_auth import (
        AuthenticatedUser,
        RequireAuthMiddleware as _Base,
    )

    class ResourceAwareAuthMiddleware(_Base):
        def __init__(self, app, required_scopes, resource_metadata_url=None):
            # Always use our computed metadata URL, never FastMCP's (None).
            super().__init__(app, required_scopes, metadata_url)

        async def __call__(self, scope, receive, send) -> None:
            if scope.get("type") != "http":
                await self.app(scope, receive, send)
                return

            user = scope.get("user")
            if not isinstance(user, AuthenticatedUser):
                if _has_bearer_token(scope):
                    # A token was presented but the verifier rejected it.
                    await self._send_challenge(
                        send,
                        401,
                        error="invalid_token",
                        description="The access token is invalid or has expired",
                    )
                else:
                    # No credentials at all: a bare challenge that only points
                    # the client at the protected-resource metadata (RFC 9728).
                    await self._send_challenge(send, 401)
                return

            credentials = scope.get("auth")
            for required_scope in self.required_scopes:
                if credentials is None or required_scope not in credentials.scopes:
                    await self._send_challenge(
                        send,
                        403,
                        error="insufficient_scope",
                        description=f"Required scope: {required_scope}",
                    )
                    return

            await self.app(scope, receive, send)

        async def _send_challenge(
            self,
            send,
            status_code: int,
            error: str | None = None,
            description: str | None = None,
        ) -> None:
            parts: list[str] = []
            body: dict[str, str] = {}
            if error:
                parts.append(f'error="{error}"')
                body["error"] = error
                if description:
                    parts.append(f'error_description="{description}"')
                    body["error_description"] = description
            if self.resource_metadata_url:
                parts.append(f'resource_metadata="{self.resource_metadata_url}"')

            www_authenticate = "Bearer" + ((" " + ", ".join(parts)) if parts else "")
            body_bytes = json.dumps(
                body or {"error_description": "Authentication required"}
            ).encode()

            await send(
                {
                    "type": "http.response.start",
                    "status": status_code,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body_bytes)).encode()),
                        (b"www-authenticate", www_authenticate.encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body_bytes})

    return ResourceAwareAuthMiddleware


def patch_require_auth_middleware(metadata_url: str) -> None:
    """Replace FastMCP's ``RequireAuthMiddleware`` reference with our subclass.

    Must be called before ``FastMCP.streamable_http_app()`` runs (i.e. before
    ``app.run()``), since the class is read at app-construction time.
    """
    import mcp.server.fastmcp.server as fastmcp_server

    fastmcp_server.RequireAuthMiddleware = _make_resource_aware_middleware(metadata_url)
    logger.info("Patched RequireAuthMiddleware with resource_metadata=%s", metadata_url)


# ─── (3) token_endpoint_auth_methods_supported += "none" ───────────────────


def patch_token_endpoint_auth_methods(extra_methods: tuple[str, ...] = ("none",)) -> None:
    """Make ``build_metadata`` advertise extra token-endpoint auth methods.

    The SDK hardcodes ``["client_secret_post", "client_secret_basic"]``; PKCE
    public clients (Claude.ai) use ``token_endpoint_auth_method: none`` and skip
    ``/token`` if they don't see it advertised. Idempotent.

    Must be called before ``app.run()`` (the metadata is built when the auth
    routes are created during ``streamable_http_app()``).
    """
    import mcp.server.auth.routes as routes_mod

    original = routes_mod.build_metadata
    if getattr(original, "_garmin_patched", False):
        return

    @functools.wraps(original)
    def build_metadata(*args, **kwargs):
        metadata = original(*args, **kwargs)
        methods = list(metadata.token_endpoint_auth_methods_supported or [])
        for method in extra_methods:
            if method not in methods:
                methods.append(method)
        metadata.token_endpoint_auth_methods_supported = methods
        return metadata

    build_metadata._garmin_patched = True  # type: ignore[attr-defined]
    routes_mod.build_metadata = build_metadata
    logger.info("Patched build_metadata to advertise auth methods: %s", extra_methods)


# ─── (4) Static client pre-registration ────────────────────────────────────


def build_static_clients(scope: str) -> list[OAuthClientInformationFull]:
    """Clients that must exist without going through dynamic registration.

    Currently just Claude.ai, which uses a fixed ``client_id`` and the PKCE
    public-client (``none``) auth method.
    """
    return [
        OAuthClientInformationFull(
            client_id=CLAUDE_AI_CLIENT_ID,
            client_secret=None,
            redirect_uris=[CLAUDE_AI_REDIRECT_URI],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=scope,
            client_name="Claude.ai",
            client_uri="https://claude.ai",
        )
    ]
