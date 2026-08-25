"""Coverage for the shared-token bug ai-docs/shared-token-store.md exists to
explain: this server used to read the shared token file once at startup and
never look at it again, so a peer service (garmin-scale-sync,
hevy2garmin-lite) rotating the refresh token left it holding a dead one until
a manual restart.

These tests pin the three behaviours that fix it: adopt a peer's rotation,
publish our own, and never publish a rejected token. Adapted from
garmin-scale-sync's ``src/tests/test_garmin_session.py`` (same bug, same
fix); Sqlite/Postgres-store and typed-401 coverage aren't ported here — this
server only uses ``FileTokenStore``, and typed-401 has its own
tests/unit/test_typed_auth_errors.py tailored to this repo's garminconnect
version.
"""

import json
import os
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from garminconnect import GarminConnectAuthenticationError

from garmin_mcp.garmin_session import FileTokenStore, GarminSession


def _blob(token: str) -> str:
    return json.dumps({"di_token": token, "di_refresh_token": f"rt-{token}", "di_client_id": "cid"})


def _session(tmp_path, store):
    return GarminSession(
        store=store,
        scratch_dir=tmp_path / "scratch",
        email="user@example.com",
        password="secret",
    )


def _client_holding(blob: str) -> MagicMock:
    """A stand-in Garmin whose dumps() reports the given token blob."""
    garmin = MagicMock()
    garmin.client.dumps.return_value = blob
    return garmin


# ---------------------------------------------------------------------------
# FileTokenStore
# ---------------------------------------------------------------------------


def test_file_store_roundtrip(tmp_path):
    store = FileTokenStore(tmp_path)
    with store.locked():
        assert store.load() is None
        store.save(_blob("a"))
        assert store.load() == _blob("a")


def test_file_store_rejects_malformed_blob(tmp_path):
    store = FileTokenStore(tmp_path)
    store.directory.mkdir(parents=True, exist_ok=True)
    store.token_path.write_text("not json", encoding="utf-8")
    assert store.load() is None


def test_file_store_rejects_blob_without_di_token(tmp_path):
    """A peer mid-migration could leave a differently shaped payload."""
    store = FileTokenStore(tmp_path)
    store.directory.mkdir(parents=True, exist_ok=True)
    store.token_path.write_text(json.dumps({"oauth1_token": "x"}), encoding="utf-8")
    assert store.load() is None


def test_file_store_save_is_atomic(tmp_path):
    """The library's own dump() is a bare write_text(), so a peer can read a
    half-written token. Ours must swap the file in via os.replace."""
    store = FileTokenStore(tmp_path)
    store.save(_blob("first"))

    real_replace = os.replace
    seen = {}

    def capture(src, dst):
        # Before the swap lands, the destination must still hold the old value.
        seen["during"] = Path(dst).read_text(encoding="utf-8")
        return real_replace(src, dst)

    with patch("garmin_mcp.garmin_session.stores.os.replace", side_effect=capture):
        store.save(_blob("second"))

    assert seen["during"] == _blob("first")
    assert store.load() == _blob("second")


def test_file_store_leaves_no_temp_files(tmp_path):
    store = FileTokenStore(tmp_path)
    store.save(_blob("a"))
    store.save(_blob("b"))
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_file_store_lock_is_exclusive(tmp_path):
    """flock must actually serialise — otherwise two processes can refresh at
    once and rotate each other's token away."""
    store = FileTokenStore(tmp_path)
    second_acquired = threading.Event()

    def grab():
        with FileTokenStore(tmp_path).locked():
            second_acquired.set()

    with store.locked():
        worker = threading.Thread(target=grab, daemon=True)
        worker.start()
        assert not second_acquired.wait(timeout=0.5), "lock did not exclude a second holder"

    worker.join(timeout=5)
    assert second_acquired.is_set(), "lock was never released"


# ---------------------------------------------------------------------------
# GarminSession — the actual bug
# ---------------------------------------------------------------------------


def test_adopts_token_rotated_by_a_peer(tmp_path):
    """The core fix: a peer rotating the shared token must not strand us."""
    store = FileTokenStore(tmp_path)
    store.save(_blob("original"))
    session = _session(tmp_path, store)

    first = _client_holding(_blob("original"))
    second = _client_holding(_blob("peer-rotated"))

    with patch.object(session, "_login_with_blob", side_effect=[first, second]) as login:
        with session.client() as client:
            assert client is first

        # A peer refreshes and publishes a new token.
        store.save(_blob("peer-rotated"))

        with session.client() as client:
            assert client is second, "session reused a client built from a superseded token"

    assert login.call_args_list[1].args[0] == _blob("peer-rotated")


def test_reuses_client_when_store_is_unchanged(tmp_path):
    """Re-reading must not mean re-logging-in every call — that would hammer
    Garmin's SSO endpoint and invite the rate limiting we're trying to avoid."""
    store = FileTokenStore(tmp_path)
    store.save(_blob("stable"))
    session = _session(tmp_path, store)

    garmin = _client_holding(_blob("stable"))
    with patch.object(session, "_login_with_blob", return_value=garmin) as login:
        with session.client():
            pass
        with session.client():
            pass

    assert login.call_count == 1


def test_publishes_our_own_rotation(tmp_path):
    """If we rotate, peers must see it immediately — otherwise they keep using
    the token we just invalidated."""
    store = FileTokenStore(tmp_path)
    store.save(_blob("before"))
    session = _session(tmp_path, store)

    garmin = _client_holding(_blob("before"))
    with patch.object(session, "_login_with_blob", return_value=garmin), session.client() as client:
        # garminconnect refreshed mid-call and rotated the token.
        client.client.dumps.return_value = _blob("after")

    assert store.load() == _blob("after")


def test_auth_failure_does_not_publish_the_dead_token(tmp_path):
    """A rejected token must never overwrite a peer's good one."""
    store = FileTokenStore(tmp_path)
    store.save(_blob("good-from-peer"))
    session = _session(tmp_path, store)

    garmin = _client_holding(_blob("good-from-peer"))
    with (
        patch.object(session, "_login_with_blob", return_value=garmin),
        pytest.raises(GarminConnectAuthenticationError),
        session.client() as client,
    ):
        client.client.dumps.return_value = _blob("dead")
        raise GarminConnectAuthenticationError("rejected")

    assert store.load() == _blob("good-from-peer")


def test_auth_failure_drops_the_cached_client(tmp_path):
    """The regression this whole module exists to fix: the poisoned client
    stayed cached and every later call failed the same way until a restart."""
    store = FileTokenStore(tmp_path)
    store.save(_blob("t"))
    session = _session(tmp_path, store)

    garmin = _client_holding(_blob("t"))
    with (
        patch.object(session, "_login_with_blob", return_value=garmin),
        pytest.raises(GarminConnectAuthenticationError),
        session.client(),
    ):
        raise GarminConnectAuthenticationError("rejected")

    assert session.is_authenticated is False


def test_falls_back_to_credentials_when_stored_token_is_rejected(tmp_path):
    store = FileTokenStore(tmp_path)
    store.save(_blob("stale"))
    session = _session(tmp_path, store)

    fresh = _client_holding(_blob("fresh"))
    with (
        patch.object(session, "_login_with_blob", side_effect=GarminConnectAuthenticationError("stale")),
        patch.object(session, "_credential_login", return_value=fresh) as credential,
        session.client() as client,
    ):
        assert client is fresh

    credential.assert_called_once()
    assert store.load() == _blob("fresh"), "credential login result was not published"


def test_credential_login_without_credentials_raises(tmp_path):
    store = FileTokenStore(tmp_path)
    session = GarminSession(store=store, scratch_dir=tmp_path / "scratch")

    with pytest.raises(GarminConnectAuthenticationError, match="No usable"), session.client():
        pass


def test_shared_store_is_never_handed_to_the_library(tmp_path):
    """garminconnect dumps rotations to whatever path it's given. Handing it
    the shared file would let it write outside our lock discipline, so it only
    ever sees a private scratch copy."""
    store = FileTokenStore(tmp_path / "shared")
    store.save(_blob("t"))
    scratch = tmp_path / "scratch"
    session = GarminSession(store=store, scratch_dir=scratch, email="e", password="p")

    with patch("garmin_mcp.garmin_session.session.Garmin") as garmin_cls:
        garmin_cls.return_value = _client_holding(_blob("t"))
        with session.client():
            pass

    login_path = garmin_cls.return_value.login.call_args.args[0]
    assert login_path == str(scratch)
    assert (scratch / "garmin_tokens.json").read_text(encoding="utf-8") == _blob("t")
