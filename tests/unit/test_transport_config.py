"""Unit tests for HTTP transport configuration (_parse_transport_config)."""

import os
import pytest
from unittest.mock import patch

from garmin_mcp import _parse_transport_config, _VALID_TRANSPORTS


class TestParseTransportConfig:
    """Tests for _parse_transport_config."""

    def test_default_is_stdio(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GARMIN_MCP_TRANSPORT", None)
            os.environ.pop("GARMIN_MCP_HOST", None)
            os.environ.pop("GARMIN_MCP_PORT", None)
            transport, host, port = _parse_transport_config()
        assert transport == "stdio"
        assert host == "0.0.0.0"
        assert port == 8000

    @pytest.mark.parametrize("value", list(_VALID_TRANSPORTS))
    def test_valid_transports_are_accepted(self, value):
        with patch.dict(os.environ, {"GARMIN_MCP_TRANSPORT": value}):
            transport, _, _ = _parse_transport_config()
        assert transport == value

    def test_transport_value_is_lowercased(self):
        with patch.dict(os.environ, {"GARMIN_MCP_TRANSPORT": "STDIO"}):
            transport, _, _ = _parse_transport_config()
        assert transport == "stdio"

    def test_transport_value_is_stripped(self):
        with patch.dict(os.environ, {"GARMIN_MCP_TRANSPORT": "  streamable-http  "}):
            transport, _, _ = _parse_transport_config()
        assert transport == "streamable-http"

    def test_invalid_transport_raises_value_error(self):
        with patch.dict(os.environ, {"GARMIN_MCP_TRANSPORT": "websocket"}):
            with pytest.raises(ValueError, match="Invalid GARMIN_MCP_TRANSPORT"):
                _parse_transport_config()

    def test_custom_host_is_read(self):
        with patch.dict(os.environ, {"GARMIN_MCP_HOST": "127.0.0.1"}):
            _, host, _ = _parse_transport_config()
        assert host == "127.0.0.1"

    def test_custom_port_is_read(self):
        with patch.dict(os.environ, {"GARMIN_MCP_PORT": "9000"}):
            _, _, port = _parse_transport_config()
        assert port == 9000

    def test_invalid_port_raises(self):
        with patch.dict(os.environ, {"GARMIN_MCP_PORT": "not-a-number"}):
            with pytest.raises(ValueError):
                _parse_transport_config()
