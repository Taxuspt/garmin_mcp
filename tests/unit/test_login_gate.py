"""Unit tests for the deferred-login helpers behind issue #255."""

import threading

import pytest

from garmin_mcp import _ThreadFilteredStream


class TestThreadFilteredStream:
    """Tests for _ThreadFilteredStream."""

    def test_owner_thread_write_passes_through(self):
        written = []
        real_stream = type("Fake", (), {"write": lambda self, s: written.append(s)})()
        stream = _ThreadFilteredStream(real_stream, threading.current_thread())

        stream.write("hello\n")

        assert written == ["hello\n"]

    def test_other_thread_write_is_swallowed(self):
        written = []
        real_stream = type("Fake", (), {"write": lambda self, s: written.append(s)})()
        other_thread = threading.Thread(target=lambda: None)
        stream = _ThreadFilteredStream(real_stream, other_thread)

        result = stream.write("stray\n")

        assert written == []
        assert result == len("stray\n")

    def test_unknown_attribute_delegates_to_real_stream(self):
        real_stream = type("Fake", (), {"encoding": "utf-8"})()
        stream = _ThreadFilteredStream(real_stream, threading.current_thread())

        assert stream.encoding == "utf-8"


from unittest.mock import Mock

from garmin_mcp import _GarminProxy, _PendingGarminClient


class TestPendingGarminClient:
    """Tests for _PendingGarminClient."""

    def test_blocks_until_login_succeeds_then_delegates(self):
        real_client = Mock()
        real_client.get_full_name.return_value = "Alice"
        release = threading.Event()

        def slow_login():
            release.wait(2)
            return real_client

        pending = _PendingGarminClient(timeout=5).start(slow_login)
        release.set()

        assert pending.get_full_name() == "Alice"

    def test_login_returning_none_raises_actionable_error(self):
        pending = _PendingGarminClient(timeout=5).start(lambda: None)

        with pytest.raises(RuntimeError, match="garmin-mcp-auth"):
            pending.get_full_name()

    def test_login_exception_is_replayed_unchanged(self):
        def failing_login():
            raise ValueError("boom")

        pending = _PendingGarminClient(timeout=5).start(failing_login)

        with pytest.raises(ValueError, match="boom"):
            pending.get_full_name()

    def test_timeout_elapses_raises_actionable_error(self):
        never_finishes = threading.Event()
        pending = _PendingGarminClient(timeout=0.05).start(lambda: never_finishes.wait(5))

        with pytest.raises(RuntimeError, match="garmin-mcp-auth"):
            pending.get_full_name()

        never_finishes.set()  # release the background thread so it can exit

    def test_composes_with_garmin_proxy_unchanged(self):
        """_GarminProxy needs zero code changes to wrap a _PendingGarminClient."""
        real_client = Mock()
        real_client.get_steps_data.return_value = [1, 2, 3]
        pending = _PendingGarminClient(timeout=5).start(lambda: real_client)

        proxy = _GarminProxy(pending)

        assert proxy.get_steps_data() == [1, 2, 3]
