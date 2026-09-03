"""Tests for lazy authentication, caching, retry, and invalidation."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from unittest.mock import Mock

import pytest
from garminconnect import (
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from garmin_mcp.runtime import GarminClientProvider, GarminGateway
from garmin_mcp.runtime import GarminRequestBudgetExceeded


def test_provider_is_lazy_and_concurrent_first_calls_share_one_factory_call():
    calls = 0
    calls_lock = Lock()
    barrier = Barrier(8)
    client = object()

    def factory():
        nonlocal calls
        with calls_lock:
            calls += 1
        return client

    provider = GarminClientProvider(factory)
    assert provider.snapshot()["state"] == "not_initialized"
    assert calls == 0

    def get_client():
        barrier.wait()
        return provider.get_client()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: get_client(), range(8)))

    assert results == [client] * 8
    assert calls == 1
    assert provider.snapshot()["state"] == "ready"


def test_provider_applies_timeout_without_changing_garmin_method_signatures():
    class Transport:
        def _run_request(self, method, path, **kwargs):
            return method, path, kwargs

    client = Mock()
    client.client = Transport()
    provider = GarminClientProvider(
        lambda: client, request_timeout_seconds=7.5
    )

    initialized = provider.get_client()
    assert initialized.client._run_request("GET", "/stats") == (
        "GET",
        "/stats",
        {"timeout": 7.5},
    )
    assert initialized.client._run_request("GET", "/stats", timeout=2) == (
        "GET",
        "/stats",
        {"timeout": 2},
    )
    assert provider.snapshot()["request_timeout_applied"] is True


def test_gateway_caches_reads_and_returns_defensive_copies():
    client = Mock()
    client.get_stats.return_value = {"values": [1]}
    gateway = GarminGateway(GarminClientProvider(lambda: client))

    first = gateway.get_stats("2024-01-01")
    first["values"].append(2)
    second = gateway.get_stats("2024-01-01")

    assert second == {"values": [1]}
    client.get_stats.assert_called_once_with("2024-01-01")
    assert gateway.stats()["cache_hits"] == 1


def test_gateway_retries_reads_with_exponential_backoff():
    client = Mock()
    client.get_stats.side_effect = [
        GarminConnectConnectionError("one"),
        GarminConnectConnectionError("two"),
        {"ok": True},
    ]
    sleeps = []
    gateway = GarminGateway(
        GarminClientProvider(lambda: client),
        base_backoff_seconds=0.25,
        sleep=sleeps.append,
    )

    assert gateway.get_stats("2024-01-01") == {"ok": True}
    assert client.get_stats.call_count == 3
    assert sleeps == [0.25, 0.5]
    assert gateway.stats()["retries"] == 2


def test_gateway_honors_retry_after_for_rate_limits():
    client = Mock()
    rate_limit = GarminConnectTooManyRequestsError("429")
    rate_limit.response = Mock(headers={"Retry-After": "2"})
    client.get_stats.side_effect = [rate_limit, {"ok": True}]
    sleeps = []
    gateway = GarminGateway(
        GarminClientProvider(lambda: client),
        sleep=sleeps.append,
    )

    assert gateway.get_stats("2024-01-01") == {"ok": True}
    assert sleeps == [2.0]


def test_gateway_ttl_expiration_causes_a_fresh_read():
    now = [100.0]
    client = Mock()
    client.get_devices.side_effect = [[{"id": 1}], [{"id": 2}]]
    gateway = GarminGateway(
        GarminClientProvider(lambda: client), monotonic=lambda: now[0]
    )

    assert gateway.get_devices() == [{"id": 1}]
    now[0] += 299
    assert gateway.get_devices() == [{"id": 1}]
    now[0] += 2
    assert gateway.get_devices() == [{"id": 2}]
    assert client.get_devices.call_count == 2


def test_gateway_never_retries_writes_and_invalidates_related_cache():
    client = Mock()
    client.get_activity.return_value = {"activityId": 1}
    client.set_activity_name.side_effect = GarminConnectConnectionError("lost")
    gateway = GarminGateway(
        GarminClientProvider(lambda: client), sleep=lambda _seconds: None
    )

    gateway.get_activity(1)
    with pytest.raises(GarminConnectConnectionError):
        gateway.set_activity_name(1, "Tempo")

    client.set_activity_name.assert_called_once()
    # A lost write response has an unknown remote outcome. The next read must
    # reconcile with Garmin instead of returning the pre-write cache entry.
    assert gateway.get_activity(1) == {"activityId": 1}
    assert client.get_activity.call_count == 2

    client.set_activity_name.side_effect = None
    client.set_activity_name.return_value = True
    gateway.set_activity_name(1, "Tempo")
    gateway.get_activity(1)
    assert client.get_activity.call_count == 3


def test_nested_transport_is_lazy_and_put_is_not_retried():
    raw_transport = Mock()
    raw_transport.put.side_effect = GarminConnectConnectionError("lost response")
    client = Mock()
    client.client = raw_transport
    factory = Mock(return_value=client)
    gateway = GarminGateway(
        GarminClientProvider(factory), sleep=lambda _seconds: None
    )

    nested = gateway.client
    factory.assert_not_called()
    with pytest.raises(GarminConnectConnectionError):
        nested.put("connectapi", "/activity-service/activity/1", json={})

    factory.assert_called_once()
    raw_transport.put.assert_called_once()


def test_generic_transport_request_classifies_http_verb_and_invalidates_cache():
    raw_transport = Mock()
    raw_transport.request.return_value = None
    client = Mock()
    client.client = raw_transport
    client.get_heart_rate_zones.return_value = [{"sport": "CYCLING"}]
    gateway = GarminGateway(GarminClientProvider(lambda: client))

    gateway.get_heart_rate_zones()
    gateway.get_heart_rate_zones()
    client.get_heart_rate_zones.assert_called_once()

    gateway.client.request(
        "PUT",
        "connectapi",
        "/biometric-service/heartRateZones",
        json=[],
        api=True,
    )
    gateway.get_heart_rate_zones()

    raw_transport.request.assert_called_once()
    assert client.get_heart_rate_zones.call_count == 2
    assert gateway.stats()["write_calls"] == 1


def test_gateway_enforces_nonblocking_rolling_request_budget():
    now = [100.0]
    client = Mock()
    client.get_stats.side_effect = [{"day": 1}, {"day": 2}, {"day": 3}]
    gateway = GarminGateway(
        GarminClientProvider(lambda: client),
        request_budget_per_minute=2,
        monotonic=lambda: now[0],
    )

    gateway.get_stats("2024-01-01")
    gateway.get_stats("2024-01-02")
    with pytest.raises(GarminRequestBudgetExceeded):
        gateway.get_stats("2024-01-03")
    assert gateway.stats()["request_budget_remaining"] == 0

    now[0] += 61
    assert gateway.get_stats("2024-01-03") == {"day": 3}
