"""Compatibility fixes for Garmin Connect China authentication.

``garminconnect`` 0.3.4 already selects the China API and DI OAuth hosts from
``is_cn=True``, but its mobile SSO service URLs are still hard-coded to the
international domain.  China service tickets are scoped to ``garmin.cn`` and
cannot be exchanged when those international service URLs are used.

The module also preserves JWT_WEB fallback state.  That state is useful when
Garmin temporarily rejects every DI client ID during MFA authentication.  The
upstream serializer currently persists only DI tokens.
"""

from __future__ import annotations

import json
from typing import Any

from garminconnect import client as garmin_client


_SERIALIZER_PATCHED = False


def configure_garmin_region(is_cn: bool) -> None:
    """Configure mobile SSO for the selected account region.

    Garmin MCP uses one account region per process.  Setting both values on
    every call also makes tests and command-line verification deterministic if
    they switch between international and China clients in one interpreter.
    """

    domain = "garmin.cn" if is_cn else "garmin.com"
    garmin_client.IOS_SERVICE_URL = f"https://mobile.integration.{domain}/gcm/ios"
    garmin_client.MOBILE_SSO_SERVICE_URL = (
        f"https://mobile.integration.{domain}/gcm/android"
    )
    _patch_token_serialization()


def _patch_token_serialization() -> None:
    """Persist JWT_WEB and cookies missing from garminconnect 0.3.4 dumps()."""

    global _SERIALIZER_PATCHED
    if _SERIALIZER_PATCHED:
        return

    original_dumps = garmin_client.Client.dumps
    original_loads = garmin_client.Client.loads

    # Avoid patching twice if a future garminconnect release adds these fields.
    probe = garmin_client.Client().dumps()
    if "jwt_web" in json.loads(probe):
        _SERIALIZER_PATCHED = True
        return

    def dumps(client: Any) -> str:
        data = json.loads(original_dumps(client))
        data.update(
            {
                "jwt_web": client.jwt_web,
                "csrf_token": client.csrf_token,
                "cookies": client.cs.cookies.get_dict(),
            }
        )
        return json.dumps(data)

    def loads(client: Any, tokenstore: str) -> None:
        data = json.loads(tokenstore)

        # Let upstream validate and load normal DI token stores.  A JWT-only
        # fallback store needs its extra fields restored before validation.
        if data.get("di_token"):
            original_loads(client, tokenstore)
        else:
            client.di_token = data.get("di_token")
            client.di_refresh_token = data.get("di_refresh_token")
            client.di_client_id = data.get("di_client_id")

        client.jwt_web = data.get("jwt_web")
        client.csrf_token = data.get("csrf_token")
        client.cs.cookies.update(data.get("cookies") or {})

        if not client.is_authenticated:
            # Reuse upstream's exception and message format.
            original_loads(client, tokenstore)

    garmin_client.Client.dumps = dumps
    garmin_client.Client.loads = loads
    _SERIALIZER_PATCHED = True
