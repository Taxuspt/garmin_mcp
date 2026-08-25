# garmin_mcp: fork hardening, 3 branches

## Context

`garmin_mcp` (this repo) doesn't handle Garmin Connect token rotation correctly — the same class of bug already fixed in your `garmin-scale-sync` (gss) and `hevy2garmin-lite` projects via the shared `garmin_session` module. Upstream (`Taxuspt/garmin_mcp`) is a large, high-PR-volume repo; you've decided contribution back isn't worth pursuing (per your `openscale` experience). The fork (`dharmendra-gupta/garmin_mcp`, `origin` remote) already exists, tracks `origin/main`, `upstream` points at `Taxuspt/garmin_mcp`.

`findings.md` (bug audit) and `branch_finding.md` (confirms none of the 44 stale upstream branches have anything reusable) already ground this. Revised per your feedback: **3 branches, grouped by type, not 7** — chores together, auth fixes together, the garminconnect version bump on its own. All *new* work follows gss's guardrails (read from `/Users/mr13/workspace/garmin_weight_bridge/GarminScaleSync/CLAUDE.md` + `ai-docs/testing.md`, verified this session):

- **Docker-only.** No local `python`/`pip`/`pytest`/`ruff`. Add a `test` stage to the `Dockerfile` and `test`/`lint` services to `docker-compose.yml`, mirroring gss's exactly (`profiles: ["tools"]`, bind-mounted `src/`, `entrypoint: ["python","-m","pytest","tests/","-n","auto","-v"]` / `["ruff","check","src"]`).
- **Strict TDD.** Write the failing test, run it, watch it fail for the right reason, then implement. For guard tests, prove they can fail by injecting the violation first.
- **Never assume.** Before touching anything garminconnect-version-sensitive, read the *installed* library source in the container (`docker compose run --rm --entrypoint python test -c "import inspect, garminconnect; print(inspect.getsource(...))"`) — gss's own `ai-docs/testing.md` flags that `garminconnect` internals differ meaningfully between the 0.3.2 (this repo, currently), 0.3.8 (hevy2garmin-lite), and 0.3.11 (gss) versions already in your other projects.
- **Never hit real Garmin from tests.** Mock the session/client, same as gss's `_patch_session_client` pattern.

**Retrofitting this discipline onto the existing ~20 test files / old source is explicit backlog — its own branch later, not part of this plan.** New code only, for now.

## Branch 1 — `chore/docker-tooling-and-token-hygiene`

Foundational — branches 2 and 3 both need the Docker test/lint tooling this adds, so it goes first.

- `.gitignore`: add `.garminconnect/` — live OAuth tokens (`garmin_tokens.json`) currently sit untracked but unprotected in the working tree.
- `docker-compose.yml`: commit the bind-mount fix already sitting uncommitted in your working tree (`./.garminconnect/:/root/.garminconnect` instead of the orphaned named volume `garmin-tokens`), and remove the now-dead `volumes: garmin-tokens: driver: local` block.
- `README.md:753-763`: fix the `docker volume inspect/rm garmin_mcp_garmin-tokens` instructions to match the bind-mount.
- `Dockerfile`: add a `test` stage (installs `pytest`/`ruff`; current single-stage image already copies `tests/` in, so this is mostly splitting the existing `RUN uv pip install -e .` step and adding dev deps to a new stage).
- `docker-compose.yml`: add `test`/`lint` services per gss's pattern above.
- `pyproject.toml`: add `[tool.ruff]` (mirror gss: `line-length = 125`, `target-version = "py312"`, `select = ["E","F","W","I","UP","B","SIM"]`) and run it — fix lint only on the files this branch actually touches, not a full-repo sweep (that's backlog).

No feature behavior changes, so TDD here just means: confirm `docker compose run --rm test` and `docker compose run --rm lint` actually work before merging.

## Branch 2 — `fix/auth-hardening`

All the auth/token fixes as one branch, built TDD-first inside the Docker tooling from branch 1:

1. **MFA `RuntimeError` crash.** `get_mfa()` (`__init__.py:43-62`) raises `RuntimeError` when non-interactive; `init_api()`'s credential-fallback exception handler doesn't catch it — confirmed still true on current `main`. Write a test that drives this path non-interactively with MFA required and asserts a clean `None` return (same shape as the other error branches), watch it fail red against current code, then add `RuntimeError` to the handler.
2. **Typed auth-error detection.** `_GarminProxy._MESSAGES` (`__init__.py:116-157`) does `isinstance` matching against `GarminConnectConnectionError`, which has no `.response` attribute in this library version — the library's own 401-recovery check never fires, so real auth failures and real network failures look identical. Port the typed-detection pattern from `garmin_session/errors.py` (`instrument()`/`install()`, monkeypatching `Client._run_request` + `requests.Session.request`) from `/Users/mr13/workspace/hevva2/src/garmin_session/errors.py`. Test first: inject a mocked 401 response, assert it's classified as an auth error, not generic connection error.
3. **Shared token store (the core ask).** Port `garmin_session` from `hevy2garmin-lite`/gss into `src/garmin_mcp/garmin_session/` — `FileTokenStore` only (flock-based; skip Sqlite/Postgres, no multi-host case here), `_acquire()`/`client()`/`_credential_login()`/`_write_scratch()` from `session.py`. Rewire `init_api()` (`__init__.py:223-365`) to go through `garmin_session.client()` instead of the current one-shot dump/load, so a token rotated by another process (or mid-session by the library) is picked up instead of going stale. Depends on (2) — invalidate-on-auth-failure needs the typed error.
   - Note for later, not required now: gss's `CLAUDE.md` says its copy of `garmin_session/` is byte-identical to hevy2garmin-lite's "change it in both, or not at all" — this port will diverge from that pact since garmin_mcp's needs differ; that's fine, just don't confuse this copy with theirs later.

## Branch 3 — `feat/garminconnect-0.3.11`

- Read the actually-installed `garminconnect==0.3.11` source in the test container first (not the changelog from memory) for what changed in `get_training_readiness` (`health_wellness.py:189-205` currently expects a shape that's flagged as breaking) and anything branch 2's `garmin_session` port touches (`Client._run_request`, token dump/load).
- Write failing tests for each identified breaking change, then fix the call site.
- Bump `pyproject.toml`/`uv.lock` pin `0.3.2` → `0.3.11`.
- Run the full suite in Docker, fix whatever else surfaces — don't assume findings.md's original version-diff table was exhaustive.

## Workflow

Branch off `origin/main`: 1, then 2, then 3 (2 and 3 could run in parallel once 1 is merged, but 3 touching call sites that 2 also touches makes it simpler sequentially — your call once branch 1 is up). PR against your own fork for each, not upstream — keeps a clean per-topic diff for cherry-picking later without needing upstream review.

## Verification

- Every branch: `docker compose run --rm test` and `docker compose run --rm lint` green before merge.
- Branch 2's token store: the same manual check already used for `garmin_session` elsewhere — two processes sharing one token store, force a rotation from one, confirm the other picks up the rotated token on next use instead of erroring.
- After branch 3: point your real Claude client at the fork build (`docker compose up -d --build`) and exercise a handful of real tool calls end-to-end, including one that forces a token refresh, against your actual account.
