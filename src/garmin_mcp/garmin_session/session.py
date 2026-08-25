"""A Garmin session that treats the shared token store as the source of truth.

The bug this exists to remove: this server read the shared token file once at
startup, cached the resulting client for the life of the process, and never
looked at the file again. Because Garmin rotates the refresh token on every
refresh, a peer service (garmin-scale-sync, hevy2garmin-lite) refreshing meant
this process was holding a token that had already been replaced — and the
resulting 401 arrived typed as a connection error (see errors.py), so the
poisoned client was never dropped. Every subsequent tool call then failed
identically until the process was restarted by hand. See
ai-docs/shared-token-store.md.

``GarminSession`` closes that by never trusting its in-memory copy:

* **Before each use** it re-reads the store. If the blob moved, a peer rotated
  the token, so the cached client is dropped and rebuilt from the current one.
* **After each use** it publishes, so a rotation this process performed is
  immediately visible to peers.
* **On an auth failure** it invalidates, so the next call rebuilds from the
  store rather than reusing a client Garmin has already rejected.

Both steps happen inside ``store.locked()``, so a peer cannot interleave a
refresh between the read and the write.

The library is never given the shared path. Tokens are materialised into a
private scratch directory and *that* is handed to ``Garmin.login()``, so
garminconnect's internal ``_refresh_session()`` dumps rotations somewhere
harmless; this class remains the only writer to the shared store.
"""

import contextlib
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

from garminconnect import Garmin, GarminConnectAuthenticationError

from . import errors
from .stores import TOKEN_FILE_NAME, TokenStore

logger = logging.getLogger("garmin_mcp")


class GarminSession:
    def __init__(
        self,
        store: TokenStore,
        scratch_dir,
        email: str | None = None,
        password: str | None = None,
        prompt_mfa: Callable[[], str] | None = None,
        is_cn: bool = False,
    ):
        errors.install()
        self._store = store
        self._scratch_dir = Path(scratch_dir).expanduser()
        self._email = email
        self._password = password
        self._prompt_mfa = prompt_mfa
        self._is_cn = is_cn

        self._client: Garmin | None = None
        # The blob this process last agreed on with the store — whether it read
        # it or wrote it. Divergence from the store means a peer rotated;
        # divergence of the live client from this means *we* rotated.
        self._synced_blob: str | None = None
        self._last_error: str | None = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ state

    @property
    def is_authenticated(self) -> bool:
        return self._client is not None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def invalidate(self) -> None:
        """Drop the cached client so the next use rebuilds from the store."""
        with self._lock:
            self._client = None
            self._synced_blob = None

    def has_stored_tokens(self) -> bool:
        with self._store.locked():
            return self._store.load() is not None

    def warm(self) -> None:
        """Establish the session up front. Raises on failure."""
        with self.client():
            pass

    # ----------------------------------------------------------------- public

    def acquire(self) -> Garmin:
        """Return a client, re-validated against the shared store.

        Holds the store lock only while resolving the client, not for the
        duration of the caller's work. Callers that don't go through
        :meth:`client` are responsible for calling :meth:`publish` and
        :meth:`invalidate` themselves — see ``_GarminProxy`` in
        ``garmin_mcp/__init__.py`` for how tool calls do this.
        """
        with self._lock, self._store.locked():
            return self._acquire()

    def publish(self) -> None:
        """Write back a rotation performed since the client was acquired."""
        with self._lock, self._store.locked():
            if self._client is not None:
                self._publish_if_rotated(self._client)

    @contextlib.contextmanager
    def client(self):
        """Yield an authenticated client, holding the store lock throughout.

        The lock spans the whole operation because garminconnect can refresh
        (and therefore rotate) inside any call, not just at login.
        """
        with self._lock, self._store.locked():
            garmin = self._acquire()
            try:
                yield garmin
            except GarminConnectAuthenticationError as exc:
                # Our token is dead. Publishing it would overwrite a peer's
                # good token with a rejected one, so drop it unpublished.
                logger.warning("Garmin rejected the session mid-call: %s", exc)
                self._last_error = str(exc)
                self._client = None
                self._synced_blob = None
                raise
            else:
                self._publish_if_rotated(garmin)

    # ---------------------------------------------------------------- internal

    def _acquire(self) -> Garmin:
        stored = self._store.load()

        if self._client is not None and stored != self._synced_blob:
            logger.info("Shared token store changed underneath us — adopting the peer's token.")
            self._client = None

        if self._client is not None:
            return self._client

        if stored is not None:
            try:
                garmin = self._login_with_blob(stored)
                self._client = garmin
                self._synced_blob = stored
                self._last_error = None
                # login() may itself have refreshed a near-expiry token.
                self._publish_if_rotated(garmin)
                return garmin
            except GarminConnectAuthenticationError as exc:
                logger.warning(
                    "Stored Garmin tokens were rejected (%s) — falling back to a credential login.",
                    exc,
                )

        garmin = self._credential_login()
        self._client = garmin
        self._last_error = None
        self._publish_if_rotated(garmin)
        return garmin

    def _publish_if_rotated(self, garmin: Garmin) -> None:
        current = garmin.client.dumps()
        if current and current != self._synced_blob:
            self._store.save(current)
            self._synced_blob = current
            logger.info("Published rotated Garmin token to the shared store.")

    def _new_client(self) -> Garmin:
        # errors.install() (called once in __init__) already patches the
        # Client class itself, so unlike the sibling module this needs no
        # per-instance instrumentation call — see garmin_session/errors.py.
        return Garmin(
            email=self._email or None,
            password=self._password or None,
            prompt_mfa=self._prompt_mfa,
            is_cn=self._is_cn,
        )

    def _login_with_blob(self, blob: str) -> Garmin:
        self._write_scratch(blob)
        garmin = self._new_client()
        garmin.login(str(self._scratch_dir))
        return garmin

    def _credential_login(self) -> Garmin:
        if not (self._email and self._password):
            raise GarminConnectAuthenticationError(
                "No usable Garmin tokens in the shared store and no "
                "GARMIN_EMAIL/GARMIN_PASSWORD configured to log in with."
            )
        # Clear the scratch copy so login() takes the credential path rather
        # than reloading the token that was just rejected.
        self._clear_scratch()
        garmin = self._new_client()
        garmin.login(str(self._scratch_dir))
        return garmin

    # ----------------------------------------------------------------- scratch

    @property
    def _scratch_token_path(self) -> Path:
        return self._scratch_dir / TOKEN_FILE_NAME

    def _write_scratch(self, blob: str) -> None:
        self._scratch_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._scratch_dir, 0o700)
        self._scratch_token_path.write_text(blob, encoding="utf-8")
        os.chmod(self._scratch_token_path, 0o600)

    def _clear_scratch(self) -> None:
        self._scratch_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            self._scratch_token_path.unlink()
