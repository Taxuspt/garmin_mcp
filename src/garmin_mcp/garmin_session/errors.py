"""Typed 401 detection for garminconnect.

``Client._run_request`` raises a bare ``GarminConnectConnectionError`` carrying
``f"API Error {status}"`` for every >=400 response, 401 included, after its own
single refresh-and-retry attempt fails. A caller that only drops its cached
session on ``GarminConnectAuthenticationError`` therefore never recovers from a
token that was rotated away by a peer process (see
ai-docs/shared-token-store.md): the poisoned client stays cached and every
later call fails identically until the process restarts.

This is the same problem ``garmin-scale-sync``/``hevy2garmin-lite`` already
fixed in their copy of this module (``/Users/mr13/workspace/hevva2/src/garmin_session/errors.py``),
but **this file is not a straight copy** of theirs. Verified against the
actually-installed ``garminconnect==0.3.2`` source (ai-docs/testing.md — "never
assume"): their version instruments ``Client._api_session``, a persistent
``requests.Session`` instance attribute that's safe to wrap once. This version
has no such attribute — ``Client._run_request`` calls
``self._fresh_api_session()`` to build a **brand-new** session on every single
call. So instead of wrapping one long-lived session, this wraps the *factory*
(``_fresh_api_session``) once, so every session it ever hands back already has
its ``.request()`` recording the real HTTP status onto the owning ``Client``.

Either way, the point is the same: key off the real HTTP status code, not a
regex match on ``"API Error 401"`` in the exception message. That couples
recovery to Garmin's error text and this library's f-string, either of which
can change silently. This module keys off what the server actually returned.

The real fix belongs upstream (decorate ``_run_request``, or raise the auth
error directly on 401). When that lands, delete this module.
"""

import threading

from garminconnect import GarminConnectAuthenticationError, GarminConnectConnectionError
from garminconnect.client import Client

_LAST_STATUS_ATTR = "_gmcp_last_http_status"
_INSTRUMENTED_ATTR = "_gmcp_instrumented"

_install_lock = threading.Lock()
_installed = False


def _instrument_session_factory() -> None:
    """Wrap ``Client._fresh_api_session`` so every session it creates records
    the real HTTP status of its last response onto the owning ``Client``.
    """
    if getattr(Client._fresh_api_session, _INSTRUMENTED_ATTR, False):
        return

    original_fresh_api_session = Client._fresh_api_session

    def _fresh_api_session(self):
        session = original_fresh_api_session(self)
        original_request = session.request

        def recording_request(*args, **kwargs):
            response = original_request(*args, **kwargs)
            setattr(self, _LAST_STATUS_ATTR, getattr(response, "status_code", None))
            return response

        session.request = recording_request
        return session

    setattr(_fresh_api_session, _INSTRUMENTED_ATTR, True)
    Client._fresh_api_session = _fresh_api_session


def install() -> None:
    """Patch ``Client._run_request`` to raise the auth error on a real 401.

    Idempotent — safe to call from every session/client construction.
    """
    global _installed
    with _install_lock:
        if _installed:
            return

        _instrument_session_factory()

        original_run_request = Client._run_request

        def _run_request(self, method, path, **kwargs):
            setattr(self, _LAST_STATUS_ATTR, None)
            try:
                return original_run_request(self, method, path, **kwargs)
            except GarminConnectConnectionError as exc:
                if getattr(self, _LAST_STATUS_ATTR, None) == 401:
                    raise GarminConnectAuthenticationError(
                        f"Garmin rejected the session (HTTP 401): {exc}"
                    ) from exc
                raise

        Client._run_request = _run_request
        _installed = True
