from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import fit_upload


@pytest.mark.asyncio
async def test_upload_fit_preview_does_not_upload(tmp_path, mock_garmin_client):
    source = tmp_path / "ride.fit"
    source.write_bytes(b"FIT")
    fit_upload.configure(mock_garmin_client)
    app = FastMCP("fit upload")
    fit_upload.register_tools(app)
    metadata = {
        "path": str(source),
        "size_bytes": 3,
        "sha256": "abc",
        "fit_valid": True,
        "record_count": 1,
        "session": {"sport": "cycling"},
    }
    staged = tmp_path / "staged.fit"
    staged.write_bytes(b"FIT")
    with patch("garmin_mcp.fit_upload._stage_fit", return_value=(staged, metadata)):
        _content, payload = await app.call_tool("upload_fit", {"path": str(source)})
    assert payload["status"] == "preview"
    assert payload["source_unchanged"] is True
    mock_garmin_client.upload_activity.assert_not_called()


@pytest.mark.asyncio
async def test_upload_fit_transform_without_fixture_fails_closed(
    tmp_path, mock_garmin_client
):
    source = tmp_path / "ride.fit"
    source.write_bytes(b"FIT")
    fit_upload.configure(mock_garmin_client)
    app = FastMCP("fit upload")
    fit_upload.register_tools(app)
    _content, payload = await app.call_tool(
        "upload_fit", {"path": str(source), "fix_elevation": True}
    )
    assert payload["status"] == "needs_fixture"
    mock_garmin_client.upload_activity.assert_not_called()


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_remote_transport_requires_explicit_upload_allowlist(
    tmp_path, monkeypatch, transport
):
    source = tmp_path / "ride.fit"
    source.write_bytes(b"FIT")
    monkeypatch.setenv("GARMIN_MCP_TRANSPORT", transport)
    monkeypatch.delenv("GARMIN_ALLOWED_UPLOAD_DIRS", raising=False)

    with pytest.raises(ValueError, match="GARMIN_ALLOWED_UPLOAD_DIRS must be configured"):
        fit_upload._resolve_source(str(source))


def test_stdio_keeps_local_unrestricted_path_compatibility(tmp_path, monkeypatch):
    source = tmp_path / "ride.fit"
    source.write_bytes(b"FIT")
    monkeypatch.setenv("GARMIN_MCP_TRANSPORT", "stdio")
    monkeypatch.delenv("GARMIN_ALLOWED_UPLOAD_DIRS", raising=False)

    assert fit_upload._resolve_source(str(source)) == source.resolve()


def test_remote_transport_accepts_path_inside_explicit_allowlist(
    tmp_path, monkeypatch
):
    source = tmp_path / "ride.fit"
    source.write_bytes(b"FIT")
    monkeypatch.setenv("GARMIN_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("GARMIN_ALLOWED_UPLOAD_DIRS", str(tmp_path))

    assert fit_upload._resolve_source(str(source)) == source.resolve()


@pytest.mark.asyncio
async def test_upload_fit_commit_requires_confirmation(
    tmp_path, mock_garmin_client, monkeypatch
):
    source = tmp_path / "ride.fit"
    source.write_bytes(b"FIT")
    staged = tmp_path / "staged.fit"
    staged.write_bytes(b"FIT")
    metadata = {
        "path": str(source),
        "size_bytes": 3,
        "sha256": "confirmation-digest",
        "fit_valid": True,
        "record_count": 1,
        "session": {"sport": "cycling"},
    }
    monkeypatch.setattr(
        fit_upload.write_confirmation,
        "confirm_garmin_write",
        AsyncMock(return_value=(False, "user declined")),
    )
    fit_upload.configure(mock_garmin_client)
    app = fit_upload.register_tools(FastMCP("fit upload"))

    with patch("garmin_mcp.fit_upload._stage_fit", return_value=(staged, metadata)):
        _content, payload = await app.call_tool(
            "upload_fit", {"path": str(source), "dry_run": False}
        )

    assert payload["status"] == "needs_confirmation"
    assert payload["write_performed"] is False
    mock_garmin_client.upload_activity.assert_not_called()


@pytest.mark.asyncio
async def test_upload_fit_lost_response_is_indeterminate(
    tmp_path, mock_garmin_client, monkeypatch
):
    source = tmp_path / "ride.fit"
    source.write_bytes(b"FIT")
    staged = tmp_path / "staged.fit"
    staged.write_bytes(b"FIT")
    metadata = {
        "path": str(source),
        "size_bytes": 3,
        "sha256": "lost-response-digest",
        "fit_valid": True,
        "record_count": 1,
        "session": {"sport": "cycling"},
    }
    mock_garmin_client.upload_activity.side_effect = RuntimeError("lost response")
    monkeypatch.setattr(
        fit_upload.write_confirmation,
        "confirm_garmin_write",
        AsyncMock(return_value=(True, None)),
    )
    fit_upload.configure(mock_garmin_client)
    app = fit_upload.register_tools(FastMCP("fit upload"))

    with patch("garmin_mcp.fit_upload._stage_fit", return_value=(staged, metadata)):
        _content, payload = await app.call_tool(
            "upload_fit", {"path": str(source), "dry_run": False}
        )

    assert payload["status"] == "indeterminate_recovery_required"
    assert payload["source_unchanged"] is True
    assert payload["recovery_checklist"]
    assert source.read_bytes() == b"FIT"
