"""Unit tests for the deferred-login helpers behind issue #255."""

import threading

import pytest

from garmin_mcp import _ThreadFilteredStream, _ThreadSuppressibleStream


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


class TestThreadSuppressibleStream:
    """Tests for _ThreadSuppressibleStream (login-thread stderr silencing)."""

    def _fake(self):
        written = []
        real_stream = type("Fake", (), {"write": lambda self, s: written.append(s)})()
        return written, _ThreadSuppressibleStream(real_stream)

    def test_suppressed_thread_write_is_swallowed(self):
        written, stream = self._fake()

        with stream.suppressed():
            result = stream.write("noisy\n")
        stream.write("after\n")

        assert written == ["after\n"]
        assert result == len("noisy\n")

    def test_other_thread_writes_while_one_is_suppressed(self):
        """The main thread's writes survive while the login thread is silenced.

        Regression: init_api() used to swap the process-wide sys.stderr for a
        StringIO, so the main thread's "Serving MCP over ..." line (and
        uvicorn's startup logs) printed during login were lost.
        """
        written, stream = self._fake()
        entered, release = threading.Event(), threading.Event()

        def login():
            with stream.suppressed():
                entered.set()
                stream.write("library warning\n")
                release.wait(5)

        worker = threading.Thread(target=login)
        worker.start()
        assert entered.wait(5)
        stream.write("Serving MCP over streamable-http\n")
        release.set()
        worker.join(5)

        assert written == ["Serving MCP over streamable-http\n"]

    def test_unknown_attribute_delegates_to_real_stream(self):
        real_stream = type("Fake", (), {"encoding": "utf-8"})()
        stream = _ThreadSuppressibleStream(real_stream)

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

    def test_repeated_login_failures_do_not_grow_the_traceback(self):
        def failing_login():
            raise ValueError("boom")

        pending = _PendingGarminClient(timeout=5).start(failing_login)

        def traceback_depth():
            with pytest.raises(ValueError) as excinfo:
                pending.get_full_name()
            depth, tb = 0, excinfo.value.__traceback__
            while tb is not None:
                depth += 1
                tb = tb.tb_next
            return depth

        first = traceback_depth()
        for _ in range(20):
            traceback_depth()

        assert traceback_depth() == first

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


from garmin_mcp import _readiness


class TestLoginState:
    """Tests for _PendingGarminClient.login_state() and the /readyz payload."""

    def test_pending_while_login_runs_and_never_blocks(self):
        release = threading.Event()
        pending = _PendingGarminClient(timeout=5).start(lambda: release.wait(5) and Mock())

        assert pending.login_state() == "pending"
        assert _readiness(pending) == (503, {"status": "pending"})

        release.set()

    def test_ready_after_successful_login(self):
        pending = _PendingGarminClient(timeout=5).start(lambda: Mock())
        pending._ready.wait(5)

        assert pending.login_state() == "ready"
        assert _readiness(pending) == (200, {"status": "ready"})

    def test_failed_when_login_returns_none(self):
        pending = _PendingGarminClient(timeout=5).start(lambda: None)
        pending._ready.wait(5)

        assert pending.login_state() == "failed"
        assert _readiness(pending) == (503, {"status": "failed"})

    def test_failed_when_login_raises(self):
        def failing_login():
            raise ValueError("boom")

        pending = _PendingGarminClient(timeout=5).start(failing_login)
        pending._ready.wait(5)

        assert pending.login_state() == "failed"
