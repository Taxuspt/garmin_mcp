"""Structural coverage for PostgresTokenStore -- mocked psycopg2, no live DB.

Ported logic from garmin-scale-sync's identical class, verified there against
the real Neon database (ai-docs/RAG.md). These tests cover the SQL shape and
locking discipline without depending on external infrastructure -- see
ai-docs/testing.md ("do not hit real [external services] from tests").
"""

from unittest.mock import MagicMock, patch

import pytest

from garmin_mcp.garmin_session.stores import PostgresTokenStore


def _blob(token: str) -> str:
    import json

    return json.dumps({"di_token": token, "di_refresh_token": f"rt-{token}", "di_client_id": "cid"})


def _mock_connection():
    """A MagicMock standing in for a psycopg2 connection, with `with conn,
    conn.cursor() as cursor` support."""
    connection = MagicMock()
    cursor = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor
    connection.__enter__.return_value = connection
    return connection, cursor


def _store_with_mocked_connect():
    connection, cursor = _mock_connection()
    with patch.object(PostgresTokenStore, "_connect", return_value=connection):
        store = PostgresTokenStore("postgresql://user:pass@host/db")
    return store, connection, cursor


def test_init_creates_table_inside_a_lock():
    store, connection, cursor = _store_with_mocked_connect()

    assert store.database_url == "postgresql://user:pass@host/db"
    # The advisory lock is taken before the table-creation statement.
    lock_call, create_call = cursor.execute.call_args_list[:2]
    assert "pg_advisory_xact_lock" in lock_call.args[0]
    assert "CREATE TABLE IF NOT EXISTS garmin_tokens" in create_call.args[0]


def test_load_outside_locked_raises():
    store, _connection, _cursor = _store_with_mocked_connect()

    with pytest.raises(RuntimeError, match="inside locked"):
        store.load()


def test_load_returns_none_when_empty():
    store, connection, cursor = _store_with_mocked_connect()
    cursor.fetchone.return_value = None

    with patch.object(PostgresTokenStore, "_connect", return_value=connection), store.locked():
        assert store.load() is None


def test_load_returns_valid_blob():
    store, connection, cursor = _store_with_mocked_connect()
    cursor.fetchone.return_value = (_blob("a"),)

    with patch.object(PostgresTokenStore, "_connect", return_value=connection), store.locked():
        assert store.load() == _blob("a")


def test_load_rejects_blob_without_di_token():
    store, connection, cursor = _store_with_mocked_connect()
    cursor.fetchone.return_value = ('{"oauth1_token": "x"}',)

    with patch.object(PostgresTokenStore, "_connect", return_value=connection), store.locked():
        assert store.load() is None


def test_save_upserts_by_key():
    store, connection, cursor = _store_with_mocked_connect()

    with patch.object(PostgresTokenStore, "_connect", return_value=connection), store.locked():
        store.save(_blob("new"))

    save_call = cursor.execute.call_args_list[-1]
    assert "ON CONFLICT (key) DO UPDATE" in save_call.args[0]
    assert save_call.args[1] == (store.key, _blob("new"))


def test_locked_scopes_the_advisory_lock_to_the_configured_key():
    store, connection, cursor = _store_with_mocked_connect()
    cursor.reset_mock()

    with patch.object(PostgresTokenStore, "_connect", return_value=connection), store.locked():
        pass

    lock_call = cursor.execute.call_args_list[0]
    assert lock_call.args[1] == (store.key,)


def test_connect_without_psycopg2_gives_actionable_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "psycopg2":
            raise ImportError("no module named psycopg2")
        return real_import(name, *args, **kwargs)

    store = PostgresTokenStore.__new__(PostgresTokenStore)
    store.database_url = "postgresql://user:pass@host/db"

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    with pytest.raises(ImportError, match="psycopg2-binary"):
        store._connect()
