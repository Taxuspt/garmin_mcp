"""
Per-user Garmin token storage, encrypted at rest in Upstash Redis.
Each user is identified by their own auth token (the one they'll put
into the iOS app / connector URL). We never store that auth token
itself unencrypted anywhere except as the lookup key.
"""

import os
import json
from upstash_redis import Redis
from cryptography.fernet import Fernet

_redis = None
_fernet = None


def _get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis(
            url=os.environ["UPSTASH_REDIS_REST_URL"],
            token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
        )
    return _redis


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = os.environ["GARMIN_MCP_ENCRYPTION_KEY"]
        _fernet = Fernet(key.encode())
    return _fernet


def save_user_tokens(user_auth_token: str, garmin_token_json: str) -> None:
    """Encrypt and store one user's Garmin OAuth tokens."""
    encrypted = _get_fernet().encrypt(garmin_token_json.encode()).decode()
    _get_redis().set(f"user:{user_auth_token}:garmin_tokens", encrypted)


def load_user_tokens(user_auth_token: str) -> str | None:
    """Look up and decrypt one user's Garmin OAuth tokens, or None if unknown."""
    encrypted = _get_redis().get(f"user:{user_auth_token}:garmin_tokens")
    if encrypted is None:
        return None
    return _get_fernet().decrypt(encrypted.encode()).decode()


def user_exists(user_auth_token: str) -> bool:
    return _get_redis().get(f"user:{user_auth_token}:garmin_tokens") is not None
