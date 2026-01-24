"""Unit tests for token_utils module."""

import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

from garmin_mcp.token_utils import (
    get_token_path,
    get_token_base64_path,
    token_exists,
    validate_tokens,
    remove_tokens,
    get_token_info,
)


class TestGetTokenPath:
    """Tests for get_token_path function."""

    def test_default_path(self):
        """Test that default path is returned when env var not set."""
        with patch.dict(os.environ, {}, clear=False):
            # Remove GARMINTOKENS if set
            os.environ.pop("GARMINTOKENS", None)
            assert get_token_path() == "~/.garminconnect"

    def test_env_var_path(self):
        """Test that environment variable path is used when set."""
        with patch.dict(os.environ, {"GARMINTOKENS": "/custom/path"}):
            assert get_token_path() == "/custom/path"


class TestGetTokenBase64Path:
    """Tests for get_token_base64_path function."""

    def test_default_path(self):
        """Test that default path is returned when env var not set."""
        with patch.dict(os.environ, {}, clear=False):
            # Remove GARMINTOKENS_BASE64 if set
            os.environ.pop("GARMINTOKENS_BASE64", None)
            assert get_token_base64_path() == "~/.garminconnect_base64"

    def test_env_var_path(self):
        """Test that environment variable path is used when set."""
        with patch.dict(os.environ, {"GARMINTOKENS_BASE64": "/custom/path.b64"}):
            assert get_token_base64_path() == "/custom/path.b64"


class TestTokenExists:
    """Tests for token_exists function."""

    def test_token_directory_exists(self):
        """Test that existing token directory is detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            assert token_exists(tmpdir) is True

    def test_token_file_exists(self):
        """Test that existing token file is detected."""
        with tempfile.NamedTemporaryFile() as tmpfile:
            assert token_exists(tmpfile.name) is True

    def test_token_not_exists(self):
        """Test that non-existent token path is detected."""
        assert token_exists("/nonexistent/path") is False

    def test_uses_default_path(self):
        """Test that default path is used when none provided."""
        with patch("garmin_mcp.token_utils.get_token_path", return_value="/test/path"):
            with patch("pathlib.Path.exists", return_value=True):
                assert token_exists() is True


class TestValidateTokens:
    """Tests for validate_tokens function."""

    def test_tokens_not_exist(self):
        """Test validation when tokens don't exist."""
        is_valid, error = validate_tokens("/nonexistent/path")
        assert is_valid is False
        assert "not found" in error.lower()

    @patch("garmin_mcp.token_utils.token_exists")
    @patch("garmin_mcp.token_utils.Garmin")
    def test_valid_tokens(self, mock_garmin, mock_exists):
        """Test validation with valid tokens."""
        mock_exists.return_value = True
        mock_garmin_instance = Mock()
        mock_garmin_instance.login = Mock()
        mock_garmin_instance.get_user_summary = Mock(return_value={"displayName": "Test"})
        mock_garmin.return_value = mock_garmin_instance

        with tempfile.TemporaryDirectory() as tmpdir:
            is_valid, error = validate_tokens(tmpdir)

        assert is_valid is True
        assert error == ""
        mock_garmin_instance.login.assert_called_once()
        mock_garmin_instance.get_user_summary.assert_called_once()

    @patch("garmin_mcp.token_utils.token_exists")
    @patch("garmin_mcp.token_utils.Garmin")
    def test_invalid_tokens_file_not_found(self, mock_garmin, mock_exists):
        """Test validation when token files not found."""
        mock_exists.return_value = True
        mock_garmin.return_value.login.side_effect = FileNotFoundError("Token files missing")

        with tempfile.TemporaryDirectory() as tmpdir:
            is_valid, error = validate_tokens(tmpdir)

        assert is_valid is False
        assert "not found in" in error.lower()

    @patch("garmin_mcp.token_utils.token_exists")
    @patch("garmin_mcp.token_utils.Garmin")
    def test_invalid_tokens_auth_failed(self, mock_garmin, mock_exists):
        """Test validation when authentication fails."""
        from garth.exc import GarthHTTPError

        mock_exists.return_value = True
        mock_garmin_instance = Mock()
        # Create a proper GarthHTTPError exception with required arguments
        auth_error = GarthHTTPError("Auth failed", error="authentication_failed")
        mock_garmin_instance.login = Mock(side_effect=auth_error)
        mock_garmin.return_value = mock_garmin_instance

        with tempfile.TemporaryDirectory() as tmpdir:
            is_valid, error = validate_tokens(tmpdir)

        assert is_valid is False
        assert "authentication" in error.lower()

    @patch("garmin_mcp.token_utils.token_exists")
    @patch("garmin_mcp.token_utils.Garmin")
    def test_invalid_tokens_api_error(self, mock_garmin, mock_exists):
        """Test validation when API call fails."""
        mock_exists.return_value = True
        mock_garmin_instance = Mock()
        mock_garmin_instance.login = Mock()
        mock_garmin_instance.get_user_summary = Mock(side_effect=Exception("API error"))
        mock_garmin.return_value = mock_garmin_instance

        with tempfile.TemporaryDirectory() as tmpdir:
            is_valid, error = validate_tokens(tmpdir)

        assert is_valid is False
        assert "authentication failed" in error.lower() or "failed" in error.lower()


class TestRemoveTokens:
    """Tests for remove_tokens function."""

    def test_remove_token_directory(self):
        """Test removing token directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_dir = Path(tmpdir) / "tokens"
            token_dir.mkdir()
            token_file = token_dir / "test.txt"
            token_file.write_text("test")

            assert token_dir.exists()
            remove_tokens(str(token_dir), "/nonexistent/base64")
            assert not token_dir.exists()

    def test_remove_token_file(self):
        """Test removing token file."""
        with tempfile.NamedTemporaryFile(delete=False) as tmpfile:
            tmpfile.write(b"test")
            tmpfile_path = tmpfile.name

        try:
            assert Path(tmpfile_path).exists()
            remove_tokens(tmpfile_path, "/nonexistent/base64")
            assert not Path(tmpfile_path).exists()
        finally:
            # Cleanup in case test fails
            if Path(tmpfile_path).exists():
                Path(tmpfile_path).unlink()

    def test_remove_both_paths(self):
        """Test removing both token directory and base64 file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_dir = Path(tmpdir) / "tokens"
            token_dir.mkdir()
            base64_file = Path(tmpdir) / "tokens.b64"
            base64_file.write_text("encoded")

            assert token_dir.exists()
            assert base64_file.exists()

            remove_tokens(str(token_dir), str(base64_file))

            assert not token_dir.exists()
            assert not base64_file.exists()

    def test_remove_nonexistent_paths(self):
        """Test removing paths that don't exist (should not error)."""
        # Should not raise any exceptions
        remove_tokens("/nonexistent/path1", "/nonexistent/path2")


class TestGetTokenInfo:
    """Tests for get_token_info function."""

    def test_token_info_not_exists(self):
        """Test getting info for non-existent tokens."""
        info = get_token_info("/nonexistent/path")

        assert info["path"] == "/nonexistent/path"
        assert info["expanded_path"] == "/nonexistent/path"
        assert info["exists"] is False
        assert info["valid"] is False
        assert info["error"] == ""

    @patch("garmin_mcp.token_utils.token_exists")
    @patch("garmin_mcp.token_utils.validate_tokens")
    def test_token_info_exists_valid(self, mock_validate, mock_exists):
        """Test getting info for existing valid tokens."""
        mock_exists.return_value = True
        mock_validate.return_value = (True, "")

        with tempfile.TemporaryDirectory() as tmpdir:
            info = get_token_info(tmpdir)

        assert info["path"] == tmpdir
        assert info["expanded_path"] == tmpdir
        assert info["exists"] is True
        assert info["valid"] is True
        assert info["error"] == ""

    @patch("garmin_mcp.token_utils.token_exists")
    @patch("garmin_mcp.token_utils.validate_tokens")
    def test_token_info_exists_invalid(self, mock_validate, mock_exists):
        """Test getting info for existing invalid tokens."""
        mock_exists.return_value = True
        mock_validate.return_value = (False, "Token expired")

        with tempfile.TemporaryDirectory() as tmpdir:
            info = get_token_info(tmpdir)

        assert info["path"] == tmpdir
        assert info["exists"] is True
        assert info["valid"] is False
        assert info["error"] == "Token expired"

    @patch("garmin_mcp.token_utils.get_token_path")
    @patch("garmin_mcp.token_utils.token_exists")
    def test_uses_default_path(self, mock_exists, mock_get_path):
        """Test that default path is used when none provided."""
        mock_get_path.return_value = "~/.garminconnect"
        mock_exists.return_value = False

        info = get_token_info()

        assert info["path"] == "~/.garminconnect"
        mock_get_path.assert_called_once()
