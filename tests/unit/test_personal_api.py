"""Tests for the official Garmin Connect Personal API backend.

Every test here is offline: Fakes stand in for ``requests`` so the suite can
assert the exact requests the backend makes (host, path, headers) without a key
or a Garmin account.
"""

import base64
import json
import tempfile
import time
from pathlib import Path

import pytest

from garminconnect import GarminConnectConnectionError

from garmin_mcp import personal_api
from garmin_mcp.personal_api import (
    PersonalApiAuthError,
    PersonalApiError,
    PersonalApiHttpClient,
    PersonalApiReadOnlyError,
)


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, status=200, payload=None, text=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload) if text is None and payload is not None else (text or "")
        self.content = self.text.encode()
        self.headers = headers or {}

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def json(self):
        return self._payload


class FakeSession:
    """Records calls and replays a scripted list of responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def _next(self):
        if not self._responses:
            raise AssertionError("FakeSession ran out of responses")
        return self._responses.pop(0)

    def post(self, url, headers=None, timeout=None, **kwargs):
        self.calls.append(("POST", url, dict(headers or {})))
        return self._next()

    def get(self, url, headers=None, params=None, timeout=None, **kwargs):
        self.calls.append(("GET", url, {"params": params, "headers": dict(headers or {})}))
        return self._next()

    def request(self, method, url, headers=None, params=None, timeout=None, **kwargs):
        self.calls.append((method, url, {"params": params, "headers": dict(headers or {})}))
        return self._next()


def make_jwt(expires_in=24 * 60 * 60, extra=None):
    payload = {"exp": int(time.time()) + expires_in, "sub": "user"}
    payload.update(extra or {})
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch):
    """Every test starts with no key, no override and a private cache."""
    for name in (
        personal_api.API_KEY_ENV_VAR,
        personal_api.API_KEY_FILE_ENV_VAR,
        personal_api.BASE_URL_ENV_VAR,
        "GARMIN_PERSONAL_API_TOKEN_CACHE",
    ):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------
# Key resolution
# --------------------------------------------------------------------------
def test_env_key_wins_over_file(monkeypatch, tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("from-file\n")
    monkeypatch.setenv(personal_api.API_KEY_FILE_ENV_VAR, str(key_file))
    monkeypatch.setenv(personal_api.API_KEY_ENV_VAR, "  from-env  ")

    assert personal_api.resolve_api_key() == ("from-env", "$GARMIN_PERSONAL_API_KEY")


def test_file_key_used_when_env_blank(monkeypatch, tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("  from-file\n\n")
    monkeypatch.setenv(personal_api.API_KEY_FILE_ENV_VAR, str(key_file))
    monkeypatch.setenv(personal_api.API_KEY_ENV_VAR, "   ")

    assert personal_api.resolve_api_key() == ("from-file", str(key_file))


def test_no_key_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv(personal_api.API_KEY_FILE_ENV_VAR, str(tmp_path / "missing"))
    assert personal_api.resolve_api_key() is None


def test_empty_key_file_counts_as_absent(monkeypatch, tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("\n")
    monkeypatch.setenv(personal_api.API_KEY_FILE_ENV_VAR, str(key_file))
    assert personal_api.resolve_api_key() is None


# --------------------------------------------------------------------------
# Token lifecycle
# --------------------------------------------------------------------------
def test_jwt_expiration_is_read_from_token():
    exp = int(time.time()) + 600
    token = make_jwt(expires_in=600)
    assert abs(personal_api._jwt_expiration(token) - exp) < 2


def test_non_jwt_token_falls_back_to_documented_24h():
    before = time.time()
    expires = personal_api._jwt_expiration("not-a-jwt")
    assert before + 24 * 60 * 60 - 5 <= expires <= time.time() + 24 * 60 * 60 + 5


def test_token_exchange_posts_key_and_caches(tmp_path):
    token = make_jwt()
    session = FakeSession([FakeResponse(200, text=token)])
    cache = tmp_path / "token.json"
    auth = personal_api.PersonalApiAuthenticator("KEY", session=session, cache_file=cache)

    assert auth.ensure_token() == token
    method, url, headers = session.calls[0]
    assert method == "POST"
    assert url == f"{personal_api.DEFAULT_BASE_URL}{personal_api.TOKEN_PATH}"
    assert headers["Authorization"] == "ApiKey KEY"
    assert cache.exists()

    # A second call inside the 24h window must not hit the network at all.
    assert auth.ensure_token() == token
    assert len(session.calls) == 1


def test_cached_token_is_reused_by_a_new_process(tmp_path):
    cache = tmp_path / "token.json"
    token = make_jwt()
    first = FakeSession([FakeResponse(200, text=token)])
    personal_api.PersonalApiAuthenticator("KEY", session=first, cache_file=cache).ensure_token()

    second = FakeSession([])  # any network call would raise
    auth = personal_api.PersonalApiAuthenticator("KEY", session=second, cache_file=cache)
    assert auth.ensure_token() == token
    assert second.calls == []


def test_cached_token_is_ignored_when_key_rotates(tmp_path):
    cache = tmp_path / "token.json"
    personal_api.PersonalApiAuthenticator(
        "OLD", session=FakeSession([FakeResponse(200, text=make_jwt())]), cache_file=cache
    ).ensure_token()

    session = FakeSession([FakeResponse(200, text=make_jwt())])
    personal_api.PersonalApiAuthenticator("NEW", session=session, cache_file=cache).ensure_token()
    assert len(session.calls) == 1


def test_cache_inside_refresh_window_triggers_exchange(tmp_path):
    cache = tmp_path / "token.json"
    expiring = make_jwt(expires_in=600)  # 10 min < 1h window
    personal_api.PersonalApiAuthenticator(
        "KEY", session=FakeSession([FakeResponse(200, text=expiring)]), cache_file=cache
    ).ensure_token()

    session = FakeSession([FakeResponse(200, text=make_jwt())])
    auth = personal_api.PersonalApiAuthenticator("KEY", session=session, cache_file=cache)
    auth.ensure_token()
    assert len(session.calls) == 1


def test_rejected_key_raises_actionable_error(tmp_path):
    auth = personal_api.PersonalApiAuthenticator(
        "BAD", session=FakeSession([FakeResponse(401, text="nope")]), cache_file=tmp_path / "t"
    )
    with pytest.raises(PersonalApiAuthError) as info:
        auth.ensure_token()
    message = str(info.value)
    assert "personalApiToken" in message
    assert "401" in message


def test_rate_limited_token_endpoint_reports_retry_after(tmp_path):
    auth = personal_api.PersonalApiAuthenticator(
        "KEY",
        session=FakeSession([FakeResponse(429, text="slow down", headers={"Retry-After": "120"})]),
        cache_file=tmp_path / "t",
    )
    with pytest.raises(PersonalApiAuthError) as info:
        auth.ensure_token()
    assert "429" in str(info.value)
    assert "120" in str(info.value)


# --------------------------------------------------------------------------
# Transport: host, headers, read-only, 401 retry
# --------------------------------------------------------------------------
def build_client(monkeypatch, responses, *, cache=None, base_url=None):
    """Build a PersonalApiHttpClient whose HTTP layer is the FakeSession."""
    session = FakeSession([FakeResponse(200, text=make_jwt())] + list(responses))
    monkeypatch.setattr(
        PersonalApiHttpClient,
        "_fresh_api_session",
        lambda self: session,
        raising=True,
    )
    client = PersonalApiHttpClient(
        "KEY",
        session=session,
        cache_file=cache or Path(tempfile.mkdtemp()) / "token.json",
        base_url=base_url,
    )
    return client, session


def test_requests_go_to_the_personal_gateway(monkeypatch):
    client, session = build_client(monkeypatch, [FakeResponse(200, payload=[{"a": 1}])])

    assert client.connectapi("/activitylist-service/activities/search/activities") == [{"a": 1}]

    method, url, kwargs = session.calls[-1]
    assert method == "GET"
    assert url == (
        f"{personal_api.DEFAULT_BASE_URL}/activitylist-service/activities/search/activities"
    )
    assert kwargs["headers"]["Authorization"].startswith("Bearer ")
    assert kwargs["headers"]["Accept"] == "application/json"


def test_base_url_override_is_honoured(monkeypatch):
    client, session = build_client(
        monkeypatch, [FakeResponse(200, payload={})], base_url="https://example.test"
    )
    client.connectapi("/x")
    assert session.calls[-1][1] == "https://example.test/x"


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_writes_are_refused_before_hitting_the_network(monkeypatch, tmp_path, method):
    client, session = build_client(monkeypatch, [])
    calls_before = len(session.calls)

    with pytest.raises(PersonalApiReadOnlyError) as info:
        getattr(client, method)("connectapi", "/workout-service/workout", json={})

    message = str(info.value)
    assert "read-only" in message
    assert "GARMIN_AUTH_MODE=sso" in message
    assert len(session.calls) == calls_before  # nothing was sent


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"json": {"a": 1}},
        {"files": {"file": ("x.gpx", b"<gpx/>")}},
        {"api": True},
        {"api": True, "json": [], "headers": {"X": "y"}},
    ],
)
def test_write_guard_covers_every_call_shape(monkeypatch, tmp_path, kwargs):
    """The real callers use json/files/api/headers -- none may slip through."""
    client, session = build_client(monkeypatch, [])
    calls_before = len(session.calls)

    with pytest.raises(PersonalApiReadOnlyError):
        client.post("connectapi", "/course-service/course", **kwargs)

    assert len(session.calls) == calls_before


def test_401_triggers_one_reexchange_and_retry(monkeypatch):
    client, session = build_client(
        monkeypatch,
        [FakeResponse(401, text="expired"), FakeResponse(200, text=make_jwt()), FakeResponse(200, payload={"ok": True})],
    )

    assert client.connectapi("/userprofile-service/socialProfile") == {"ok": True}

    kinds = [call[0] for call in session.calls]
    # token, GET (401), token again, GET (200)
    assert kinds == ["POST", "GET", "POST", "GET"]


def test_read_only_guard_does_not_block_downloads(monkeypatch):
    client, session = build_client(monkeypatch, [FakeResponse(200, text="zipbytes")])
    assert client.download("/download-service/files/activity/1") == b"zipbytes"


def test_persistent_401_raises_instead_of_looping(monkeypatch):
    """A key revoked mid-session must surface, not retry forever."""
    client, session = build_client(
        monkeypatch,
        [
            FakeResponse(401, text="expired"),
            FakeResponse(200, text=make_jwt()),
            FakeResponse(401, text="still expired"),
        ],
    )

    with pytest.raises(GarminConnectConnectionError):
        client.connectapi("/userprofile-service/socialProfile")

    kinds = [call[0] for call in session.calls]
    assert kinds == ["POST", "GET", "POST", "GET"]  # exactly one re-exchange


def test_corrupted_cache_is_ignored(tmp_path):
    cache = tmp_path / "token.json"
    cache.write_text("{ not json", encoding="utf-8")
    session = FakeSession([FakeResponse(200, text=make_jwt())])

    token = personal_api.PersonalApiAuthenticator(
        "KEY", session=session, cache_file=cache
    ).ensure_token()

    assert token and len(session.calls) == 1


def test_cache_is_bound_to_the_endpoint(tmp_path):
    cache = tmp_path / "token.json"
    personal_api.PersonalApiAuthenticator(
        "KEY",
        session=FakeSession([FakeResponse(200, text=make_jwt())]),
        cache_file=cache,
        base_url="https://one.test",
    ).ensure_token()

    session = FakeSession([FakeResponse(200, text=make_jwt())])
    personal_api.PersonalApiAuthenticator(
        "KEY", session=session, cache_file=cache, base_url="https://two.test"
    ).ensure_token()
    assert len(session.calls) == 1


def test_millisecond_exp_is_normalized():
    exp_ms = (time.time() + 3600) * 1000
    payload = {"exp": int(exp_ms)}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    assert abs(personal_api._jwt_expiration(f"h.{body}.s") - exp_ms / 1000) < 2


# --------------------------------------------------------------------------
# Login wrapper
# --------------------------------------------------------------------------
def test_login_without_key_raises(monkeypatch, tmp_path):
    monkeypatch.setenv(personal_api.API_KEY_FILE_ENV_VAR, str(tmp_path / "missing"))
    with pytest.raises(PersonalApiAuthError) as info:
        personal_api.login_personal()
    assert "GARMIN_PERSONAL_API_KEY" in str(info.value)


def test_login_populates_display_name_from_settings(monkeypatch):
    client, session = build_client(
        monkeypatch,
        [
            FakeResponse(
                200,
                payload={
                    "displayName": "denis",
                    "fullName": "Denis V",
                    "userData": {"measurementSystem": "metric"},
                },
            )
        ],
    )
    monkeypatch.setattr(
        personal_api, "PersonalApiHttpClient", lambda *a, **k: client, raising=True
    )

    garmin = personal_api.login_personal("KEY")

    assert garmin.display_name == "denis"
    assert garmin.full_name == "Denis V"
    assert garmin.unit_system == "metric"
    assert garmin.client.base_url == personal_api.DEFAULT_BASE_URL


def test_login_falls_back_to_social_profile(monkeypatch):
    client, session = build_client(
        monkeypatch,
        [
            FakeResponse(403, text="forbidden"),
            FakeResponse(200, payload={"displayName": "fallback", "fullName": "F"}),
        ],
    )
    monkeypatch.setattr(
        personal_api, "PersonalApiHttpClient", lambda *a, **k: client, raising=True
    )

    garmin = personal_api.login_personal("KEY")
    assert garmin.display_name == "fallback"


def test_read_only_reason_only_for_personal_backend():
    class SsoClient:
        client = object()

    assert personal_api.read_only_reason(SsoClient()) is None
    assert personal_api.is_personal_backend(SsoClient()) is False
