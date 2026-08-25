# garmin_mcp Auth/Token-Rotation Audit — Findings

*Written 25 Aug 2026. Read-only investigation, no code changes made.*

---

## Context

This repo (`/Users/mr13/workspace/garmin_mcp`) is based on the upstream project [Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp), a Python MCP server exposing Garmin Connect data via the `garminconnect` PyPI library. Suspected the auth/token-rotation handling has real bugs, based on having already fixed a similar class of bug in two other projects (`garmin-scale-sync`, `hevy2garmin-lite`). This document confirms that suspicion with exact code citations, compares against the existing fix, researches the upstream project's health, and recommends a path forward.

---

## 1. Confirmed bugs in this repo

### 1.1 The core bug — auth failures get mislabeled as network errors

This is the exact "silently logged out with a misleading error" pattern suspected, and it's reproducible from the pinned library's own code:

1. `Client._run_request()` (in the pinned `garminconnect==0.3.2`, `client.py:1136-1192`) proactively refreshes the token if it's expiring soon, and reactively refreshes-and-retries once on a `401`.
2. If that refresh itself fails, the failure is **swallowed silently** (`except Exception as err: _LOGGER.debug(...)`, `client.py:1005-1006`), and the retried request still comes back `401`.
3. `_run_request` then raises a bare `GarminConnectConnectionError(f"API Error {resp.status_code} - ...")` — a plain exception with **no `.response` attribute** (`garminconnect/exceptions.py:1-2`).
4. The outer `Garmin.connectapi()` wrapper (the method every MCP tool call goes through) tries to recover the status code to re-classify the error: `status = getattr(getattr(e, "response", None), "status_code", None)` (`garminconnect/__init__.py:332`). Since the exception has no `.response`, this is always `None`, so the `status == 401` check (`__init__.py:339`) never matches.
5. It falls through to `raise GarminConnectConnectionError(f"HTTP error: {e}")` (`__init__.py:352`) — **never** the intended `GarminConnectAuthenticationError`.
6. `garmin_mcp`'s `_GarminProxy` (`src/garmin_mcp/__init__.py:116-157`) then maps this mislabeled error to: *"Garmin Connect is unreachable. Check your network connection or try again later."* — actively wrong guidance for what is really an expired/rotated-token condition requiring re-auth.

**Fix reference:** `_GarminProxy`'s message table is at `__init__.py:125-136`.

### 1.2 Crash-loop risk on non-interactive MFA

- `init_api()`'s credential-login fallback calls `mfa_code = get_mfa()` at `__init__.py:296`, inside a `try:` block (`__init__.py:284-319`).
- The matching `except (...)` at `__init__.py:320-326` lists `FileNotFoundError, GarminConnectConnectionError, GarminConnectTooManyRequestsError, GarminConnectAuthenticationError, requests.exceptions.HTTPError` — **`RuntimeError` is not caught**.
- `get_mfa()` raises `RuntimeError("MFA required but non-interactive environment")` when running non-interactively (`__init__.py:49-56`).
- This uncaught `RuntimeError` propagates out of `init_api()`/`main()` and **crashes the whole process** instead of hitting the graceful "Failed to initialize... Exiting." path (`__init__.py:392-394`).
- Combined with `restart: unless-stopped` (`docker-compose.yml:24`) and `GARMIN_EMAIL`/`GARMIN_PASSWORD` left as permanent env vars, a container whose tokens ever become invalid will **crash-loop while repeatedly hitting Garmin's login endpoint with plaintext credentials** — the exact pattern that makes Garmin more likely to demand MFA in the first place.

### 1.3 Zero application-level re-login/retry logic

`garmin_mcp` calls `.login()` exactly once, from `init_api()` (`__init__.py:223-365`), called once from `main()` (`__init__.py:391`). Confirmed via grep across `src/garmin_mcp/` — no other module ever calls `.login()` again during the server's lifetime. All mid-session token handling is 100% delegated to the pinned library's internal refresh (see 1.1) — there is no application-level fallback if that internal refresh silently fails.

### 1.4 No cross-process token-file locking

Neither `garmin_mcp` nor the pinned `Client.dump()` (`client.py:1057-1063`, a plain `Path.write_text()`) uses any file lock. An empty `.garmin_tokens.lock` file exists at `/Users/mr13/workspace/garmin_mcp/.garminconnect/.garmin_tokens.lock`, but nothing creates or uses it (grep across `src/garmin_mcp/*.py` and the library found no references) — unexplained leftover, not an active mechanism.

**This matters concretely:** if `garmin_mcp`, `garmin-scale-sync`, `hevy2garmin-lite`, and `fitness-dashboard`'s Garmin sync all touch the same account, this is exactly the multi-service token-rotation-lockout bug already found and fixed twice elsewhere (see §2).

### 1.5 No thread-safety around the shared client for concurrent tool calls

`garmin_mcp` builds one global `Garmin`/`_GarminProxy` instance shared across every tool module (wired at `__init__.py:402-416`). The library's `_refresh_session()`/`_refresh_di_token()` (`client.py:933-1006`) mutate shared mutable state (`self.di_token`, `self.di_refresh_token`) with no lock. Since `GARMIN_MCP_TRANSPORT=streamable-http`/`sse` is supported (`__init__.py:160-173, 457-468`), concurrent tool calls are architecturally possible — two coroutines racing to refresh at once could both use the same (rotating) refresh token, one succeeds, the other silently fails per the swallowed-exception path in §1.1. **Flagged as an architectural risk based on the code, not an observed failure.**

### 1.6 Version pin

`pyproject.toml:12` and `uv.lock:357,372-377`: `garminconnect==0.3.2` — an exact pin, not a range. Likely rationale (not just staleness): see §3.

### 1.7 Minor: docs/config mismatch

`docker-compose.yml:22-24,27-28` declares a named volume `garmin-tokens` under the top-level `volumes:` key, but the service's actual `volumes:` list (`docker-compose.yml:19-20`) uses a bind mount (`./.garminconnect/:/root/.garminconnect`) instead — that named volume is never attached. `README.md:753-763` documents `docker volume inspect/rm garmin_mcp_garmin-tokens` commands that operate on a volume the container never actually uses; following that section to "wipe tokens and force re-auth" would silently no-op.

### What's *not* broken

- Startup token reuse works as intended: `init_api()` always tries `garmin.login(tokenstore)` first (`__init__.py:256-258`) and only falls back to plaintext credentials on failure — a valid, persisted `garmin_tokens.json` genuinely prevents a fresh email/password login (and its MFA flow) on every container restart.
- No `TODO`/`FIXME`/known-issue comments related to auth anywhere in the codebase (one unrelated TODO exists at `training.py:146`).
- Other error paths are reasonably clear — `_GarminProxy` turns known exceptions into readable strings, and each tool's broad `except Exception` surfaces that text instead of a raw traceback.
- No test coverage exists for the §1.1 mislabeling scenario (`tests/unit/test_garmin_proxy.py` only tests `_GarminProxy` re-labeling exceptions it's directly handed by a mock — never exercises the real `_run_request`/`connectapi` path where the mislabeling actually originates).

---

## 2. The existing, directly-reusable fix

`garmin-scale-sync` (`/Users/mr13/workspace/garmin_weight_bridge/GarminScaleSync`) and `hevy2garmin-lite` (`/Users/mr13/workspace/hevva2`) share a **byte-for-byte identical** `garmin_session/` module (`diff` across `session.py`, `stores.py`, `errors.py`, `__init__.py` produces zero output). Its own docstring states it's "self-contained by design... so the directory can be copied as-is into the other services that share this Garmin account." Origin: `garmin-scale-sync` commit `238f77e`; adopted into `hevy2garmin-lite` in commit `e05e494`.

**Mechanism (three parts):**

1. **`TokenStore` abstraction** (`stores.py`) — pluggable backends (`FileTokenStore`, `SqliteTokenStore`, `PostgresTokenStore`), each with `load()`/`save()` (atomic write via temp-file + `os.replace`)/`locked()` (cross-process exclusive lock — `flock` for file, `BEGIN IMMEDIATE` for sqlite, `pg_advisory_xact_lock` for postgres — chosen because PgBouncer transaction-mode pooling breaks session-scoped locks).

2. **`GarminSession`** (`session.py`) — not a scheduled-refresh timer, not blind re-login-on-every-failure:
   - **Before each use** (`_acquire`, `session.py:144-176`): re-reads the store inside the lock; if the on-disk blob differs from what this process last synced, drops its cached client and re-logs-in from the *peer's* newer token instead of fighting it.
   - **After each use** (`_publish_if_rotated`, `session.py:178-183`): if this process's own token state changed, writes it back to the shared store immediately so peers see it.
   - **On auth failure** (`client()` context manager, `session.py:118-140`): catches `GarminConnectAuthenticationError`, explicitly does **not** publish the dead token (avoids overwriting a peer's good token), clears the cached client, re-raises — forcing the next call to rebuild from the store.
   - Falls back to fresh credential login (`_credential_login`, `session.py:203-214`) only if the stored tokens themselves are rejected.
   - The shared token path is never given directly to `garminconnect.Garmin.login()` — tokens are copied into a private per-process scratch directory first (`_write_scratch`, `session.py:222-226`) so the library's own internal writes go somewhere harmless; `GarminSession` remains the sole writer to the shared store.

3. **`errors.py` — typed-401 detection**, replacing a fragile regex-on-error-string hack (`_reraise_401_as_auth_error`, referenced in `hevy2garmin-lite`'s old `garmin_client.py` docstring). `errors.install()` monkey-patches `Client._run_request` once (idempotent, lock-guarded) to record the real HTTP status via a wrapped `requests.Session.request`, and re-raises a proper `GarminConnectAuthenticationError` when the recorded status is actually `401` — keying off the real status code, not string-matching an error message. **This directly fixes the §1.1 bug.**

**Citations:** `session.py:144-176, 118-140, 178-183`; `stores.py:62-113`; `errors.py:38-84`; integration examples at `hevva2/src/garmin_client.py:1-20,79-153` and `GarminScaleSync/src/garmin_client.py:41-87,239-264`.

---

## 3. Upstream repo health & `garminconnect` library changelog

### Taxuspt/garmin_mcp

- **1,052 stars, 334 forks, 30+ contributors**, created 2026-03-08, last push 2026-08-04 — actively maintained, weekly merges.
- 41 open issues+PRs (11 issues, 30 PRs). No CONTRIBUTING.md, but has CI/PR-validation workflows (`.github/workflows/{ci,pr-validation,security}.yml`).
- Majority of the last 30 merged PRs are from **external contributors**, not just the maintainer — a well-scoped PR is realistic to get merged.
- **Auth/token/session/MFA is the single most recurring complaint category**, spanning from the project's earliest days through last week:
  - Open: #257 (garmin.cn TLS auth failure, 17 Aug 2026), #255 (login blocks handshake, 13 Aug 2026), #249/#229/#121/#43 (auth-related PRs), #109 (email-MFA detection failure, still open since 15 May 2026).
  - Closed/merged: #63 (garth deprecated, auth broken), #55 (MFA refresh not working), #58 + fix PRs #69/#70/#73 (429 rate limiting / Cloudflare TLS fingerprinting), #79 (SSO auth route), #138 (token file world-readable), #140 (fail loudly on bad saved tokens), #182 (harden transport bind + token permissions), #205 (stdout corrupting MCP stdio during login), #217/#220 (DXT token-path fixes), #9/#12/#31 (early MFA work).

### `garminconnect` PyPI library, v0.3.2 → v0.3.11

| Version | Date | Key auth/token change |
|---|---|---|
| 0.3.2 | 2026-04-11 | *(pinned version)* |
| 0.3.4 | 2026-05-02 | Fixed CN account token refresh routing |
| 0.3.5 | 2026-06-04 | **High-severity CVE fix** (GHSA-wjhr-76vg-2hvc, CWE-732): token file was world-readable (0o644), exposing the refresh token to any local user on shared hosts — fixed to 0o600/0o700. Also: in-chain token validation, self-healing from poisoned cache, working `logout()`, Cloudflare 403/CAPTCHA detection. **Also: `get_training_readiness` return type changed dict→list** — explicitly flagged in the library's own changelog as breaking "downstream tooling, e.g. pydantic-based MCP servers." Almost certainly why this repo is pinned at 0.3.2. |
| 0.3.6 | 2026-06-14 | Fixed email-MFA detection in Widget SSO path — directly matches still-open upstream issue #109 |
| 0.3.7 | 2026-07-26 | Follow-up email-MFA chain fix |
| 0.3.9 | 2026-08-07 | Further MFA/OTP-delivery fix |
| 0.3.10 | 2026-08-11 | Major hardening: reject symlinked tokenstore paths, atomic/locked token writes, `logout()` fully clears state, `login()` clears stale state, resume_login verifies result, fixed corruption from interleaved MFA logins, JWT hardening (rejects `alg:none`, malformed `exp`) |
| 0.3.11 | 2026-08-19 | Fixed TOCTOU/inode-reuse in token-write temp files; fixed symlink-bypass in tokenstore-path validation |

**New API surface:** 132 → 151 public methods (19 new), including `download_health_snapshot`, `get_heart_rate_zones`/`get_power_zones`, `upload_strength_workout` + exercise catalog, `update_workout` (in-place edit), `push_workout_to_device`, `get_calories_daily`/`get_rhr_daily`/`get_sleep_daily`/range-query wrappers, and an optional Pydantic-typed accessor namespace.

**No breaking signature changes** other than the `get_training_readiness` shape change above — everything else is additive or internal hardening. `Taxuspt/garmin_mcp`'s README states it targets 0.3.2 with "~90% coverage."

---

## 4. Recommendation

**Fork, don't rebuild from scratch.** A new repo throws away ~90% working API coverage, all MCP tool wiring, and Docker setup for a problem scoped entirely to the auth layer.

**Two-track plan:**

1. **Personal fork** (own GitHub, becomes the daily-driver — especially relevant once running on the mini PC with Remote Control access):
   - Copy `garmin_session/` in as-is, point it at the same shared token store used by `garmin-scale-sync`/`hevy2garmin-lite`/`fitness-dashboard` so all services cooperate instead of fighting over rotation (fixes §1.4).
   - Catch `RuntimeError` alongside the existing exception list at `__init__.py:320-326` (fixes §1.2).
   - Bump to `garminconnect==0.3.11`, patch the `get_training_readiness` dict→list handling (gets the CVE fix + MFA hardening + 19 new methods).
   - Fix the `docker-compose.yml` volume/README mismatch (§1.7).

2. **Separate, small upstream PRs** for the generically useful fixes only — keep the multi-service shared-token-store out of these, that's your specific architecture:
   - The `RuntimeError` catch (trivial, clear bug).
   - The typed-401 classification fix from `errors.py` (high value — directly fixes the misleading "unreachable" error other users have almost certainly hit given how often auth issues get reported upstream).
   - The docs/compose mismatch.
   - Small, focused PRs merge faster in an actively-gated repo like this than one large "rewrote your auth" PR would.

Standard flow either way: fork on GitHub → clone the fork → branch → commit → push to the fork → open PR against upstream from that branch. Never push to `Taxuspt/garmin_mcp` `main` directly.
