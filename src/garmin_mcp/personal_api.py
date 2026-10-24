"""Official Garmin Connect Personal API backend.

On 2026-10-21 Garmin reopened and split its Connect developer program into
Personal / Starter / Enterprise tiers. The Personal tier is the only one an
individual can turn on alone: a key generated on
https://connect.garmin.com/app/settings/personalApiToken, exchanged for a
24-hour bearer token, read-only.

The Personal API is not a new data model. It is a gateway
(``https://personal.connectapi.garmin.com``) that serves the very same service
paths Garmin Connect's own web client uses -- ``/usersummary-service/...``,
``/sleep-service/...``, ``/activitylist-service/...`` -- behind OAuth2 bearer
auth instead of the SSO cookie. That is why this backend can reuse every
``garminconnect`` high-level method unchanged: only the transport changes
(host, headers, token lifecycle), which is exactly what this module replaces.

What it does NOT change:

* **Writes.** The Personal tier is read-only. Garmin gates Courses, Training
  and every other write behind the business tiers, so ``post``/``put``/
  ``delete`` raise :class:`PersonalApiReadOnlyError` here instead of failing
  with an opaque 403 from the gateway.
* **Endpoints Garmin does not expose on this gateway** (badges, challenges,
  gear, courses, nutrition settings, ...). Those fail with the gateway's status
  code; ``scripts/probe_personal_api.py`` records which ones work.

Auth lifecycle (from Garmin's own client, ``garmin/connect-personal-api-python``):

1. ``POST /personalapitoken-service/personal-api/personal-api-key/token``
   with ``Authorization: ApiKey <key>``. The response body is the raw JWT.
2. The JWT carries its own ``exp`` (24h, or the key's expiry, whichever is
   first). There are no refresh tokens: re-exchange the key instead.
3. Keys expire after 30/90/180/365 days and are shown once, so a 401 means
   "generate a new key", not "refresh".
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping

import requests

from garminconnect import Garmin, GarminConnectAuthenticationError, GarminConnectConnectionError
from garminconnect.client import Client as GarminHttpClient

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://personal.connectapi.garmin.com"
BASE_URL_ENV_VAR = "GARMIN_PERSONAL_API_BASE_URL"
API_KEY_ENV_VAR = "GARMIN_PERSONAL_API_KEY"
API_KEY_FILE_ENV_VAR = "GARMIN_PERSONAL_API_KEY_FILE"
DEFAULT_API_KEY_FILE = "~/.config/garmin/connect-personal-api-key"
DEFAULT_TOKEN_CACHE_FILE = "~/.config/garmin/connect-personal-api-bearer-token.json"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
DEFAULT_TIMEOUT_SECONDS = 30.0

# Re-exchange the key this long before the token expires. Garmin uses the same
# one-hour window in its own client; tokens live 24h, so a long-lived process
# re-auths at most once a day either way.
REFRESH_WINDOW_SECONDS = 60 * 60

# A cached token is bound to the key and the endpoint it came from: a token
# minted for another account (or the staging base URL) must never be reused.
_READ_METHODS = frozenset({"GET"})


class PersonalApiError(RuntimeError):
    """Base error for the Personal API backend."""


class PersonalApiReadOnlyError(PersonalApiError):
    """Raised when a write is attempted through the read-only Personal tier."""


class PersonalApiAuthError(PersonalApiError):
    """Raised when the API key is missing, rejected, or expired."""


def resolve_base_url() -> str:
    """Return the Personal API base URL, honouring the official override."""
    return (os.getenv(BASE_URL_ENV_VAR) or DEFAULT_BASE_URL).rstrip("/")


def resolve_api_key_file() -> Path:
    """Return the key file path (``GARMIN_PERSONAL_API_KEY_FILE`` or default)."""
    raw = os.getenv(API_KEY_FILE_ENV_VAR) or DEFAULT_API_KEY_FILE
    return Path(os.path.expandvars(raw)).expanduser()


def resolve_api_key() -> tuple[str, str] | None:
    """Find a Personal API key and say where it came from.

    Precedence mirrors Garmin's own client: the environment variable first
    (what a Docker/k8s deployment sets), then the key file. A key that is empty
    or only whitespace counts as absent, so a template left with
    ``GARMIN_PERSONAL_API_KEY=`` does not silently select this backend.

    Returns:
        ``(key, source)`` or ``None`` when no key is configured.
    """
    raw = os.getenv(API_KEY_ENV_VAR)
    if raw is not None and raw.strip():
        return raw.strip(), f"${API_KEY_ENV_VAR}"

    path = resolve_api_key_file()
    try:
        from_file = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as err:
        raise PersonalApiAuthError(
            f"Cannot read the Personal API key file '{path}': {err}"
        ) from err
    if not from_file:
        return None
    return from_file, str(path)


def _token_cache_file() -> Path:
    raw = os.getenv("GARMIN_PERSONAL_API_TOKEN_CACHE") or DEFAULT_TOKEN_CACHE_FILE
    return Path(os.path.expandvars(raw)).expanduser()


def _jwt_expiration(token: str, *, default_ttl: float = 24 * 60 * 60) -> float:
    """Return the ``exp`` claim of a JWT, without verifying its signature.

    Garmin documents a 24-hour lifetime but does not promise the body is a JWT.
    A body without a readable ``exp`` is therefore treated as valid for the
    documented 24 hours instead of failing the whole login.
    """
    try:
        payload_b64 = token.split(".")[1]
        payload = json.loads(
            base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        )
        expires = float(payload["exp"])
    except (IndexError, KeyError, TypeError, ValueError):
        logger.debug("Personal API token has no readable exp claim; assuming 24h")
        return time.time() + default_ttl
    # RFC 7519 says seconds, but a millisecond value would otherwise read as a
    # token valid for ~50,000 years and we would only notice on a 401.
    if expires > 1e12:
        expires /= 1000
    return expires


class PersonalApiAuthenticator:
    """Exchange a Personal API key for bearer tokens and cache them.

    The cache is keyed by a fingerprint of (base URL, key) so that rotating the
    key or pointing at another endpoint never reuses a stale token. Writes are
    owner-only and atomic enough for the single-process MCP server (a lock
    guards concurrent tool calls in the same process).
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
        cache_file: str | Path | None = None,
        clock=time.time,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise PersonalApiAuthError("A non-empty Garmin Personal API key is required")
        if timeout <= 0:
            raise PersonalApiError("timeout must be greater than zero")
        self.api_key = api_key.strip()
        self.base_url = (base_url or resolve_base_url()).rstrip("/")
        self.timeout = timeout
        self._session = session or requests.Session()
        self._cache_file = Path(cache_file).expanduser() if cache_file else _token_cache_file()
        self._clock = clock
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._fingerprint = hashlib.sha256(
            f"{self.base_url}\0{self.api_key}".encode()
        ).hexdigest()

    @property
    def token(self) -> str | None:
        return self._token

    @property
    def expires_at(self) -> float:
        return self._expires_at

    def ensure_token(self, *, force: bool = False) -> str:
        """Return a usable bearer token, exchanging the key when needed."""
        with self._lock:
            if not force and self._token and self._expires_at - self._clock() > REFRESH_WINDOW_SECONDS:
                return self._token
            if not force and self._load_cached_token():
                return self._token  # type: ignore[return-value]
            self._exchange()
            return self._token  # type: ignore[return-value]

    def _load_cached_token(self) -> bool:
        try:
            cache = json.loads(self._cache_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False
        except (OSError, ValueError):
            logger.warning("Ignoring unreadable token cache %s", self._cache_file)
            return False
        if cache.get("fingerprint") != self._fingerprint:
            logger.debug("Token cache belongs to another key or endpoint; ignoring")
            return False
        try:
            token = str(cache["access_token"])
            expires_at = float(cache["expires_at"])
        except (KeyError, TypeError, ValueError):
            return False
        if not token:
            return False
        if expires_at - self._clock() <= REFRESH_WINDOW_SECONDS:
            logger.debug("Cached Personal API token is inside the refresh window")
            return False
        self._token = token
        self._expires_at = expires_at
        return True

    def _exchange(self) -> None:
        url = f"{self.base_url}{TOKEN_PATH}"
        logger.info("Requesting a Garmin Personal API token")
        try:
            response = self._session.post(
                url,
                headers={"Accept": "*/*", "Authorization": f"ApiKey {self.api_key}"},
                timeout=self.timeout,
            )
        except requests.RequestException as err:
            raise PersonalApiError(
                f"Could not reach the Garmin Personal API at {self.base_url}: {err}"
            ) from err

        if response.status_code in (401, 403):
            raise PersonalApiAuthError(
                "Garmin rejected the Personal API key "
                f"(HTTP {response.status_code}). The key is invalid, revoked, or expired: "
                "generate a new one at https://connect.garmin.com/app/settings/personalApiToken "
                "and update GARMIN_PERSONAL_API_KEY (or the key file)."
            )
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            hint = f" Retry after {retry_after}s." if retry_after else ""
            raise PersonalApiAuthError(
                f"Garmin rate-limited the token endpoint (HTTP 429).{hint} "
                "Wait a few minutes; the key itself is probably fine."
            )
        if not response.ok:
            # The response body is deliberately not echoed: this is the one
            # request that carries the API key, so a gateway that reflects
            # request details would put it in the server's stderr log.
            raise PersonalApiError(
                f"Personal API token request failed (HTTP {response.status_code}). "
                "The response body is withheld because it can echo the credential."
            )

        token = response.text.strip().strip('"')
        if not token:
            raise PersonalApiError("Personal API token endpoint returned an empty body")
        self._token = token
        self._expires_at = _jwt_expiration(token)
        self._save_cached_token()
        logger.info("Obtained a Garmin Personal API token, valid until %s",
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._expires_at)))

    def _save_cached_token(self) -> None:
        path = self._cache_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "fingerprint": self._fingerprint,
                        "access_token": self._token,
                        "expires_at": self._expires_at,
                        "base_url": self.base_url,
                    },
                    handle,
                )
        except OSError as err:
            # The token still works for this process; losing the cache only
            # costs one extra exchange per start.
            logger.warning("Could not cache the Personal API token in %s: %s", path, err)


class PersonalApiHttpClient(GarminHttpClient):
    """``garminconnect`` HTTP client pointed at the official Personal gateway.

    Subclassing the library's transport (rather than reimplementing every
    ``get_activities``/``get_sleep_data`` wrapper) keeps the ~90% of this
    server that reads data identical across backends.

    ``timeout`` and ``session`` apply to both the token exchange and the data
    calls this client makes. The parent still builds a fresh ``requests``
    session per call for connection reuse; ``session`` here is the one the
    authenticator uses, which is what makes the class testable offline.
    """

    # Documentation only: the enforcement is in _run_request, which is the one
    # method every read/write in the library funnels through. This flag is not
    # consulted by tool code, so do not treat it as a security boundary.
    read_only = True

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
        cache_file: str | Path | None = None,
    ) -> None:
        super().__init__(domain="garmin.com")
        self._auth = PersonalApiAuthenticator(
            api_key,
            base_url=base_url,
            timeout=timeout,
            session=session,
            cache_file=cache_file,
        )
        self.base_url = self._auth.base_url
        # ``Client._run_request`` builds every URL from ``_connectapi``; the
        # high-level methods only supply the path, so one assignment moves the
        # whole library onto the official gateway.
        self._connectapi = self.base_url
        self._connect = self.base_url
        self.ensure_token()

    # -- token lifecycle ---------------------------------------------------
    def ensure_token(self, *, force: bool = False) -> str:
        self.di_token = self._auth.ensure_token(force=force)
        return self.di_token

    def _refresh_di_token(self) -> None:
        """Re-exchange the API key (Personal tokens have no refresh token)."""
        self.ensure_token(force=True)

    def get_api_headers(self) -> dict[str, str]:
        """Send exactly what Garmin's own Personal client sends.

        The library's default headers impersonate the Android app; the Personal
        gateway is documented against a bare ``Authorization: Bearer`` request,
        so keep the surface minimal.
        """
        if not self.di_token:
            raise GarminConnectAuthenticationError("Not authenticated")
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.di_token}",
        }

    # -- transport ---------------------------------------------------------
    def _run_request(self, method: str, path: str, **kwargs: Any) -> Any:
        if method.upper() not in _READ_METHODS:
            raise PersonalApiReadOnlyError(
                f"{method.upper()} {path} writes to Garmin Connect, but the Personal API "
                "tier is read-only. Garmin gates Courses and Training (and every other "
                "write) behind the business Starter tier. Set GARMIN_AUTH_MODE=sso to use "
                "the SSO backend for writes, or keep this backend for reads."
            )
        # Refresh before the call so the 401 retry in the parent class is a
        # genuine last resort rather than the normal path.
        self.ensure_token()
        # The parent uses a fresh session per call and defaults the timeout to
        # 15s. Honour the timeout this client was built with instead, so
        # GARMIN_PERSONAL_API_TIMEOUT-style configuration actually applies to
        # data calls and not only to the token exchange.
        kwargs.setdefault("timeout", self._auth.timeout)
        return super()._run_request(method, path, **kwargs)

    def describe(self) -> dict[str, Any]:
        """Non-secret summary for diagnostics and the auth CLI."""
        return {
            "backend": "personal",
            "base_url": self.base_url,
            "read_only": True,
            "timeout": self._auth.timeout,
            "token_cached_until": self._auth.expires_at,
            "token_cache_file": str(self._auth._cache_file),
        }


def _read_profile(client: PersonalApiHttpClient) -> tuple[str | None, str | None, str | None]:
    """Return ``(display_name, full_name, unit_system)`` from the gateway.

    ``Garmin.login()`` populates these from ``/userprofile-service/socialProfile``
    and ``/userprofile-service/userprofile/user-settings``. The Personal API
    documents the settings endpoint but not socialProfile, so try the
    documented one first and fall back -- ``display_name`` ends up in the URLs
    of many tools, so failing to load it breaks them later with a confusing 403.
    """
    attempts = (
        ("/userprofile-service/userprofile/settings", None),
        ("/userprofile-service/socialProfile", None),
        ("/userprofile-service/userprofile/user-settings", "userData"),
    )
    last_error: Exception | None = None
    for path, unit_key in attempts:
        try:
            payload = client.connectapi(path)
        except Exception as err:  # noqa: BLE001 - any failure means "try the next one"
            last_error = err
            logger.debug("Profile endpoint %s unavailable: %s", path, err)
            continue
        if not isinstance(payload, Mapping):
            continue
        user_data = payload.get("userData") if isinstance(payload.get("userData"), Mapping) else {}
        display_name = payload.get("displayName") or user_data.get("displayName")
        full_name = payload.get("fullName") or user_data.get("fullName")
        unit_system = user_data.get("measurementSystem") or payload.get("measurementSystem")
        if display_name or full_name or unit_system:
            return display_name, full_name, unit_system
    if last_error is not None:
        logger.debug("No profile available through the Personal API: %s", last_error)
    return None, None, None


def login_personal(
    api_key: str | None = None,
    *,
    base_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    cache_file: str | Path | None = None,
) -> Garmin:
    """Return a ``Garmin`` client backed by the official Personal API.

    The returned object is a normal ``garminconnect.Garmin`` instance, so every
    module in this server keeps working unchanged. No SSO request is made: the
    API key replaces the email/password login entirely, which is what makes this
    backend immune to the 429 rate limiting and token-store confusion behind
    issues #330, #366 and #477.

    Raises:
        PersonalApiAuthError: when no key is configured or Garmin rejects it.
        PersonalApiError: when the gateway is unreachable or answers badly.
    """
    source = "argument"
    if api_key is None:
        resolved = resolve_api_key()
        if resolved is None:
            raise PersonalApiAuthError(
                "No Garmin Personal API key found. Set GARMIN_PERSONAL_API_KEY or write "
                f"the key to {resolve_api_key_file()}. Create one at "
                "https://connect.garmin.com/app/settings/personalApiToken"
            )
        api_key, source = resolved

    garmin = Garmin()
    garmin.client = PersonalApiHttpClient(
        api_key,
        base_url=base_url,
        timeout=timeout,
        cache_file=cache_file,
    )
    garmin.display_name, garmin.full_name, garmin.unit_system = _read_profile(garmin.client)
    logger.info(
        "Garmin Personal API backend ready (key from %s, user %s)",
        source,
        garmin.display_name or "unknown",
    )
    return garmin


def is_personal_backend(client: Any) -> bool:
    """True when ``client`` (a ``Garmin``) talks to the Personal API."""
    return isinstance(getattr(client, "client", None), PersonalApiHttpClient)


def read_only_reason(client: Any) -> str | None:
    """Human-readable read-only notice for tool output, or ``None``."""
    if is_personal_backend(client):
        return (
            "This server is using the read-only Garmin Personal API. Writes "
            "(workouts, courses, weigh-ins, nutrition, ...) need the SSO backend "
            "(GARMIN_AUTH_MODE=sso) or Garmin's business Starter tier."
        )
    return None


def describe_backend(client: Any) -> dict[str, Any]:
    """Diagnostics for ``describe_backend``-style tools and the auth CLI."""
    inner = getattr(client, "client", None)
    if isinstance(inner, PersonalApiHttpClient):
        return inner.describe()
    return {"backend": "sso", "read_only": False}


def describe_login_error(err: BaseException) -> str:
    """Turn a Personal API failure into an actionable sentence."""
    if isinstance(err, PersonalApiReadOnlyError):
        return str(err)
    if isinstance(err, PersonalApiAuthError):
        return str(err)
    if isinstance(err, GarminConnectAuthenticationError):
        return f"Personal API authentication failed: {err}"
    if isinstance(err, (GarminConnectConnectionError, PersonalApiError)):
        return f"Personal API request failed: {err}"
    return f"Personal API backend failed: {err}"
