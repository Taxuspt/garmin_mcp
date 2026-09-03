"""Privacy guardrails for recorded Garmin GET-contract fixtures.

This module does not perform network recording.  A recorder must pass every
captured request/response through :func:`build_get_fixture`; non-GET traffic is
rejected so write operations can only use manually reviewed fixtures.
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SECRET_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "access_token",
    "refresh_token",
    "oauth_token",
    "oauth_token_secret",
    "token",
    "password",
}
_IDENTITY_KEYS = {
    "userid",
    "userprofileid",
    "profileid",
    "personid",
    "athleteid",
    "displayname",
    "fullname",
}
_NAME_KEYS = {"activityname", "workoutname", "coursename"}
_LOCATION_FRAGMENTS = (
    "latitude",
    "longitude",
    "location",
    "coordinate",
    "polyline",
    "geoposition",
    "gps",
)


def _key(value: Any) -> str:
    return str(value).strip().lower().replace("_", "").replace("-", "")


_NORMALIZED_SECRET_KEYS = frozenset(_key(item) for item in _SECRET_KEYS)


def sanitize_recording(value: Any) -> Any:
    """Recursively redact secrets, identity, activity names, and GPS location."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key_text = str(raw_key)
            normalized = _key(key_text)
            if normalized in _NORMALIZED_SECRET_KEYS:
                result[key_text] = "<redacted-secret>"
            elif normalized in _IDENTITY_KEYS:
                result[key_text] = "<redacted-identity>"
            elif normalized in _NAME_KEYS:
                result[key_text] = "<redacted-name>"
            elif any(fragment in normalized for fragment in _LOCATION_FRAGMENTS):
                result[key_text] = "<redacted-location>"
            else:
                result[key_text] = sanitize_recording(item)
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize_recording(item) for item in value]
    return value


def sanitize_url(url: str) -> str:
    """Redact sensitive query values while retaining endpoint shape."""

    parsed = urlsplit(url)
    sanitized_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = _key(key)
        if (
            normalized in _IDENTITY_KEYS
            or normalized in _NORMALIZED_SECRET_KEYS
            or any(fragment in normalized for fragment in _LOCATION_FRAGMENTS)
        ):
            value = "<redacted>"
        sanitized_query.append((key, value))
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(sanitized_query), "")
    )


def build_get_fixture(
    *,
    method: str,
    url: str,
    request_headers: Mapping[str, Any] | None,
    response_status: int,
    response_headers: Mapping[str, Any] | None,
    response_body: Any,
) -> dict[str, Any]:
    """Build a sanitized fixture and refuse any non-GET recording."""

    if method.strip().upper() != "GET":
        raise ValueError(
            "Only GET responses may be recorded automatically; write fixtures require manual review"
        )
    return {
        "request": {
            "method": "GET",
            "url": sanitize_url(url),
            "headers": sanitize_recording(dict(request_headers or {})),
        },
        "response": {
            "status": int(response_status),
            "headers": sanitize_recording(dict(response_headers or {})),
            "body": sanitize_recording(response_body),
        },
    }
