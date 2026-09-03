"""Runtime infrastructure shared by every Garmin MCP tool.

The objects in this module deliberately implement the same attribute-oriented
interface as ``garminconnect.Garmin``.  Existing tool modules can therefore be
configured with a :class:`GarminGateway` without knowing that authentication is
lazy or that reads are cached and retried.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Mapping

import requests
from garminconnect import (
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)


class GarminClientUnavailableError(RuntimeError):
    """Raised when the lazy Garmin client could not be initialized."""


class GarminRequestBudgetExceeded(RuntimeError):
    """Raised before a remote call would exceed the configured minute budget."""


class GarminClientProvider:
    """Create the Garmin client on the first actual tool call.

    ``get_client`` is protected by a re-entrant lock, so concurrent first calls
    share one login attempt.  A short failure cooldown prevents a burst of
    waiting callers from immediately repeating a failed authentication request.
    """

    def __init__(
        self,
        factory: Callable[[], Any],
        *,
        token_path: str | None = None,
        is_cn: bool = False,
        request_timeout_seconds: float | None = 15.0,
        failure_cooldown_seconds: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = factory
        self.token_path = token_path
        self.is_cn = is_cn
        self.request_timeout_seconds = request_timeout_seconds
        self._failure_cooldown_seconds = max(0.0, failure_cooldown_seconds)
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._client: Any | None = None
        self._state = "not_initialized"
        self._attempts = 0
        self._last_attempt_monotonic: float | None = None
        self._last_attempt_at: str | None = None
        self._last_success_at: str | None = None
        self._last_error: Exception | None = None
        self._request_timeout_applied: bool | None = None
        self._request_timeout_note: str | None = None

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def get_client(self, *, force_retry: bool = False) -> Any:
        """Return the initialized client, performing login when necessary."""
        with self._lock:
            if self._client is not None:
                return self._client

            now = self._monotonic()
            if (
                not force_retry
                and self._last_error is not None
                and self._last_attempt_monotonic is not None
                and now - self._last_attempt_monotonic
                < self._failure_cooldown_seconds
            ):
                raise GarminClientUnavailableError(str(self._last_error)) from None

            self._attempts += 1
            self._state = "initializing"
            self._last_attempt_monotonic = now
            self._last_attempt_at = self._now_iso()
            try:
                client = self._factory()
                if client is None:
                    raise GarminClientUnavailableError(
                        "Garmin authentication is not available. Run "
                        "'garmin-mcp-auth' and try again."
                    )
                self._configure_request_timeout(client)
            except Exception as exc:
                self._state = "error"
                self._last_error = exc
                if isinstance(exc, GarminClientUnavailableError):
                    raise
                raise GarminClientUnavailableError(str(exc)) from exc

            self._client = client
            self._last_error = None
            self._state = "ready"
            self._last_success_at = self._now_iso()
            return client

    def _configure_request_timeout(self, client: Any) -> None:
        """Apply a timeout without changing public Garmin method signatures.

        garminconnect 0.3.x funnels authenticated API calls through
        ``Client._run_request(method, path, **kwargs)``. Wrapping that transport
        seam gives every high-level call the configured default while preserving
        an explicit per-call override. If a future transport removes the seam,
        its native timeout remains active and diagnostics report the limitation.
        """
        timeout = self.request_timeout_seconds
        if timeout is None:
            self._request_timeout_applied = False
            self._request_timeout_note = "Gateway timeout override is disabled"
            return

        transport = getattr(client, "client", None)
        original = getattr(transport, "_run_request", None)
        if transport is None or not callable(original):
            self._request_timeout_applied = False
            self._request_timeout_note = (
                "This garminconnect transport does not expose _run_request; "
                "its native timeout remains in effect"
            )
            return

        @functools.wraps(original)
        def with_timeout(method: str, path: str, **kwargs: Any) -> Any:
            kwargs.setdefault("timeout", timeout)
            return original(method, path, **kwargs)

        setattr(transport, "_run_request", with_timeout)
        self._request_timeout_applied = True
        self._request_timeout_note = None

    def reset(self) -> None:
        """Forget the current client so the next call authenticates again."""
        with self._lock:
            self._client = None
            self._state = "not_initialized"
            self._last_error = None
            self._last_attempt_monotonic = None

    def snapshot(self) -> dict[str, Any]:
        """Return non-secret provider state without triggering authentication."""
        with self._lock:
            return {
                "state": self._state,
                "initialized": self._client is not None,
                "attempts": self._attempts,
                "last_attempt_at": self._last_attempt_at,
                "last_success_at": self._last_success_at,
                "last_error": str(self._last_error) if self._last_error else None,
                "last_error_type": (
                    type(self._last_error).__name__ if self._last_error else None
                ),
                "request_timeout_seconds": self.request_timeout_seconds,
                "request_timeout_applied": self._request_timeout_applied,
                "request_timeout_note": self._request_timeout_note,
            }


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float
    resource: str


class GarminGateway:
    """Thread-safe cache/retry façade over a lazy Garmin client provider.

    Only clearly read-only methods are eligible for caching and retry.  Unknown
    methods are treated as writes, which is intentionally conservative: a
    connection failure after a write may mean Garmin applied it even though the
    response was lost, so automatically repeating it can create duplicates.
    """

    _READ_PREFIXES = (
        "get_",
        "count_",
        "search_",
        "download_",
        "query_",
    )
    _WRITE_PREFIXES = (
        "add_",
        "create_",
        "delete_",
        "join_",
        "leave_",
        "log_",
        "post",
        "put",
        "remove_",
        "request_",
        "schedule_",
        "set_",
        "unschedule_",
        "update_",
        "upload_",
    )

    def __init__(
        self,
        provider: GarminClientProvider,
        *,
        max_read_attempts: int = 3,
        base_backoff_seconds: float = 0.5,
        max_backoff_seconds: float = 30.0,
        request_budget_per_minute: int | None = 120,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.provider = provider
        self.max_read_attempts = max(1, max_read_attempts)
        self.base_backoff_seconds = max(0.0, base_backoff_seconds)
        self.max_backoff_seconds = max(0.0, max_backoff_seconds)
        if request_budget_per_minute is not None and request_budget_per_minute < 1:
            raise ValueError("request_budget_per_minute must be positive or None")
        self.request_budget_per_minute = request_budget_per_minute
        self._sleep = sleep
        self._monotonic = monotonic
        self._cache: dict[str, _CacheEntry] = {}
        self._remote_call_times: deque[float] = deque()
        self._lock = threading.RLock()
        self._stats: dict[str, int] = {
            "logical_calls": 0,
            "remote_calls": 0,
            "read_calls": 0,
            "write_calls": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "retries": 0,
            "failures": 0,
            "invalidations": 0,
            "budget_rejections": 0,
        }
        self._transport = _TransportGateway(self)

    def __getattr__(self, name: str) -> Any:
        # Returning the nested façade itself must not initialize Garmin.  This
        # preserves lazy startup even for modules that call ``client.put``.
        if name == "client":
            return self._transport

        if self._method_kind(name) is not None:
            def call(*args: Any, **kwargs: Any) -> Any:
                client = self.provider.get_client()
                method = getattr(client, name)
                return self._invoke(name, method, args, kwargs, namespace="garmin")

            return call

        # Constants such as endpoint URLs and display_name are needed only
        # while executing a tool, so resolving them here remains first-call lazy.
        return getattr(self.provider.get_client(), name)

    def _method_kind(self, name: str) -> str | None:
        lower = name.lower()
        if lower in {"connectapi", "get", "query_garmin_graphql"}:
            return "read"
        if lower in {"post", "put", "delete", "patch"}:
            return "write"
        if lower.startswith(self._READ_PREFIXES):
            return "read"
        if lower.startswith(self._WRITE_PREFIXES):
            return "write"
        # Most Garmin API methods follow verb prefixes.  Returning None allows
        # plain non-callable attributes to pass through without wrapping.
        return None

    @staticmethod
    def _stable(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): GarminGateway._stable(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        if isinstance(value, (list, tuple)):
            return [GarminGateway._stable(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, (date, datetime, Path)):
            return str(value)
        return repr(value)

    @classmethod
    def _cache_key(
        cls, namespace: str, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> str:
        encoded = json.dumps(
            [namespace, name, cls._stable(args), cls._stable(kwargs)],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _copy(value: Any) -> Any:
        try:
            return copy.deepcopy(value)
        except Exception:
            return value

    @staticmethod
    def _all_text(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
        return " ".join(
            [*(str(item) for item in args), *(str(item) for item in kwargs.values())]
        ).lower()

    @classmethod
    def _resource_for(
        cls, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> str:
        text = f"{name.lower()} {cls._all_text(args, kwargs)}"
        for resource, markers in (
            ("workouts", ("workout", "trainingplan")),
            ("activities", ("activity", "activities")),
            ("nutrition", ("nutrition", "food", "meal")),
            ("profile", ("profile", "heart-rate", "heartrate", "zone")),
            ("health", ("sleep", "hrv", "stress", "body", "health", "weight")),
        ):
            if any(marker in text for marker in markers):
                return resource
        return "general"

    @staticmethod
    def _has_today(args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
        today = date.today().isoformat()
        return today in {str(item) for item in args} | {
            str(item) for item in kwargs.values()
        }

    @classmethod
    def _ttl_for(
        cls, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> float:
        lower = name.lower()
        if lower.startswith("download_activity"):
            return 7 * 24 * 60 * 60
        if "activit" in lower or "activity-service" in cls._all_text(args, kwargs):
            return 2 * 60
        if cls._has_today(args, kwargs):
            return 5 * 60
        # Historical daily health data is effectively immutable in normal use.
        if any(
            marker in lower
            for marker in (
                "stats",
                "sleep",
                "hrv",
                "stress",
                "heart_rate",
                "body",
                "training",
                "respiration",
                "spo2",
                "steps",
            )
        ):
            return 24 * 60 * 60
        return 5 * 60

    def _cache_get(self, key: str) -> tuple[bool, Any]:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return False, None
            if entry.expires_at <= self._monotonic():
                del self._cache[key]
                return False, None
            return True, self._copy(entry.value)

    def _cache_put(self, key: str, value: Any, ttl: float, resource: str) -> None:
        with self._lock:
            self._cache[key] = _CacheEntry(
                value=self._copy(value),
                expires_at=self._monotonic() + ttl,
                resource=resource,
            )

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(
            exc,
            (
                GarminConnectTooManyRequestsError,
                GarminConnectConnectionError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ),
        ):
            return True
        if isinstance(exc, requests.exceptions.HTTPError):
            response = getattr(exc, "response", None)
            return getattr(response, "status_code", None) == 429
        return False

    @staticmethod
    def _retry_after(exc: Exception) -> float | None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if not headers:
            return None
        value = headers.get("Retry-After")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(
                    0.0,
                    (retry_at - datetime.now(timezone.utc)).total_seconds(),
                )
            except (TypeError, ValueError, OverflowError):
                return None

    def _increment(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self._stats[key] += amount

    def _reserve_remote_call(self) -> None:
        """Reserve one real request without blocking the MCP worker thread."""

        with self._lock:
            now = self._monotonic()
            cutoff = now - 60.0
            while self._remote_call_times and self._remote_call_times[0] <= cutoff:
                self._remote_call_times.popleft()
            if (
                self.request_budget_per_minute is not None
                and len(self._remote_call_times) >= self.request_budget_per_minute
            ):
                self._stats["budget_rejections"] += 1
                raise GarminRequestBudgetExceeded(
                    "Garmin request budget exhausted; wait for the rolling one-minute window to clear"
                )
            self._remote_call_times.append(now)

    def _invoke(
        self,
        name: str,
        method: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        *,
        namespace: str,
        kind: str | None = None,
    ) -> Any:
        kind = kind or self._method_kind(name) or "write"
        resource = self._resource_for(name, args, kwargs)
        self._increment("logical_calls")
        self._increment("read_calls" if kind == "read" else "write_calls")

        cache_key: str | None = None
        if kind == "read":
            cache_key = self._cache_key(namespace, name, args, kwargs)
            found, cached = self._cache_get(cache_key)
            if found:
                self._increment("cache_hits")
                return cached
            self._increment("cache_misses")

        attempts = self.max_read_attempts if kind == "read" else 1
        for attempt in range(1, attempts + 1):
            self._reserve_remote_call()
            self._increment("remote_calls")
            try:
                result = method(*args, **kwargs)
            except Exception as exc:
                if kind == "read" and attempt < attempts and self._is_retryable(exc):
                    self._increment("retries")
                    retry_after = self._retry_after(exc)
                    delay = (
                        retry_after
                        if retry_after is not None
                        else self.base_backoff_seconds * (2 ** (attempt - 1))
                    )
                    self._sleep(min(delay, self.max_backoff_seconds))
                    continue
                if kind == "write":
                    # A timeout or disconnected response does not prove that
                    # Garmin rejected the mutation. Never retry the write, but
                    # also never serve a pre-write cached view afterward: the
                    # next read must reconcile against the remote account.
                    self.invalidate(resource=resource)
                self._increment("failures")
                raise

            if kind == "read" and cache_key is not None:
                self._cache_put(
                    cache_key,
                    result,
                    self._ttl_for(name, args, kwargs),
                    resource,
                )
            elif kind == "write":
                self.invalidate(resource=resource)
            return result

        raise AssertionError("unreachable retry state")

    def invalidate(self, *, resource: str | None = None) -> int:
        """Invalidate cached reads, optionally limiting to one resource group."""
        with self._lock:
            if resource is None or resource == "general":
                removed = len(self._cache)
                self._cache.clear()
            else:
                keys = [
                    key
                    for key, entry in self._cache.items()
                    if entry.resource in {resource, "general"}
                ]
                for key in keys:
                    del self._cache[key]
                removed = len(keys)
            self._stats["invalidations"] += removed
            return removed

    def stats(self) -> dict[str, int]:
        """Return a consistent snapshot of cache and request counters."""
        with self._lock:
            snapshot = dict(self._stats)
            snapshot["cache_entries"] = len(self._cache)
            now = self._monotonic()
            cutoff = now - 60.0
            while self._remote_call_times and self._remote_call_times[0] <= cutoff:
                self._remote_call_times.popleft()
            snapshot["request_budget_per_minute"] = (
                self.request_budget_per_minute or 0
            )
            snapshot["request_budget_used"] = len(self._remote_call_times)
            snapshot["request_budget_remaining"] = (
                max(0, self.request_budget_per_minute - len(self._remote_call_times))
                if self.request_budget_per_minute is not None
                else -1
            )
            return snapshot


class _TransportGateway:
    """Lazy façade for ``Garmin.client`` low-level HTTP methods."""

    def __init__(self, gateway: GarminGateway) -> None:
        self._gateway = gateway

    def __getattr__(self, name: str) -> Any:
        if name == "request":
            def request(*args: Any, **kwargs: Any) -> Any:
                raw = self._gateway.provider.get_client().client
                method = getattr(raw, name)
                verb = str(
                    args[0] if args else kwargs.get("method", "")
                ).strip().upper()
                kind = "read" if verb in {"GET", "HEAD", "OPTIONS"} else "write"
                return self._gateway._invoke(
                    name,
                    method,
                    args,
                    kwargs,
                    namespace="transport",
                    kind=kind,
                )

            return request

        kind = self._gateway._method_kind(name)
        if kind is not None:
            def call(*args: Any, **kwargs: Any) -> Any:
                raw = self._gateway.provider.get_client().client
                method = getattr(raw, name)
                return self._gateway._invoke(
                    name,
                    method,
                    args,
                    kwargs,
                    namespace="transport",
                    kind=kind,
                )

            return call
        return getattr(self._gateway.provider.get_client().client, name)
