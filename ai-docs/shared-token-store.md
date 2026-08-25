# Shared token store — target design (branch 2, not yet built)

Read this before touching anything auth-related. This document describes what
`fix/auth-hardening` is porting in; until that branch lands, `init_api()`
still does the naive one-shot dump/load described in `architecture.md`.

## Why it's needed

The same Garmin account is used by several services: this one, `hevy2garmin-lite`,
`garmin-scale-sync`, and `fitness-dashboard`. They cannot each hold their own
login — Garmin flags the account for that. So they share one refresh token.

Garmin **rotates the refresh token on every refresh** (`Client._refresh_di_token`
sets `self.di_refresh_token = data.get("refresh_token", ...)`). The old refresh
token dies server-side the moment a new one is issued. Exactly one is valid at
any moment, fleet-wide.

## The failure mode this is fixing

1. This server starts, reads the token file once, holds it in memory for the
   life of the process (`architecture.md` — "one client for the process
   lifetime").
2. A peer service (gss, hevy2garmin-lite) refreshes independently and writes a
   new refresh token to its own copy of the store. Our in-memory token is now
   dead.
3. Our next call attempts a refresh with the dead token. `garminconnect`'s
   `_refresh_session()` swallows that failure silently (`except Exception:
   _LOGGER.debug(...)`) and the caller retries with the same dead token → 401.
4. `_GarminProxy` (`architecture.md`) reports that 401 as **"Garmin Connect is
   unreachable"** — a `GarminConnectConnectionError`, not an auth error — because
   this library version's exception carries no `.response` attribute, so
   nothing downstream can tell a real network failure from a stale token.
5. Every subsequent call fails identically until the process is restarted by
   hand. This is exactly the bug already fixed in gss/hevy2garmin-lite via
   `garmin_session` (see
   `/Users/mr13/workspace/garmin_weight_bridge/GarminScaleSync/ai-docs/shared-token-store.md`
   for the original writeup and incident history — that doc's own words:
   *"This is the failure mode that took the service down daily."*)

## What's being ported (branch `fix/auth-hardening`)

From `/Users/mr13/workspace/hevva2/src/garmin_session/` (identical copy also
in gss), into `src/garmin_mcp/garmin_session/`:

- **`FileTokenStore`** only — flock-based, atomic (`tmp` + `os.replace`) read
  and write. Skipping `SqliteTokenStore`/`PostgresTokenStore` for now: this
  server runs as a single local MCP subprocess, not a multi-host deployment.
- **`_acquire()`** — re-reads the store before use; if the blob differs from
  what we last synced, a peer rotated, so drop our client and rebuild from the
  current token instead of trusting stale in-memory state.
- **`client()`** context manager — read-before-use, then publish-after-use (if
  our own `dumps()` differs from what we last synced, we rotated — write it
  back so peers adopt it), and **never publish on auth failure** (a dropped
  client is not republished, so a rejected token can't overwrite a peer's good
  one).
- **`_credential_login()`** fallback — same idea as `init_api()`'s current
  fallback, reworked to go through the shared store instead of a private
  token dir.
- **`_write_scratch()`** — the library never sees the shared path directly.
  Tokens get materialized into a private scratch directory before
  `Garmin.login()` receives them, so `garminconnect`'s own internal
  `_refresh_session()` dump goes somewhere harmless. `garmin_session` remains
  the only writer to the shared store.

Depends on **typed 401 detection** landing first (same branch, built first):
`_GarminProxy`'s current `isinstance`-based classification can't tell a 401
from a generic connection failure (see above), and `garmin_session`'s
invalidate-on-auth-failure logic needs to know the difference to decide
whether it's safe to keep the client.

## Path / backend compatibility with the rest of the fleet

gss's `.env.example` documents the fleet default: `TOKEN_STORE=file`, path
`$DATA_DIR/.garminconnect/garmin_tokens.json` — the same JSON shape this
server already reads/writes at `~/.garminconnect/garmin_tokens.json`
(`token_utils.get_token_path()`). **If this server and the other services run
on the same host, point this server's store at the same directory the others
use, or it just becomes a fifth independent holder of a token that goes stale
the same way.** If they run on different hosts, `file` doesn't solve
cross-host sharing at all — that needs `sqlite` (same-host only, unreliable
over network filesystems) or `postgres` (works across hosts, requires
`TOKEN_DB_URL`). This fork-hardening plan only builds the `file` backend;
cross-host sharing is out of scope until the actual deployment topology
(same box as gss/hevy2garmin-lite, or not) is confirmed. Don't assume same-host
without checking.
