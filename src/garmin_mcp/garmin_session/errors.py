"""Typed 401 detection for garminconnect.

``Client._run_request`` raises a bare ``GarminConnectConnectionError`` carrying
``f"API Error {status}"`` for every >=400 response, 401 included, after its own
single refresh-and-retry attempt fails. A caller that only drops its cached
session on ``GarminConnectAuthenticationError`` therefore never recovers from a
token that was rotated away by a peer process (see
ai-docs/shared-token-store.md): the poisoned client stays cached and every
later call fails identically until the process restarts.

This is the same problem ``garmin-scale-sync``/``hevy2garmin-lite`` already
fixed in their copy of this module
(``/Users/mr13/workspace/hevva2/src/garmin_session/errors.py``), but this file
is not a straight copy of theirs. Verified against the actually-installed
``garminconnect==0.3.11`` source (ai-docs/testing.md -- "never assume"):
``Client._api_session`` is a persistent ``requests.Session`` instance
attribute, created once in ``Client.__init__`` -- the same shape the sibling
module targets (unlike ``garminconnect==0.3.2``, which this file previously
supported: that version had no ``_api_session`` attribute at all, building a
brand-new session per call via a now-removed ``_fresh_api_session()`` factory
instead). Rather than the sibling module's ``instrument(garmin)`` pattern --
called explicitly by the caller after each client is built -- this wraps
``Client.__init__`` itself, so every instance's session is instrumented the
moment it exists and no call site needs to remember a separate call. See
``GarminSession._new_client()``, which relies on that.

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


def _instrument_session(client: Client) -> None:
    """Wrap one Client instance's persistent ``_api_session`` so its
    ``.request()`` records the real HTTP status of its last response onto
    the owning ``Client``.
    """
    session = client._api_session
    if getattr(session, _INSTRUMENTED_ATTR, False):
        return

    original_request = session.request

    def recording_request(*args, **kwargs):
        response = original_request(*args, **kwargs)
        setattr(client, _LAST_STATUS_ATTR, getattr(response, "status_code", None))
        return response

    session.request = recording_request
    setattr(session, _INSTRUMENTED_ATTR, True)


def _instrument_client_init() -> None:
    """Wrap ``Client.__init__`` so every instance is instrumented as soon as
    it's constructed, instead of requiring each caller to instrument it.
    """
    if getattr(Client.__init__, _INSTRUMENTED_ATTR, False):
        return

    original_init = Client.__init__

    def _init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        _instrument_session(self)

    setattr(_init, _INSTRUMENTED_ATTR, True)
    Client.__init__ = _init


def install() -> None:
    """Patch ``Client`` to raise the typed auth error on a real 401.

    Idempotent -- safe to call from every session/client construction.
    """
    global _installed
    with _install_lock:
        if _installed:
            return

        _instrument_client_init()

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
