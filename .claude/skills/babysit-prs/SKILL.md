---
name: babysit-prs
description: Review all open GitHub Issues and Pull Requests for the garmin-mcp project. Fix straightforward issues, merge clean PRs, and flag design decisions for the user. Invoke with /babysit-prs.
---

# Garmin MCP — PR & Issue Review Workflow

## Start every run

```bash
git checkout main && git pull origin main
gh pr list --state open --json number,title,headRefName --limit 50
gh issue list --state open --json number,title --limit 30
```

Check CI status for each open PR:
```bash
gh pr view <N> --json statusCheckRollup --jq '.statusCheckRollup[0].state // "NO_CI"'
```

---

## Always skip (do not touch)

- **#43, #121, #124** — Remote OAuth2 multi-user mode. Explicitly deferred by the user.
- PRs touching `.github/workflows/` cannot be merged via the GitHub API (needs `workflow` OAuth scope). Flag them for manual merge by the user instead.

---

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

---

## Pull Requests

### PRs from `Taxuspt` (first-party, have CI)

If CI passes: review the diff, run tests locally, squash-merge.

If CI fails: read the failure, fix it, push to the same branch.

### Fork PRs without CI

Most contributor PRs have no CI. Workflow:

```bash
# 1. Create a clean branch from current main
git checkout main && git pull origin main
git checkout -b apply/<N>-<slug> main

# 2. Apply the diff (3-way merge handles context drift)
gh pr diff <N> | git apply --3way

# 3. If README.md has a conflict (almost always does — fork is on old base):
git checkout HEAD -- README.md

# 4. If other files conflict, resolve manually, then:
git add <resolved-files> && git commit -m "chore: resolve merge conflict"

# 5. Run the full test suite
uv run pytest tests/unit/ tests/integration/ -x -q --timeout=30

# 6. Commit, push, open PR, squash-merge
git add <changed-files>
git commit -m "<type>(<scope>): <subject>\n\nCo-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
git push origin apply/<N>-<slug>
gh pr create --title "..." --body "Closes #<fork-pr-number> via clean apply.\n\n..."
gh pr merge <new-pr> --squash --subject "..."

# 7. Close the fork PR with a thank-you comment
gh pr close <N> --comment "Applied and merged via #<new-pr>. Thank you!"
```

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

---

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

---

## End of run

Report:
- What was merged (PR numbers and one-line description each)
- What issues were closed
- What still needs the user's decision (from the list above, plus any new ones found)