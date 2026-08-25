# Verified facts & gotchas

Findings confirmed by reading source directly this session (not recalled from
training data). Consult before re-deriving anything; re-verify before relying
on a claim once `garminconnect` gets bumped to `0.3.11` (branch
`feat/garminconnect-0.3.11`) — internals move between versions, see `testing.md`.

## This repo's auth bugs (as of `main` @ `3610be6`)

- **`get_mfa()` raises `RuntimeError` when non-interactive**
  (`__init__.py:43-62`), and `init_api()`'s credential-login fallback except
  clause — `(FileNotFoundError, GarminConnectConnectionError,
  GarminConnectTooManyRequestsError, GarminConnectAuthenticationError,
  requests.exceptions.HTTPError)` — does **not** include `RuntimeError`.
  *Verified: read `__init__.py` directly on current `main`, both the raise
  site and the except tuple.* Confirmed still true after all the merged PR
  history described below — no prior PR touched this specific gap.
- **`_GarminProxy._MESSAGES` (`__init__.py:116-157`) can't distinguish a 401
  from a real network failure.** It does `isinstance` matching against
  `GarminConnectConnectionError`, `GarminConnectAuthenticationError`,
  `GarminConnectTooManyRequestsError` — but in `garminconnect==0.3.2`,
  `GarminConnectConnectionError` has no `.response` attribute, so the
  library's own `connectapi()` 401-recovery check
  (`getattr(getattr(e,"response",None),"status_code",None) == 401`) never
  matches, and a rejected/rotated token surfaces identically to a genuine
  outage: *"Garmin Connect is unreachable."* This is the same class of bug gss
  already fixed with a typed-401 monkeypatch — see `shared-token-store.md`.
- **HTTP/SSE transport has no built-in auth.** `_parse_transport_config()`
  (`__init__.py:160-173`) says so explicitly in its own comment; mitigated
  only by defaulting `GARMIN_MCP_HOST` to `127.0.0.1`. Out of scope for the
  current plan, noted for later.
- **`docker-compose.yml` token volume**: committed `main` version mounts a
  *named* volume `garmin-tokens:/root/.garminconnect` (declared and attached
  consistently, not actually broken) but this is opaque to inspect/back up —
  the working tree already had an uncommitted switch to a bind mount
  (`./.garminconnect/:/root/.garminconnect`) before this branch, just missing
  removal of the now-dead `volumes: garmin-tokens: driver: local` block.
  `README.md:753-763` still documents `docker volume inspect/rm
  garmin_mcp_garmin-tokens`, which won't apply once the bind mount lands.
- **`.garminconnect/` was untracked but not gitignored.** Real token data
  (`garmin_tokens.json`) and an empty `.garmin_tokens.lock` sit there in the
  working tree. Fixed this branch.

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

| Service | garminconnect | client lifetime | notes |
|---|---|---|---|
| garmin-scale-sync | 0.3.11 | `GarminSession` | fixed |
| hevy2garmin-lite | 0.3.8 | `GarminSession` | fixed; shares the identical `garmin_session` module |
| fitness-dashboard | 0.3.2 | per call | `login()` every call; also has a `garmin.garth.dump` bug — not yet fixed |
| **garmin_mcp (this repo)** | **0.3.2 → targeting 0.3.11** | singleton at startup | fix in progress this fork — see `shared-token-store.md` |

(Table mirrored from gss's own `ai-docs/RAG.md` "Fleet" section, cross-checked
against this repo's `pyproject.toml:13` pin — `garminconnect==0.3.2` — verified
this session.)

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
