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
| **garmin_mcp (this repo)** | **0.3.11** | `GarminSession` | **`postgres`** (this deployment's `.env`) | **fixed** on `fix/auth-hardening`, backend deliberately matched to gss (not hevy2garmin-lite) since gss is the source of truth. Verified live: real connection to the shared Neon DB, read gss's actual current token, full `garmin-mcp` startup succeeded against it end to end. Version bumped to 0.3.11 on `feat/garminconnect-0.3.11`, matching gss exactly |

(Table originally mirrored from gss's own `ai-docs/RAG.md` "Fleet" section;
`TOKEN_STORE` column added after checking each project's actual `.env`
directly rather than trusting `.env.example` defaults — cross-checked
against this repo's `pyproject.toml:13` pin, since updated to
`garminconnect==0.3.11`.)

## `feat/garminconnect-0.3.11` — what actually changed, verified against installed source

Read directly from the `garminconnect==0.3.11` package installed in the test
image (`docker exec ... python -c "import inspect, garminconnect..."`), not
assumed from the 0.3.2 facts above or from what gss's/hevy2garmin-lite's own
docs say about their (also-0.3.11) copy:

- **`Client._api_session` is a persistent instance attribute again**,
  created once in `Client.__init__` — the `_fresh_api_session()` per-call
  factory that 0.3.2 used (see the 0.3.2 facts above) is gone entirely.
  This is the shape gss's/hevy2garmin-lite's copy of `garmin_session/errors.py`
  always assumed; this repo's copy no longer needs to special-case it.
  **Breaking**: `errors.py`'s `_instrument_session_factory()` referenced
  `Client._fresh_api_session`, which raised `AttributeError` at
  `GarminSession.__init__` time (i.e. every request), 13 of 160 unit tests
  red for this exact reason. **Fixed**: `errors.py` now wraps
  `Client.__init__` itself so every instance's `_api_session` is instrumented
  the moment it's constructed — no call site needs a separate `instrument()`
  call (unlike the sibling module's pattern), preserving
  `GarminSession._new_client()`'s existing "no per-instance call needed"
  assumption without changing `session.py` at all.
- **`Client._run_request` now does its own refresh-and-retry on a 401**
  (acquires `_token_lock`, calls `_refresh_session()`, retries once) before
  falling through to raise `GarminConnectConnectionError("API Error 401")` —
  0.3.2 had no such retry. The typed-error problem this fork's `errors.py`
  exists to fix is unchanged: even after that retry, a real 401 still
  surfaces as the generic connection error, not `GarminConnectAuthenticationError`.
- **`Client._refresh_session()` now auto-dumps to `self._tokenstore_path`**
  after a successful DI-token refresh (wrapped in `contextlib.suppress`) —
  0.3.2 didn't do this. Reinforces (doesn't change) why `GarminSession` must
  keep pointing the library at a private scratch directory rather than the
  shared store: any internal refresh the library performs on its own now
  writes back to wherever `_tokenstore_path` last pointed, not just writes
  this class explicitly triggers.
- **`Client.dump()`/`load()` hardened** (atomic `O_EXCL`+0600 write, symlink
  refusal on read) — behavior-compatible with this repo's usage, no call site
  changes needed. `dumps()`'s shape (`di_token`/`di_refresh_token`/
  `di_client_id`) is unchanged.
- **`Garmin.login()`/`Client.login()`/`Client.put`/`post`/`delete`/
  `connectapi` signatures are unchanged** from what this repo already calls
  them with — verified directly, not assumed compatible. `Garmin.__init__`
  still defaults `return_on_mfa=False`; `resume_login(client_state, mfa_code)`
  unchanged.
- **`garminconnect==0.3.11` requires Python >=3.12** (`uv lock` failed
  outright against this repo's then-`requires-python = ">=3.10"`, confirming
  this rather than reading it from a changelog). **Fixed**: bumped
  `requires-python` to `>=3.12` and trimmed `.github/workflows/ci.yml`'s
  matrix from `['3.10','3.11','3.12','3.13']` to `['3.12','3.13']` — the
  Docker runtime image was already `python:3.12-slim`, so this only changes
  what pip/uvx installs and CI claim to support, not actual server behavior.
  User-approved tradeoff (dropping 3.10/3.11 support to keep version parity
  with gss over staying on an older garminconnect).
- **`get_training_readiness(cdate)` signature and `self.connectapi(url)`
  body are byte-identical** between 0.3.2 and 0.3.11 — the shape
  `health_wellness.py:189-205` curates comes from Garmin's live API response,
  not this library method, so nothing to fix here from the version bump
  itself; not verifiable further without hitting the live account, which
  `testing.md` forbids from tests.

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
- `get_training_readiness`'s actual live-API response shape on the account
  this repo will run against — the curated fields in `health_wellness.py`
  were never verified against a real call (`testing.md` forbids that from
  tests); only the library method's own shape was confirmed unchanged by the
  version bump.
