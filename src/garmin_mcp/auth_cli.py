"""Pre-authentication CLI tool for Garmin MCP server.

This tool allows users to authenticate with Garmin Connect and save OAuth tokens
before running the MCP server in non-interactive environments like Claude Desktop.
"""

import argparse
import os
import re
import sys
import time
import getpass

import requests
from garth.exc import GarthHTTPError
from garminconnect import Garmin, GarminConnectAuthenticationError

from garmin_mcp.token_utils import (
    get_token_path,
    get_token_base64_path,
    token_exists,
    validate_tokens,
    get_token_info,
)


_RETRY_WAITS = (30, 60)  # seconds to wait between direct-request retry attempts


def _fetch_oauth_consumer() -> tuple[str, str]:
    """Return (consumer_key, consumer_secret), reusing garth's module-level cache."""
    import garth.sso as _sso

    if _sso.OAUTH_CONSUMER:
        c = _sso.OAUTH_CONSUMER
    else:
        c = requests.get(_sso.OAUTH_CONSUMER_URL, timeout=10).json()
        _sso.OAUTH_CONSUMER = c
    return c["consumer_key"], c["consumer_secret"]


def _build_oauth1_auth_header(
    method: str,
    url: str,
    consumer_key: str,
    consumer_secret: str,
    resource_owner_key: str | None = None,
    resource_owner_secret: str | None = None,
    body: dict | None = None,
) -> str:
    """Generate an OAuth1 HMAC-SHA1 Authorization header string for Playwright headers."""
    from requests_oauthlib import OAuth1

    auth = OAuth1(
        consumer_key,
        consumer_secret,
        resource_owner_key=resource_owner_key,
        resource_owner_secret=resource_owner_secret,
    )
    prepared = requests.Request(method, url, data=body or {}).prepare()
    auth(prepared)
    raw = prepared.headers["Authorization"]
    return raw.decode("utf-8") if isinstance(raw, bytes) else raw


def _exchange_via_playwright(ticket: str, domain: str, playwright_context) -> tuple:
    """Exchange SSO ticket for OAuth tokens using Playwright's request context.

    Routes through Chromium's network stack (browser TLS fingerprint, session cookies)
    as a fallback when direct Python requests are rate-limited.
    """
    from urllib.parse import parse_qs

    from garth.auth_tokens import OAuth1Token, OAuth2Token
    from garth.sso import USER_AGENT, set_expirations

    consumer_key, consumer_secret = _fetch_oauth_consumer()
    login_url = f"https://sso.{domain}/sso/embed"
    preauth_url = (
        f"https://connectapi.{domain}/oauth-service/oauth/preauthorized"
        f"?ticket={ticket}&login-url={login_url}&accepts-mfa-tokens=true"
    )

    # Step 1: GET preauthorized → OAuth1Token
    auth1 = _build_oauth1_auth_header("GET", preauth_url, consumer_key, consumer_secret)
    resp1 = playwright_context.request.get(
        preauth_url,
        headers={**USER_AGENT, "Authorization": auth1},
        fail_on_status_code=False,
    )
    if not resp1.ok:
        raise RuntimeError(f"Playwright preauthorized request failed: HTTP {resp1.status}")
    token_dict = {k: v[0] for k, v in parse_qs(resp1.text()).items()}
    oauth1 = OAuth1Token(domain=domain, **token_dict)

    # Step 2: POST exchange/user/2.0 → OAuth2Token
    exchange_url = f"https://connectapi.{domain}/oauth-service/oauth/exchange/user/2.0"
    body = {"mfa_token": oauth1.mfa_token} if oauth1.mfa_token else {}
    auth2 = _build_oauth1_auth_header(
        "POST",
        exchange_url,
        consumer_key,
        consumer_secret,
        resource_owner_key=oauth1.oauth_token,
        resource_owner_secret=oauth1.oauth_token_secret,
        body=body,
    )
    headers2 = {
        **USER_AGENT,
        "Authorization": auth2,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    resp2 = playwright_context.request.post(
        exchange_url,
        headers=headers2,
        form=body or None,
        fail_on_status_code=False,
    )
    if not resp2.ok:
        raise RuntimeError(f"Playwright exchange request failed: HTTP {resp2.status}")
    oauth2 = OAuth2Token(**set_expirations(resp2.json()))
    return oauth1, oauth2


def _capture_tokens_via_web_app(domain: str, context, page) -> tuple:
    """Navigate to Garmin Connect web app and capture OAuth tokens via response interception.

    The browser context already has SSO session cookies from the login step.
    The web app re-authenticates server-side (Garmin's servers exchange the SSO
    ticket — not the user's IP), then the browser makes API calls. We intercept
    all network responses with context.on("response"), which captures everything
    regardless of CORS.
    """
    from urllib.parse import parse_qs

    from garth.auth_tokens import OAuth1Token, OAuth2Token
    from garth.sso import set_expirations

    captured: dict = {}

    def on_response(response):
        url = response.url
        if f"connectapi.{domain}/oauth-service/oauth" not in url:
            return
        try:
            if "preauthorized" in url and response.status == 200:
                parsed = parse_qs(response.text())
                if "oauth_token" in parsed:
                    captured["oauth1"] = {k: v[0] for k, v in parsed.items()}
            elif "exchange" in url and response.status == 200:
                data = response.json()
                if "access_token" in data:
                    captured["oauth2"] = data
        except Exception:
            pass  # Response body not yet fully available; next response may succeed

    context.on("response", on_response)

    web_url = f"https://connect.{domain}/modern/home"
    print("  Navigating to Garmin Connect web app to bypass rate-limited endpoint...")
    try:
        page.goto(web_url, wait_until="networkidle", timeout=120_000)
    except Exception:
        pass  # networkidle timeout is acceptable — tokens may already be captured

    context.remove_listener("response", on_response)

    if "oauth1" not in captured or "oauth2" not in captured:
        missing = [k for k in ("oauth1", "oauth2") if k not in captured]
        raise RuntimeError(
            f"Web app token interception did not capture: {missing}.\n"
            "  The web app may use a different auth flow on this account."
        )

    oauth1 = OAuth1Token(domain=domain, **captured["oauth1"])
    oauth2 = OAuth2Token(**set_expirations(captured["oauth2"]))
    return oauth1, oauth2


def _exchange_with_retry_and_fallback(ticket: str, client, playwright_context, page) -> tuple:
    """Try direct OAuth token exchange with backoff retries, then web app interception."""
    from garth.sso import exchange as exchange_oauth
    from garth.sso import get_oauth1_token

    last_error = None
    for wait in (0, *_RETRY_WAITS):
        if wait:
            print(f"  429 rate limit — waiting {wait}s before retry...")
            time.sleep(wait)
        try:
            oauth1 = get_oauth1_token(ticket, client)
            oauth2 = exchange_oauth(oauth1, client)
            return oauth1, oauth2
        except Exception as e:
            last_error = e
            err_str = str(e)
            if "429" in err_str or "too many" in err_str.lower():
                continue
            raise  # non-429 errors propagate immediately

    print("  All direct attempts failed — navigating to web app to intercept tokens...")
    try:
        return _capture_tokens_via_web_app(client.domain, playwright_context, page)
    except Exception as web_err:
        # Last resort: Playwright context.request (may also 429 but worth trying)
        try:
            return _exchange_via_playwright(ticket, client.domain, playwright_context)
        except Exception as pw_err:
            raise RuntimeError(
                f"Token exchange failed via all methods.\n"
                f"  Direct error: {last_error}\n"
                f"  Web app error: {web_err}\n"
                f"  Browser context error: {pw_err}"
            ) from pw_err


def get_mfa() -> str:
    """Get MFA code from user input."""
    print("\nGarmin Connect MFA required. Please check your email/phone for the code.")
    return input("Enter MFA code: ")


def get_credentials() -> tuple[str, str]:
    """Get credentials from environment variables or user input.

    Returns:
        Tuple of (email, password)

    Raises:
        ValueError: If credentials cannot be obtained
    """
    # Try environment variables first
    email = os.environ.get("GARMIN_EMAIL")
    email_file = os.environ.get("GARMIN_EMAIL_FILE")

    if email and email_file:
        raise ValueError(
            "Must only provide one of GARMIN_EMAIL and GARMIN_EMAIL_FILE, got both"
        )
    elif email_file:
        with open(email_file, "r") as f:
            email = f.read().rstrip()

    password = os.environ.get("GARMIN_PASSWORD")
    password_file = os.environ.get("GARMIN_PASSWORD_FILE")

    if password and password_file:
        raise ValueError(
            "Must only provide one of GARMIN_PASSWORD and GARMIN_PASSWORD_FILE, got both"
        )
    elif password_file:
        with open(password_file, "r") as f:
            password = f.read().rstrip()

    # Prompt for missing credentials
    if not email:
        print("\nGarmin Connect Credentials")
        print("-" * 40)
        email = input("Email: ").strip()
        if not email:
            raise ValueError("Email is required")

    if not password:
        password = getpass.getpass("Password: ")
        if not password:
            raise ValueError("Password is required")

    return email, password


def _browser_login_and_exchange(
    email: str | None,
    password: str | None,
    is_cn: bool,
    garth_client,
) -> tuple:
    """Open a real browser, let the user log in, then exchange the SSO ticket for OAuth tokens.

    Keeps the browser context alive during the token exchange so that
    Playwright's request context (Chromium TLS stack + session cookies) can
    serve as a fallback when direct Python requests hit Garmin's rate limit.

    Returns:
        Tuple of (OAuth1Token, OAuth2Token)
    """
    try:
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ImportError(
            "playwright is not installed.\n"
            "  Install with:  pip install playwright\n"
            "  Then run:      playwright install chromium"
        )

    domain = "garmin.cn" if is_cn else "garmin.com"
    SSO_EMBED = f"https://sso.{domain}/sso/embed"
    signin_url = (
        f"https://sso.{domain}/sso/signin"
        f"?id=gauth-widget&embedWidget=true"
        f"&gauthHost={SSO_EMBED}"
        f"&service={SSO_EMBED}"
        f"&source={SSO_EMBED}"
        f"&redirectAfterAccountLoginUrl={SSO_EMBED}"
        f"&redirectAfterAccountCreationUrl={SSO_EMBED}"
    )
    TICKET_RE = re.compile(
        r'(?:embed\?ticket=|serviceTicket["\']?\s*:\s*["\'])(ST-[^"\'&\s,}]+)'
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        # Use an explicit BrowserContext so context.request is available for fallback
        context = browser.new_context()
        page = context.new_page()

        print("\n  Opening browser for Garmin authentication...")
        page.goto(signin_url)

        # Auto-fill credentials when provided
        if email:
            try:
                page.wait_for_selector("#username", timeout=8_000)
                page.fill("#username", email)
            except PWTimeout:
                pass
        if password:
            try:
                page.wait_for_selector("#password", timeout=5_000)
                page.fill("#password", password)
            except PWTimeout:
                pass
            for selector in ("#login-btn", "[type=submit]"):
                try:
                    page.click(selector, timeout=3_000)
                    break
                except Exception:
                    pass

        print("  Complete the login in the browser window (MFA if prompted)...")
        print("  Waiting up to 2 minutes...")

        ticket: str | None = None
        for _ in range(240):  # 240 × 0.5 s = 2 min
            try:
                m = TICKET_RE.search(page.content())
                if m:
                    ticket = m.group(1)
                    break
            except Exception:
                pass
            time.sleep(0.5)

        if not ticket:
            browser.close()
            raise RuntimeError(
                "Authentication timed out — no SSO ticket was captured.\n"
                "  Make sure you completed the login in the browser window."
            )

        print("  ✓ Login successful — ticket captured")
        print("  Exchanging ticket for OAuth tokens...")

        try:
            # Browser stays open so context.request can be used as fallback
            oauth1, oauth2 = _exchange_with_retry_and_fallback(ticket, garth_client, context, page)
        finally:
            browser.close()

    return oauth1, oauth2


def browser_authenticate(
    token_path: str,
    token_base64_path: str,
    force_reauth: bool = False,
    is_cn: bool = False,
) -> bool:
    """Authenticate via a real browser to bypass Garmin's API rate limiting.

    Opens Chromium via Playwright so Garmin's Cloudflare protection treats the
    request as a legitimate browser session (no 429).  After the user logs in,
    the SSO ticket is exchanged for OAuth tokens using garth's own internals,
    and the tokens are saved in the standard locations.
    """
    # Check if existing tokens are still valid (unless forced)
    if not force_reauth and token_exists(token_path):
        print(f"\nChecking existing tokens in '{token_path}'...")
        import io
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            is_valid, error_msg = validate_tokens(token_path, is_cn=is_cn)
        finally:
            sys.stderr = old_stderr

        if is_valid:
            print("✓ Existing tokens are valid. Authentication not needed.")
            print("  Use --force-reauth to generate new tokens.")
            return True
        else:
            print(f"✗ Existing tokens are invalid: {error_msg}")
            print("  Proceeding with browser re-authentication...\n")

    # Collect credentials for auto-fill (optional)
    try:
        email, password = get_credentials()
    except ValueError:
        email, password = None, None

    import garth

    client = garth.Client(domain="garmin.cn" if is_cn else "garmin.com")

    try:
        oauth1, oauth2 = _browser_login_and_exchange(email, password, is_cn, client)
    except ImportError as e:
        print(f"\n✗ {e}", file=sys.stderr)
        return False
    except RuntimeError as e:
        print(f"\n✗ {e}", file=sys.stderr)
        return False

    print("  ✓ OAuth tokens obtained")

    try:
        client.configure(oauth1_token=oauth1, oauth2_token=oauth2)
    except Exception as e:
        print(f"\n✗ Failed to configure tokens: {e}", file=sys.stderr)
        return False

    # Save tokens
    try:
        client.dump(token_path)
        print(f"\n✓ OAuth tokens saved to: {os.path.expanduser(token_path)}")

        token_base64 = client.dumps()
        expanded_base64 = os.path.expanduser(token_base64_path)
        with open(expanded_base64, "w") as f:
            f.write(token_base64)
        print(f"✓ OAuth tokens (base64) saved to: {expanded_base64}")
    except Exception as e:
        print(f"\n✗ Failed to save tokens: {e}", file=sys.stderr)
        return False

    # Verify
    print("\nVerifying tokens...")
    try:
        garmin = Garmin(is_cn=is_cn)
        garmin.login(token_path)
        full_name = garmin.get_full_name()
        print(f"✓ Authentication successful!")
        print(f"  Logged in as: {full_name}")
    except Exception:
        print("✓ Tokens saved. Run 'garmin-mcp-auth --verify' to confirm.")

    print("\n" + "=" * 60)
    print("SUCCESS: You can now use the Garmin MCP server!")
    print("=" * 60)
    print("\nTokens are valid for approximately 6 months.")
    return True


def authenticate(token_path: str, token_base64_path: str, force_reauth: bool = False, is_cn: bool = False) -> bool:
    """Authenticate with Garmin Connect and save tokens.

    Args:
        token_path: Path to save token directory
        token_base64_path: Path to save base64 token file
        force_reauth: Force re-authentication even if tokens exist
        is_cn: Use Garmin Connect China (garmin.cn) instead of international

    Returns:
        bool: True if authentication succeeded, False otherwise
    """
    import io

    # Check if tokens already exist and are valid
    if not force_reauth and token_exists(token_path):
        print(f"\nChecking existing tokens in '{token_path}'...")

        # Suppress stderr during validation
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()

        try:
            is_valid, error_msg = validate_tokens(token_path, is_cn=is_cn)
        finally:
            sys.stderr = old_stderr

        if is_valid:
            print("✓ Existing tokens are valid. Authentication not needed.")
            print(f"  Use --force-reauth to generate new tokens.")
            return True
        else:
            print(f"✗ Existing tokens are invalid: {error_msg}")
            print("  Proceeding with re-authentication...\n")

    # Get credentials
    try:
        email, password = get_credentials()
    except ValueError as e:
        print(f"\nError: {e}", file=sys.stderr)
        return False

    # Authenticate with Garmin Connect
    region = "Garmin Connect CN (garmin.cn)" if is_cn else "Garmin Connect"
    print(f"\nAuthenticating with {region}...")
    print(f"Email: {email}")

    try:
        garmin = Garmin(email=email, password=password, is_cn=is_cn, prompt_mfa=get_mfa)
        garmin.login()

        # Save tokens to directory
        garmin.garth.dump(token_path)
        print(f"\n✓ OAuth tokens saved to: {os.path.expanduser(token_path)}")

        # Save tokens as base64
        token_base64 = garmin.garth.dumps()
        expanded_base64_path = os.path.expanduser(token_base64_path)
        with open(expanded_base64_path, "w") as token_file:
            token_file.write(token_base64)
        print(f"✓ OAuth tokens (base64) saved to: {expanded_base64_path}")

        # Verify tokens work
        print("\nVerifying tokens...")
        try:
            # Try to get user's full name as a simple verification
            full_name = garmin.get_full_name()
            print(f"✓ Authentication successful!")
            print(f"  Logged in as: {full_name}")
        except Exception:
            # Fallback: just confirm tokens were saved
            print(f"✓ Authentication successful!")
            print(f"  OAuth tokens saved and ready to use.")

        print("\n" + "=" * 60)
        print("SUCCESS: You can now use the Garmin MCP server!")
        print("=" * 60)
        print("\nNext steps:")
        print("1. Add the server to your MCP client (e.g., Claude Desktop)")
        print("2. No need to include GARMIN_EMAIL or GARMIN_PASSWORD in config")
        print("3. The server will use your saved OAuth tokens")
        print("\nTokens are valid for approximately 6 months.")

        return True

    except GarminConnectAuthenticationError as e:
        error_msg = str(e)
        print(f"\n✗ Authentication failed", file=sys.stderr)

        # Provide helpful hints based on error type
        if "MFA" in error_msg or "code" in error_msg.lower():
            print("  MFA code may be incorrect or expired.", file=sys.stderr)
            print("  Please request a new code and try again.", file=sys.stderr)
        elif "password" in error_msg.lower() or "credentials" in error_msg.lower():
            print("  Invalid email or password.", file=sys.stderr)
            print("  Please check your Garmin Connect credentials.", file=sys.stderr)
        else:
            print(f"  {error_msg}", file=sys.stderr)

        return False

    except GarthHTTPError as e:
        error_msg = str(e)
        print(f"\n✗ Authentication error", file=sys.stderr)

        if "429" in error_msg:
            print("  Too many requests. Please wait a few minutes and try again.", file=sys.stderr)
        elif "401" in error_msg or "403" in error_msg:
            print("  Invalid credentials. Please check your email and password.", file=sys.stderr)
        elif "500" in error_msg or "503" in error_msg:
            print("  Garmin Connect service issue. Please try again later.", file=sys.stderr)
        else:
            print(f"  {error_msg.split(':')[0]}", file=sys.stderr)

        return False

    except requests.exceptions.HTTPError as e:
        print(f"\n✗ Network error", file=sys.stderr)

        if e.response is not None:
            if e.response.status_code == 429:
                print("  Rate limited. Please wait a few minutes and try again.", file=sys.stderr)
            elif e.response.status_code >= 500:
                print("  Garmin Connect is experiencing issues. Please try again later.", file=sys.stderr)
            else:
                print(f"  HTTP {e.response.status_code} error", file=sys.stderr)
        else:
            print("  Please check your internet connection.", file=sys.stderr)

        return False

    except Exception as e:
        error_msg = str(e)
        print(f"\n✗ Unexpected error", file=sys.stderr)

        # Only show detailed error in debug scenarios
        if "timeout" in error_msg.lower():
            print("  Connection timeout. Please check your internet connection.", file=sys.stderr)
        elif "connection" in error_msg.lower():
            print("  Network connection issue. Please check your internet.", file=sys.stderr)
        else:
            print(f"  {error_msg.split(':')[0]}", file=sys.stderr)

        return False


def verify_tokens(token_path: str) -> bool:
    """Verify existing tokens are valid.

    Args:
        token_path: Path to token directory

    Returns:
        bool: True if tokens are valid, False otherwise
    """
    print(f"\nVerifying tokens in '{token_path}'...")

    info = get_token_info(token_path)

    if not info["exists"]:
        print(f"✗ Tokens not found at: {info['expanded_path']}")
        print("\nRun 'garmin-mcp-auth' without --verify to authenticate.")
        return False

    if info["valid"]:
        print(f"✓ Tokens are valid!")
        print(f"  Location: {info['expanded_path']}")
        print("\nYou can use the Garmin MCP server without re-authenticating.")
        return True
    else:
        print(f"✗ Tokens are invalid: {info['error']}")
        print("\nRun 'garmin-mcp-auth --force-reauth' to re-authenticate.")
        return False


def main():
    """Main entry point for the authentication CLI tool."""
    parser = argparse.ArgumentParser(
        description="Pre-authenticate with Garmin Connect for MCP server use",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Authenticate and save tokens (interactive)
  garmin-mcp-auth

  # Use browser-based login (bypasses Garmin API rate limiting / 429 errors)
  garmin-mcp-auth --browser

  # Use environment variables for credentials
  GARMIN_EMAIL=you@example.com GARMIN_PASSWORD=secret garmin-mcp-auth

  # Verify existing tokens
  garmin-mcp-auth --verify

  # Force re-authentication
  garmin-mcp-auth --force-reauth

  # Use custom token location
  garmin-mcp-auth --token-path ~/.garmin_tokens
        """
    )

    parser.add_argument(
        "--token-path",
        type=str,
        default=None,
        help="Custom token storage directory (default: ~/.garminconnect or $GARMINTOKENS)"
    )

    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify existing tokens without re-authenticating"
    )

    parser.add_argument(
        "--force-reauth",
        action="store_true",
        help="Force re-authentication even if valid tokens exist"
    )

    parser.add_argument(
        "--browser",
        action="store_true",
        help=(
            "Use a real browser (Chromium via Playwright) to log in. "
            "Bypasses Garmin API rate limiting (429 errors). "
            "Requires: pip install playwright && playwright install chromium"
        ),
    )

    parser.add_argument(
        "--is-cn",
        action="store_true",
        default=None,
        help="Use Garmin Connect China (garmin.cn) instead of the international version"
    )

    args = parser.parse_args()

    # Get token paths
    token_path = args.token_path or get_token_path()
    token_base64_path = get_token_base64_path()

    # Resolve is_cn: CLI flag takes priority, then env var, then default False
    if args.is_cn:
        is_cn = True
    else:
        is_cn = os.getenv("GARMIN_IS_CN", "false").lower() in ("true", "1", "yes")

    print("\n" + "=" * 60)
    print("Garmin MCP Pre-Authentication Tool")
    if is_cn:
        print("Region: China (garmin.cn)")
    print("=" * 60)

    # Verify mode
    if args.verify:
        success = verify_tokens(token_path)
        sys.exit(0 if success else 1)

    # Authenticate mode
    if args.browser:
        success = browser_authenticate(token_path, token_base64_path, args.force_reauth, is_cn)
    else:
        success = authenticate(token_path, token_base64_path, args.force_reauth, is_cn)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
