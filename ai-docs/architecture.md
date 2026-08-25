# Architecture

## Request flow

```
MCP client (Claude Desktop/Code, etc.)
        |  stdio (default) | streamable-http | sse   [GARMIN_MCP_TRANSPORT]
        v
main() [__init__.py]
        |  _parse_transport_config()
        |  init_api(email, password) -> GarminSession | None
        v
_GarminProxy(session)                                  [__init__.py]
        |  every attribute access: session.acquire() first, then relabels
        |  known exceptions; publishes/invalidates around the call
        v
tool modules (health_wellness.py, training.py, workouts.py, ...)
        |  configure(client) wires the proxy in at import time
        v
GarminSession -> garminconnect.Garmin -> Garmin Connect API
```

Each tool module exposes a `configure(client)` function; `main()` calls it once
per module with the same `_GarminProxy`-wrapped session. **As of
`fix/auth-hardening`, there is no longer a single client held for the process
lifetime** — see `shared-token-store.md` for the full design: every tool call
re-validates against the shared token store first, so a token rotated by a
peer service is picked up on the next call instead of leaving a rejected
client cached until a restart.

## Auth model

`init_api()` (`__init__.py`) builds a `FileTokenStore(tokenstore)` (default
`~/.garminconnect`, see `token_utils.get_token_path()`) and a `GarminSession`
over it, then calls `session.warm()` once to fail fast at startup with a
clear stderr message — same UX as before, different mechanism underneath.
See `shared-token-store.md` for what `GarminSession` does on every
subsequent call (`_acquire()`, `client()`, credential fallback, scratch dir).

`get_mfa()` (`__init__.py`) raises `RuntimeError` when
`is_interactive_terminal()` is false — expected for a non-interactive process
(the normal case: an MCP client launches this as a subprocess) with no
terminal to prompt for a code. `init_api()`'s wrapper around `session.warm()`
catches `RuntimeError` specifically and returns `None` cleanly, same as every
other login-failure case.

## `_GarminProxy` error relabeling + session lifecycle

`_MESSAGES` maps three exception types to human-readable strings and
re-raises the same exception type with the friendlier message appended —
unchanged from before. What's new: every attribute access first calls
`session.acquire()` (re-reads the store, adopts a peer's rotation); a
successful call publishes any rotation this process performed; a
`GarminConnectAuthenticationError` specifically invalidates the session
before re-raising (a rate limit or generic connection error does not — see
`shared-token-store.md` for why). Typed-401 detection
(`garmin_session/errors.py`) is what makes that distinction reliable in the
first place — this `garminconnect` version's `GarminConnectConnectionError`
otherwise carries no `.response` attribute, so a rejected token and a real
network failure would look identical.

## Transport / auth boundary

`_parse_transport_config()` (`__init__.py:160-173`) supports `stdio` (default),
`streamable-http`, `sse`. HTTP/SSE transports have **no built-in
authentication** — the only mitigation is defaulting the bind address to
`127.0.0.1`. Binding `GARMIN_MCP_HOST` beyond loopback hands out
unauthenticated full read/write access to the configured Garmin account to
anyone who can reach the port. (Out of scope for the current fork-hardening
plan — noted here so it isn't forgotten if this server is ever bound
non-locally, e.g. reached from a phone via a mini-PC.)

## Tool modules

One file per Garmin Connect domain — `health_wellness.py`, `training.py`,
`workouts.py`, `workout_builders.py`, `workout_templates.py`, `nutrition.py`,
`activity_management.py`, `activity_analysis.py`, `challenges.py`, `courses.py`,
`data_management.py`, `devices.py`, `gear_management.py`, `user_profile.py`,
`weight_management.py`, `womens_health.py`. Each registers its own `@app.tool()`
functions and receives the shared `_GarminProxy`-wrapped client via
`configure(client)`. `_ToolFilter` (`__init__.py`) can enable/disable
individual tools by name via env var, independent of which modules are loaded.
