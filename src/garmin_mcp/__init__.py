"""
Modular MCP Server for Garmin Connect Data
"""

import asyncio
import copy
import functools
import inspect
import os
import sys
import base64
import contextlib
import threading

import anyio
import requests
from mcp.server.fastmcp import FastMCP

from garminconnect import Garmin, GarminConnectAuthenticationError, GarminConnectConnectionError, GarminConnectTooManyRequestsError

# Import all modules
from garmin_mcp import token_utils
from garmin_mcp import activity_management
from garmin_mcp import health_wellness
from garmin_mcp import user_profile
from garmin_mcp import devices
from garmin_mcp import gear_management
from garmin_mcp import weight_management
from garmin_mcp import challenges
from garmin_mcp import training
from garmin_mcp import workouts
from garmin_mcp import workout_templates
from garmin_mcp import data_management
from garmin_mcp import womens_health
from garmin_mcp import nutrition
from garmin_mcp import workout_builders
from garmin_mcp import courses
from garmin_mcp import activity_analysis
from garmin_mcp import calendar_events
from garmin_mcp import exercise_catalog
from garmin_mcp.tool_annotations import annotations_for
from garmin_mcp.tool_profiles import resolve_tool_profile
from garmin_mcp.write_errors import _upstream


def is_interactive_terminal() -> bool:
    """Detect if running in interactive terminal vs MCP subprocess.

    Returns:
        bool: True if running in an interactive terminal, False otherwise
    """
    return sys.stdin.isatty() and sys.stdout.isatty()


def get_mfa() -> str:
    """Direct server MFA to the auth CLI so stdin belongs to the MCP transport."""
    message = "MFA required; run 'garmin-mcp-auth' before starting the server."
    print(f"\nERROR: {message}\n", file=sys.stderr)
    raise RuntimeError(message)


def _normalize_optional_user_config(value: str | None, key: str) -> str | None:
    """Treat an unresolved optional Desktop Extension value as unset."""
    unresolved_placeholder = f"${{user_config.{key}}}"
    return None if value == unresolved_placeholder else value


# --- Credentials and token locations ----------------------------------------
# Read when main() or init_api() runs, not at import time, so importing the
# package (tests, garmin-mcp-auth, the DXT tool generator) has no side effects
# and a bad environment fails with a message instead of an import traceback.
#   GARMIN_EMAIL / GARMIN_EMAIL_FILE       - one of the two, never both
#   GARMIN_PASSWORD / GARMIN_PASSWORD_FILE - one of the two, never both
#   GARMINTOKENS / GARMINTOKENS_BASE64     - token locations (see token_utils)
#   GARMIN_IS_CN                           - true/1/yes for garmin.cn
def _read_secret_env(name: str) -> str | None:
    """Return $NAME, or the contents of the file named by $NAME_FILE."""
    value = os.environ.get(name)
    file_path = os.environ.get(f"{name}_FILE")
    if value and file_path:
        raise ValueError(f"Must only provide one of {name} and {name}_FILE, got both")
    if file_path:
        try:
            with open(file_path, "r") as fh:
                return fh.read().rstrip()
        except OSError as exc:
            raise ValueError(
                f"Cannot read {name}_FILE '{file_path}': {exc.strerror or exc}"
            ) from exc
    return value


def load_credentials() -> tuple[str | None, str | None]:
    """Read the Garmin email and password from the environment.

    Raises ValueError when a variable and its _FILE twin are both set, or
    when a referenced file cannot be read.
    """
    return _read_secret_env("GARMIN_EMAIL"), _read_secret_env("GARMIN_PASSWORD")


def _resolve_is_cn() -> bool:
    return os.getenv("GARMIN_IS_CN", "false").lower() in ("true", "1", "yes")


# --- Tool filtering ---------------------------------------------------------
# Optionally expose only a subset of tools, to reduce the context an LLM must
# carry. No modules are removed; tools are simply not registered when filtered.
#   GARMIN_TOOL_PROFILE   - built-in subset by area (see tool_profiles.py);
#                           several may be combined with commas; "all" = default
#   GARMIN_ENABLED_TOOLS  - comma-separated allowlist; if set, ONLY these register
#   GARMIN_DISABLED_TOOLS - comma-separated denylist; ignored if an allowlist is set
# With a profile the lists compose instead: the allowlist ADDS tools to the
# profile and the denylist REMOVES tools from the result.
# Tool names are case-insensitive. Unset = all tools register (default behaviour).
def _parse_tool_set(value):
    if not value:
        return set()
    return {name.strip().lower() for name in value.split(",") if name.strip()}


def _resolve_tool_filters():
    """Read and validate tool filter environment variables at server startup."""
    enabled_value = os.getenv("GARMIN_ENABLED_TOOLS")
    enabled_tools = _parse_tool_set(enabled_value)
    if enabled_value and enabled_value.strip() and not enabled_tools:
        raise ValueError(
            "Invalid GARMIN_ENABLED_TOOLS: expected at least one tool name"
        )
    disabled_tools = _parse_tool_set(os.getenv("GARMIN_DISABLED_TOOLS"))
    return enabled_tools, disabled_tools


def _resolve_tool_profile():
    """Read GARMIN_TOOL_PROFILE; None means every tool. Unknown names raise."""
    return resolve_tool_profile(os.getenv("GARMIN_TOOL_PROFILE"))


_VALID_TRANSPORTS = ("stdio", "streamable-http", "sse")

# Default per-call timeout (seconds). Garmin's API occasionally stalls a single
# request indefinitely; without a bound the blocking client call hangs until the
# MCP client's own timeout (~4 min) fires, reporting the whole server as
# unresponsive (see issue #248). 90s sits comfortably above a normal slow call
# yet well below that ceiling. Override with GARMIN_MCP_CALL_TIMEOUT; set 0 to
# disable the bound entirely.
_DEFAULT_CALL_TIMEOUT = 90.0


def _resolve_call_timeout() -> float:
    """Read GARMIN_MCP_CALL_TIMEOUT; fall back to the default on bad/absent input.

    A value <= 0 disables the timeout (returns 0.0).
    """
    raw = os.getenv("GARMIN_MCP_CALL_TIMEOUT")
    if raw is None or not raw.strip():
        return _DEFAULT_CALL_TIMEOUT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        print(
            f"Invalid GARMIN_MCP_CALL_TIMEOUT {raw!r}; using default "
            f"{_DEFAULT_CALL_TIMEOUT}s.",
            file=sys.stderr,
        )
        return _DEFAULT_CALL_TIMEOUT
    return value if value > 0 else 0.0


class _GarminProxy:
    """Wraps the Garmin client to bound call duration and clarify runtime errors.

    Two jobs:

    1. Timeout: each client call runs on a daemon worker thread and is abandoned
       if it does not return within the configured timeout (issue #248 — an
       occasional Garmin request stalls forever and the blocking call would
       otherwise hang the whole server until the MCP client gives up minutes
       later). A stalled call raises a clear, retry-able error instead; the
       abandoned daemon thread dies with the process and never blocks shutdown.
       Such stalls are rare and transient, so a fresh thread per call is cheap
       relative to the network round-trip it guards.

    2. Error translation: token expiry or rate-limiting during a tool call would
       otherwise surface a raw library traceback. Known Garmin exceptions are
       re-raised with an actionable hint appended to the original message.
    """

    # (prefix, hint): the original exception text is inserted between them so
    # the real cause is never hidden behind the generic hint.
    _MESSAGES = {
        GarminConnectAuthenticationError: (
            "Garmin authentication failed",
            "Re-run 'garmin-mcp-auth' to refresh your tokens and restart the server.",
        ),
        GarminConnectTooManyRequestsError: (
            "Garmin rate limit hit",
            "Wait a few minutes before retrying.",
        ),
        GarminConnectConnectionError: (
            "Garmin Connect request failed",
            "Garmin Connect may be unreachable; check your network connection or try again later.",
        ),
    }

    def __init__(self, client, timeout=None):
        self._client = client
        self._timeout = _resolve_call_timeout() if timeout is None else timeout

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if name == "client":
            # Direct HTTP writes must share runtime hints and the call bound.
            return _GarminProxy(attr, timeout=self._timeout)
        if not callable(attr):
            return attr

        def _invoke(*args, **kwargs):
            try:
                return attr(*args, **kwargs)
            except tuple(self._MESSAGES) as exc:
                for exc_type, messages in self._MESSAGES.items():
                    if isinstance(exc, exc_type):
                        prefix, hint = messages
                        break
                # garminconnect 0.3.2 wraps every API HTTP failure as a
                # ConnectionError, with status only in its message.
                status, _ = _upstream(exc)
                if status == 401:
                    prefix, hint = self._MESSAGES[GarminConnectAuthenticationError]
                elif status == 429:
                    prefix, hint = self._MESSAGES[GarminConnectTooManyRequestsError]
                elif status is not None and 400 <= status < 500:
                    prefix = f"Garmin rejected the request (HTTP {status})"
                    hint = "Check the request arguments and account permissions."
                elif status is not None and status >= 500:
                    prefix = "Garmin Connect server error"
                    hint = "Garmin returned a server error; try again later."
                details = str(exc).strip().rstrip(".") or "unknown error"
                full_msg = f"{prefix}: {details}. {hint}"
                raise type(exc)(full_msg) from exc

        def _call(*args, **kwargs):
            if not self._timeout:
                return _invoke(*args, **kwargs)

            # Run on a daemon thread and join with a timeout. The worker's
            # return value or exception is captured and replayed in the caller
            # so translated Garmin errors propagate unchanged.
            outcome = {}

            def _worker():
                try:
                    outcome["value"] = _invoke(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001 - replayed below
                    outcome["error"] = exc

            worker = threading.Thread(
                target=_worker, name=f"garmin-call:{name}", daemon=True
            )
            worker.start()
            worker.join(self._timeout)
            if worker.is_alive():
                raise TimeoutError(
                    f"Garmin request '{name}' did not return within "
                    f"{self._timeout:g}s and was abandoned. This is usually a "
                    f"transient stall on Garmin's side — please try again. "
                    f"(Adjust with GARMIN_MCP_CALL_TIMEOUT, or set it to 0 to "
                    f"disable the limit.)"
                )
            if "error" in outcome:
                raise outcome["error"]
            return outcome.get("value")

        return _call


class _ThreadFilteredStream:
    """Wraps a stream so only ``owner_thread``'s writes reach it.

    Installed as ``sys.stdout`` before the background Garmin login thread
    starts (issue #255): the login call may still emit stray progress
    output, and since ``sys.stdout`` is a single process-wide object, an
    unfiltered write from that thread would land in the middle of the
    JSON-RPC messages the main thread writes once ``app.run()`` starts,
    corrupting the stdio framing. Writes from any other thread are silently
    discarded, matching the previous (single-threaded) behavior of
    swallowing that output entirely.
    """

    def __init__(self, real_stream, owner_thread):
        self._real_stream = real_stream
        self._owner_thread = owner_thread

    def write(self, data):
        if threading.current_thread() is self._owner_thread:
            return self._real_stream.write(data)
        return len(data)

    def flush(self):
        self._real_stream.flush()

    def __getattr__(self, name):
        return getattr(self._real_stream, name)


class _ThreadSuppressibleStream:
    """Wraps a stream so individual threads can opt out of writing to it.

    Used for ``sys.stderr`` while the background login thread validates
    tokens. Swapping ``sys.stderr`` itself for a ``StringIO`` there would be
    process-wide, so everything the main thread wrote during login -- the
    "Serving MCP over ..." line, uvicorn's startup logs, and any logging
    handler that captured ``sys.stderr`` in that window, for the life of the
    process -- would vanish too. Only threads inside ``suppressed()`` are
    silenced; every other thread's writes pass through.
    """

    def __init__(self, real_stream):
        self._real_stream = real_stream
        self._suppressed = set()

    def write(self, data):
        if threading.get_ident() in self._suppressed:
            return len(data)
        return self._real_stream.write(data)

    def flush(self):
        self._real_stream.flush()

    @contextlib.contextmanager
    def suppressed(self):
        ident = threading.get_ident()
        self._suppressed.add(ident)
        try:
            yield
        finally:
            self._suppressed.discard(ident)

    def __getattr__(self, name):
        return getattr(self._real_stream, name)


def _suppress_stderr_in_this_thread():
    """Silence ``sys.stderr`` for the calling thread only (see above)."""
    if not isinstance(sys.stderr, _ThreadSuppressibleStream):
        sys.stderr = _ThreadSuppressibleStream(sys.stderr)
    return sys.stderr.suppressed()


class _PendingGarminClient:
    """Stands in for the real Garmin client until a background login finishes.

    Passed as the ``client`` argument to ``_GarminProxy``, so ``_GarminProxy``
    itself needs no changes: every attribute it looks up on ``self._client``
    comes through here first. Before login finishes, that lookup blocks (up
    to ``timeout`` seconds, ``0``/``None`` disables the bound) instead of
    returning immediately -- this is what lets ``main()`` call ``app.run()``
    right away instead of waiting on Garmin login before the MCP handshake
    can be answered (issue #255).
    """

    def __init__(self, timeout):
        self._timeout = timeout
        self._ready = threading.Event()
        self._client = None
        self._login_error = None

    def start(self, login_fn):
        """Run ``login_fn`` on a background daemon thread; store its outcome."""

        def _worker():
            try:
                client = login_fn()
            except BaseException as exc:  # noqa: BLE001 - stored, not raised here
                self._login_error = exc
            else:
                if client is None:
                    self._login_error = RuntimeError(
                        "Garmin login failed. Run 'garmin-mcp-auth' to "
                        "authenticate, then restart the server."
                    )
                    print(
                        "Garmin Connect client failed to initialize (see "
                        "errors above). Tool calls will fail until this is "
                        "fixed; run 'garmin-mcp-auth' and restart the server.",
                        file=sys.stderr,
                    )
                else:
                    self._client = client
                    print(
                        "Garmin Connect client initialized successfully.",
                        file=sys.stderr,
                    )
            finally:
                self._ready.set()

        threading.Thread(target=_worker, name="garmin-login", daemon=True).start()
        return self

    def login_state(self):
        """Non-blocking login status: ``"pending"``, ``"ready"`` or ``"failed"``.

        A regular method, so it is found before ``__getattr__`` and never
        waits on the background login.
        """
        if not self._ready.is_set():
            return "pending"
        return "failed" if self._login_error is not None else "ready"

    def __getattr__(self, name):
        if not self._ready.wait(self._timeout if self._timeout else None):
            raise RuntimeError(
                f"Garmin login did not finish within {self._timeout:g}s. "
                "Run 'garmin-mcp-auth' to verify your credentials, then "
                "restart the server."
            )
        if self._login_error is not None:
            # Raise a copy, not the stored instance: re-raising it would append
            # this call's frames to its __traceback__ on every tool call, so it
            # (and the frame locals it pins) would grow without bound. The copy
            # keeps the type, message and attributes callers inspect.
            err = self._login_error
            try:
                fresh = copy.copy(err)
            except Exception:
                fresh = RuntimeError(f"Garmin login failed: {err}")
            raise fresh from err
        return getattr(self._client, name)


def _readiness(pending_client) -> tuple[int, dict]:
    """(HTTP status, JSON body) for ``GET /readyz``.

    ``/healthz`` answers as soon as the HTTP server is up, but since login
    moved to a background thread (#255) that is before Garmin login has
    resolved -- so an orchestrator using it as a readiness probe routes
    traffic to a server whose every tool call will still fail. ``/readyz``
    reports 200 only once login has succeeded, 503 while it is pending or
    after it failed (``status`` says which).
    """
    state = pending_client.login_state()
    return (200 if state == "ready" else 503), {"status": state}


def _parse_transport_config() -> tuple[str, str, int]:
    """Read and validate HTTP transport env vars. Raises ValueError on bad input."""
    transport = os.getenv("GARMIN_MCP_TRANSPORT", "stdio").strip().lower()
    if transport not in _VALID_TRANSPORTS:
        raise ValueError(
            f"Invalid GARMIN_MCP_TRANSPORT {transport!r}; "
            f"expected one of {', '.join(_VALID_TRANSPORTS)}"
        )
    # Bind to loopback by default: the HTTP transport performs no authentication,
    # so a 0.0.0.0 default would expose full read/write access to the user's
    # Garmin account to the whole network. Opt in explicitly with GARMIN_MCP_HOST.
    http_host = os.getenv("GARMIN_MCP_HOST", "127.0.0.1")
    http_port = int(os.getenv("GARMIN_MCP_PORT", "8000"))
    return transport, http_host, http_port


_TRUE_VALUES = ("1", "true", "yes", "on")
_FALSE_VALUES = ("", "0", "false", "no", "off")


def _resolve_stateless_http() -> bool:
    """Read GARMIN_MCP_STATELESS_HTTP. Raises ValueError on unrecognized input.

    Stateless mode serves each Streamable HTTP request on a fresh transport with
    no Mcp-Session-Id, so there is no server-side session for the SDK to reap
    after its idle timeout (30 min in mcp>=1.30). Without it, the first call after
    an idle gap gets 404 "Session not found" and some clients (e.g. claude.ai
    connectors) surface that as a failed tool call instead of replaying it. No
    tool here relies on session state (progress, sampling, elicitation, server
    push), so nothing is lost. Only affects the streamable-http transport.
    """
    raw = os.getenv("GARMIN_MCP_STATELESS_HTTP", "").strip().lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ValueError(
        f"Invalid GARMIN_MCP_STATELESS_HTTP {raw!r}; expected true or false"
    )


def _off_event_loop(fn):
    """Run a tool body on a worker thread so it cannot block the event loop.

    Every tool is ``async def`` but awaits nothing: each calls the synchronous
    garminconnect client through _GarminProxy, which joins a worker thread for
    up to GARMIN_MCP_CALL_TIMEOUT. FastMCP awaits the tool coroutine on its
    own event loop, so that join stalled every other request, including the
    MCP ping, for the length of the Garmin call (#338). The coroutine is
    driven to completion on a worker thread instead. The proxy's timeout is
    unchanged, and tools keep their signature and docstring for the schema.
    """
    if not inspect.iscoroutinefunction(fn):
        return fn

    @functools.wraps(fn)
    async def run_off_loop(*args, **kwargs):
        return await anyio.to_thread.run_sync(
            lambda: asyncio.run(fn(*args, **kwargs))
        )

    return run_off_loop


class _ToolFilter:
    """Wraps a FastMCP app to conditionally register tools by function name.

    Modules register via ``@app.tool()``; we intercept that decorator and skip
    registration for any tool not permitted by the env-var filter. All other
    attribute access (``run``, ``resource``, ...) passes through to the app.
    """

    def __init__(self, app, enabled, disabled, profile=None):
        self._app = app
        self._enabled = enabled
        self._disabled = disabled
        self._profile = profile  # None = every tool; else the profile's names
        self._seen = set()  # tool names encountered, for typo detection
        self._unannotated = set()

    def _allowed(self, name):
        name = name.lower()
        if self._profile is not None:
            # Profile first; the allowlist adds to it, the denylist removes.
            return (
                name in self._profile or name in self._enabled
            ) and name not in self._disabled
        if self._enabled:
            return name in self._enabled
        return name not in self._disabled

    def tool(self, *args, **kwargs):
        # Most tools return json.dumps(...) as str. FastMCP's default
        # structured_output auto-detection then wraps that string again as
        # structuredContent {"result": "<escaped json>"}, doubling payload
        # size for clients that forward both blocks (issue #331). Opt out
        # globally; callers can still pass structured_output=True explicitly.
        kwargs.setdefault("structured_output", False)
        # Prefer the explicit registered name if given (@app.tool(name="x")),
        # so the env-var filter matches what the user actually configures.
        explicit = kwargs.get("name") or (
            args[0] if args and isinstance(args[0], str) else None
        )

        def wrapper(fn):
            name = explicit or getattr(fn, "__name__", "")
            self._seen.add(name.lower())
            if self._allowed(name):
                annotations = annotations_for(name)
                if annotations is None:
                    self._unannotated.add(name)
                else:
                    kwargs.setdefault("annotations", annotations)
                decorator = self._app.tool(*args, **kwargs)
                decorator(_off_event_loop(fn))
                return fn
            return fn  # skip registration; tool never reaches the LLM

        return wrapper

    def unknown_filter_names(self):
        """Configured names that never matched a real tool (likely typos)."""
        if self._profile is not None:
            configured = self._enabled | self._disabled
        else:
            configured = self._enabled or self._disabled
        return sorted(configured - self._seen)

    def unannotated_tool_names(self):
        """Registered tools missing an explicit effect classification."""
        return sorted(self._unannotated)

    def __getattr__(self, item):
        return getattr(self._app, item)
# ---------------------------------------------------------------------------


def init_api(email, password, tokenstore=None, tokenstore_base64=None, is_cn=None):
    """Initialize Garmin API with your credentials.

    tokenstore, tokenstore_base64 and is_cn default to the environment
    (GARMINTOKENS, GARMINTOKENS_BASE64, GARMIN_IS_CN), read when this runs.
    """
    if tokenstore is None:
        tokenstore = token_utils.get_token_path()
    if tokenstore_base64 is None:
        tokenstore_base64 = token_utils.get_token_base64_path()
    if is_cn is None:
        is_cn = _resolve_is_cn()

    # Claude Desktop may leave blank optional user_config values as literal
    # placeholders. Do not mistake those strings for credentials and trigger a
    # rate-limited Garmin login from a non-interactive MCP process.
    email = _normalize_optional_user_config(email, "garmin_email")
    password = _normalize_optional_user_config(password, "garmin_password")

    try:
        # Using Oauth1 and OAuth2 token files from directory
        print(
            f"Trying to login to Garmin Connect using token data from directory '{tokenstore}'...\n",
            file=sys.stderr,
        )

        # Using Oauth1 and Oauth2 tokens from base64 encoded string
        # print(
        #     f"Trying to login to Garmin Connect using token data from file '{tokenstore_base64}'...\n"
        # )
        # dir_path = os.path.expanduser(tokenstore_base64)
        # with open(dir_path, "r") as token_file:
        #     tokenstore = token_file.read()

        # Suppress stderr during token validation to hide noisy library
        # warnings. stdout is deliberately NOT swapped here: by the time
        # init_api() runs, sys.stdout is a _ThreadFilteredStream installed
        # in main() before this call's background thread was started, which
        # already discards any stray write from this thread on its own. A
        # second swap here would instead risk swallowing real MCP protocol
        # output written concurrently by the server's own thread (#255).
        # stderr is silenced for this thread only, for the same reason.
        with _suppress_stderr_in_this_thread():
            garmin = Garmin(is_cn=is_cn)
            garmin.login(tokenstore)

    except (FileNotFoundError, GarminConnectConnectionError, GarminConnectTooManyRequestsError, GarminConnectAuthenticationError) as token_error:
        # Tokens are missing, unreadable, rejected, or Garmin was unreachable.
        # Say which: the library raises the same "Username and password are
        # required" error for a missing directory, a corrupt file and a path
        # garmin-mcp-auth never wrote to, and the old "OAuth tokens not found"
        # message hid all of that (#335).
        token_problem = token_utils.describe_token_failure(tokenstore, token_error)

        # The server never prompts for credentials; a TTY does not supply them.
        if not email or not password:
            print(
                "ERROR: Garmin login from saved tokens failed and no credentials were provided.\n"
                f"  {token_problem}\n"
                "To fix it:\n"
                "  1. Run: garmin-mcp-auth  (add --force-reauth if tokens exist but were rejected)\n"
                "  2. Start the MCP server with the same GARMINTOKENS (or none) that\n"
                "     garmin-mcp-auth used; `garmin-mcp-auth --diagnose` prints the resolved paths\n"
                "  3. Restart your MCP client\n",
                file=sys.stderr,
            )
            return None

        print(
            f"{token_problem}\n"
            "Logging in with your Garmin Connect credentials to generate new tokens; "
            f"they will be stored in '{tokenstore}' for future use.\n",
            file=sys.stderr,
        )
        try:
            garmin = Garmin(
                email=email, password=password, is_cn=is_cn, prompt_mfa=get_mfa, return_on_mfa=True
            )
            # sys.stdout is a _ThreadFilteredStream (installed in main());
            # any stray progress output from this call is already discarded
            # without a local swap here (see the token-load path above).
            # Exchange the credentials on the client directly. Garmin.login()
            # with no tokenstore falls back to $GARMINTOKENS and would reload
            # the same tokens that just failed instead of using the password.
            result1, result2 = garmin.client.login(email, password, return_on_mfa=True)
            if result1 == "needs_mfa":
                mfa_code = get_mfa()
                garmin.resume_login(result2, mfa_code)
            # Save Oauth1 and Oauth2 token files to directory for next login
            garmin.client.dump(tokenstore)
            # Restrict the freshly written tokens to owner-only. These are
            # ~6-month bearer credentials; the default umask would otherwise
            # leave them world-readable on multi-user hosts.
            token_utils.secure_token_dir(tokenstore)
            print(
                f"Oauth tokens stored in '{tokenstore}' directory for future use. (first method)\n",
                file=sys.stderr,
            )
            # The directory above is the login source; base64 is only an
            # optional export and must not prevent loading the saved tokens.
            try:
                token_json_path = os.path.join(tokenstore, "garmin_tokens.json")
                with open(token_json_path, "r") as f:
                    token_data = f.read()
                token_base64 = base64.b64encode(token_data.encode()).decode()
                with open(tokenstore_base64, "w") as token_file:
                    # Secure the optional file before writing bearer tokens.
                    os.chmod(tokenstore_base64, 0o600)
                    token_file.write(token_base64)
            except OSError as err:
                print(
                    f"WARNING: OAuth tokens were saved to '{tokenstore}', but the optional "
                    f"base64 export to '{tokenstore_base64}' failed: {err}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"Oauth tokens encoded as base64 string and saved to '{tokenstore_base64}' file for future use. (second method)\n",
                    file=sys.stderr,
                )
            # The early-return login above does not load the profile or unit
            # settings (display_name stays None, so tools that build URLs from
            # it fail). Log in again from the saved tokens to load them, which
            # also points token refreshes at the token directory.
            try:
                garmin = Garmin(is_cn=is_cn)
                garmin.login(tokenstore)
            except Exception as err:
                print(
                    f"ERROR: OAuth tokens were saved to '{tokenstore}', but loading the "
                    f"profile/settings from the saved tokens failed: {err}\n"
                    "The token files were retained; the server has no initialized client.",
                    file=sys.stderr,
                )
                return None
        except (
            FileNotFoundError,
            GarminConnectConnectionError,
            GarminConnectTooManyRequestsError,
            GarminConnectAuthenticationError,
            requests.exceptions.HTTPError,
        ) as err:
            error_msg = str(err)

            # Provide clean, actionable error messages
            print("\nAuthentication failed.", file=sys.stderr)

            if isinstance(err, GarminConnectAuthenticationError):
                if "MFA" in error_msg or "code" in error_msg.lower():
                    print("MFA code may be incorrect or expired.", file=sys.stderr)
                else:
                    print("Invalid email or password.", file=sys.stderr)
            elif isinstance(err, GarminConnectTooManyRequestsError):
                print(
                    "Too many requests. Please wait and try again.", file=sys.stderr
                )
            elif isinstance(err, GarminConnectConnectionError):
                # Credential rejection has its own library exception type.
                # Keep the failed strategy/status for HTML or transport failures.
                print(f"Garmin login request failed: {error_msg}", file=sys.stderr)
            elif isinstance(err, requests.exceptions.HTTPError):
                print("Network error. Please check your connection.", file=sys.stderr)
            else:
                print(f"Error: {error_msg.split(':')[0]}", file=sys.stderr)

            print(
                f"\nTip: Run 'garmin-mcp-auth' to authenticate interactively.",
                file=sys.stderr,
            )
            return None

    return garmin


def main():
    """Initialize the MCP server and register all tools"""

    # On Windows, stdout runs in text mode and translates \n to \r\n, which
    # breaks the MCP stdio framing that Claude Desktop and other clients expect.
    # Force binary-transparent newlines so JSON messages arrive intact.
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, newline="\n")

    # Garmin login (init_api) runs on a background thread below so it can't
    # block the MCP handshake (issue #255). That thread may still emit
    # stray writes to stdout; route sys.stdout so only this thread's writes
    # reach the real stream, protecting the JSON-RPC framing this thread
    # writes once app.run() starts.
    sys.stdout = _ThreadFilteredStream(sys.stdout, threading.current_thread())

    # --- Transport configuration --------------------------------------------
    # By default the server speaks stdio (Claude Desktop, MCP Inspector, etc.).
    # Set GARMIN_MCP_TRANSPORT=streamable-http (or sse) to serve over HTTP.
    #   GARMIN_MCP_TRANSPORT - stdio (default) | streamable-http | sse
    #   GARMIN_MCP_HOST      - bind address for HTTP transports (default 127.0.0.1)
    #   GARMIN_MCP_PORT      - bind port for HTTP transports (default 8000)
    #   GARMIN_MCP_STATELESS_HTTP - true to run streamable-http without sessions
    try:
        email, password = load_credentials()
        profile_tools = _resolve_tool_profile()
        enabled_tools, disabled_tools = _resolve_tool_filters()
        transport, http_host, http_port = _parse_transport_config()
        stateless_http = _resolve_stateless_http()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    # Start Garmin login in the background so it never blocks the MCP
    # handshake (issue #255). Tool calls block on it individually instead,
    # through _GarminProxy -> _PendingGarminClient, once they're actually
    # invoked. 90s comfortably covers a normal slow login while still
    # failing well before a client's own initialize timeout would matter
    # again on a later call.
    pending_client = _PendingGarminClient(timeout=90.0)
    pending_client.start(lambda: init_api(email, password))
    garmin_client = _GarminProxy(pending_client)

    # Configure all modules with the Garmin client
    activity_management.configure(garmin_client)
    health_wellness.configure(garmin_client)
    user_profile.configure(garmin_client)
    devices.configure(garmin_client)
    gear_management.configure(garmin_client)
    weight_management.configure(garmin_client)
    challenges.configure(garmin_client)
    training.configure(garmin_client)
    workouts.configure(garmin_client)
    data_management.configure(garmin_client)
    womens_health.configure(garmin_client)
    nutrition.configure(garmin_client)
    workout_builders.configure(garmin_client)
    courses.configure(garmin_client)
    activity_analysis.configure(garmin_client)
    calendar_events.configure(garmin_client)

    # Create the MCP app, wrapped so the env-var filter can drop tools.
    # host/port only matter for the HTTP transports; stdio ignores them.
    fastmcp = FastMCP(
        "Garmin Connect v1.0",
        host=http_host,
        port=http_port,
        stateless_http=stateless_http,
    )
    app = _ToolFilter(fastmcp, enabled_tools, disabled_tools, profile_tools)
    if profile_tools is not None:
        profile_name = os.getenv("GARMIN_TOOL_PROFILE", "").strip().lower()
        print(
            f"Tool profile: {profile_name} ({len(profile_tools)} tool(s))"
            + (f", plus {len(enabled_tools)} from the allowlist" if enabled_tools else "")
            + (f", minus the {len(disabled_tools)} in the denylist" if disabled_tools else "")
            + ".",
            file=sys.stderr,
        )
    elif enabled_tools:
        print(f"Tool filter: allowlist of {len(enabled_tools)} tool(s).", file=sys.stderr)
    elif disabled_tools:
        print(f"Tool filter: denylist of {len(disabled_tools)} tool(s).", file=sys.stderr)

    # Register tools from all modules
    app = exercise_catalog.register_tools(app)
    app = activity_management.register_tools(app)
    app = health_wellness.register_tools(app)
    app = user_profile.register_tools(app)
    app = devices.register_tools(app)
    app = gear_management.register_tools(app)
    app = weight_management.register_tools(app)
    app = challenges.register_tools(app)
    app = training.register_tools(app)
    app = workouts.register_tools(app)
    app = data_management.register_tools(app)
    app = womens_health.register_tools(app)
    app = nutrition.register_tools(app)
    app = workout_builders.register_tools(app)
    app = courses.register_tools(app)
    app = activity_analysis.register_tools(app)
    app = calendar_events.register_tools(app)

    # Register resources (workout templates)
    app = workout_templates.register_resources(app)

    # Warn about filter entries that matched no tool (most likely typos)
    unknown = app.unknown_filter_names()
    if unknown:
        print(
            f"Tool filter: warning — name(s) not found and ignored: {', '.join(unknown)}",
            file=sys.stderr,
        )

    unannotated = app.unannotated_tool_names()
    if unannotated:
        print(
            "Tool annotations: warning — unclassified tool(s): "
            + ", ".join(unannotated),
            file=sys.stderr,
        )

    # When serving over HTTP, expose liveness/readiness endpoints for k8s probes.
    # The MCP endpoint itself requires a handshake and isn't probe-friendly.
    if transport != "stdio":
        from starlette.requests import Request
        from starlette.responses import JSONResponse, PlainTextResponse

        # Liveness: the process is up and serving HTTP.
        @fastmcp.custom_route("/healthz", methods=["GET"])
        async def healthz(_request: "Request") -> "PlainTextResponse":
            return PlainTextResponse("ok")

        # Readiness: Garmin login has succeeded, tool calls can work.
        @fastmcp.custom_route("/readyz", methods=["GET"])
        async def readyz(_request: "Request") -> "JSONResponse":
            status_code, body = _readiness(pending_client)
            return JSONResponse(body, status_code=status_code)

        # Optional shared secret (#339). Without it the endpoint is open, which
        # is fine on loopback and dangerous anywhere else.
        from garmin_mcp import http_auth

        http_token = http_auth.resolve_http_token()
        if http_token:
            http_auth.install_http_bearer_auth(fastmcp, http_token)
            print(
                "HTTP requests must send 'Authorization: Bearer <GARMIN_MCP_HTTP_TOKEN>'; "
                "/healthz and /readyz stay open.",
                file=sys.stderr,
            )
        elif not http_auth.is_loopback(http_host):
            print(http_auth.exposure_warning(http_host, http_port), file=sys.stderr)

        mode = " (stateless)" if stateless_http and transport == "streamable-http" else ""
        print(
            f"Serving MCP over {transport}{mode} on {http_host}:{http_port}",
            file=sys.stderr,
        )

    # Run the MCP server
    app.run(transport=transport)


if __name__ == "__main__":
    main()
