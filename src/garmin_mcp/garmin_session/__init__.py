"""Shared-token-store auth layer for Garmin Connect.

Ported from the ``garmin_session`` module already used by ``garmin-scale-sync``
and ``hevy2garmin-lite`` — see ai-docs/shared-token-store.md. Not a byte-for-byte
copy: adapted for garminconnect==0.3.2's different internals (errors.py) and
scoped to the file-only backend this single-host server needs (stores.py).
"""

from .session import GarminSession
from .stores import FileTokenStore, TokenStore

__all__ = ["FileTokenStore", "GarminSession", "TokenStore"]
