# Verified facts & gotchas

Findings confirmed by reading source directly this session (not recalled from
training data). Consult before re-deriving anything; re-verify before relying
on a claim once `garminconnect` gets bumped to `0.3.11` (branch
`feat/garminconnect-0.3.11`) — internals move between versions, see `testing.md`.

## This repo's auth bugs — original state (as of `main` @ `3610be6`) and fix status

- **`get_mfa()` raises `RuntimeError` when non-interactive** (`__init__.py`),
  and `init_api()`'s wrapping except clause didn't include `RuntimeError`.
  *Verified: read `__init__.py` directly on `main`, both the raise site and
  the except tuple.* No prior upstream PR touched this specific gap.
  **Fixed** on `fix/auth-hardening`: the except clause around
  `session.warm()` now catches `RuntimeError` and returns `None` cleanly.
- **`_GarminProxy._MESSAGES` couldn't distinguish a 401 from a real network
  failure.** It did `isinstance` matching against `GarminConnectConnectionError`
  — but in `garminconnect==0.3.2`, that exception has no `.response`
  attribute, so the library's own `connectapi()` 401-recovery check
  (`getattr(getattr(e,"response",None),"status_code",None) == 401`) never
  matched, and a rejected/rotated token surfaced identically to a genuine
  outage: *"Garmin Connect is unreachable."* **Fixed**:
  `garmin_session/errors.py` — verified against the actually-installed 0.3.2
  source that `Client._run_request` has no persistent `_api_session` to
  instrument once (unlike the version gss's copy of this module targets); it
  builds a brand-new session per call via `_fresh_api_session()`, so this
  wraps that factory instead. See `shared-token-store.md`.
- **No re-login/rotation-recovery once `init_api()` returned a client.** A
  token rotated by a peer service left the singleton client stuck rejected
  until the process was restarted by hand. **Fixed**: `GarminSession` +
  `_GarminProxy` route every tool call through `session.acquire()` (adopt a
  peer's rotation) / `publish()` (propagate our own) / `invalidate()` (drop a
  rejected client) — including the ~19 tool-call sites across 5 modules that
  bypass `Garmin` for the raw `garmin_client.client.*` HTTP verbs, via a
  second `_ClientProxy` wrapping that sub-object the same way. See
  `shared-token-store.md`.
- **HTTP/SSE transport has no built-in auth.** `_parse_transport_config()`
  says so explicitly in its own comment; mitigated only by defaulting
  `GARMIN_MCP_HOST` to `127.0.0.1`. **Still open** — out of scope for the
  current plan, noted in `architecture.md` for later.
- **`docker-compose.yml` token volume**: committed `main` version mounted a
  *named* volume `garmin-tokens:/root/.garminconnect` (declared and attached
  consistently, not actually broken) but was opaque to inspect/back up.
  **Fixed** on `chore/docker-tooling-and-token-hygiene`: switched to a bind
  mount (`./.garminconnect/:/root/.garminconnect`), README updated to match.
- **`.garminconnect/` was untracked but not gitignored.** Real token data
  (`garmin_tokens.json`) sat unprotected in the working tree. **Fixed** on
  `chore/docker-tooling-and-token-hygiene`.

## New facts verified while implementing `fix/auth-hardening`

Read directly from the installed `garminconnect==0.3.2` source (not assumed
from the sibling `garmin_session` module, which targets 0.3.8/0.3.11 and
differs in places — see `testing.md`'s "never assume"):

- **`Client._run_request` calls `self._fresh_api_session()`**, which builds
  a **brand-new** `requests.Session` on every single call — there is no
  persistent `self._api_session` instance attribute in this version. This is
  why `garmin_session/errors.py` here wraps the session *factory*, not a
  session instance, unlike the sibling module.
- **`Garmin.__init__` defaults `return_on_mfa=False`.** With
  `prompt_mfa=callback` and `return_on_mfa` left at its default, `login()`
  invokes the callback *synchronously during* the call and returns
  `(None, None)` on success — no two-phase `needs_mfa`/`resume_login` dance
  needed (that dance is only required when `return_on_mfa=True` is passed
  explicitly, which the old `init_api()` did and the new `GarminSession`
  based flow does not).
- **`Client.dumps()`** returns `json.dumps({"di_token", "di_refresh_token",
  "di_client_id"})` — matches what `garmin_session/stores.py`'s
  `_is_valid_blob()` checks for (`di_token` present).
- **`Client.dump(path)`/`load(path)` are not atomic** (`dump` is a bare
  `write_text()`) and **`load(path)` sets `self._tokenstore_path = path`**,
  so any later `_refresh_session()`-driven dump lands wherever `login()` was
  last pointed — confirms why `GarminSession` must hand the library a private
  scratch directory, never the shared store, or its own internal rotation
  writes would land outside the store's lock discipline.

## What's already in this repo that isn't broken

- `src/garmin_mcp/token_utils.py` / `auth_cli.py` (added across PR #77, #140,
  #157, #201, #220 upstream) already handle `${HOME}` path expansion,
  `secure_token_dir()` (chmod 0700/0600 — closes the world-readable-token
  CVE), and a `garmin-mcp-auth` CLI entrypoint. Does not touch rotation or
  cross-process locking — that's what `fix/auth-hardening` adds.
- All 44 non-`main` branches present on this fork (copied automatically when
  GitHub forked `Taxuspt/garmin_mcp` — forks carry every branch, not just
  `main`) are stale/already-squash-merged; nothing there to cherry-pick. Full
  audit in `.claude/branch_finding.md`.

## garminconnect fleet versions

Four services share one Garmin account (same rotation hazard described in
`shared-token-store.md`). Do not assume one service's library behavior
matches another's — versions differ.

| Service | garminconnect | client lifetime | live `TOKEN_STORE` | notes |
|---|---|---|---|---|
| garmin-scale-sync | 0.3.11 | `GarminSession` | **`postgres`** | fixed; the fleet's actual source of truth (checked its real `.env`, not `.env.example`) |
| hevy2garmin-lite | 0.3.8 | `GarminSession` | `file` | fixed rotation-safety, but its own `config.py` says this "MUST match" gss and currently doesn't — pre-existing drift, not fixed by this fork |
| fitness-dashboard | 0.3.2 | per call | n/a | `login()` every call; also has a `garmin.garth.dump` bug — not yet fixed |
| **garmin_mcp (this repo)** | **0.3.2 → targeting 0.3.11** | `GarminSession` | **`postgres`** (this deployment's `.env`) | **fixed** on `fix/auth-hardening`, backend deliberately matched to gss (not hevy2garmin-lite) since gss is the source of truth. Verified live: real connection to the shared Neon DB, read gss's actual current token, full `garmin-mcp` startup succeeded against it end to end. Version bump to 0.3.11 still pending (`feat/garminconnect-0.3.11`) |

(Table originally mirrored from gss's own `ai-docs/RAG.md` "Fleet" section;
`TOKEN_STORE` column added after checking each project's actual `.env`
directly rather than trusting `.env.example` defaults — cross-checked
against this repo's `pyproject.toml:13` pin — `garminconnect==0.3.2` —
verified this session.)

## PR/branch landscape on upstream (`Taxuspt/garmin_mcp`)

- 250+ PRs total in upstream history; heavy automated/agent-authored volume
  (`codex/`, `apply/NNN`, `agent/` branch-name prefixes throughout).
- 28 PRs currently open, several overlapping this exact plan (`#214`/`#230`
  garminconnect version bumps, `#229` container token bootstrap, `#121`/`#43`
  remote OAuth2 direct login) — all show `mergeable: CONFLICTING` against
  current `main`, all stale (no activity in weeks/months). Nothing usable to
  pull in; re-implementing is less work than untangling the conflicts.
  Full detail in `.claude/branch_finding.md`.

## Unverified / open

- Whether `garmin_mcp` and `garmin-scale-sync`/`hevy2garmin-lite` run on the
  same host. Matters for `shared-token-store.md`'s file-store path choice —
  don't assume either way without checking the actual deployment.
- Full `garminconnect` 0.3.2→0.3.11 changelog diff — findings.md's version
  table is a starting point, not verified exhaustive. `feat/garminconnect-0.3.11`
  must read the installed 0.3.11 source directly (per `testing.md`), not trust
  this list.
