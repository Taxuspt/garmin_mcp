"""_build_token_store() must match the rest of this Garmin account's fleet.

garmin-scale-sync's live deployment runs TOKEN_STORE=postgres against a
shared Neon database -- see ai-docs/RAG.md. Pointing this service at a
different store splits it onto its own session; whichever service refreshes
second gets locked out. See ai-docs/shared-token-store.md.
"""

import pytest

from garmin_mcp import _build_token_store
from garmin_mcp.garmin_session import FileTokenStore, PostgresTokenStore


def test_defaults_to_file_store(monkeypatch):
    monkeypatch.delenv("TOKEN_STORE", raising=False)

    store = _build_token_store()

    assert isinstance(store, FileTokenStore)


def test_explicit_file_store(monkeypatch):
    monkeypatch.setenv("TOKEN_STORE", "file")

    store = _build_token_store()

    assert isinstance(store, FileTokenStore)


def test_postgres_store_requires_db_url(monkeypatch):
    monkeypatch.setenv("TOKEN_STORE", "postgres")
    monkeypatch.delenv("TOKEN_DB_URL", raising=False)

    with pytest.raises(ValueError, match="TOKEN_DB_URL"):
        _build_token_store()


def test_postgres_store_selected_with_db_url(monkeypatch):
    monkeypatch.setenv("TOKEN_STORE", "postgres")
    monkeypatch.setenv("TOKEN_DB_URL", "postgresql://user:pass@host/db")
    # PostgresTokenStore.__init__ connects immediately to create its table --
    # avoid a live DB in this test by patching the connection.
    from unittest.mock import MagicMock, patch

    with patch("garmin_mcp.garmin_session.stores.PostgresTokenStore._connect") as mock_connect:
        mock_connect.return_value = MagicMock()
        store = _build_token_store()

    assert isinstance(store, PostgresTokenStore)
    assert store.database_url == "postgresql://user:pass@host/db"


def test_unknown_store_kind_raises(monkeypatch):
    monkeypatch.setenv("TOKEN_STORE", "carrier-pigeon")

    with pytest.raises(ValueError, match="Unknown TOKEN_STORE"):
        _build_token_store()


def test_store_kind_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("TOKEN_STORE", "FILE")

    store = _build_token_store()

    assert isinstance(store, FileTokenStore)
