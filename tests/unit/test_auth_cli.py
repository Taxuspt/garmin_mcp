"""Unit tests for auth_cli module."""

import os
import sys
import tempfile
import base64
from pathlib import Path
from unittest.mock import Mock, patch, call, mock_open

import pytest

from garmin_mcp.auth_cli import (
    get_mfa,
    get_credentials,
    authenticate,
    verify_tokens,
    main,
    _secure_token_dir,
    _verify_saved_tokens,
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
    @patch("garmin_mcp.auth_cli._verify_saved_tokens", return_value=(True, "Test User"))
    @patch("garmin_mcp.auth_cli.os.chmod")
    def test_existing_valid_tokens_with_force(self, mock_chmod, mock_verify, mock_garmin, mock_get_creds, mock_validate, mock_exists):
        """Test that force flag re-authenticates even with valid tokens."""
        mock_exists.return_value = True
        mock_validate.return_value = (True, "")
        mock_get_creds.return_value = ("test@example.com", "secret")

        mock_garmin_instance = Mock()
        mock_garmin_instance.client = Mock()
        mock_garmin_instance.client.login.return_value = (None, None)
        mock_garmin.return_value = mock_garmin_instance

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("builtins.open", mock_open(read_data="{}")):
                result = authenticate(tmpdir, f"{tmpdir}/base64", force_reauth=True)

        assert result is True
        mock_garmin_instance.client.login.assert_called_once_with(
            "test@example.com", "secret", return_on_mfa=True
        )

    @patch("garmin_mcp.auth_cli.token_exists")
    @patch("garmin_mcp.auth_cli.get_credentials")
    @patch("garmin_mcp.auth_cli.Garmin")
    @patch("garmin_mcp.auth_cli._verify_saved_tokens", return_value=(True, "Test User"))
    @patch("garmin_mcp.auth_cli.os.chmod")
    def test_successful_authentication(self, mock_chmod, mock_verify, mock_garmin, mock_get_creds, mock_exists):
        """Test successful authentication flow."""
        mock_exists.return_value = False
        mock_get_creds.return_value = ("test@example.com", "secret")

        mock_garmin_instance = Mock()
        mock_garmin_instance.client = Mock()
        mock_garmin_instance.client.login.return_value = (None, None)
        mock_garmin.return_value = mock_garmin_instance

        token_data = '{"token": "test"}'
        expected_b64 = base64.b64encode(token_data.encode()).decode()
        m = mock_open(read_data=token_data)

        with tempfile.TemporaryDirectory() as tmpdir:
            base64_path = f"{tmpdir}/base64.txt"
            with patch("builtins.open", m):
                result = authenticate(tmpdir, base64_path, force_reauth=False)

        assert result is True
        mock_garmin_instance.client.login.assert_called_once_with(
            "test@example.com", "secret", return_on_mfa=True
        )
        mock_garmin_instance.client.dump.assert_called_once_with(tmpdir)
        # Tokens are verified by an independent token-based login
        mock_verify.assert_called_once()
        # Verify base64-encoded token data was written to the base64 file
        m().write.assert_called_once_with(expected_b64)
        # Verify restrictive permissions were applied to the base64 file
        mock_chmod.assert_any_call(os.path.expanduser(base64_path), 0o600)

    @patch("garmin_mcp.auth_cli.token_exists", return_value=False)
    @patch(
        "garmin_mcp.auth_cli.get_credentials",
        return_value=("test@example.com", "secret"),
    )
    @patch("garmin_mcp.auth_cli.Garmin")
    @patch(
        "garmin_mcp.auth_cli._verify_saved_tokens",
        return_value=(True, "Test User"),
    )
    @patch("garmin_mcp.auth_cli.os.chmod")
    def test_authentication_resolves_home_in_token_paths(
        self,
        mock_chmod,
        mock_verify,
        mock_garmin,
        _mock_get_creds,
        mock_exists,
        monkeypatch,
        tmp_path,
    ):
        """The CLI writes and verifies tokens at the same resolved paths."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        mock_garmin.return_value.client.login.return_value = (None, None)

        with patch("builtins.open", mock_open(read_data="{}")):
            result = authenticate(
                "${HOME}/.garminconnect",
                "${HOME}/.garminconnect_base64",
            )

        expected_token_path = str(tmp_path / ".garminconnect")
        expected_base64_path = str(tmp_path / ".garminconnect_base64")
        assert result is True
        mock_exists.assert_called_once_with(expected_token_path)
        mock_garmin.return_value.client.dump.assert_called_once_with(
            expected_token_path
        )
        mock_verify.assert_called_once_with(expected_token_path, False)
        mock_chmod.assert_any_call(expected_base64_path, 0o600)

    @patch("garmin_mcp.auth_cli.token_exists")
    @patch("garmin_mcp.auth_cli.get_credentials")
    @patch("garmin_mcp.auth_cli.Garmin")
    @patch("garmin_mcp.auth_cli._verify_saved_tokens")
    @patch("garmin_mcp.auth_cli.os.chmod")
    def test_unverifiable_tokens_fail(self, mock_chmod, mock_verify, mock_garmin, mock_get_creds, mock_exists):
        """Login that produces unauthenticated tokens must fail, not report success."""
        mock_exists.return_value = False
        mock_get_creds.return_value = ("test@example.com", "secret")
        # Login "succeeds" but the saved tokens don't actually authenticate.
        mock_verify.return_value = (False, "session is not authenticated (no profile returned)")

        mock_garmin_instance = Mock()
        mock_garmin_instance.client = Mock()
        mock_garmin_instance.client.login.return_value = (None, None)
        mock_garmin.return_value = mock_garmin_instance

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("builtins.open", mock_open(read_data="{}")):
                result = authenticate(tmpdir, f"{tmpdir}/base64", force_reauth=False)

        assert result is False

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
        mock_garmin.return_value.client.login.side_effect = GarminConnectAuthenticationError("Invalid credentials")

        with tempfile.TemporaryDirectory() as tmpdir:
            result = authenticate(tmpdir, f"{tmpdir}/base64", force_reauth=False)

        assert result is False

    @patch("garmin_mcp.auth_cli.token_exists", return_value=False)
    @patch("garmin_mcp.auth_cli.get_credentials", return_value=("test@example.com", "secret"))
    @patch("garmin_mcp.auth_cli.Garmin")
    @patch("garmin_mcp.auth_cli.os.chmod")
    def test_profile_without_full_name_succeeds(self, mock_chmod, mock_garmin, mock_get_creds, mock_exists, capsys):
        """Valid tokens for a profile with no full name must not be reported as failed."""
        # Mock both entry points: the credential exchange and the token login
        # used to verify the saved tokens.
        mock_garmin.return_value.login.return_value = (None, None)
        mock_garmin.return_value.client.login.return_value = (None, None)
        mock_garmin.return_value.get_full_name.return_value = ""
        mock_garmin.return_value.display_name = "abc123"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("builtins.open", mock_open(read_data="{}")):
                result = authenticate(tmpdir, f"{tmpdir}/base64", force_reauth=False)

        assert result is True
        assert "Logged in as: abc123" in capsys.readouterr().out


class TestVerifySavedTokens:
    """Tests for _verify_saved_tokens, with Garmin mocked."""

    @patch("garmin_mcp.auth_cli.Garmin")
    def test_full_name_is_reported(self, mock_garmin):
        mock_garmin.return_value.get_full_name.return_value = "Jane Doe"

        assert _verify_saved_tokens("/tokens") == (True, "Jane Doe")
        mock_garmin.return_value.login.assert_called_once_with("/tokens")

    @pytest.mark.parametrize("full_name", ["", None])
    @patch("garmin_mcp.auth_cli.Garmin")
    def test_missing_full_name_falls_back_to_display_name(self, mock_garmin, full_name):
        # login() raises unless the profile loads; the library then sets
        # full_name to "" (or None) for profiles without a name.
        mock_garmin.return_value.get_full_name.return_value = full_name
        mock_garmin.return_value.display_name = "abc123"

        assert _verify_saved_tokens("/tokens") == (True, "abc123")

    @patch("garmin_mcp.auth_cli.Garmin")
    def test_no_name_at_all_still_verifies(self, mock_garmin):
        mock_garmin.return_value.get_full_name.return_value = ""
        mock_garmin.return_value.display_name = None

        assert _verify_saved_tokens("/tokens") == (True, "your Garmin account")

    @patch("garmin_mcp.auth_cli.Garmin")
    def test_login_failure_is_reported(self, mock_garmin):
        from garminconnect import GarminConnectAuthenticationError

        mock_garmin.return_value.login.side_effect = GarminConnectAuthenticationError(
            "Failed to retrieve social profile"
        )

        assert _verify_saved_tokens("/tokens") == (False, "Failed to retrieve social profile")


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

    @patch("sys.argv", ["garmin-mcp-auth", "--is-cn"])
    @patch("garmin_mcp.auth_cli.authenticate")
    def test_main_is_cn_flag(self, mock_authenticate):
        """Test main function with --is-cn flag."""
        mock_authenticate.return_value = True

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        # Check that is_cn=True was passed
        assert mock_authenticate.call_args[0][3] is True

    @patch("sys.argv", ["garmin-mcp-auth"])
    @patch("garmin_mcp.auth_cli.authenticate")
    def test_main_is_cn_env_var(self, mock_authenticate):
        """Test that GARMIN_IS_CN env var is used when --is-cn flag is not set."""
        mock_authenticate.return_value = True

        with patch.dict(os.environ, {"GARMIN_IS_CN": "true"}):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # Check that is_cn=True was passed via env var
        assert mock_authenticate.call_args[0][3] is True

    @patch("sys.argv", ["garmin-mcp-auth"])
    @patch("garmin_mcp.auth_cli.authenticate")
    def test_main_is_cn_default_false(self, mock_authenticate):
        """Test that is_cn defaults to False when neither flag nor env var is set."""
        mock_authenticate.return_value = True

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GARMIN_IS_CN", None)
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # Check that is_cn=False was passed
        assert mock_authenticate.call_args[0][3] is False


class TestAuthenticateIsCn:
    """Tests for is_cn parameter in authenticate function."""

    @patch("garmin_mcp.auth_cli.token_exists")
    @patch("garmin_mcp.auth_cli.get_credentials")
    @patch("garmin_mcp.auth_cli.Garmin")
    @patch("garmin_mcp.auth_cli._verify_saved_tokens", return_value=(True, "Test User"))
    @patch("garmin_mcp.auth_cli.os.chmod")
    def test_authenticate_passes_is_cn_true(self, mock_chmod, mock_verify, mock_garmin, mock_get_creds, mock_exists):
        """Test that is_cn=True is passed to Garmin constructor."""
        mock_exists.return_value = False
        mock_get_creds.return_value = ("test@example.com", "secret")

        mock_garmin_instance = Mock()
        mock_garmin_instance.client = Mock()
        mock_garmin_instance.client.login.return_value = (None, None)
        mock_garmin.return_value = mock_garmin_instance

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("builtins.open", mock_open(read_data="{}")):
                result = authenticate(tmpdir, f"{tmpdir}/base64", force_reauth=False, is_cn=True)

        assert result is True
        # Verify Garmin was called with is_cn=True (verification login is mocked out)
        mock_garmin.assert_called_once_with(
            email="test@example.com",
            password="secret",
            is_cn=True,
            prompt_mfa=get_mfa,
            return_on_mfa=True,
        )

    @patch("garmin_mcp.auth_cli.token_exists")
    @patch("garmin_mcp.auth_cli.get_credentials")
    @patch("garmin_mcp.auth_cli.Garmin")
    @patch("garmin_mcp.auth_cli._verify_saved_tokens", return_value=(True, "Test User"))
    @patch("garmin_mcp.auth_cli.os.chmod")
    def test_authenticate_passes_is_cn_false(self, mock_chmod, mock_verify, mock_garmin, mock_get_creds, mock_exists):
        """Test that is_cn=False is passed to Garmin constructor by default."""
        mock_exists.return_value = False
        mock_get_creds.return_value = ("test@example.com", "secret")

        mock_garmin_instance = Mock()
        mock_garmin_instance.client = Mock()
        mock_garmin_instance.client.login.return_value = (None, None)
        mock_garmin.return_value = mock_garmin_instance

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("builtins.open", mock_open(read_data="{}")):
                result = authenticate(tmpdir, f"{tmpdir}/base64", force_reauth=False)

        assert result is True
        # Verify Garmin was called with is_cn=False (verification login is mocked out)
        mock_garmin.assert_called_once_with(
            email="test@example.com",
            password="secret",
            is_cn=False,
            prompt_mfa=get_mfa,
            return_on_mfa=True,
        )


class TestSecureTokenDir:
    """Tests for _secure_token_dir: verifies owner-only permissions are applied."""

    def test_directory_gets_700_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _secure_token_dir(tmpdir)
            assert oct(os.stat(tmpdir).st_mode)[-3:] == "700"

    def test_files_inside_get_600_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = os.path.join(tmpdir, "garmin_tokens.json")
            with open(token_file, "w") as f:
                f.write("{}")
            _secure_token_dir(tmpdir)
            assert oct(os.stat(token_file).st_mode)[-3:] == "600"

    def test_multiple_files_all_get_600_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ("garmin_tokens.json", "oauth1_tokens.json"):
                with open(os.path.join(tmpdir, name), "w") as f:
                    f.write("{}")
            _secure_token_dir(tmpdir)
            for name in ("garmin_tokens.json", "oauth1_tokens.json"):
                path = os.path.join(tmpdir, name)
                assert oct(os.stat(path).st_mode)[-3:] == "600", f"{name} should be 600"

    def test_empty_directory_only_sets_dir_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _secure_token_dir(tmpdir)
            assert oct(os.stat(tmpdir).st_mode)[-3:] == "700"


class TestDiagnoseTokens:
    """--diagnose prints the resolved paths and the state of the store."""

    @staticmethod
    def _write_tokens(store):
        store.mkdir(parents=True, exist_ok=True)
        (store / "garmin_tokens.json").write_text(
            '{"di_token": "a", "di_refresh_token": "b", "di_client_id": "c"}'
        )

    def test_missing_directory_fails_without_network(self, monkeypatch, tmp_path, capsys):
        from garmin_mcp.auth_cli import diagnose_tokens

        missing = tmp_path / "nope"
        monkeypatch.setenv("GARMINTOKENS", str(missing))
        monkeypatch.setenv("GARMINTOKENS_BASE64", str(tmp_path / "b64"))
        with patch("garmin_mcp.auth_cli.validate_tokens") as mock_validate:
            result = diagnose_tokens(None, is_cn=False)

        out = capsys.readouterr().out
        assert result is False
        mock_validate.assert_not_called()
        assert f"GARMINTOKENS         : {missing}" in out
        assert f"Token directory      : {missing}" in out
        assert "state              : MISSING" in out
        assert "garmin_tokens.json   : MISSING" in out
        assert "the server does not read it" in out

    def test_valid_tokens_report_success(self, monkeypatch, tmp_path, capsys):
        from garmin_mcp.auth_cli import diagnose_tokens

        store = tmp_path / "tokens"
        self._write_tokens(store)
        monkeypatch.delenv("GARMINTOKENS", raising=False)
        monkeypatch.setenv("GARMINTOKENS_BASE64", str(tmp_path / "b64"))
        with patch("garmin_mcp.auth_cli.validate_tokens", return_value=(True, "")) as mock_validate:
            result = diagnose_tokens(str(store), is_cn=True)

        out = capsys.readouterr().out
        assert result is True
        mock_validate.assert_called_once_with(str(store), is_cn=True)
        assert "garmin_tokens.json   : present" in out
        assert "Region               : garmin.cn" in out
        assert "✓ Tokens authenticate" in out

    def test_rejected_tokens_report_reauth(self, monkeypatch, tmp_path, capsys):
        from garmin_mcp.auth_cli import diagnose_tokens

        store = tmp_path / "tokens"
        self._write_tokens(store)
        monkeypatch.setenv("GARMINTOKENS", str(tmp_path / "elsewhere"))
        monkeypatch.setenv("GARMINTOKENS_BASE64", str(tmp_path / "b64"))
        with patch(
            "garmin_mcp.auth_cli.validate_tokens",
            return_value=(False, "Tokens expired or invalid"),
        ):
            result = diagnose_tokens(str(store), is_cn=False)

        out = capsys.readouterr().out
        assert result is False
        assert "points elsewhere" in out
        assert "do not authenticate: Tokens expired or invalid" in out
        assert "--force-reauth" in out

    def test_corrupt_token_file_is_reported(self, monkeypatch, tmp_path, capsys):
        from garmin_mcp.auth_cli import diagnose_tokens

        store = tmp_path / "tokens"
        store.mkdir()
        (store / "garmin_tokens.json").write_text("{nope")
        (store / "oauth2_token.json").write_text("{}")
        monkeypatch.setenv("GARMINTOKENS_BASE64", str(tmp_path / "b64"))
        with patch("garmin_mcp.auth_cli.validate_tokens") as mock_validate:
            result = diagnose_tokens(str(store), is_cn=False)

        out = capsys.readouterr().out
        assert result is False
        mock_validate.assert_not_called()
        assert "garmin_tokens.json   : present, but unreadable or not JSON" in out
        assert "Other files          : oauth2_token.json" in out

    @patch("sys.argv", ["garmin-mcp-auth", "--diagnose"])
    @patch("garmin_mcp.auth_cli.diagnose_tokens", return_value=True)
    @patch("garmin_mcp.auth_cli.verify_tokens")
    @patch("garmin_mcp.auth_cli.authenticate")
    def test_main_diagnose_mode(self, mock_authenticate, mock_verify, mock_diagnose):
        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        mock_diagnose.assert_called_once_with(None, False)
        mock_verify.assert_not_called()
        mock_authenticate.assert_not_called()

    @patch("sys.argv", ["garmin-mcp-auth", "--diagnose", "--token-path", "/custom/path", "--is-cn"])
    @patch("garmin_mcp.auth_cli.diagnose_tokens", return_value=False)
    def test_main_diagnose_failure_exit_code(self, mock_diagnose):
        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        mock_diagnose.assert_called_once_with("/custom/path", True)

    @patch("sys.argv", ["garmin-mcp-auth", "--verify", "--is-cn"])
    @patch("garmin_mcp.auth_cli.verify_tokens", return_value=True)
    def test_main_verify_passes_region(self, mock_verify):
        with pytest.raises(SystemExit):
            main()

        assert mock_verify.call_args[0][1] is True
