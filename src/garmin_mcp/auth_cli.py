"""Pre-authentication CLI tool for Garmin MCP server.

This tool allows users to authenticate with Garmin Connect and save OAuth tokens
before running the MCP server in non-interactive environments like Claude Desktop.
"""

import argparse
import os
import sys
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


def authenticate(token_path: str, token_base64_path: str, force_reauth: bool = False) -> bool:
    """Authenticate with Garmin Connect and save tokens.

    Args:
        token_path: Path to save token directory
        token_base64_path: Path to save base64 token file
        force_reauth: Force re-authentication even if tokens exist

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
            is_valid, error_msg = validate_tokens(token_path)
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
    print(f"\nAuthenticating with Garmin Connect...")
    print(f"Email: {email}")

    try:
        garmin = Garmin(email=email, password=password, is_cn=False, prompt_mfa=get_mfa)
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

    args = parser.parse_args()

    # Get token paths
    token_path = args.token_path or get_token_path()
    token_base64_path = get_token_base64_path()

    print("\n" + "=" * 60)
    print("Garmin MCP Pre-Authentication Tool")
    print("=" * 60)

    # Verify mode
    if args.verify:
        success = verify_tokens(token_path)
        sys.exit(0 if success else 1)

    # Authenticate mode
    success = authenticate(token_path, token_base64_path, args.force_reauth)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
