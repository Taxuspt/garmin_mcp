"""
Unit tests for SessionManager token persistence.

Regression coverage for the garth -> garminconnect token-format bridge: the
login flow yields garth OAuth2 tokens, but garminconnect 0.3.2 (used to restore
sessions and make API calls) persists/loads a single ``garmin_tokens.json`` with
a DI bearer token. Writing garth's two-file format here used to make every
restore fail with a generic "session expired" error.

These tests verify the on-disk format only (no network / no live Garmin login).
"""

import os
from types import SimpleNamespace

import pytest

from garminconnect.client import Client
from garmin_mcp.session_manager import SessionManager

pytestmark = pytest.mark.unit


def _fake_oauth2(access="fake-di-token", refresh="fake-di-refresh"):
    # Mimics the attributes SessionManager reads off a garth OAuth2Token.
    return SimpleNamespace(access_token=access, refresh_token=refresh)


def test_persists_in_garminconnect_native_format(tmp_path):
    sm = SessionManager(str(tmp_path))
    sm.create_session_from_garth_tokens("user-1", oauth1_token=None, oauth2_token=_fake_oauth2())

    token_dir = os.path.join(str(tmp_path), "user-1")
    files = os.listdir(token_dir)

    # garminconnect 0.3.2 restores from garmin_tokens.json, NOT the old
    # garth oauth1_token.json / oauth2_token.json pair.
    assert files == ["garmin_tokens.json"]
    assert "oauth1_token.json" not in files
    assert "oauth2_token.json" not in files


def test_persisted_tokens_load_into_garminconnect_client(tmp_path):
    sm = SessionManager(str(tmp_path))
    sm.create_session_from_garth_tokens(
        "user-1", oauth1_token=None, oauth2_token=_fake_oauth2(access="di-bearer-xyz")
    )
    token_dir = os.path.join(str(tmp_path), "user-1")

    # A fresh garminconnect client must be able to load what we wrote and end up
    # authenticated (di_token present). This is exactly the load step that ran
    # inside get_client() -> Garmin().login(token_dir) before the live API call.
    client = Client()
    client.load(token_dir)
    assert client.di_token == "di-bearer-xyz"
    assert client.is_authenticated is True
