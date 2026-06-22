"""Unit tests for _GarminProxy: runtime exception translation."""

import pytest
from unittest.mock import Mock

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from garmin_mcp import _GarminProxy


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
