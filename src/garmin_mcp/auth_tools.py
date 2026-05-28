"""
Interactive Garmin authentication tools for MCP clients.

The server can start before Garmin tokens exist. These tools let the user check
auth state, trigger Garmin login, enter an OTP only when Garmin asks for one,
and save reusable tokens without restarting the MCP process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests
from garth.exc import GarthHTTPError
from garminconnect import Garmin, GarminConnectAuthenticationError

from garmin_mcp.token_utils import (
    ensure_token_directory,
    get_token_base64_path,
    get_token_info,
    get_token_path,
    resolve_path,
    without_token_env,
)


_activate_client: Callable[[Garmin], None] | None = None


class MfaRequired(RuntimeError):
    """Raised when Garmin asks for MFA and no code was supplied."""


def configure(activate_client: Callable[[Garmin], None]) -> None:
    """Set callback used to activate a successfully authenticated Garmin client."""
    global _activate_client
    _activate_client = activate_client


def register_tools(app):
    """Register authentication tools."""

    @app.tool()
    async def check_garmin_auth(token_path: str | None = None) -> str:
        """Check whether Garmin tokens exist and still work.

        Args:
            token_path: Optional token directory. Defaults to GARMINTOKENS or ~/.garminconnect.
        """
        try:
            path = token_path or get_token_path()
            info = get_token_info(path)
            return json.dumps(
                {
                    "authenticated": bool(info["exists"] and info["valid"]),
                    "token_path": info["path"],
                    "expanded_path": info["expanded_path"],
                    "tokens_exist": info["exists"],
                    "tokens_valid": info["valid"],
                    "error": info["error"] or None,
                    "next_step": _auth_next_step(bool(info["exists"]), bool(info["valid"])),
                },
                indent=2,
            )
        except Exception as e:
            return json.dumps(
                {
                    "authenticated": False,
                    "error": str(e),
                    "next_step": "Run login_to_garmin with email and password.",
                },
                indent=2,
            )

    @app.tool()
    async def login_to_garmin(
        email: str | None = None,
        password: str | None = None,
        otp_code: str | None = None,
        token_path: str | None = None,
        token_base64_path: str | None = None,
        force_reauth: bool = False,
    ) -> str:
        """Log in to Garmin Connect and save reusable OAuth tokens.

        Call once with email/password. If Garmin returns mfa_required, call it
        again with the same email/password and otp_code from Garmin email/SMS.
        Accounts without OTP authenticate and save tokens in one call.

        Args:
            email: Garmin Connect email. Defaults to GARMIN_EMAIL.
            password: Garmin Connect password. Defaults to GARMIN_PASSWORD.
            otp_code: One-time Garmin verification code, only needed when requested.
            token_path: Token directory. Defaults to GARMINTOKENS or ~/.garminconnect.
            token_base64_path: Optional base64 token file path.
            force_reauth: Ignore existing valid tokens and login again.
        """
        path = resolve_path(token_path or get_token_path())
        base64_path = resolve_path(
            token_base64_path or get_token_base64_path(),
            "~/.garminconnect_base64",
        )
        if not force_reauth:
            info = get_token_info(path)
            if info["exists"] and info["valid"]:
                return json.dumps(
                    {
                        "status": "authenticated",
                        "message": "Existing Garmin tokens are valid.",
                        "token_path": info["expanded_path"],
                    },
                    indent=2,
                )

        login_email = (email or os.getenv("GARMIN_EMAIL") or "").strip()
        if not login_email:
            login_email = _prompt_local_input(
                "Garmin Connect email",
                "Enter your Garmin Connect email.",
            )
        login_password = password or os.getenv("GARMIN_PASSWORD") or ""
        if not login_password:
            login_password = _prompt_local_input(
                "Garmin Connect password",
                "Enter your Garmin Connect password. It will be used only for this login and will not be stored.",
                hidden=True,
            )
        if not login_email or not login_password:
            return json.dumps(
                {
                    "status": "missing_credentials",
                    "message": "Email and password are required to start Garmin login.",
                    "token_path": path,
                },
                indent=2,
            )

        try:
            garmin = Garmin(
                email=login_email,
                password=login_password,
                is_cn=False,
                prompt_mfa=_mfa_prompt(otp_code),
            )
            with without_token_env():
                garmin.login()
            ensure_token_directory(path)
            garmin.garth.dump(path)
            expanded_base64_path = resolve_path(base64_path, "~/.garminconnect_base64")
            Path(expanded_base64_path).parent.mkdir(parents=True, exist_ok=True)
            with open(expanded_base64_path, "w") as token_file:
                token_file.write(garmin.garth.dumps())
            if _activate_client is not None:
                _activate_client(garmin)
            return json.dumps(
                {
                    "status": "authenticated",
                    "message": "Garmin login succeeded and tokens were saved.",
                    "email": login_email,
                    "token_path": path,
                    "token_base64_path": expanded_base64_path,
                },
                indent=2,
            )
        except MfaRequired:
            return json.dumps(
                {
                    "status": "mfa_required",
                    "message": "Garmin requested a one-time code. Check your email or phone, then call login_to_garmin again with otp_code.",
                    "email": login_email,
                    "token_path": path,
                },
                indent=2,
            )
        except (GarminConnectAuthenticationError, GarthHTTPError, requests.exceptions.HTTPError) as e:
            return json.dumps(
                {
                    "status": "failed",
                    "message": _clean_login_error(e),
                    "email": login_email,
                    "token_path": path,
                },
                indent=2,
            )
        except Exception as e:
            return json.dumps(
                {
                    "status": "failed",
                    "message": str(e).split(":")[0],
                    "email": login_email,
                    "token_path": path,
                },
                indent=2,
            )

    return app


def _mfa_prompt(otp_code: str | None) -> Callable[[], str]:
    def prompt() -> str:
        code = (otp_code or os.getenv("GARMIN_MFA_CODE") or os.getenv("GARMIN_OTP") or "").strip()
        if not code:
            code = _prompt_local_input(
                "Garmin verification code",
                "Enter the one-time code Garmin sent to your email or phone.",
            )
        if not code:
            raise MfaRequired("Garmin MFA required")
        return code

    return prompt


def _prompt_local_input(title: str, message: str, hidden: bool = False) -> str:
    """Prompt for a local value on macOS without sending it through chat."""
    if sys.platform != "darwin" or os.getenv("GARMIN_DISABLE_LOCAL_PROMPTS") == "1":
        return ""

    script = [
        'on run argv',
        'set dialogMessage to item 1 of argv',
        'set dialogTitle to item 2 of argv',
    ]
    if hidden:
        script.append(
            'set dialogResult to display dialog dialogMessage default answer "" with title dialogTitle with hidden answer buttons {"Cancel", "OK"} default button "OK"'
        )
    else:
        script.append(
            'set dialogResult to display dialog dialogMessage default answer "" with title dialogTitle buttons {"Cancel", "OK"} default button "OK"'
        )
    script.extend(
        [
            'return text returned of dialogResult',
            'end run',
        ]
    )

    try:
        result = subprocess.run(
            ["osascript", *sum([["-e", line] for line in script], []), "--", message, title],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception:
        return ""

    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _auth_next_step(exists: bool, valid: bool) -> str:
    if exists and valid:
        return "You can use Garmin tools now."
    if exists:
        return "Tokens exist but are invalid. Run login_to_garmin with force_reauth=true."
    return "Run login_to_garmin with email and password."


def _clean_login_error(error: Exception) -> str:
    message = str(error)
    if "MFA" in message or "code" in message.lower():
        return "OTP code may be incorrect or expired."
    if "401" in message or "403" in message or "password" in message.lower():
        return "Invalid Garmin email or password."
    if "429" in message:
        return "Garmin rate limited the login attempt. Wait and try again."
    if "500" in message or "503" in message:
        return "Garmin Connect service is currently failing. Try again later."
    return message.split(":")[0]
