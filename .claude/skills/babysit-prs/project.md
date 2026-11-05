# garmin_mcp: project rules for /babysit-prs

The rules below are copied word for word from the previous SKILL.md.
SKILL.md now holds the process (how to branch, merge and credit); edit this
file to change project policy.

Repository: `Taxuspt/garmin_mcp` (default branch `main`)

Test command:

```bash
uv run pytest tests/unit/ tests/integration/ -x -q --timeout=30
```

## Always skip (do not touch)

- **#43, #121, #124** — Remote OAuth2 multi-user mode. Explicitly deferred by the user.

## Workflow PRs

- PRs touching `.github/workflows/` cannot be merged via the GitHub API (needs `workflow` OAuth scope). Flag them for manual merge by the user instead.

## PRs from `Taxuspt` (first-party, have CI)

If CI passes: review the diff, run tests locally, squash-merge.

If CI fails: read the failure, fix it, push to the same branch.

## Issues

**Straightforward issue** (clear bug, no API/design ambiguity):
1. Fix in the correct module (`src/garmin_mcp/<module>.py`)
2. Add tests (success case + exception case) in `tests/integration/test_<module>_tools.py`
3. Run: `uv run pytest tests/unit/ tests/integration/ -x -q --timeout=30`
4. Create a branch, push, open a PR, merge it, close the issue

**Stop and ask the user** if the issue involves:
- Changing the minimum Python version or garminconnect version
- Choosing between competing implementation approaches
- Breaking changes to existing tool signatures
- Garmin China auth, Docker/GHCR publishing, or remote multi-user mode

## Pull requests

**Stop and ask the user** if the PR:
- Bumps `garminconnect` or `mcp` versions (see pyproject.toml constraints)
- Changes the Python minimum version (`requires-python`)
- Reverses or significantly changes existing tool behavior
- Introduces a new module that conflicts with another open PR
- Has two or more competing implementations of the same feature (pick the better one or ask)
- Touches `.github/workflows/`

## PR and commit messages

- Don't add emojis to the description
- Don't add "coauthored by claude" or similar messages

## Coding conventions

- All tools return `str`. Wrap API calls in `try/except Exception as e: return f"Error: {str(e)}"`.
- Use `x.get(key) or {}` (not `x.get(key, {})`) for nested Garmin sections — Garmin sends explicit `null` for empty sections, and a default only fires when the key is absent.
- Use `_as_dict(value)` helper (in `training.py`) for deeply nested dicts.
- Date params: `YYYY-MM-DD`. Activity IDs: large integers.
- Module pattern: `garmin_client = None` global → `configure(client)` → `register_tools(app)`.
- No comments unless the WHY is non-obvious.

## Testing conventions

Each tool needs at minimum:
- **Success case**: mock returns valid data, assert key fields in parsed JSON
- **Exception case**: mock raises `Exception("API Error")`, assert `"Error"` in result

Run the full suite (not just the changed module) to catch regressions.

## Key version constraints

```
garminconnect==0.3.2   ← pinned; upgrade is a design decision
mcp>=1.28.1,<2         ← mcp 2.x renamed FastMCP; cap until ported
requires-python>=3.10  ← 3.12 minimum is a pending design decision
```

## Design decisions — always ask the user

These are open questions that require user input before any code change:

1. **garminconnect 0.3.7 / 0.3.8 upgrade** (PRs #214, #230)
2. **Python 3.12 minimum** (PR #214)
3. **Strength exercise catalog** (PRs #238, #251 — competing implementations)
4. **AGENTS.md / CLAUDE.md** (PR #235 — conflicts with local CLAUDE.md)
5. **Garmin China auth** (PRs #180, #249, #268 — three competing implementations)
6. **Cycling power target ID reversal** (PR #194 — controversial, adds docs but changes behavior)
7. **Docker token bootstrap / GHCR publishing** (PRs #229, #170, #195 — touch workflows)
8. **HTTP FIT file serving** (PR #171)
9. **Structured strength activity creation** (PR #208, depends on #214)
10. **Remote OAuth2 multi-user mode** (PRs #43, #121, #124 — explicitly skipped)
