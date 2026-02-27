"""
Session manager for per-user Garmin sessions in remote mode.

Manages Garmin Connect sessions using garth tokens, storing them
per-user on disk and caching active clients in memory.
"""

from __future__ import annotations

import os
import time
import threading
from typing import Dict, Optional

from garminconnect import Garmin


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
                return None

            try:
                garmin = Garmin()
                garmin.login(token_dir)
                self._cache[user_id] = _CachedClient(garmin, self.CACHE_TTL)
                return garmin
            except Exception:
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
        """Persist garth tokens and invalidate the cache.

        Args:
            user_id: The user identifier.
            oauth1_token: garth OAuth1Token from SSO login.
            oauth2_token: garth OAuth2Token from SSO login.
        """
        from garth import Client as GarthClient

        garth_client = GarthClient()
        garth_client.oauth1_token = oauth1_token
        garth_client.oauth2_token = oauth2_token

        token_dir = self._user_token_dir(user_id)
        os.makedirs(token_dir, exist_ok=True)
        garth_client.dump(token_dir)

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
