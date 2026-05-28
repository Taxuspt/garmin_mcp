"""
Modular MCP Server for Garmin Connect Data
"""

import os
import sys

import requests
from mcp.server.fastmcp import FastMCP

from garth.exc import GarthHTTPError
from garminconnect import Garmin, GarminConnectAuthenticationError

# Import all modules
from garmin_mcp import activity_management
from garmin_mcp import health_wellness
from garmin_mcp import user_profile
from garmin_mcp import devices
from garmin_mcp import gear_management
from garmin_mcp import weight_management
from garmin_mcp import challenges
from garmin_mcp import training
from garmin_mcp import workouts
from garmin_mcp import workout_templates
from garmin_mcp import data_management
from garmin_mcp import womens_health
from garmin_mcp import analytics
from garmin_mcp import auth_tools
from garmin_mcp.client_resolver import set_global_client
from garmin_mcp.token_utils import (
    ensure_token_directory,
    get_token_base64_path,
    get_token_path,
    resolve_path,
    without_token_env,
)


def is_interactive_terminal() -> bool:
    """Detect if running in interactive terminal vs MCP subprocess.

    Returns:
        bool: True if running in an interactive terminal, False otherwise
    """
    return sys.stdin.isatty() and sys.stdout.isatty()


def get_mfa() -> str:
    """Get MFA code from user input.

    Raises:
        RuntimeError: If running in non-interactive environment
    """
    mfa_code = os.getenv("GARMIN_MFA_CODE") or os.getenv("GARMIN_OTP")
    if mfa_code:
        return mfa_code.strip()

    if not is_interactive_terminal():
        print(
            "\nERROR: MFA code required but no interactive terminal available.\n"
            "Please run 'garmin-mcp-auth' in your terminal first, or set GARMIN_MFA_CODE "
            "to the one-time code Garmin sent you.\n"
            "See: https://github.com/Taxuspt/garmin_mcp#mfa-setup\n",
            file=sys.stderr,
        )
        raise RuntimeError("MFA required but non-interactive environment")

    print(
        "\nGarmin Connect MFA required. Please check your email/phone for the code.",
        file=sys.stderr,
    )
    return input("Enter MFA code: ")


# Get credentials from environment
email = os.environ.get("GARMIN_EMAIL")
email_file = os.environ.get("GARMIN_EMAIL_FILE")
if email and email_file:
    raise ValueError(
        "Must only provide one of GARMIN_EMAIL and GARMIN_EMAIL_FILE, got both"
    )
elif email_file:
    with open(email_file, "r") as email_file:
        email = email_file.read().rstrip()

password = os.environ.get("GARMIN_PASSWORD")
password_file = os.environ.get("GARMIN_PASSWORD_FILE")
if password and password_file:
    raise ValueError(
        "Must only provide one of GARMIN_PASSWORD and GARMIN_PASSWORD_FILE, got both"
    )
elif password_file:
    with open(password_file, "r") as password_file:
        password = password_file.read().rstrip()

tokenstore = resolve_path(get_token_path())
tokenstore_base64 = resolve_path(get_token_base64_path(), "~/.garminconnect_base64")


def init_api(email, password):
    """Initialize Garmin API with your credentials."""
    import io

    try:
        # Using Oauth1 and OAuth2 token files from directory
        print(
            f"Trying to login to Garmin Connect using token data from directory '{tokenstore}'...\n",
            file=sys.stderr,
        )

        # Using Oauth1 and Oauth2 tokens from base64 encoded string
        # print(
        #     f"Trying to login to Garmin Connect using token data from file '{tokenstore_base64}'...\n"
        # )
        # dir_path = os.path.expanduser(tokenstore_base64)
        # with open(dir_path, "r") as token_file:
        #     tokenstore = token_file.read()

        # Suppress stderr for token validation to avoid confusing library errors
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()

        try:
            garmin = Garmin()
            garmin.login(tokenstore)
        finally:
            sys.stderr = old_stderr

    except (FileNotFoundError, GarthHTTPError, GarminConnectAuthenticationError):
        # Session is expired. You'll need to log in again

        # Check if we're in a non-interactive environment without credentials
        if not is_interactive_terminal() and (not email or not password):
            print(
                "ERROR: OAuth tokens not found and no interactive terminal available.\n"
                "Please authenticate first:\n"
                "  - In Claude, run login_to_garmin and provide an OTP only if Garmin asks.\n"
                "  - Or run garmin-mcp-auth in a terminal before starting this server.\n"
                f"Tokens will be saved to: {tokenstore}\n",
                file=sys.stderr,
            )
            return None

        print(
            "Login tokens not present, login with your Garmin Connect credentials to generate them.\n"
            f"They will be stored in '{tokenstore}' for future use.\n",
            file=sys.stderr,
        )
        try:
            garmin = Garmin(
                email=email, password=password, is_cn=False, prompt_mfa=get_mfa
            )
            with without_token_env():
                garmin.login()
            # Save Oauth1 and Oauth2 token files to directory for next login
            ensure_token_directory(tokenstore)
            garmin.garth.dump(tokenstore)
            print(
                f"Oauth tokens stored in '{tokenstore}' directory for future use. (first method)\n",
                file=sys.stderr,
            )
            # Encode Oauth1 and Oauth2 tokens to base64 string and safe to file for next login (alternative way)
            token_base64 = garmin.garth.dumps()
            dir_path = resolve_path(tokenstore_base64, "~/.garminconnect_base64")
            with open(dir_path, "w") as token_file:
                token_file.write(token_base64)
            print(
                f"Oauth tokens encoded as base64 string and saved to '{dir_path}' file for future use. (second method)\n",
                file=sys.stderr,
            )
        except (
            FileNotFoundError,
            GarthHTTPError,
            GarminConnectAuthenticationError,
            RuntimeError,
            requests.exceptions.HTTPError,
        ) as err:
            error_msg = str(err)

            # Provide clean, actionable error messages
            print("\nAuthentication failed.", file=sys.stderr)

            if isinstance(err, GarminConnectAuthenticationError):
                if "MFA" in error_msg or "code" in error_msg.lower():
                    print("MFA code may be incorrect or expired.", file=sys.stderr)
                else:
                    print("Invalid email or password.", file=sys.stderr)
            elif isinstance(err, GarthHTTPError):
                if "401" in error_msg or "Unauthorized" in error_msg:
                    print(
                        "Invalid credentials. Please check your email and password.",
                        file=sys.stderr,
                    )
                elif "429" in error_msg:
                    print(
                        "Too many requests. Please wait and try again.", file=sys.stderr
                    )
                elif "500" in error_msg or "503" in error_msg:
                    print(
                        "Garmin Connect service issue. Please try again later.",
                        file=sys.stderr,
                    )
                else:
                    print(f"Error: {error_msg.split(':')[0]}", file=sys.stderr)
            elif isinstance(err, RuntimeError):
                print(str(err).split(":")[0], file=sys.stderr)
            elif isinstance(err, requests.exceptions.HTTPError):
                print("Network error. Please check your connection.", file=sys.stderr)
            else:
                print(f"Error: {error_msg.split(':')[0]}", file=sys.stderr)

            print(
                "\nTip: Run the MCP tool 'login_to_garmin', or run "
                "'garmin-mcp-auth' in a terminal.",
                file=sys.stderr,
            )
            return None

    return garmin


def activate_garmin_client(garmin_client):
    """Make a Garmin client available to all stdio MCP tools."""
    set_global_client(garmin_client)
    activity_management.configure(garmin_client)
    health_wellness.configure(garmin_client)
    user_profile.configure(garmin_client)
    devices.configure(garmin_client)
    gear_management.configure(garmin_client)
    weight_management.configure(garmin_client)
    challenges.configure(garmin_client)
    training.configure(garmin_client)
    workouts.configure(garmin_client)
    data_management.configure(garmin_client)
    womens_health.configure(garmin_client)
    analytics.configure(garmin_client)


def main():
    """Initialize the MCP server and register all tools"""

    auth_tools.configure(activate_garmin_client)

    # Initialize Garmin client when tokens already exist. If they do not, keep
    # the MCP server alive so check_garmin_auth/login_to_garmin can fix it.
    try:
        garmin_client = init_api(email, password)
    except Exception as err:
        print(
            f"Garmin Connect auto-login skipped: {str(err).split(':')[0]}",
            file=sys.stderr,
        )
        garmin_client = None
    if garmin_client:
        activate_garmin_client(garmin_client)
        print("Garmin Connect client initialized successfully.", file=sys.stderr)
    else:
        print(
            "Garmin Connect client not initialized. Auth tools are available; run "
            "check_garmin_auth or login_to_garmin.",
            file=sys.stderr,
        )

    # Create the MCP app
    app = FastMCP("Garmin Connect v1.0")

    # Register tools from all modules
    app = auth_tools.register_tools(app)
    app = activity_management.register_tools(app)
    app = health_wellness.register_tools(app)
    app = user_profile.register_tools(app)
    app = devices.register_tools(app)
    app = gear_management.register_tools(app)
    app = weight_management.register_tools(app)
    app = challenges.register_tools(app)
    app = training.register_tools(app)
    app = workouts.register_tools(app)
    app = data_management.register_tools(app)
    app = womens_health.register_tools(app)
    app = analytics.register_tools(app)

    # Register resources (workout templates)
    app = workout_templates.register_resources(app)

    # Run the MCP server
    app.run()


if __name__ == "__main__":
    main()
