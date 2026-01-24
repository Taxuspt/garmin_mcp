"""Unit tests for auth_cli module."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, call

import pytest

from garmin_mcp.auth_cli import (
    get_mfa,
    get_credentials,
    authenticate,
    verify_tokens,
    main,
)


class TestGetMfa:
    """Tests for get_mfa function."""

    @patch("builtins.input", return_value="123456")
    @patch("builtins.print")
    def test_get_mfa_success(self, mock_print, mock_input):
        """Test getting MFA code from user input."""
        result = get_mfa()
        assert result == "123456"
        mock_input.assert_called_once()
        mock_print.assert_called_once()


class TestGetCredentials:
    """Tests for get_credentials function."""

    def test_both_email_sources_error(self):
        """Test error when both GARMIN_EMAIL and GARMIN_EMAIL_FILE are set."""
        with patch.dict(os.environ, {"GARMIN_EMAIL": "test@example.com", "GARMIN_EMAIL_FILE": "/path/to/file"}):
            with pytest.raises(ValueError, match="Must only provide one"):
                get_credentials()

    def test_both_password_sources_error(self):
        """Test error when both GARMIN_PASSWORD and GARMIN_PASSWORD_FILE are set."""
        with patch.dict(os.environ, {"GARMIN_PASSWORD": "secret", "GARMIN_PASSWORD_FILE": "/path/to/file"}):
            with pytest.raises(ValueError, match="Must only provide one"):
                get_credentials()

    def test_from_env_vars(self):
        """Test getting credentials from environment variables."""
        with patch.dict(os.environ, {"GARMIN_EMAIL": "test@example.com", "GARMIN_PASSWORD": "secret"}):
            email, password = get_credentials()
            assert email == "test@example.com"
            assert password == "secret"

    def test_from_files(self):
        """Test getting credentials from files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            email_file = Path(tmpdir) / "email.txt"
            password_file = Path(tmpdir) / "password.txt"
            email_file.write_text("file@example.com")
            password_file.write_text("filesecret")

            with patch.dict(os.environ, {
                "GARMIN_EMAIL_FILE": str(email_file),
                "GARMIN_PASSWORD_FILE": str(password_file)
            }):
                email, password = get_credentials()
                assert email == "file@example.com"
                assert password == "filesecret"

    @patch("builtins.input", return_value="input@example.com")
    @patch("getpass.getpass", return_value="inputsecret")
    def test_from_user_input(self, mock_getpass, mock_input):
        """Test getting credentials from user input."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GARMIN_EMAIL", None)
            os.environ.pop("GARMIN_PASSWORD", None)
            os.environ.pop("GARMIN_EMAIL_FILE", None)
            os.environ.pop("GARMIN_PASSWORD_FILE", None)

            email, password = get_credentials()

        assert email == "input@example.com"
        assert password == "inputsecret"
        mock_input.assert_called_once()
        mock_getpass.assert_called_once()

    @patch("builtins.input", return_value="")
    def test_empty_email_error(self, mock_input):
        """Test error when email is empty."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GARMIN_EMAIL", None)
            os.environ.pop("GARMIN_EMAIL_FILE", None)

            with pytest.raises(ValueError, match="Email is required"):
                get_credentials()

    @patch("builtins.input", return_value="test@example.com")
    @patch("getpass.getpass", return_value="")
    def test_empty_password_error(self, mock_getpass, mock_input):
        """Test error when password is empty."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GARMIN_PASSWORD", None)
            os.environ.pop("GARMIN_PASSWORD_FILE", None)

            with pytest.raises(ValueError, match="Password is required"):
                get_credentials()


class TestAuthenticate:
    """Tests for authenticate function."""

    @patch("garmin_mcp.auth_cli.token_exists")
    @patch("garmin_mcp.auth_cli.validate_tokens")
    def test_existing_valid_tokens_no_force(self, mock_validate, mock_exists):
        """Test that existing valid tokens are not replaced without force flag."""
        mock_exists.return_value = True
        mock_validate.return_value = (True, "")

        with tempfile.TemporaryDirectory() as tmpdir:
            result = authenticate(tmpdir, f"{tmpdir}/base64", force_reauth=False)

        assert result is True
        mock_exists.assert_called_once()
        mock_validate.assert_called_once()

    @patch("garmin_mcp.auth_cli.token_exists")
    @patch("garmin_mcp.auth_cli.validate_tokens")
    @patch("garmin_mcp.auth_cli.get_credentials")
    @patch("garmin_mcp.auth_cli.Garmin")
    def test_existing_valid_tokens_with_force(self, mock_garmin, mock_get_creds, mock_validate, mock_exists):
        """Test that force flag re-authenticates even with valid tokens."""
        mock_exists.return_value = True
        mock_validate.return_value = (True, "")
        mock_get_creds.return_value = ("test@example.com", "secret")

        mock_garmin_instance = Mock()
        mock_garmin_instance.login = Mock()
        mock_garmin_instance.garth = Mock()
        mock_garmin_instance.garth.dump = Mock()
        mock_garmin_instance.garth.dumps = Mock(return_value="base64data")
        mock_garmin_instance.get_full_name = Mock(return_value="Test User")
        mock_garmin.return_value = mock_garmin_instance

        with tempfile.TemporaryDirectory() as tmpdir:
            result = authenticate(tmpdir, f"{tmpdir}/base64", force_reauth=True)

        assert result is True
        mock_garmin_instance.login.assert_called_once()

    @patch("garmin_mcp.auth_cli.token_exists")
    @patch("garmin_mcp.auth_cli.get_credentials")
    @patch("garmin_mcp.auth_cli.Garmin")
    def test_successful_authentication(self, mock_garmin, mock_get_creds, mock_exists):
        """Test successful authentication flow."""
        mock_exists.return_value = False
        mock_get_creds.return_value = ("test@example.com", "secret")

        mock_garmin_instance = Mock()
        mock_garmin_instance.login = Mock()
        mock_garmin_instance.garth = Mock()
        mock_garmin_instance.garth.dump = Mock()
        mock_garmin_instance.garth.dumps = Mock(return_value="base64data")
        mock_garmin_instance.get_full_name = Mock(return_value="Test User")
        mock_garmin.return_value = mock_garmin_instance

        with tempfile.TemporaryDirectory() as tmpdir:
            base64_path = f"{tmpdir}/base64.txt"
            result = authenticate(tmpdir, base64_path, force_reauth=False)

            assert result is True
            mock_garmin_instance.login.assert_called_once()
            mock_garmin_instance.garth.dump.assert_called_once_with(tmpdir)
            mock_garmin_instance.get_full_name.assert_called_once()

            # Check base64 file was created (use expanded path)
            expanded_base64_path = os.path.expanduser(base64_path)
            base64_file = Path(expanded_base64_path)
            assert base64_file.exists()
            assert base64_file.read_text() == "base64data"

    @patch("garmin_mcp.auth_cli.token_exists")
    @patch("garmin_mcp.auth_cli.get_credentials")
    def test_credential_error(self, mock_get_creds, mock_exists):
        """Test handling of credential errors."""
        mock_exists.return_value = False
        mock_get_creds.side_effect = ValueError("Email is required")

        with tempfile.TemporaryDirectory() as tmpdir:
            result = authenticate(tmpdir, f"{tmpdir}/base64", force_reauth=False)

        assert result is False

    @patch("garmin_mcp.auth_cli.token_exists")
    @patch("garmin_mcp.auth_cli.get_credentials")
    @patch("garmin_mcp.auth_cli.Garmin")
    def test_authentication_error(self, mock_garmin, mock_get_creds, mock_exists):
        """Test handling of authentication errors."""
        from garminconnect import GarminConnectAuthenticationError

        mock_exists.return_value = False
        mock_get_creds.return_value = ("test@example.com", "wrongpassword")
        mock_garmin.return_value.login.side_effect = GarminConnectAuthenticationError("Invalid credentials")

        with tempfile.TemporaryDirectory() as tmpdir:
            result = authenticate(tmpdir, f"{tmpdir}/base64", force_reauth=False)

        assert result is False


class TestVerifyTokens:
    """Tests for verify_tokens function."""

    @patch("garmin_mcp.auth_cli.get_token_info")
    def test_verify_nonexistent_tokens(self, mock_get_info):
        """Test verifying tokens that don't exist."""
        mock_get_info.return_value = {
            "path": "/test/path",
            "expanded_path": "/test/path",
            "exists": False,
            "valid": False,
            "error": ""
        }

        result = verify_tokens("/test/path")
        assert result is False

    @patch("garmin_mcp.auth_cli.get_token_info")
    def test_verify_valid_tokens(self, mock_get_info):
        """Test verifying valid tokens."""
        mock_get_info.return_value = {
            "path": "/test/path",
            "expanded_path": "/test/path",
            "exists": True,
            "valid": True,
            "error": ""
        }

        result = verify_tokens("/test/path")
        assert result is True

    @patch("garmin_mcp.auth_cli.get_token_info")
    def test_verify_invalid_tokens(self, mock_get_info):
        """Test verifying invalid tokens."""
        mock_get_info.return_value = {
            "path": "/test/path",
            "expanded_path": "/test/path",
            "exists": True,
            "valid": False,
            "error": "Token expired"
        }

        result = verify_tokens("/test/path")
        assert result is False


class TestMain:
    """Tests for main function."""

    @patch("sys.argv", ["garmin-mcp-auth", "--verify"])
    @patch("garmin_mcp.auth_cli.verify_tokens")
    def test_main_verify_mode(self, mock_verify):
        """Test main function in verify mode."""
        mock_verify.return_value = True

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        mock_verify.assert_called_once()

    @patch("sys.argv", ["garmin-mcp-auth"])
    @patch("garmin_mcp.auth_cli.authenticate")
    def test_main_authenticate_mode_success(self, mock_authenticate):
        """Test main function in authenticate mode (success)."""
        mock_authenticate.return_value = True

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        mock_authenticate.assert_called_once()

    @patch("sys.argv", ["garmin-mcp-auth"])
    @patch("garmin_mcp.auth_cli.authenticate")
    def test_main_authenticate_mode_failure(self, mock_authenticate):
        """Test main function in authenticate mode (failure)."""
        mock_authenticate.return_value = False

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        mock_authenticate.assert_called_once()

    @patch("sys.argv", ["garmin-mcp-auth", "--force-reauth"])
    @patch("garmin_mcp.auth_cli.authenticate")
    def test_main_force_reauth(self, mock_authenticate):
        """Test main function with force-reauth flag."""
        mock_authenticate.return_value = True

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        # Check that force_reauth=True was passed
        assert mock_authenticate.call_args[0][2] is True

    @patch("sys.argv", ["garmin-mcp-auth", "--token-path", "/custom/path"])
    @patch("garmin_mcp.auth_cli.authenticate")
    def test_main_custom_token_path(self, mock_authenticate):
        """Test main function with custom token path."""
        mock_authenticate.return_value = True

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        # Check that custom path was used
        assert "/custom/path" in mock_authenticate.call_args[0][0]
