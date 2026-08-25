# What's actually in those 44 branches

## TL;DR

None of it is usable. Every branch that looked auth/token-relevant is **already squash-merged into `main`** — the branches are just stale clutter (this repo has "auto-delete branch on merge" turned off). The findings.md bug I flagged as the core issue (uncaught `RuntimeError` from `get_mfa()` in the non-interactive path) is **still present on current `main`** even after all this merged work — verified fresh below. There's nothing to salvage; building the fixes ourselves on the fork is still the only path.

One correction to what I said before creating this file: I described these 44 branches as if they were something unusual sitting on *your* fork. They're not — they exist on `Taxuspt/garmin_mcp` itself and got copied over automatically when GitHub forked the repo (forks always carry every branch, not just `main`). So this is upstream's own branch hygiene, not anything specific to your account.

## The branches I checked (the ones that looked auth/token-relevant)

| Branch | Status | What it was |
|---|---|---|
| `feat/improved_mfa` | Merged (PR #31, #32 — Jan 2026) | Early MFA support, ancient history |
| `fix/suppress-stdout-during-login` | Merged (PR #205, Jul 22) | Suppresses stdout during `garmin.login()` so progress dots don't corrupt MCP stdio framing — **already in `main`** |
| `apply/217-dxt-token-path` | Merged (PR #220, Jul 28) | `${HOME}` expansion + unset `user_config` handling for DXT token path — **already in `main`** as `token_utils.resolve_token_path()` |
| `fix/workout-tools-garth-client` | Merged (PR #102, May 8) | Old `garth`→`garminconnect` client API migration in `workouts.py` — ancient, already in `main` |
| `chore/dep-security-upgrades` | Merged (PR #95, May 8) | Batch dependabot bumps — already in `main` |
| `apply/237-startup-improvements` | Merged (PR #242, Aug 4) | Packaging/pytest-path/startup test improvements — already in `main` |
| `fix/windows-stdio-128` | Merged | LF line-ending fix for Windows stdio — already in `main` |
| `copilot/sub-pr-22`, `copilot/sub-pr-22-again` | Merged (PR #23, #24) | Two small Copilot-authored fixes (module-level import move, double-encoding fix in `get_full_name()`) — trivial, already in `main` |

Confirmed via `git merge-base --is-ancestor <branch> main` returning false for the "not ancient" ones — that's just because they were **squash-merged** (new commit hash on `main`, original branch tip orphaned), not because they're unmerged. `gh pr list --head <branch>` confirmed MERGED for every single one.

## What `main` already has that findings.md didn't call out separately

There's a `src/garmin_mcp/token_utils.py` and `src/garmin_mcp/auth_cli.py` already in the repo (added across PR #77, #140, #157, #201, #220) — a `garmin-mcp-auth` CLI entrypoint, path resolution (`${HOME}` expansion), `secure_token_dir()` (chmod 0700/0600 on token files — closes the world-readable CVE), and `validate_tokens()`. This is a real, separate auth-adjacent module I didn't dwell on in the original audit since it's not in the request-time hot path — worth knowing about since our `garmin_session` port will either replace it or need to coexist with it. It does **not** touch the actual gaps we care about: no proactive refresh, no cross-process locking, no rotation-safe adopt-on-read.

## Confirmed still broken on current `main` (the finding.md headline bug)

`get_mfa()` (`__init__.py:43-62`) still unconditionally raises `RuntimeError` when non-interactive:

```python
raise RuntimeError("MFA required but non-interactive environment")
```

And the credential-login fallback's exception handler (`__init__.py` in `init_api()`, the block wrapping `garmin.login()` / `garmin.resume_login()`) is still:

```python
except (
    FileNotFoundError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
    GarminConnectAuthenticationError,
    requests.exceptions.HTTPError,
) as err:
```

No `RuntimeError`. So: email/password present via env vars, account requires MFA, process is non-interactive (i.e. launched by an MCP client) → `get_mfa()` raises → propagates straight through this handler uncaught → hard crash instead of the clean "Failed to initialize Garmin Connect client" exit. Still real, still worth fixing in our fork.

## The actually useful signal: 28 currently-OPEN PRs on upstream

Pulled the full PR list (250+ total). Several open PRs target exactly the same problem space we care about — none are mergeable as-is (`gh pr view` shows `mergeable: CONFLICTING` on every one I checked, all stale against current `main`), so there's no code to cherry-pick, but they confirm other contributors independently hit the same gaps:

| PR | Opened | Status | Relevance |
|---|---|---|---|
| #214 `pr/expose-0.3.7-features` | Jul 26 | Open, conflicting | Bump `garminconnect` 0.3.2→0.3.7. Blocked mid-review on a rebase conflict with #221; maintainer doesn't allow maintainer-edits on this org's PRs so the fix-up PR from a contributor never landed. Dead in the water. |
| #230 `codex/chore-bump-garminconnect-0.3.8` | — | Open | Incremental bump 0.3.7→0.3.8, same story, further from 0.3.11. |
| #229 `codex/feat-docker-token-bootstrap` | Jul 29 | Open, conflicting | "Securely bootstrap Garmin tokens in containers" — auth/token related, no activity since opened. |
| #121 / #43 `feat/remote-oauth2-garmin-direct-login` | May 28 | Open, conflicting, zero comments | "Add remote Garmin login, DXT auth, and smart analytics" / direct OAuth2 + 2FA login — closest thing upstream has to what you originally asked for (direct email/password, no token-file dance), but abandoned since May. |
| #124 | — | Open | "Remote multi-user mode: OAuth2 server with Claude.ai-compatible discovery" |
| #254 `fix/issue-248-call-timeout` | — | Open | Bound Garmin call duration so a stalled request can't hang the server — reliability gap adjacent to ours. |
| #253 `fix/harden-null-sections` | — | Open | Null-handling hardening for HRV/sleep/progress/body-battery responses. |

Two closed-but-not-merged ones worth knowing, in case you wondered if the maintainer rejects things outright:
- **#182** "Harden HTTP transport bind and token file permissions" — closed, but only because it got folded into batch-merge PR #201 (`Taxuspt`'s own comment: *"the changes from this PR were incorporated into the codebase via the batch merge PR #201. Thank you for the contribution!"*) — so that's a non-issue, already in `main`.
- **#154 / #199** — two earlier attempts at the `_GarminProxy` runtime-error-handling pattern, closed unmerged, superseded by the version that did land in PR #157.

So: this maintainer isn't hostile to auth/token PRs in principle (several got merged fine), but the ones still open are genuinely stuck on merge conflicts and zero review bandwidth, not a "reviewed and declined" situation — consistent with a repo getting far more automated/agent-authored PR volume (`codex/`, `apply/NNN`, `agent/` branch prefixes are all over the closed+open list) than one maintainer can realistically keep rebasing and reviewing.

## Bottom line

Nothing to reuse. `main` already absorbed everything mergeable from the old local branches, and the still-open related PRs are stale and conflicting against current `main` — re-implementing them ourselves would be less work than untangling their conflicts anyway. The plan from findings.md §4 stands as originally written: port `garmin_session` in, bump `garminconnect` to 0.3.11 ourselves, fix the `RuntimeError` catch, fix `docker-compose.yml`'s volume mismatch — all directly on our fork's `main`, no upstream dependency.
