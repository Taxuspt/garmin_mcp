"""Shared-token-store auth layer for Garmin Connect.

Ported from the ``garmin_session`` module already used by ``garmin-scale-sync``
and ``hevy2garmin-lite`` — see ai-docs/shared-token-store.md. This package is
being built incrementally: ``errors.py`` (typed 401 detection) lands first,
the token store and session manager follow.
"""
