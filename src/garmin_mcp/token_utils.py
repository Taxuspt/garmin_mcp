"""Token management utilities for Garmin MCP authentication."""

import os
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator
from typing import Tuple

from garminconnect import Garmin
from garth.exc import GarthHTTPError


def _clean_config_value(value: str | None, default: str) -> str:
    """Return a usable config value, ignoring blank or unresolved DXT placeholders."""
    if value is None:
        return default
    cleaned = value.strip()
    if not cleaned or cleaned.startswith("${user_config."):
        return default
    return cleaned


def resolve_path(path: str | None, default: str | None = None) -> str:
    """Expand user/env variables and return an absolute filesystem path."""
    fallback = default or "~/.garminconnect"
    cleaned = _clean_config_value(path, fallback)
    return os.path.abspath(os.path.expandvars(os.path.expanduser(cleaned)))


def ensure_token_directory(token_path: str | None = None) -> str:
    """Create the token directory, replacing an empty placeholder file if needed."""
    resolved_token_path = resolve_path(token_path or get_token_path())
    path = Path(resolved_token_path)
    if path.exists() and path.is_file():
        if path.stat().st_size == 0:
            path.unlink()
        else:
            raise ValueError(f"Token path must be a directory, but a file exists: {resolved_token_path}")
    path.mkdir(parents=True, exist_ok=True)
    return resolved_token_path


@contextmanager
def without_token_env() -> Iterator[None]:
    """Temporarily remove token env vars so credential login does not load tokens."""
    old_tokens = os.environ.pop("GARMINTOKENS", None)
    old_tokens_base64 = os.environ.pop("GARMINTOKENS_BASE64", None)
    try:
        yield
    finally:
        if old_tokens is not None:
            os.environ["GARMINTOKENS"] = old_tokens
        if old_tokens_base64 is not None:
            os.environ["GARMINTOKENS_BASE64"] = old_tokens_base64


def get_token_path() -> str:
    """Get token path from environment or default.

    Returns:
        str: Path to token storage directory
    """
    return _clean_config_value(os.getenv("GARMINTOKENS"), "~/.garminconnect")


def get_token_base64_path() -> str:
    """Get base64 token file path from environment or default.

    Returns:
        str: Path to base64 token file
    """
    return _clean_config_value(os.getenv("GARMINTOKENS_BASE64"), "~/.garminconnect_base64")


def token_exists(token_path: str = None) -> bool:
    """Check if token directory or file exists.

    Args:
        token_path: Optional custom token path. Uses default if not provided.

    Returns:
        bool: True if tokens exist, False otherwise
    """
    if token_path is None:
        token_path = get_token_path()

    expanded_path = Path(resolve_path(token_path))
    if expanded_path.is_file() and expanded_path.stat().st_size == 0:
        return False
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
    resolved_token_path = resolve_path(token_path)

    # Check if tokens exist
    if not token_exists(resolved_token_path):
        return False, f"Token directory not found: {resolved_token_path}"

    # Suppress stderr during validation to avoid confusing library error messages
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()

    try:
        garmin = Garmin()
        garmin.login(resolved_token_path)

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
        return False, f"Token files not found in: {resolved_token_path}"
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
    expanded_token_path = Path(resolve_path(token_path))
    if expanded_token_path.exists():
        if expanded_token_path.is_dir():
            shutil.rmtree(expanded_token_path)
        else:
            expanded_token_path.unlink()

    # Remove base64 token file
    expanded_base64_path = Path(resolve_path(base64_path, "~/.garminconnect_base64"))
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

    resolved_token_path = resolve_path(token_path)
    exists = token_exists(resolved_token_path)
    is_valid = False
    error_msg = ""

    if exists:
        is_valid, error_msg = validate_tokens(resolved_token_path)

    return {
        "path": token_path,
        "expanded_path": resolved_token_path,
        "exists": exists,
        "valid": is_valid,
        "error": error_msg
    }
