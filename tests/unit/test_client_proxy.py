"""Unit tests for the runtime error-handling proxy (_GarminClientProxy)."""

import pytest
from unittest.mock import Mock

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from garmin_mcp import _GarminClientProxy


@pytest.fixture
def client():
    mock = Mock()
    mock.some_attribute = "attr_value"
    mock.get_stats = Mock(return_value={"steps": 10000})
    return mock


@pytest.fixture
def proxy(client):
    return _GarminClientProxy(client)


def test_callable_methods_pass_through(proxy, client):
    result = proxy.get_stats("2024-01-15")
    client.get_stats.assert_called_once_with("2024-01-15")
    assert result == {"steps": 10000}


def test_non_callable_attributes_pass_through(proxy):
    assert proxy.some_attribute == "attr_value"


def test_kwargs_pass_through(proxy, client):
    proxy.get_stats(date="2024-01-15", extra="value")
    client.get_stats.assert_called_once_with(date="2024-01-15", extra="value")


def test_auth_error_becomes_runtime_error(proxy, client):
    client.get_stats.side_effect = GarminConnectAuthenticationError("token expired")
    with pytest.raises(RuntimeError, match="authentication expired"):
        proxy.get_stats("2024-01-15")


def test_auth_error_preserves_original(proxy, client):
    original = GarminConnectAuthenticationError("token expired")
    client.get_stats.side_effect = original
    with pytest.raises(RuntimeError) as exc_info:
        proxy.get_stats("2024-01-15")
    assert exc_info.value.__cause__ is original


def test_rate_limit_error_becomes_runtime_error(proxy, client):
    client.get_stats.side_effect = GarminConnectTooManyRequestsError("429")
    with pytest.raises(RuntimeError, match="rate limit"):
        proxy.get_stats("2024-01-15")


def test_connection_error_becomes_runtime_error(proxy, client):
    client.get_stats.side_effect = GarminConnectConnectionError("timeout")
    with pytest.raises(RuntimeError, match="unreachable"):
        proxy.get_stats("2024-01-15")


def test_non_garmin_exceptions_propagate_unwrapped(proxy, client):
    client.get_stats.side_effect = ValueError("bad argument")
    with pytest.raises(ValueError, match="bad argument"):
        proxy.get_stats("2024-01-15")


def test_error_message_includes_original(proxy, client):
    client.get_stats.side_effect = GarminConnectAuthenticationError("specific detail")
    with pytest.raises(RuntimeError, match="specific detail"):
        proxy.get_stats("2024-01-15")


def test_missing_attribute_raises_attribute_error():
    client = Mock(spec=["get_stats"])
    proxy = _GarminClientProxy(client)
    with pytest.raises(AttributeError):
        proxy.nonexistent_method()


def test_different_methods_wrapped_independently(proxy, client):
    client.method_a = Mock(return_value="a")
    client.method_b = Mock(return_value="b")
    assert proxy.method_a() == "a"
    assert proxy.method_b() == "b"
    client.method_a.assert_called_once()
    client.method_b.assert_called_once()
