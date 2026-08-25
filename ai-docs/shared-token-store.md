# Shared token store — as built (branch `fix/auth-hardening`)

Read this before touching anything auth-related. This describes the design
actually delivered on `fix/auth-hardening`: `init_api()` now builds a
`GarminSession` (`garmin_session/session.py`) backed by a `FileTokenStore`
(`garmin_session/stores.py`), and `_GarminProxy` routes every tool call
through it. `architecture.md`'s description of the old one-shot dump/load is
now historical.

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

## What was ported

From `/Users/mr13/workspace/hevva2/src/garmin_session/` (identical copy also
in gss), into `src/garmin_mcp/garmin_session/` — not a byte-for-byte copy,
adapted where this repo's garminconnect version (0.3.2) differs (see
`errors.py` and `ai-docs/RAG.md`):

- **`FileTokenStore`** only (`stores.py`) — flock-based, atomic (`tmp` +
  `os.replace`) read and write. `SqliteTokenStore`/`PostgresTokenStore` were
  not ported: this server runs as a single local MCP subprocess, not a
  multi-host deployment (see "Path / backend compatibility" below).
- **`GarminSession`** (`session.py`) — `_acquire()` re-reads the store before
  use and adopts a peer's rotation; `client()` context manager does
  read-before-use then publish-after-use, and never publishes on an auth
  failure (a dropped client is not republished, so a rejected token can't
  overwrite a peer's good one); `_credential_login()` and `_write_scratch()`
  keep the library's own token dump away from the shared store, same as the
  sibling module.

## How `init_api()` and `_GarminProxy` use it

`init_api()` (`__init__.py`) builds one `FileTokenStore(tokenstore)` and one
`GarminSession` at startup, calls `session.warm()` to fail fast with the same
clear stderr messages the old code had, and returns the `GarminSession`
itself (not a raw client) — or `None` on failure, unchanged contract.

`_GarminProxy.__init__` now takes that session, not a bare client.
**Every** attribute access calls `session.acquire()` first — re-reads the
store, adopts a peer's rotation — before resolving the requested attribute,
so a token rotated by another process is picked up on the very next tool
call rather than leaving a rejected client cached until a restart. On a
successful call it publishes any rotation this process performed; on a
`GarminConnectAuthenticationError` specifically (not a rate limit or generic
connection error — those don't mean the client itself is bad) it invalidates
the session before re-raising the relabeled exception.

**The `.client` bypass is also covered — not deferred.** Several tool modules
(`activity_management`, `courses`, `nutrition`, `workouts`,
`workout_builders`) reach past `Garmin` into the raw `garmin.client`
sub-object for HTTP verbs not exposed at the higher level
(`garmin_client.client.put(...)`, `.post(...)`, `.delete(...)`,
`.connectapi(...)`) — undocumented Garmin Connect endpoints, the same reason
`hevy2garmin-lite`'s `push.py` does the exact same thing
(`client.client.put("connectapi", ...)`, confirmed by reading that file
directly). Their fix was manual: every call site wrapped by hand in
`try/except GarminConnectAuthenticationError: reset_garmin_client()` plus a
`finally: publish_garmin_tokens()` — `GarminSession.acquire()`'s documented
"long-running operation" pattern, where the caller takes on the
publish/invalidate obligation itself.

That doesn't scale to garmin_mcp's ~19 bypass call sites across 5 files the
way it does to hevy2garmin-lite's 2 in one file, so instead `_GarminProxy`
special-cases `name == "client"` and hands back a `_ClientProxy` — a second,
structurally identical proxy wrapping the raw `Client` object with the exact
same `_session_protected_call()` helper `_GarminProxy` uses for Garmin's own
methods. One place closes it for all 19 call sites automatically, rather
than requiring each to remember the manual pattern. Covered by
`tests/unit/test_garmin_proxy.py`'s `test_client_*` cases.

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
