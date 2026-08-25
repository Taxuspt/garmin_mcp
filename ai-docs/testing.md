# Testing & investigation workflow — applies to new work only

**Existing tests (`tests/unit`, `tests/integration`, `tests/e2e`) and the
current `uv`-based CI (`.github/workflows/ci.yml`) are untouched by this.**
Retrofitting Docker/lint discipline onto that pre-existing suite is its own
backlog item, not part of the fork-hardening branches. This workflow governs
new code written on `chore/docker-tooling-and-token-hygiene`,
`fix/auth-hardening`, and `feat/garminconnect-0.3.11`.

Mirrors `/Users/mr13/workspace/garmin_weight_bridge/GarminScaleSync/ai-docs/testing.md` —
read that if something here is ambiguous, it's the same discipline this repo's
sibling project already runs under.

## Everything runs in Docker

No local `python`, `pip`, `pytest`, or `ruff` for new work.

```bash
docker compose run --rm test      # full suite, parallel (pytest-xdist -n auto)
docker compose run --rm lint      # ruff check src/
```

Both services (`profiles: ["tools"]`, not started by `docker compose up`)
bind-mount `src/` (and `test` also mounts `tests/`), so edits apply without a
rebuild and `ruff --fix` changes persist on the host.

## Reuse one container for anything the compose services don't cover

```bash
docker build --target test -t garmin-mcp:dev .
docker run -d --name garmin_mcp_dev -v "$PWD/src:/app/src" -v "$PWD/tests:/app/tests" \
  garmin-mcp:dev sleep infinity

docker exec garmin_mcp_dev pytest tests/unit/test_garmin_proxy.py -q
docker exec garmin_mcp_dev python -c "import inspect, garminconnect; print(inspect.getsource(garminconnect.Garmin.login))"
```

Tear down with `docker rm -f garmin_mcp_dev` when finished.

## Strict TDD

1. Write the test. Run it. **Watch it fail for the right reason** — a test
   that passes before the fix tests nothing.
2. Implement the smallest change that makes it pass.
3. Re-run the whole suite, not just the new test.

### Prove guard tests can fail

A test that cannot fail is worse than none. When adding one, inject the
violation and confirm red before trusting it — e.g. for the MFA `RuntimeError`
fix, temporarily remove the new `except` clause and confirm the test goes red
for that exact reason, then put it back.

## Never assume — read the installed library

`garminconnect` internals differ meaningfully between versions already in use
across this account's fleet (this repo: `0.3.2` today, target `0.3.11`;
hevy2garmin-lite: `0.3.8`; gss: `0.3.11`). Before writing code against any
`garminconnect` internal (`Client._run_request`, `Client.dump`/`load`,
`_refresh_session`), read the version actually installed in the test image,
not memory of another version:

```bash
docker compose run --rm --entrypoint python test -c "
import inspect, garminconnect.client as c
print(inspect.getsource(c.Client._run_request))"
```

`ai-docs/RAG.md` has facts already verified this way — check there first, but
re-verify before relying on a claim if the pinned version has since moved.

## Do not hit Garmin from tests

Garmin rate-limits SSO **per IP**, and this account is shared with the phone
app and other services. Mock the session/client — never a real credential
login from a test. Never send a real weigh-in, workout, or other write call
to the live account from a test.
