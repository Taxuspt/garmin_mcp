"""Interactive confirmation boundary for new Garmin write tools."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import Context
from pydantic import BaseModel, Field


class GarminWriteConfirmation(BaseModel):
    """Non-secret acknowledgement used by MCP form elicitation."""

    confirm: bool = Field(
        description="Set true only after reviewing this exact Garmin write preview"
    )


_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "mfa",
    "mfa_code",
    "password",
    "secret",
    "token",
}


def _redact_secrets(value: Any) -> Any:
    """Recursively redact credential-shaped keys before rendering a preview."""

    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            parts = set(normalized.split("_"))
            redacted[str(key)] = (
                "[REDACTED]"
                if normalized in _SENSITIVE_KEYS or parts & _SENSITIVE_KEYS
                else _redact_secrets(item)
            )
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact_secrets(item) for item in value]
    return value


async def confirm_garmin_write(
    ctx: Context,
    *,
    action: str,
    summary: dict[str, Any],
) -> tuple[bool, str | None]:
    """Ask the connected human to approve one already prepared write.

    The prepared payload stays in the calling tool invocation, so approval is
    bound to that exact operation. Unattended clients and scheduled tasks that
    cannot answer elicitation safely receive ``needs_confirmation``.
    """

    safe_summary = _redact_secrets(summary)
    rendered_summary = json.dumps(
        safe_summary,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        default=str,
    )
    try:
        elicited = await ctx.elicit(
            (
                f"Confirm Garmin write: {action}. Review this exact prepared "
                f"change summary:\n{rendered_summary}\n"
                "No password, MFA code, or token is requested."
            ),
            GarminWriteConfirmation,
        )
    except Exception as exc:
        return False, f"Interactive confirmation is unavailable: {type(exc).__name__}: {exc}"
    if elicited.action != "accept" or elicited.data is None:
        return False, "The Garmin write was not accepted by the user."
    if elicited.data.confirm is not True:
        return False, "The confirmation form did not authorize the Garmin write."
    return True, None


def needs_confirmation_result(
    *,
    preview: dict[str, Any],
    message: str | None,
) -> dict[str, Any]:
    """Return a consistent structured refusal without performing a write."""

    return {
        "status": "needs_confirmation",
        "dry_run": False,
        "write_performed": False,
        "message": message or "Interactive user confirmation is required.",
        "preview": preview,
    }
