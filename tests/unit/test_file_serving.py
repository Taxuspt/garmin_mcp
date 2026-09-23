"""Unit tests for the capability-token file store (file_serving)."""

import os
import time

import pytest

from garmin_mcp import file_serving


@pytest.fixture(autouse=True)
def _clear_tokens():
    """Each test starts with an empty token table."""
    file_serving._TOKENS.clear()
    yield
    file_serving._TOKENS.clear()


class TestTtlSeconds:
    def test_default_is_900(self, monkeypatch):
        monkeypatch.delenv("GARMIN_MCP_FILE_TOKEN_TTL_SECONDS", raising=False)
        assert file_serving.ttl_seconds() == 900

    def test_reads_env_var(self, monkeypatch):
        monkeypatch.setenv("GARMIN_MCP_FILE_TOKEN_TTL_SECONDS", "60")
        assert file_serving.ttl_seconds() == 60


class TestIssueAndResolveToken:
    def test_resolve_returns_path_for_valid_token(self, tmp_path):
        f = tmp_path / "123.fit"
        f.write_bytes(b"data")

        token = file_serving.issue_token(str(f))

        assert file_serving._TOKEN_RE.match(token)
        assert file_serving.resolve_token(token) == str(f)

    def test_resolve_returns_none_for_unknown_token(self):
        assert file_serving.resolve_token("does-not-exist") is None

    @pytest.mark.parametrize("bad_token", [
        "../etc/passwd",
        "a/b",
        "a\\b",
        "",
        "..",
    ])
    def test_resolve_rejects_traversal_shaped_tokens(self, bad_token, tmp_path):
        f = tmp_path / "1.fit"
        f.write_bytes(b"data")
        file_serving.issue_token(str(f))  # a valid token also exists in the table

        assert file_serving.resolve_token(bad_token) is None


class TestExpiry:
    def test_resolve_returns_none_after_expiry(self, tmp_path, monkeypatch):
        f = tmp_path / "1.fit"
        f.write_bytes(b"data")
        monkeypatch.setenv("GARMIN_MCP_FILE_TOKEN_TTL_SECONDS", "10")
        token = file_serving.issue_token(str(f))

        future = time.time() + 11
        monkeypatch.setattr(file_serving.time, "time", lambda: future)

        assert file_serving.resolve_token(token) is None

    def test_expired_file_is_deleted_from_disk(self, tmp_path, monkeypatch):
        f = tmp_path / "1.fit"
        f.write_bytes(b"data")
        monkeypatch.setenv("GARMIN_MCP_FILE_TOKEN_TTL_SECONDS", "10")
        token = file_serving.issue_token(str(f))
        assert os.path.isfile(str(f))

        future = time.time() + 11
        monkeypatch.setattr(file_serving.time, "time", lambda: future)

        # Purge is lazy: trigger it via a resolve/issue call.
        file_serving.resolve_token(token)

        assert not os.path.isfile(str(f))
