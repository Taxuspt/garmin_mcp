"""
Session manager for per-user Garmin sessions in remote mode.

Persists Garmin Connect sessions in garminconnect's native token format
(``garmin_tokens.json`` with a DI bearer token) per-user on disk, and caches
active clients in memory. The login flow obtains tokens via garth SSO and they
are bridged into garminconnect's format on save (see
:meth:`SessionManager.create_session_from_garth_tokens`).
"""

from __future__ import annotations

import logging
import os
import time
import threading
from typing import Dict, Optional

from garminconnect import Garmin

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages per-user Garmin Connect sessions."""

    # Cache TTL in seconds (1 hour)
    CACHE_TTL = 3600

    def __init__(self, storage_path: str):
        self.storage_path = storage_path
        self._cache: Dict[str, _CachedClient] = {}
        self._token_user_map: Dict[str, str] = {}
        self._lock = threading.Lock()
        os.makedirs(storage_path, exist_ok=True)

    def get_user_id_for_token(self, token: str) -> Optional[str]:
        """Get the user_id associated with an access token."""
        return self._token_user_map.get(token)

    def set_token_user_mapping(self, token: str, user_id: str) -> None:
        """Associate an access token with a user_id."""
        self._token_user_map[token] = user_id

    def _user_token_dir(self, user_id: str) -> str:
        """Get the token storage directory for a user."""
        return os.path.join(self.storage_path, user_id)

    def get_client(self, user_id: str) -> Optional[Garmin]:
        """Get or restore a Garmin client for a user.

        Returns None if no session exists for this user.
        """
        with self._lock:
            # Check memory cache first
            cached = self._cache.get(user_id)
            if cached and not cached.is_expired():
                return cached.client

            # Try to restore from disk
            token_dir = self._user_token_dir(user_id)
            if not os.path.isdir(token_dir):
                logger.warning(
                    "No Garmin session on disk for user %s (looked in %s)",
                    user_id,
                    token_dir,
                )
                return None

            try:
                garmin = Garmin()
                # NOTE: this performs live Garmin Connect API calls (social
                # profile + user settings) and refreshes tokens if needed, so it
                # can fail for reasons unrelated to the stored tokens being
                # missing (expired refresh token, Garmin rate-limiting/blocking
                # the server IP, transient network errors, etc.).
                garmin.login(token_dir)
                self._cache[user_id] = _CachedClient(garmin, self.CACHE_TTL)
                return garmin
            except Exception:
                # Don't swallow silently: the cause is otherwise invisible and
                # surfaces only as a generic "session expired" to the user.
                logger.exception(
                    "Failed to restore Garmin session for user %s from %s",
                    user_id,
                    token_dir,
                )
                return None

    def create_session(self, user_id: str, email: str, password: str) -> Garmin:
        """Create a new Garmin session for a user.

        Logs in with credentials and persists the garth tokens.

        Raises:
            Exception: If login fails.
        """
        garmin = Garmin(email=email, password=password, is_cn=False)
        garmin.login()

        # Persist tokens to disk
        token_dir = self._user_token_dir(user_id)
        os.makedirs(token_dir, exist_ok=True)
        garmin.garth.dump(token_dir)

        # Cache in memory
        with self._lock:
            self._cache[user_id] = _CachedClient(garmin, self.CACHE_TTL)

        return garmin

    def create_session_from_garth_tokens(
        self, user_id: str, oauth1_token, oauth2_token
    ) -> None:
        """Persist an SSO login as a garminconnect-native session.

        The login flow obtains tokens via garth SSO (OAuth1/OAuth2), but
        garminconnect 0.3.2 — which makes the actual API calls when we restore
        a session in :meth:`get_client` — no longer uses garth. It authenticates
        with a DI bearer token persisted as a single ``garmin_tokens.json``
        (keys: ``di_token`` / ``di_refresh_token`` / ``di_client_id``).

        The garth OAuth2 access/refresh tokens ARE that DI bearer/refresh pair,
        so we bridge them into garminconnect's client and let it persist in its
        own format. Dumping with garth's two-file format here is what previously
        made every restore fail with "session expired" (garminconnect looked for
        garmin_tokens.json, found oauth{1,2}_token.json, and fell back to
        username/password it doesn't have).

        Args:
            user_id: The user identifier.
            oauth1_token: garth OAuth1Token from SSO login (unused; kept for the
                call signature — garminconnect 0.3.2 does not use OAuth1).
            oauth2_token: garth OAuth2Token from SSO login; its ``access_token``
                is the DI bearer and ``refresh_token`` the DI refresh token.
        """
        garmin = Garmin()
        client = garmin.client
        client.di_token = oauth2_token.access_token
        client.di_refresh_token = oauth2_token.refresh_token
        extract = getattr(client, "_extract_client_id_from_jwt", None)
        if extract is not None:
            try:
                client.di_client_id = extract(oauth2_token.access_token)
            except Exception:
                logger.debug("Could not extract di_client_id from JWT", exc_info=True)

        token_dir = self._user_token_dir(user_id)
        os.makedirs(token_dir, exist_ok=True)
        client.dump(token_dir)  # writes garmin_tokens.json

        # Invalidate cache so next get_client() reloads from disk
        with self._lock:
            self._cache.pop(user_id, None)

    def remove_session(self, user_id: str) -> bool:
        """Remove a user's Garmin session and tokens.

        Returns True if session existed and was removed.
        """
        import shutil

        removed = False

        with self._lock:
            if user_id in self._cache:
                del self._cache[user_id]
                removed = True

        token_dir = self._user_token_dir(user_id)
        if os.path.isdir(token_dir):
            shutil.rmtree(token_dir)
            removed = True

        return removed

    def has_session(self, user_id: str) -> bool:
        """Check if a user has a stored Garmin session."""
        token_dir = self._user_token_dir(user_id)
        return os.path.isdir(token_dir)


class _CachedClient:
    """A cached Garmin client with TTL."""

    def __init__(self, client: Garmin, ttl: int):
        self.client = client
        self._expires_at = time.time() + ttl

    def is_expired(self) -> bool:
        return time.time() > self._expires_at
