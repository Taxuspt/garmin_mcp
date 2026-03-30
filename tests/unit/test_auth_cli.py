"""Unit tests for auth_cli module."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, call

import pytest

from garmin_mcp.auth_cli import (
    _build_oauth1_auth_header,
    _capture_tokens_via_web_app,
    _exchange_via_playwright,
    _exchange_with_retry_and_fallback,
    authenticate,
    get_credentials,
    get_mfa,
    main,
    verify_tokens,
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
    def test_authenticate_passes_is_cn_true(self, mock_garmin, mock_get_creds, mock_exists):
        """Test that is_cn=True is passed to Garmin constructor."""
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
            result = authenticate(tmpdir, f"{tmpdir}/base64", force_reauth=False, is_cn=True)

        assert result is True
        # Verify Garmin was called with is_cn=True
        mock_garmin.assert_called_once_with(
            email="test@example.com",
            password="secret",
            is_cn=True,
            prompt_mfa=get_mfa,
        )

    @patch("garmin_mcp.auth_cli.token_exists")
    @patch("garmin_mcp.auth_cli.get_credentials")
    @patch("garmin_mcp.auth_cli.Garmin")
    def test_authenticate_passes_is_cn_false(self, mock_garmin, mock_get_creds, mock_exists):
        """Test that is_cn=False is passed to Garmin constructor by default."""
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
            result = authenticate(tmpdir, f"{tmpdir}/base64", force_reauth=False)

        assert result is True
        # Verify Garmin was called with is_cn=False
        mock_garmin.assert_called_once_with(
            email="test@example.com",
            password="secret",
            is_cn=False,
            prompt_mfa=get_mfa,
        )


class TestBuildOAuth1AuthHeader:
    """Tests for _build_oauth1_auth_header."""

    def test_returns_string_starting_with_oauth(self):
        header = _build_oauth1_auth_header(
            "GET", "https://example.com/resource", "consumer_key", "consumer_secret"
        )
        assert isinstance(header, str)
        assert header.startswith("OAuth ")

    def test_contains_required_oauth_fields(self):
        header = _build_oauth1_auth_header(
            "GET", "https://example.com/resource", "key", "secret"
        )
        assert "oauth_signature=" in header
        assert "oauth_consumer_key=" in header
        assert "oauth_nonce=" in header
        assert "oauth_timestamp=" in header

    def test_includes_resource_owner_when_provided(self):
        header = _build_oauth1_auth_header(
            "POST",
            "https://example.com/resource",
            "ck",
            "cs",
            resource_owner_key="tok",
            resource_owner_secret="toksecret",
        )
        assert "oauth_token=" in header

    def test_get_and_post_produce_different_signatures(self):
        url = "https://example.com/resource"
        h_get = _build_oauth1_auth_header("GET", url, "ck", "cs")
        h_post = _build_oauth1_auth_header("POST", url, "ck", "cs")
        assert h_get != h_post


class TestExchangeWithRetryAndFallback:
    """Tests for _exchange_with_retry_and_fallback."""

    def _make_mock_tokens(self):
        return Mock(name="oauth1"), Mock(name="oauth2")

    @patch("garth.sso.exchange")
    @patch("garth.sso.get_oauth1_token")
    def test_success_on_first_attempt_no_sleep(self, mock_get_oauth1, mock_exchange):
        oauth1, oauth2 = self._make_mock_tokens()
        mock_get_oauth1.return_value = oauth1
        mock_exchange.return_value = oauth2

        with patch("time.sleep") as mock_sleep:
            result = _exchange_with_retry_and_fallback("ticket", Mock(), Mock(), Mock())

        assert result == (oauth1, oauth2)
        mock_sleep.assert_not_called()

    @patch("garth.sso.exchange")
    @patch("garth.sso.get_oauth1_token")
    def test_retries_on_429_with_waits(self, mock_get_oauth1, mock_exchange):
        oauth1, oauth2 = self._make_mock_tokens()
        mock_get_oauth1.side_effect = [
            Exception("HTTP 429 Too Many Requests"),
            Exception("too many 429 error responses"),
            oauth1,
        ]
        mock_exchange.return_value = oauth2

        with patch("time.sleep") as mock_sleep:
            result = _exchange_with_retry_and_fallback("ticket", Mock(), Mock(), Mock())

        assert result == (oauth1, oauth2)
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0] == call(30)
        assert mock_sleep.call_args_list[1] == call(60)

    @patch("garth.sso.get_oauth1_token")
    def test_non_429_error_raises_immediately_without_sleep(self, mock_get_oauth1):
        mock_get_oauth1.side_effect = ValueError("Invalid credentials")

        with patch("time.sleep") as mock_sleep:
            with pytest.raises(ValueError, match="Invalid credentials"):
                _exchange_with_retry_and_fallback("ticket", Mock(), Mock(), Mock())

        mock_sleep.assert_not_called()

    @patch("garmin_mcp.auth_cli._capture_tokens_via_web_app")
    @patch("garth.sso.get_oauth1_token")
    def test_falls_back_to_web_app_after_all_retries(
        self, mock_get_oauth1, mock_web_app
    ):
        oauth1, oauth2 = self._make_mock_tokens()
        mock_get_oauth1.side_effect = Exception("too many 429 error responses")
        mock_web_app.return_value = (oauth1, oauth2)

        mock_client = Mock()
        mock_client.domain = "garmin.com"

        with patch("time.sleep"):
            result = _exchange_with_retry_and_fallback("ticket", mock_client, Mock(), Mock())

        assert result == (oauth1, oauth2)
        mock_web_app.assert_called_once()

    @patch("garmin_mcp.auth_cli._exchange_via_playwright")
    @patch("garmin_mcp.auth_cli._capture_tokens_via_web_app")
    @patch("garth.sso.get_oauth1_token")
    def test_raises_runtime_error_when_all_methods_fail(
        self, mock_get_oauth1, mock_web_app, mock_playwright_exchange
    ):
        mock_get_oauth1.side_effect = Exception("too many 429 error responses")
        mock_web_app.side_effect = RuntimeError("web app interception failed")
        mock_playwright_exchange.side_effect = RuntimeError("browser also 429")

        mock_client = Mock()
        mock_client.domain = "garmin.com"

        with patch("time.sleep"):
            with pytest.raises(RuntimeError, match="Token exchange failed"):
                _exchange_with_retry_and_fallback("ticket", mock_client, Mock(), Mock())


class TestExchangeViaPlaywright:
    """Tests for _exchange_via_playwright."""

    def _make_mock_context(self, resp1_text, resp2_json, resp1_ok=True, resp2_ok=True):
        mock_context = Mock()
        mock_resp1 = Mock()
        mock_resp1.ok = resp1_ok
        mock_resp1.status = 200 if resp1_ok else 429
        mock_resp1.text.return_value = resp1_text

        mock_resp2 = Mock()
        mock_resp2.ok = resp2_ok
        mock_resp2.status = 200 if resp2_ok else 500
        mock_resp2.json.return_value = resp2_json

        mock_context.request.get.return_value = mock_resp1
        mock_context.request.post.return_value = mock_resp2
        return mock_context

    @patch("garmin_mcp.auth_cli._build_oauth1_auth_header", return_value="OAuth xxx")
    @patch("garmin_mcp.auth_cli._fetch_oauth_consumer", return_value=("ck", "cs"))
    def test_successful_exchange_returns_tokens(self, mock_consumer, mock_header):
        resp1_text = "oauth_token=tok&oauth_token_secret=sec"
        resp2_data = {
            "access_token": "at",
            "refresh_token": "rt",
            "token_type": "Bearer",
            "scope": "CONNECT",
            "jti": "jti",
            "expires_in": 3600,
            "refresh_token_expires_in": 7776000,
        }
        mock_context = self._make_mock_context(resp1_text, resp2_data)

        from garth.auth_tokens import OAuth1Token, OAuth2Token

        oauth1, oauth2 = _exchange_via_playwright("ST-ticket", "garmin.com", mock_context)

        assert isinstance(oauth1, OAuth1Token)
        assert isinstance(oauth2, OAuth2Token)
        assert oauth1.oauth_token == "tok"
        assert oauth1.oauth_token_secret == "sec"

    @patch("garmin_mcp.auth_cli._build_oauth1_auth_header", return_value="OAuth xxx")
    @patch("garmin_mcp.auth_cli._fetch_oauth_consumer", return_value=("ck", "cs"))
    def test_raises_on_preauth_failure(self, mock_consumer, mock_header):
        mock_context = self._make_mock_context("", {}, resp1_ok=False)

        with pytest.raises(RuntimeError, match="Playwright preauthorized request failed"):
            _exchange_via_playwright("ST-ticket", "garmin.com", mock_context)

    @patch("garmin_mcp.auth_cli._build_oauth1_auth_header", return_value="OAuth xxx")
    @patch("garmin_mcp.auth_cli._fetch_oauth_consumer", return_value=("ck", "cs"))
    def test_raises_on_exchange_failure(self, mock_consumer, mock_header):
        resp1_text = "oauth_token=tok&oauth_token_secret=sec"
        mock_context = self._make_mock_context(resp1_text, {}, resp2_ok=False)

        with pytest.raises(RuntimeError, match="Playwright exchange request failed"):
            _exchange_via_playwright("ST-ticket", "garmin.com", mock_context)

    @patch("garmin_mcp.auth_cli._build_oauth1_auth_header", return_value="OAuth xxx")
    @patch("garmin_mcp.auth_cli._fetch_oauth_consumer", return_value=("ck", "cs"))
    def test_correct_urls_used(self, mock_consumer, mock_header):
        resp1_text = "oauth_token=tok&oauth_token_secret=sec"
        resp2_data = {
            "access_token": "at",
            "refresh_token": "rt",
            "token_type": "Bearer",
            "scope": "CONNECT",
            "jti": "jti",
            "expires_in": 3600,
            "refresh_token_expires_in": 7776000,
        }
        mock_context = self._make_mock_context(resp1_text, resp2_data)

        _exchange_via_playwright("ST-abc", "garmin.com", mock_context)

        get_url = mock_context.request.get.call_args[0][0]
        post_url = mock_context.request.post.call_args[0][0]
        assert "ST-abc" in get_url
        assert "connectapi.garmin.com" in get_url
        assert "exchange/user/2.0" in post_url


class TestCaptureTokensViaWebApp:
    """Tests for _capture_tokens_via_web_app."""

    def _make_mock_preauth_response(self, oauth_token="tok", oauth_token_secret="sec", status=200):
        resp = Mock()
        resp.url = "https://connectapi.garmin.com/oauth-service/oauth/preauthorized?ticket=ST-x"
        resp.status = status
        resp.text.return_value = f"oauth_token={oauth_token}&oauth_token_secret={oauth_token_secret}"
        return resp

    def _make_mock_exchange_response(self, status=200):
        resp = Mock()
        resp.url = "https://connectapi.garmin.com/oauth-service/oauth/exchange/user/2.0"
        resp.status = status
        resp.json.return_value = {
            "access_token": "at",
            "refresh_token": "rt",
            "token_type": "Bearer",
            "scope": "CONNECT",
            "jti": "jti",
            "expires_in": 3600,
            "refresh_token_expires_in": 7776000,
        }
        return resp

    def _make_mock_context_and_page(self):
        mock_context = Mock()
        mock_page = Mock()
        captured_listener = {}

        def on_side_effect(event, fn):
            if event == "response":
                captured_listener["fn"] = fn

        mock_context.on.side_effect = on_side_effect
        mock_context.remove_listener = Mock()
        mock_page.goto = Mock()
        return mock_context, mock_page, captured_listener

    def test_successful_capture_returns_tokens(self):
        from garth.auth_tokens import OAuth1Token, OAuth2Token

        mock_context, mock_page, captured_listener = self._make_mock_context_and_page()

        preauth_resp = self._make_mock_preauth_response()
        exchange_resp = self._make_mock_exchange_response()

        # Call _capture_tokens_via_web_app — it registers the listener and calls page.goto
        # We simulate the listener being called during goto by using a side effect
        def goto_side_effect(*args, **kwargs):
            # Simulate browser firing responses during navigation
            captured_listener["fn"](preauth_resp)
            captured_listener["fn"](exchange_resp)

        mock_page.goto.side_effect = goto_side_effect

        oauth1, oauth2 = _capture_tokens_via_web_app("garmin.com", mock_context, mock_page)

        assert isinstance(oauth1, OAuth1Token)
        assert isinstance(oauth2, OAuth2Token)
        assert oauth1.oauth_token == "tok"
        assert oauth1.oauth_token_secret == "sec"
        assert oauth2.access_token == "at"

    def test_raises_when_tokens_not_captured(self):
        mock_context, mock_page, _ = self._make_mock_context_and_page()
        # page.goto doesn't fire any relevant responses

        with pytest.raises(RuntimeError, match="did not capture"):
            _capture_tokens_via_web_app("garmin.com", mock_context, mock_page)

    def test_raises_when_only_oauth1_captured(self):
        mock_context, mock_page, captured_listener = self._make_mock_context_and_page()
        preauth_resp = self._make_mock_preauth_response()

        def goto_side_effect(*args, **kwargs):
            captured_listener["fn"](preauth_resp)  # only preauth, no exchange

        mock_page.goto.side_effect = goto_side_effect

        with pytest.raises(RuntimeError, match="did not capture"):
            _capture_tokens_via_web_app("garmin.com", mock_context, mock_page)

    def test_ignores_non_oauth_responses(self):
        mock_context, mock_page, captured_listener = self._make_mock_context_and_page()
        preauth_resp = self._make_mock_preauth_response()
        exchange_resp = self._make_mock_exchange_response()

        other_resp = Mock()
        other_resp.url = "https://connect.garmin.com/modern/home"
        other_resp.status = 200

        def goto_side_effect(*args, **kwargs):
            captured_listener["fn"](other_resp)  # non-oauth response, should be ignored
            captured_listener["fn"](preauth_resp)
            captured_listener["fn"](exchange_resp)

        mock_page.goto.side_effect = goto_side_effect

        from garth.auth_tokens import OAuth1Token, OAuth2Token
        oauth1, oauth2 = _capture_tokens_via_web_app("garmin.com", mock_context, mock_page)
        assert isinstance(oauth1, OAuth1Token)
        assert isinstance(oauth2, OAuth2Token)

    def test_navigates_to_correct_web_app_url(self):
        mock_context, mock_page, captured_listener = self._make_mock_context_and_page()
        preauth_resp = self._make_mock_preauth_response()
        exchange_resp = self._make_mock_exchange_response()

        def goto_side_effect(*args, **kwargs):
            captured_listener["fn"](preauth_resp)
            captured_listener["fn"](exchange_resp)

        mock_page.goto.side_effect = goto_side_effect

        _capture_tokens_via_web_app("garmin.com", mock_context, mock_page)

        goto_url = mock_page.goto.call_args[0][0]
        assert "connect.garmin.com/modern/home" in goto_url
