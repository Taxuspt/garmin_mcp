"""
Structured error results for tools that write to Garmin Connect.

A failed write needs more than an error string: the caller has to know whether
the write may already have landed before deciding to retry, and an operator
needs a handle to find the matching server log line. ``WriteTracker`` records
whether the write request has been sent yet and turns any exception into a JSON
error result carrying:

- ``stage``: ``auth_refresh`` | ``garmin_request`` | ``response_parse`` | ``internal``
- ``upstream_status`` / ``upstream_body``: Garmin's HTTP status and message, when known
- ``error_type``: the exception class name
- ``write_committed``: ``true`` | ``false`` | ``"unknown"``
- ``correlation_id``: also written to the server log along with the traceback

Writes are never retried here: a request that timed out after being sent may
already have committed, and replaying it would create a duplicate. (The
garminconnect client already refreshes the token and retries once on a 401;
that retry is safe because Garmin rejected the first request.)
"""
import json
import logging
import re
import uuid

import requests
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

logger = logging.getLogger(__name__)

_MAX_BODY_CHARS = 500

# garminconnect raises GarminConnectConnectionError("API Error <status> - <msg>")
# for any HTTP status >= 400; the response object itself is not attached.
_API_ERROR_RE = re.compile(r"API Error (\d{3})(?: - (.*))?", re.DOTALL)


def _upstream(exc):
    """Return (status, body) parsed from a garminconnect error, or (None, None)."""
    if isinstance(exc, GarminConnectTooManyRequestsError):
        return 429, str(exc)[:_MAX_BODY_CHARS] or None
    match = _API_ERROR_RE.search(str(exc))
    if not match:
        return None, None
    body = (match.group(2) or "").strip()[:_MAX_BODY_CHARS]
    return int(match.group(1)), body or None


def _connection_never_opened(exc):
    """True if a requests error proves the request never reached Garmin."""
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    # DNS failure / connection refused: urllib3 wraps these in MaxRetryError
    # whose reason is a NewConnectionError (NameResolutionError subclasses it).
    reason = getattr(exc.args[0], "reason", None) if exc.args else None
    try:
        from urllib3.exceptions import NewConnectionError
    except ImportError:  # pragma: no cover - urllib3 always ships with requests
        return False
    return isinstance(reason, NewConnectionError)


class WriteTracker:
    """Tracks one write tool call so a failure can say whether it committed.

    Usage::

        tracker = WriteTracker("log_food", "Error logging food")
        try:
            ...read-only lookups...
            tracker.sending()
            resp = garmin_client.client.put(...)
            tracker.committed()
            ...
        except Exception as exc:
            return tracker.error(exc)
    """

    def __init__(self, tool, message_prefix):
        self.tool = tool
        self.message_prefix = message_prefix
        self.sent = False
        self.done = False

    def sending(self):
        """Mark that the write request is about to go out."""
        self.sent = True

    def committed(self):
        """Mark that Garmin accepted the write."""
        self.done = True

    def _classify(self, exc):
        """Return (stage, write_committed) for ``exc``."""
        if self.done:
            # Garmin accepted the write; only handling its response failed.
            return "response_parse", True
        if isinstance(exc, GarminConnectAuthenticationError):
            # Token refresh runs before the request is sent.
            return "auth_refresh", False
        if isinstance(exc, (GarminConnectConnectionError, GarminConnectTooManyRequestsError)):
            # Garmin answered with an error status: the write was rejected.
            return "garmin_request", False
        if isinstance(exc, requests.exceptions.JSONDecodeError):
            # Garmin answered 2xx but the body wasn't JSON. Checked before
            # RequestException, which JSONDecodeError also subclasses.
            return "response_parse", self.sent
        if isinstance(exc, requests.exceptions.RequestException):
            if not self.sent or _connection_never_opened(exc):
                return "garmin_request", False
            # Read timeout / connection reset after the request went out.
            return "garmin_request", "unknown"
        if isinstance(exc, TimeoutError):
            return "garmin_request", "unknown" if self.sent else False
        return "internal", "unknown" if self.sent else False

    def error(self, exc):
        """Log ``exc`` with a correlation ID and return a JSON error result."""
        correlation_id = uuid.uuid4().hex[:12]
        stage, committed = self._classify(exc)
        status, body = _upstream(exc)
        logger.error(
            "%s failed [correlation_id=%s stage=%s error_type=%s "
            "upstream_status=%s write_committed=%s]: %s",
            self.tool, correlation_id, stage, type(exc).__name__,
            status, committed, exc,
            exc_info=exc,
        )
        result = {
            "status": "error",
            "message": f"{self.message_prefix}: {exc}",
            "tool": self.tool,
            "stage": stage,
            "error_type": type(exc).__name__,
            "upstream_status": status,
            "upstream_body": body,
            "write_committed": committed,
            "correlation_id": correlation_id,
        }
        if committed == "unknown":
            result["hint"] = (
                "The request may have reached Garmin before failing. Check "
                "whether the change was applied before retrying, to avoid a "
                "duplicate."
            )
        elif committed is False:
            result["hint"] = "Nothing was written, so a retry cannot create a duplicate."
        return json.dumps(result, indent=2)
