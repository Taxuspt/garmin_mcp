"""Token store for a Garmin account shared by several services.

Garmin rotates the refresh token on every refresh: ``_refresh_di_token`` does
``self.di_refresh_token = data.get("refresh_token", self.di_refresh_token)``,
and the previous refresh token dies server-side (verified against the
installed garminconnect==0.3.2 source — see ai-docs/RAG.md). There is
therefore exactly one valid refresh token for the account at any moment, no
matter how many processes are using it.

A store is the shared, authoritative home for that one token:

``load()``   read the current blob (``None`` when absent or malformed)
``save()``   replace it, atomically — a peer must never read a half-written token
``locked()`` hold an exclusive cross-process lock across a read/refresh/write

Only ``FileTokenStore`` is ported here — this server runs as a single local
MCP subprocess, not a multi-host deployment, so the Sqlite/Postgres backends
the sibling module also provides (for same-host and cross-host sharing
respectively) aren't needed yet. See ai-docs/shared-token-store.md.
"""

import contextlib
import fcntl
import json
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

TOKEN_FILE_NAME = "garmin_tokens.json"


def _is_valid_blob(blob: str | None) -> bool:
    """A usable token payload is JSON carrying a non-empty ``di_token``."""
    if not blob:
        return False
    try:
        data = json.loads(blob)
    except (TypeError, ValueError):
        return False
    return isinstance(data, dict) and bool(data.get("di_token"))


class TokenStore(ABC):
    @abstractmethod
    def load(self) -> str | None:
        """Return the stored token blob, or None if absent/unusable."""

    @abstractmethod
    def save(self, blob: str) -> None:
        """Atomically replace the stored token blob."""

    @abstractmethod
    def locked(self):
        """Context manager holding an exclusive cross-process lock."""


class FileTokenStore(TokenStore):
    """``garmin_tokens.json`` in a directory — the same format/path shape
    ``token_utils.get_token_path()`` already reads, and the format the other
    services sharing this account use. Writes go through a temp file +
    ``os.replace`` because the library's own ``dump()`` is a bare
    ``write_text()`` that a peer can catch mid-write."""

    def __init__(self, directory):
        self.directory = Path(directory).expanduser()

    @property
    def token_path(self) -> Path:
        return self.directory / TOKEN_FILE_NAME

    @property
    def lock_path(self) -> Path:
        return self.directory / ".garmin_tokens.lock"

    def load(self) -> str | None:
        try:
            blob = self.token_path.read_text(encoding="utf-8")
        except OSError:
            return None
        return blob if _is_valid_blob(blob) else None

    def save(self, blob: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        handle, tmp_path = tempfile.mkstemp(dir=str(self.directory), prefix=".garmin_tokens.", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self.token_path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    @contextlib.contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "w", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
