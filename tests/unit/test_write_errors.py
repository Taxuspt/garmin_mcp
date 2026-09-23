"""Unit tests for structured write-tool errors (write_errors.WriteTracker).

Exception shapes mirror what garminconnect 0.3.x / requests raised live:
- invalid/expired token (after the library's own refresh+retry):
  GarminConnectConnectionError("API Error 401 - ")
- Garmin rejecting a quickAdd with a bad mealId:
  GarminConnectConnectionError("API Error 400 - Required Parameter meal id is null")
- DNS failure: requests ConnectionError wrapping MaxRetryError(NameResolutionError)
- unroutable host: requests ConnectTimeout
"""

import json
import logging

import pytest
import requests
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
from urllib3.exceptions import MaxRetryError, NameResolutionError

from garmin_mcp.write_errors import WriteTracker


def _error(exc, *, sent=False, committed=False):
    tracker = WriteTracker("log_food", "Error logging food")
    if sent:
        tracker.sending()
    if committed:
        tracker.committed()
    return json.loads(tracker.error(exc))


def _dns_failure():
    reason = NameResolutionError("connectapi.garmin.com", None, "nodename nor servname")
    return requests.exceptions.ConnectionError(
        MaxRetryError(None, "/nutrition-service/food/logs/quickAdd", reason)
    )


class TestClassification:
    def test_garmin_401_is_rejected_request(self):
        result = _error(GarminConnectConnectionError("API Error 401 - "), sent=True)
        assert result["stage"] == "garmin_request"
        assert result["upstream_status"] == 401
        assert result["upstream_body"] is None
        assert result["write_committed"] is False
        assert result["error_type"] == "GarminConnectConnectionError"

    def test_garmin_400_carries_upstream_body(self):
        exc = GarminConnectConnectionError(
            "API Error 400 - Required Parameter meal id is null"
        )
        result = _error(exc, sent=True)
        assert result["upstream_status"] == 400
        assert result["upstream_body"] == "Required Parameter meal id is null"
        assert result["write_committed"] is False

    def test_upstream_body_is_trimmed(self):
        exc = GarminConnectConnectionError("API Error 500 - " + "x" * 2000)
        result = _error(exc, sent=True)
        assert len(result["upstream_body"]) == 500

    def test_rate_limit_is_429(self):
        result = _error(GarminConnectTooManyRequestsError("slow down"), sent=True)
        assert result["upstream_status"] == 429
        assert result["write_committed"] is False

    def test_auth_error_is_auth_refresh(self):
        result = _error(GarminConnectAuthenticationError("DI token refresh failed"), sent=True)
        assert result["stage"] == "auth_refresh"
        assert result["write_committed"] is False

    def test_dns_failure_never_committed(self):
        result = _error(_dns_failure(), sent=True)
        assert result["stage"] == "garmin_request"
        assert result["error_type"] == "ConnectionError"
        assert result["write_committed"] is False

    def test_connect_timeout_never_committed(self):
        result = _error(requests.exceptions.ConnectTimeout("connect timed out"), sent=True)
        assert result["write_committed"] is False

    def test_read_timeout_after_send_is_unknown(self):
        result = _error(requests.exceptions.ReadTimeout("read timed out"), sent=True)
        assert result["stage"] == "garmin_request"
        assert result["write_committed"] == "unknown"
        assert "duplicate" in result["hint"]

    def test_read_timeout_before_send_is_not_committed(self):
        # e.g. the meal lookup GET timed out; the write was never attempted
        result = _error(requests.exceptions.ReadTimeout("read timed out"), sent=False)
        assert result["write_committed"] is False

    def test_proxy_timeout_after_send_is_unknown(self):
        result = _error(TimeoutError("did not return within 90s"), sent=True)
        assert result["write_committed"] == "unknown"

    def test_non_json_2xx_body_is_response_parse_committed(self):
        exc = requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)
        result = _error(exc, sent=True)
        assert result["stage"] == "response_parse"
        assert result["write_committed"] is True

    def test_failure_after_commit_is_committed(self):
        result = _error(TypeError("not JSON serializable"), sent=True, committed=True)
        assert result["stage"] == "response_parse"
        assert result["write_committed"] is True

    def test_unexpected_error_before_send(self):
        result = _error(KeyError("mealId"), sent=False)
        assert result["stage"] == "internal"
        assert result["write_committed"] is False


class TestResultShape:
    def test_message_keeps_tool_prefix(self):
        result = _error(GarminConnectConnectionError("API Error 401 - "), sent=True)
        assert result["status"] == "error"
        assert result["tool"] == "log_food"
        assert result["message"].startswith("Error logging food: API Error 401")

    def test_correlation_id_is_logged(self, caplog):
        with caplog.at_level(logging.ERROR, logger="garmin_mcp.write_errors"):
            result = _error(requests.exceptions.ReadTimeout("read timed out"), sent=True)
        cid = result["correlation_id"]
        assert len(cid) == 12
        record = next(r for r in caplog.records if cid in r.getMessage())
        assert "log_food failed" in record.getMessage()
        assert "write_committed=unknown" in record.getMessage()
        assert record.exc_info is not None

    def test_correlation_ids_are_unique(self):
        exc = GarminConnectConnectionError("API Error 401 - ")
        assert _error(exc)["correlation_id"] != _error(exc)["correlation_id"]
