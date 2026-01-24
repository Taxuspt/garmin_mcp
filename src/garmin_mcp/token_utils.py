"""Token management utilities for Garmin MCP authentication."""

import os
from pathlib import Path
from typing import Tuple

from garminconnect import Garmin
from garth.exc import GarthHTTPError


def get_token_path() -> str:
    """Get token path from environment or default.

    Returns:
        str: Path to token storage directory
    """
    return os.getenv("GARMINTOKENS") or "~/.garminconnect"


def get_token_base64_path() -> str:
    """Get base64 token file path from environment or default.

    Returns:
        str: Path to base64 token file
    """
    return os.getenv("GARMINTOKENS_BASE64") or "~/.garminconnect_base64"


def token_exists(token_path: str = None) -> bool:
    """Check if token directory or file exists.

    Args:
        token_path: Optional custom token path. Uses default if not provided.

    Returns:
        bool: True if tokens exist, False otherwise
    """
    if token_path is None:
        token_path = get_token_path()

    expanded_path = Path(os.path.expanduser(token_path))
    return expanded_path.exists()


def validate_tokens(token_path: str = None) -> Tuple[bool, str]:
    """Validate tokens by attempting to use them.

    Args:
        token_path: Optional custom token path. Uses default if not provided.

    Returns:
        Tuple of (is_valid, error_message). error_message is empty string if valid.
    """
    import sys
    import io

    if token_path is None:
        token_path = get_token_path()

    # Check if tokens exist
    if not token_exists(token_path):
        return False, f"Token directory not found: {token_path}"

    # Suppress stderr during validation to avoid confusing library error messages
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()

    try:
        garmin = Garmin()
        garmin.login(token_path)

        # Try a simple API call to verify tokens work
        try:
            # Use get_full_name() as it doesn't require parameters
            garmin.get_full_name()
            return True, ""
        except Exception as e:
            # Extract clean error message
            error_msg = str(e)
            if "401" in error_msg or "Unauthorized" in error_msg:
                return False, "Tokens expired or invalid"
            elif "403" in error_msg or "Forbidden" in error_msg:
                return False, "Access denied with current tokens"
            else:
                return False, f"Authentication failed: {error_msg.split(':')[0]}"

    except FileNotFoundError:
        return False, f"Token files not found in: {token_path}"
    except GarthHTTPError as e:
        error_msg = str(e)
        if "401" in error_msg or "Unauthorized" in error_msg:
            return False, "Tokens expired or invalid"
        elif "403" in error_msg or "Forbidden" in error_msg:
            return False, "Access denied with current tokens"
        else:
            return False, f"Authentication error: {error_msg.split(':')[0]}"
    except Exception as e:
        error_msg = str(e)
        # Clean up error message
        if "401" in error_msg:
            return False, "Tokens expired or invalid"
        else:
            return False, f"Validation error: {error_msg.split(':')[0]}"
    finally:
        # Restore stderr
        sys.stderr = old_stderr


def remove_tokens(token_path: str = None, base64_path: str = None) -> None:
    """Safely remove stored tokens.

    Args:
        token_path: Optional custom token directory path. Uses default if not provided.
        base64_path: Optional custom base64 token file path. Uses default if not provided.
    """
    import shutil

    if token_path is None:
        token_path = get_token_path()
    if base64_path is None:
        base64_path = get_token_base64_path()

    # Remove token directory
    expanded_token_path = Path(os.path.expanduser(token_path))
    if expanded_token_path.exists():
        if expanded_token_path.is_dir():
            shutil.rmtree(expanded_token_path)
        else:
            expanded_token_path.unlink()

    # Remove base64 token file
    expanded_base64_path = Path(os.path.expanduser(base64_path))
    if expanded_base64_path.exists():
        expanded_base64_path.unlink()


def get_token_info(token_path: str = None) -> dict:
    """Get information about stored tokens.

    Args:
        token_path: Optional custom token path. Uses default if not provided.

    Returns:
        dict: Token information including existence, validity, and path
    """
    if token_path is None:
        token_path = get_token_path()

    exists = token_exists(token_path)
    is_valid = False
    error_msg = ""

    if exists:
        is_valid, error_msg = validate_tokens(token_path)

    return {
        "path": token_path,
        "expanded_path": os.path.expanduser(token_path),
        "exists": exists,
        "valid": is_valid,
        "error": error_msg
    }
