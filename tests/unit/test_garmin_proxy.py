"""Unit tests for _GarminProxy: runtime exception translation."""

import pytest
from unittest.mock import Mock

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from garmin_mcp import _GarminProxy, _resolve_call_timeout, _DEFAULT_CALL_TIMEOUT


class TestGarminProxy:
    """Tests for _GarminProxy."""

    def _proxy(self, **methods):
        client = Mock()
        for name, behaviour in methods.items():
            if isinstance(behaviour, Exception):
                getattr(client, name).side_effect = behaviour
            else:
                getattr(client, name).return_value = behaviour
        return _GarminProxy(client)

    def test_successful_call_passes_through(self):
        proxy = self._proxy(get_full_name="Alice")
        assert proxy.get_full_name() == "Alice"

    def test_non_callable_attribute_passes_through(self):
        client = Mock()
        client.some_attr = 42
        proxy = _GarminProxy(client)
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
        proxy = _GarminProxy(client)
        proxy.get_activities(0, 10, activityType="running")
        client.get_activities.assert_called_once_with(0, 10, activityType="running")

    def test_slow_call_times_out_with_actionable_message(self):
        """A call that stalls past the timeout raises a clear, retry-able error (issue #248)."""
        import threading

        blocked = threading.Event()
        client = Mock()
        # Simulate a hung Garmin request: block until released by the test.
        client.get_activities.side_effect = lambda *a, **k: blocked.wait(30)
        proxy = _GarminProxy(client, timeout=0.05)
        try:
            with pytest.raises(TimeoutError) as exc:
                proxy.get_activities()
            assert "did not return within" in str(exc.value)
            assert "GARMIN_MCP_CALL_TIMEOUT" in str(exc.value)
        finally:
            blocked.set()  # release the abandoned worker thread

    def test_fast_call_returns_before_timeout(self):
        """A normal call completes and returns through the timeout wrapper."""
        client = Mock()
        client.get_full_name.return_value = "Alice"
        proxy = _GarminProxy(client, timeout=5)
        assert proxy.get_full_name() == "Alice"

    def test_timeout_zero_disables_bound(self):
        """timeout=0 bypasses the executor and calls the client inline."""
        client = Mock()
        client.get_full_name.return_value = "Zed"
        proxy = _GarminProxy(client, timeout=0)
        assert proxy.get_full_name() == "Zed"

    def test_known_exception_still_translated_through_timeout_wrapper(self):
        """Error translation survives the worker-thread hop (future re-raises)."""
        proxy = self._proxy(get_activities=GarminConnectAuthenticationError("expired"))
        with pytest.raises(GarminConnectAuthenticationError) as exc:
            proxy.get_activities()
        assert "Re-run 'garmin-mcp-auth'" in str(exc.value)


class TestResolveCallTimeout:
    """Tests for GARMIN_MCP_CALL_TIMEOUT parsing."""

    def test_absent_uses_default(self, monkeypatch):
        monkeypatch.delenv("GARMIN_MCP_CALL_TIMEOUT", raising=False)
        assert _resolve_call_timeout() == _DEFAULT_CALL_TIMEOUT

    def test_blank_uses_default(self, monkeypatch):
        monkeypatch.setenv("GARMIN_MCP_CALL_TIMEOUT", "   ")
        assert _resolve_call_timeout() == _DEFAULT_CALL_TIMEOUT

    def test_valid_value_is_used(self, monkeypatch):
        monkeypatch.setenv("GARMIN_MCP_CALL_TIMEOUT", "5")
        assert _resolve_call_timeout() == 5.0

    def test_zero_disables(self, monkeypatch):
        monkeypatch.setenv("GARMIN_MCP_CALL_TIMEOUT", "0")
        assert _resolve_call_timeout() == 0.0

    def test_negative_disables(self, monkeypatch):
        monkeypatch.setenv("GARMIN_MCP_CALL_TIMEOUT", "-1")
        assert _resolve_call_timeout() == 0.0

    def test_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("GARMIN_MCP_CALL_TIMEOUT", "bogus")
        assert _resolve_call_timeout() == _DEFAULT_CALL_TIMEOUT
