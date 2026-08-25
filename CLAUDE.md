# garmin_mcp (fork)

MCP server exposing Garmin Connect data/tools to MCP clients (Claude Desktop,
Claude Code, etc.) over stdio, `streamable-http`, or `sse`. Fork of
`Taxuspt/garmin_mcp` — `origin` is this fork, `upstream` is the original.
Not contributing back upstream; forked to fix Garmin auth/token-rotation
handling that this account's other services (`garmin-scale-sync`,
`hevy2garmin-lite`) already had to fix independently.

## Guardrails — new work only

**Existing tests, existing source outside the fork-hardening branches, and the
current `uv`-based CI are exempt from all of this — retrofitting is explicit
backlog, not part of the active plan.** These apply to
`chore/docker-tooling-and-token-hygiene`, `fix/auth-hardening`,
`feat/garminconnect-0.3.11`, and anything built after them the same way.

**1. Never assume.** Verify before asserting — read the installed library
source, not memory of it. `garminconnect` internals differ meaningfully
between the versions already in use across this account's fleet (see
`ai-docs/RAG.md`). Cite `file:line`. If you couldn't verify something, say so
instead of implying you did.

**2. Strict TDD.** Write the failing test first and *watch it fail* for the
right reason, then implement. A guard test that cannot fail is worse than
none — prove a new one by injecting the violation and seeing red first.

**3. Everything runs in Docker.** No local `python`, `pip`, or `pytest` for
new work. The test suite and lint both run in a container.

**4. Reuse one container.** Don't spin up a fresh container per command while
testing or investigating — start one, `docker exec` into it repeatedly,
rebuild only when dependencies change. See `ai-docs/testing.md`.

## Commands

```bash
docker compose run --rm test      # full suite, parallel (pytest-xdist -n auto)
docker compose run --rm lint      # ruff check

# Single file:
docker compose run --rm --entrypoint python test -m pytest tests/unit/test_x.py -q
```

Lint is `ruff` (`pyproject.toml`).

## Layout

```
src/garmin_mcp/__init__.py     init_api(), _GarminProxy, transport config, main()
src/garmin_mcp/token_utils.py  token path resolution, chmod hardening, garmin-mcp-auth support
src/garmin_mcp/auth_cli.py     garmin-mcp-auth CLI entrypoint
src/garmin_mcp/garmin_session/ shared-token session (added by fix/auth-hardening) — see ai-docs/shared-token-store.md
src/garmin_mcp/*.py            one file per Garmin Connect tool domain (health_wellness, training, workouts, ...)
tests/unit/ tests/integration/ tests/e2e/   existing suite — see ai-docs/testing.md for the discipline new tests follow
```

## Critical context

This Garmin account is shared by several services (`garmin-scale-sync`,
`hevy2garmin-lite`, `fitness-dashboard`, this one). Garmin **rotates the
refresh token on every refresh**, so only one is valid at a time — anything
touching auth must read `ai-docs/shared-token-store.md` first, it's the
failure mode this fork exists to fix. `ai-docs/RAG.md` has the specific bugs
already confirmed in this repo, with `file:line` citations.

## Further reading

- `ai-docs/architecture.md` — request flow, auth model, error mapping, transport boundary
- `ai-docs/shared-token-store.md` — the token-rotation bug and the fix being ported in
- `ai-docs/testing.md` — Docker/TDD workflow for new work
- `ai-docs/RAG.md` — verified facts and gotchas; read before re-deriving anything
- `ai-docs/fork-hardening-plan.md` — the approved 3-branch plan
- `.claude/findings.md` — original full auth/version audit that started this
- `.claude/branch_finding.md` — audit of the 44 pre-existing upstream branches (nothing reusable)
