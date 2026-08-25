"""Unit tests for _GarminProxy: runtime exception translation + shared-session
lifecycle (acquire-before-use, publish-after-success, invalidate-on-auth-failure).

_GarminProxy wraps a GarminSession, not a bare client -- every attribute
access resolves the current client via session.acquire() (which re-reads the
shared token store and adopts a peer's rotation, see
tests/unit/test_garmin_session.py), so a token rotated by another process
mid-process-lifetime is picked up on the next tool call instead of leaving a
rejected client cached until a restart. See ai-docs/shared-token-store.md.
"""

from unittest.mock import Mock

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from garmin_mcp import _GarminProxy


def _session(client):
    session = Mock()
    session.acquire.return_value = client
    return session


class TestGarminProxy:
    """Tests for _GarminProxy."""

    def _proxy(self, **methods):
        client = Mock()
        for name, behaviour in methods.items():
            if isinstance(behaviour, Exception):
                getattr(client, name).side_effect = behaviour
            else:
                getattr(client, name).return_value = behaviour
        return _GarminProxy(_session(client))

    def test_successful_call_passes_through(self):
        proxy = self._proxy(get_full_name="Alice")
        assert proxy.get_full_name() == "Alice"

    def test_non_callable_attribute_passes_through(self):
        client = Mock()
        client.some_attr = 42
        proxy = _GarminProxy(_session(client))
        assert proxy.some_attr == 42

    def test_auth_error_message_is_actionable(self):
        proxy = self._proxy(get_activities=GarminConnectAuthenticationError("expired"))
        exc = pytest.raises(GarminConnectAuthenticationError, proxy.get_activities)
        assert "Re-run 'garmin-mcp-auth'" in str(exc.value)

    def test_rate_limit_error_message_is_actionable(self):
        proxy = self._proxy(get_activities=GarminConnectTooManyRequestsError("429"))
        exc = pytest.raises(GarminConnectTooManyRequestsError, proxy.get_activities)
        assert "Wait a few minutes" in str(exc.value)

    def test_connection_error_message_is_actionable(self):
        proxy = self._proxy(get_steps_data=GarminConnectConnectionError("timeout"))
        exc = pytest.raises(GarminConnectConnectionError, proxy.get_steps_data)
        assert "unreachable" in str(exc.value)

    def test_unknown_exception_is_re_raised_unchanged(self):
        proxy = self._proxy(get_activities=ValueError("unexpected"))
        with pytest.raises(ValueError, match="unexpected"):
            proxy.get_activities()

    def test_args_and_kwargs_forwarded_to_client(self):
        client = Mock()
        client.get_activities.return_value = []
        proxy = _GarminProxy(_session(client))
        proxy.get_activities(0, 10, activityType="running")
        client.get_activities.assert_called_once_with(0, 10, activityType="running")

    # ---------------------------------------------------------- session lifecycle

    def test_every_access_reacquires_from_the_session(self):
        """The whole point: a rotated token must be picked up on the next
        call, not just the first one."""
        client = Mock()
        client.get_full_name.return_value = "Alice"
        session = _session(client)
        proxy = _GarminProxy(session)

        proxy.get_full_name()
        proxy.get_steps_data()

        assert session.acquire.call_count == 2

    def test_successful_call_publishes_any_rotation(self):
        client = Mock()
        client.get_full_name.return_value = "Alice"
        session = _session(client)
        proxy = _GarminProxy(session)

        proxy.get_full_name()

        session.publish.assert_called_once()
        session.invalidate.assert_not_called()

    def test_auth_failure_invalidates_the_session(self):
        client = Mock()
        client.get_activities.side_effect = GarminConnectAuthenticationError("expired")
        session = _session(client)
        proxy = _GarminProxy(session)

        with pytest.raises(GarminConnectAuthenticationError):
            proxy.get_activities()

        session.invalidate.assert_called_once()
        session.publish.assert_not_called()

    @pytest.mark.parametrize(
        "exc",
        [GarminConnectTooManyRequestsError("429"), GarminConnectConnectionError("timeout")],
    )
    def test_transient_failures_do_not_invalidate_the_session(self, exc):
        """Dropping the cached client on a rate limit or a network blip would
        force a needless re-login on the next call -- only a real auth
        rejection means the client itself is bad."""
        client = Mock()
        client.get_activities.side_effect = exc
        session = _session(client)
        proxy = _GarminProxy(session)

        with pytest.raises(type(exc)):
            proxy.get_activities()

        session.invalidate.assert_not_called()

    # ------------------------------------------------------- raw .client bypass
    #
    # Several tool modules reach past Garmin's own methods into the raw
    # garminconnect Client for HTTP verbs Garmin doesn't expose
    # (garmin_client.client.put(...), .post(...), .delete(...),
    # .connectapi(...)). These must get the exact same protection as a
    # top-level Garmin method call -- see ai-docs/shared-token-store.md.

    def test_client_non_callable_attribute_passes_through(self):
        garmin = Mock()
        garmin.client.domain = "garmin.com"
        proxy = _GarminProxy(_session(garmin))

        assert proxy.client.domain == "garmin.com"

    def test_client_successful_call_publishes_any_rotation(self):
        garmin = Mock()
        garmin.client.put.return_value = {"ok": True}
        session = _session(garmin)
        proxy = _GarminProxy(session)

        result = proxy.client.put("connectapi", "some/path", json={})

        assert result == {"ok": True}
        garmin.client.put.assert_called_once_with("connectapi", "some/path", json={})
        session.publish.assert_called_once()
        session.invalidate.assert_not_called()

    def test_client_auth_failure_invalidates_the_session(self):
        garmin = Mock()
        garmin.client.put.side_effect = GarminConnectAuthenticationError("expired")
        session = _session(garmin)
        proxy = _GarminProxy(session)

        with pytest.raises(GarminConnectAuthenticationError) as exc:
            proxy.client.put("connectapi", "some/path", json={})

        assert "Re-run 'garmin-mcp-auth'" in str(exc.value)
        session.invalidate.assert_called_once()
        session.publish.assert_not_called()

    def test_client_transient_failure_does_not_invalidate_the_session(self):
        garmin = Mock()
        garmin.client.post.side_effect = GarminConnectConnectionError("timeout")
        session = _session(garmin)
        proxy = _GarminProxy(session)

        with pytest.raises(GarminConnectConnectionError):
            proxy.client.post("connectapi", "some/path")

        session.invalidate.assert_not_called()

    def test_each_client_access_reacquires_from_the_session(self):
        garmin = Mock()
        garmin.client.put.return_value = None
        garmin.client.delete.return_value = None
        session = _session(garmin)
        proxy = _GarminProxy(session)

        proxy.client.put("connectapi", "a")
        proxy.client.delete("connectapi", "b")

        assert session.acquire.call_count == 2
