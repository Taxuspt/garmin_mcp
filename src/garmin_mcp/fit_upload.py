"""Safe, preview-first FIT activity upload pipeline.

The initial implementation deliberately supports a validated passthrough only.
Vendor-specific rewrites are enabled only when a fixture-backed adapter exists;
unknown files are never rewritten by heuristics.
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

import fitparse
from mcp.server.fastmcp import Context
from pydantic import BaseModel, Field

from garmin_mcp import write_confirmation
from garmin_mcp.activity_analysis import _extract_fit_bytes, _get_field
from garmin_mcp.result_models import FitUploadResult


garmin_client = None
_uploaded_hashes: dict[str, Any] = {}
_upload_lock = threading.RLock()


class FitPathSelection(BaseModel):
    """Non-secret local file selection used by MCP form elicitation."""

    path: str = Field(description="Absolute path to the FIT activity file")


async def _report_progress(
    ctx: Context, progress: float, total: float, message: str
) -> None:
    """Report progress when invoked through a real MCP request context."""

    try:
        await ctx.report_progress(progress, total, message)
    except (RuntimeError, ValueError):
        # FastMCP's direct call_tool test helper has no request context. The
        # operation remains valid; only the optional notification is omitted.
        return


def configure(client) -> None:
    global garmin_client
    garmin_client = client


def _allowed_upload_roots() -> list[Path]:
    configured = os.getenv("GARMIN_ALLOWED_UPLOAD_DIRS")
    if not configured:
        return []
    return [
        Path(item).expanduser().resolve()
        for item in configured.split(os.pathsep)
        if item.strip()
    ]


def _resolve_source(path: str) -> Path:
    roots = _allowed_upload_roots()
    transport = os.getenv("GARMIN_MCP_TRANSPORT", "stdio").strip().lower()
    if transport in {"streamable-http", "sse"} and not roots:
        raise ValueError(
            "GARMIN_ALLOWED_UPLOAD_DIRS must be configured before upload_fit "
            "can read local paths over an HTTP/SSE MCP transport"
        )
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError(f"FIT upload source is not a regular file: {source}")
    if roots and not any(source == root or root in source.parents for root in roots):
        raise ValueError(
            "FIT upload path is outside GARMIN_ALLOWED_UPLOAD_DIRS: "
            f"{source}"
        )
    if source.stat().st_size > 100 * 1024 * 1024:
        raise ValueError("FIT upload file exceeds the 100 MiB safety limit")
    return source


def inspect_fit_file(path: str) -> dict:
    """Validate an activity FIT and return a small, non-location summary."""
    source = _resolve_source(path)
    raw = source.read_bytes()
    payload = _extract_fit_bytes(raw)
    digest = hashlib.sha256(payload).hexdigest()

    fit = fitparse.FitFile(io.BytesIO(payload))
    session: dict[str, Any] = {}
    record_count = 0
    for message in fit.get_messages():
        if message.name == "session" and not session:
            session = {
                "sport": _get_field(message, "sport"),
                "sub_sport": _get_field(message, "sub_sport"),
                "start_time": str(_get_field(message, "start_time") or ""),
                "elapsed_time_s": _get_field(message, "total_elapsed_time"),
                "timer_time_s": _get_field(message, "total_timer_time"),
                "distance_m": _get_field(message, "total_distance"),
            }
            session = {k: v for k, v in session.items() if v not in (None, "")}
        elif message.name == "record":
            record_count += 1

    return {
        "path": str(source),
        "size_bytes": len(payload),
        "sha256": digest,
        "fit_valid": True,
        "record_count": record_count,
        "session": session,
    }


def _stage_fit(path: str) -> tuple[Path, dict]:
    metadata = inspect_fit_file(path)
    raw = _resolve_source(path).read_bytes()
    payload = _extract_fit_bytes(raw)
    handle = tempfile.NamedTemporaryFile(
        prefix="garmin-mcp-upload-", suffix=".fit", delete=False
    )
    try:
        handle.write(payload)
        handle.flush()
    finally:
        handle.close()
    return Path(handle.name), metadata


def register_tools(app):
    @app.tool(structured_output=True)
    async def upload_fit(
        ctx: Context,
        path: Optional[str] = None,
        repair_profile: str = "auto",
        force_sport: Optional[str] = None,
        fix_elevation: bool = False,
        dry_run: bool = True,
    ) -> FitUploadResult:
        """Validate and preview or upload a third-party FIT activity.

        The source is never overwritten. ``auto`` currently means validated
        passthrough. Sport/elevation rewrites require a fixture-backed vendor
        adapter and therefore fail closed instead of guessing.
        """
        staged: Optional[Path] = None
        try:
            if path is None:
                elicited = await ctx.elicit(
                    "Choose a local FIT activity file. Do not enter Garmin credentials or MFA codes.",
                    FitPathSelection,
                )
                if elicited.action != "accept" or elicited.data is None:
                    return {
                        "status": "needs_input",
                        "dry_run": dry_run,
                        "message": "FIT file selection was not accepted; nothing was uploaded.",
                    }
                path = elicited.data.path
            await _report_progress(ctx, 0, 3, "Validating FIT file")
            profile = repair_profile.strip().lower()
            if profile not in {"auto", "none", "passthrough"}:
                return {
                    "status": "needs_fixture",
                    "dry_run": dry_run,
                    "repair_profile": profile,
                    "message": (
                        "No fixture-backed adapter is registered for this repair "
                        "profile; the file was not changed or uploaded."
                    ),
                }
            if force_sport is not None or fix_elevation:
                return {
                    "status": "needs_fixture",
                    "dry_run": dry_run,
                    "requested": {
                        "force_sport": force_sport,
                        "fix_elevation": fix_elevation,
                    },
                    "message": (
                        "FIT field rewrites are disabled until a vendor fixture "
                        "proves the encoding; use passthrough without transforms."
                    ),
                }

            staged, metadata = _stage_fit(path)
            await _report_progress(ctx, 1, 3, "Staged a source-preserving FIT copy")
            preview = {
                "status": "preview" if dry_run else "ready",
                "dry_run": dry_run,
                "source_unchanged": True,
                "repair_profile": "passthrough",
                "transformations": [],
                "file": metadata,
            }
            if dry_run:
                await _report_progress(ctx, 3, 3, "FIT preview complete")
                return preview

            confirmed, message = await write_confirmation.confirm_garmin_write(
                ctx,
                action="upload one validated FIT activity",
                summary={
                    "sha256": metadata["sha256"],
                    "size_bytes": metadata["size_bytes"],
                    "session": metadata.get("session", {}),
                    "transformations": [],
                },
            )
            if not confirmed:
                return write_confirmation.needs_confirmation_result(
                    preview={**preview, "status": "preview", "dry_run": True},
                    message=message,
                )

            digest = metadata["sha256"]
            await _report_progress(ctx, 2, 3, "Checking FIT upload idempotency")
            with _upload_lock:
                if digest in _uploaded_hashes:
                    response = _uploaded_hashes[digest]
                    already_uploaded = True
                else:
                    try:
                        response = garmin_client.upload_activity(str(staged))
                    except Exception as exc:
                        return {
                            **preview,
                            "status": "indeterminate_recovery_required",
                            "error": f"{type(exc).__name__}: {exc}",
                            "recovery_checklist": [
                                "Check Garmin Connect for an activity with this SHA-256/source session before retrying the upload."
                            ],
                        }
                    _uploaded_hashes[digest] = response
                    already_uploaded = False
            if already_uploaded:
                await _report_progress(ctx, 3, 3, "FIT upload already recorded")
                return {
                    **preview,
                    "status": "already_uploaded",
                    "idempotent": True,
                    "upload_result": response,
                }
            await _report_progress(ctx, 3, 3, "FIT upload complete")
            return {
                **preview,
                "status": "uploaded",
                "upload_result": response,
            }
        except Exception as exc:
            return {"status": "error", "dry_run": dry_run, "error": str(exc)}
        finally:
            if staged is not None:
                try:
                    staged.unlink()
                except OSError:
                    pass

    return app
