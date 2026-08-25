# Architecture

## Request flow

```
MCP client (Claude Desktop/Code, etc.)
        |  stdio (default) | streamable-http | sse   [GARMIN_MCP_TRANSPORT]
        v
main() [__init__.py]
        |  _parse_transport_config()
        |  init_api(email, password) -> garmin
        v
_GarminProxy(garmin)                                  [__init__.py:116-157]
        |  wraps every attribute access; relabels known exceptions
        v
tool modules (health_wellness.py, training.py, workouts.py, ...)
        |  configure(client) wires the proxy in at import time
        v
garminconnect.Garmin -> Garmin Connect API
```

Each tool module exposes a `configure(client)` function; `main()` calls it once
per module with the same `_GarminProxy`-wrapped client (`__init__.py:402-416`).
There is currently **one client for the process lifetime** — no per-call
re-login, no refresh beyond what `garminconnect` itself does internally.

## Auth model (current — see `shared-token-store.md` for the target)

`init_api()` (`__init__.py:223-365`) tries, in order:

1. **Token login** — `Garmin(is_cn=is_cn).login(tokenstore)` where `tokenstore`
   defaults to `~/.garminconnect` (see `token_utils.get_token_path()`).
2. **Credential login fallback** — on `FileNotFoundError` /
   `GarminConnectConnectionError` / `GarminConnectTooManyRequestsError` /
   `GarminConnectAuthenticationError`, falls back to
   `Garmin(email=..., password=..., prompt_mfa=get_mfa, return_on_mfa=True)`,
   then dumps fresh tokens back to `tokenstore` (both as files and as a
   base64-encoded sidecar).

`get_mfa()` (`__init__.py:43-62`) raises `RuntimeError` when
`is_interactive_terminal()` is false. **The credential-login fallback's
exception handler does not catch `RuntimeError`** — a non-interactive process
(the normal case: an MCP client launches this as a subprocess) with
MFA-enabled credentials crashes instead of failing cleanly. Confirmed still
true on `main` as of this audit — see `RAG.md`.

There is **no re-login and no refresh-failure recovery** once `init_api()`
returns. If the in-memory client's token is rejected mid-session (e.g. a peer
service rotated the shared refresh token — see `shared-token-store.md`),
every subsequent tool call fails until the process is restarted.

## `_GarminProxy` error relabeling

`_MESSAGES` (`__init__.py:125-136`) maps three exception types to
human-readable strings and re-raises the same exception type with the
friendlier message appended. It does **not** retry, does **not** re-login, and
does **not** distinguish a real network failure from an expired/rotated
token — both surface as `GarminConnectConnectionError` (see `RAG.md` for why:
this `garminconnect` version's exception has no `.response` attribute, so the
library's own 401-recovery check never fires).

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
