"""Token management utilities for Garmin MCP authentication."""

import errno
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from garminconnect import Garmin, GarminConnectConnectionError


class TokenBootstrapError(ValueError):
    """Raised when configured bootstrap tokens cannot be installed safely."""


@dataclass(frozen=True)
class TokenBootstrapResult:
    """Outcome of a configured token bootstrap."""

    path: Path
    installed: bool


_REQUIRED_TOKEN_FIELDS = ("di_token",)
_LINK_FALLBACK_ERRNOS = {
    errno.EACCES,
    errno.EPERM,
    errno.EXDEV,
    errno.EINVAL,
    getattr(errno, "ENOSYS", errno.EPERM),
    getattr(errno, "ENOTSUP", errno.EPERM),
    getattr(errno, "EOPNOTSUPP", errno.EPERM),
}


def resolve_token_path(path: str) -> str:
    """Resolve environment variables and the user-home marker in a token path.

    Some MCP clients leave ``${HOME}`` unresolved when it comes from a nested
    user-config default. The explicit replacement also covers Windows, where
    ``HOME`` may be unset but Python can still resolve ``~`` via ``USERPROFILE``.
    """
    expanded = os.path.expandvars(path)
    expanded = expanded.replace("${HOME}", os.path.expanduser("~"))
    return os.path.expanduser(expanded)


def secure_token_dir(path: str) -> None:
    """Set owner-only permissions on a token directory and the files inside it.

    OAuth tokens are ~6-month bearer credentials to the full Garmin account, so
    they must not be left world-readable on multi-user hosts. Safe to call on a
    path that is a single file rather than a directory.
    """
    expanded = resolve_token_path(path)
    if not os.path.exists(expanded):
        return
    if os.path.isdir(expanded):
        os.chmod(expanded, 0o700)
        for entry in os.scandir(expanded):
            if entry.is_file():
                os.chmod(entry.path, 0o600)
    else:
        os.chmod(expanded, 0o600)


def get_token_path() -> str:
    """Get token path from environment or default.

    Returns:
        str: Path to token storage directory
    """
    return resolve_token_path(os.getenv("GARMINTOKENS") or "~/.garminconnect")


def get_token_base64_path() -> str:
    """Get base64 token file path from environment or default.

    Returns:
        str: Path to base64 token file
    """
    return resolve_token_path(
        os.getenv("GARMINTOKENS_BASE64") or "~/.garminconnect_base64"
    )


def get_token_json_path(token_path: str) -> Path:
    """Return the JSON file used by garminconnect for a token-store path."""
    store = Path(resolve_token_path(token_path))
    if store.is_dir() or not store.name.endswith(".json"):
        return store / "garmin_tokens.json"
    return store


def _parse_bootstrap_tokens(raw_tokens: str) -> dict:
    """Validate bootstrap JSON without making a Garmin network request."""
    if not raw_tokens.strip():
        raise TokenBootstrapError("the configured token value is empty")

    try:
        tokens = json.loads(raw_tokens)
    except json.JSONDecodeError as exc:
        raise TokenBootstrapError(
            f"the configured token value is not valid JSON ({exc.msg})"
        ) from None

    if not isinstance(tokens, dict):
        raise TokenBootstrapError("the configured token value must be a JSON object")

    missing = [
        field
        for field in _REQUIRED_TOKEN_FIELDS
        if not isinstance(tokens.get(field), str) or not tokens[field].strip()
    ]
    if missing:
        raise TokenBootstrapError(
            "the configured token value is missing non-empty field(s): "
            + ", ".join(missing)
        )

    return tokens


def _write_tokens_atomically(target: Path, tokens: dict) -> bool:
    """Install validated tokens safely without replacing another writer."""
    parent_existed = target.parent.exists()
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not parent_existed:
            os.chmod(target.parent, 0o700)
    except OSError as exc:
        raise TokenBootstrapError(
            f"cannot prepare token directory '{target.parent}': {exc.strerror or exc}"
        ) from None

    fd = None
    temp_path = None
    try:
        fd, temp_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
        )
        temp_path = Path(temp_name)
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as token_file:
            fd = None
            json.dump(tokens, token_file, separators=(",", ":"))
            token_file.write("\n")
            token_file.flush()
            os.fsync(token_file.fileno())
        try:
            # The hard link is an atomic create-if-absent because the temporary
            # file lives in the destination directory. Concurrent replicas can
            # bootstrap the same shared volume without overwriting each other.
            os.link(temp_path, target)
        except FileExistsError:
            if target.is_file() and not target.is_symlink():
                return False
            raise
        except OSError as exc:
            if exc.errno not in _LINK_FALLBACK_ERRNOS:
                raise
            return _write_tokens_exclusively(target, temp_path)
        return True
    except OSError as exc:
        raise TokenBootstrapError(
            f"cannot write token file '{target}': {exc.strerror or exc}"
        ) from None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _write_tokens_exclusively(target: Path, source: Path) -> bool:
    """Fallback for filesystems that cannot hard-link the prepared token file."""
    fd = None
    created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        fd = os.open(target, flags, 0o600)
        created = True
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        payload = source.read_bytes()
        remaining = memoryview(payload)
        while remaining:
            written = os.write(fd, remaining)
            if written == 0:
                raise OSError("zero-byte write while installing token file")
            remaining = remaining[written:]
        os.fsync(fd)
        return True
    except FileExistsError:
        if target.is_file() and not target.is_symlink():
            return False
        raise
    except Exception:
        if created:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _non_blank_env(name: str) -> str | None:
    """Return a configured value, treating empty/whitespace values as unset."""
    value = os.getenv(name)
    return value if value is not None and value.strip() else None


def bootstrap_tokens(token_path: str = None) -> TokenBootstrapResult | None:
    """Initialize an empty writable token store from a deployment secret.

    ``GARMIN_TOKENS_FILE`` points to a JSON secret file and is the recommended
    input. ``GARMIN_TOKENS_JSON`` accepts the same document inline for platforms
    that cannot mount secrets. Exactly one may be configured.

    Existing ``garmin_tokens.json`` files are never overwritten. This lets
    garminconnect persist refreshed tokens in the writable token store rather
    than replacing them with stale bootstrap credentials on every restart.

    Returns:
        The configured bootstrap outcome, or ``None`` when no source was set.
    """
    source_file = _non_blank_env("GARMIN_TOKENS_FILE")
    inline_tokens = _non_blank_env("GARMIN_TOKENS_JSON")

    if source_file is None and inline_tokens is None:
        return None
    if source_file is not None and inline_tokens is not None:
        raise TokenBootstrapError(
            "set only one of GARMIN_TOKENS_FILE and GARMIN_TOKENS_JSON"
        )

    if token_path is None:
        token_path = get_token_path()
    target = get_token_json_path(token_path)

    # A writable store may contain tokens refreshed after the bootstrap secret
    # was created. It is therefore the source of truth once initialized.
    if target.is_symlink():
        raise TokenBootstrapError(
            f"token destination '{target}' must not be a symbolic link"
        )
    if target.is_file():
        return TokenBootstrapResult(target, installed=False)
    if target.exists():
        raise TokenBootstrapError(
            f"token destination '{target}' exists but is not a regular file"
        )

    if source_file is not None:
        source = Path(resolve_token_path(source_file))
        try:
            raw_tokens = source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise TokenBootstrapError(
                f"token secret '{source}' is not valid UTF-8"
            ) from None
        except OSError as exc:
            raise TokenBootstrapError(
                f"cannot read token secret '{source}': {exc.strerror or exc}"
            ) from None
    else:
        raw_tokens = inline_tokens

    tokens = _parse_bootstrap_tokens(raw_tokens)
    installed = _write_tokens_atomically(target, tokens)
    return TokenBootstrapResult(target, installed=installed)


def token_exists(token_path: str = None) -> bool:
    """Check if token directory or file exists.

    Args:
        token_path: Optional custom token path. Uses default if not provided.

    Returns:
        bool: True if tokens exist, False otherwise
    """
    if token_path is None:
        token_path = get_token_path()

    expanded_path = Path(resolve_token_path(token_path))
    return expanded_path.exists()


def validate_tokens(token_path: str = None, is_cn: bool = False) -> Tuple[bool, str]:
    """Validate tokens by attempting to use them.

    Args:
        token_path: Optional custom token path. Uses default if not provided.
        is_cn: Use Garmin Connect China (garmin.cn) instead of international.

    Returns:
        Tuple of (is_valid, error_message). error_message is empty string if valid.
    """
    import sys
    import io

    if token_path is None:
        token_path = get_token_path()
    token_path = resolve_token_path(token_path)

    # Check if tokens exist
    if not token_exists(token_path):
        return False, f"Token directory not found: {token_path}"

    # Suppress stderr during validation to avoid confusing library error messages
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()

    try:
        garmin = Garmin(is_cn=is_cn)
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
    except GarminConnectConnectionError as e:
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
    token_path = resolve_token_path(token_path)
    base64_path = resolve_token_path(base64_path)

    # Remove token directory
    expanded_token_path = Path(token_path)
    if expanded_token_path.exists():
        if expanded_token_path.is_dir():
            shutil.rmtree(expanded_token_path)
        else:
            expanded_token_path.unlink()

    # Remove base64 token file
    expanded_base64_path = Path(base64_path)
    if expanded_base64_path.exists():
        expanded_base64_path.unlink()


def get_token_info(token_path: str = None, is_cn: bool = False) -> dict:
    """Get information about stored tokens.

    Args:
        token_path: Optional custom token path. Uses default if not provided.
        is_cn: Use Garmin Connect China (garmin.cn) instead of international.

    Returns:
        dict: Token information including existence, validity, and path
    """
    if token_path is None:
        token_path = get_token_path()
    token_path = resolve_token_path(token_path)

    exists = token_exists(token_path)
    is_valid = False
    error_msg = ""

    if exists:
        is_valid, error_msg = validate_tokens(token_path, is_cn=is_cn)

    return {
        "path": token_path,
        "expanded_path": token_path,
        "exists": exists,
        "valid": is_valid,
        "error": error_msg
    }
