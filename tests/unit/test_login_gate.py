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
