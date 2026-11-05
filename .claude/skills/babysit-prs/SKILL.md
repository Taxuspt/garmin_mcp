---
name: babysit-prs
description: Work through the open pull requests and issues of Taxuspt/garmin_mcp in one batch. Merges ready PRs with each contributor kept as the author, fixes straightforward issues, and lists what needs the maintainer's decision. Run "/babysit-prs" to work the queue, or "/babysit-prs preview" for a read-only plan.
disable-model-invocation: true
argument-hint: "[preview]"
---

# Babysit PRs

Read `project.md` (next to this file) first. It holds this project's skip
list, stop-and-ask lists, message rules, conventions and test command.

**Preview mode:** if the arguments contain `preview`, do steps 1 and 2 only and
run read-only commands only (`git remote`, `git fetch`, `gh pr list/view/diff/checks`,
`gh run list`, `gh issue list/view`). Do not check out, push, comment, approve,
merge or close anything, and do not ask for approval. End with: "Preview only,
nothing was changed. Run /babysit-prs to act on this plan." Don't act on any
reply in the same run.

## Rules for the whole run

1. **The contributor keeps the credit.** Land outside work through the
   contributor's own PR. Re-land only when step 3 says so, and then keep their
   commits. Never apply someone's diff with `gh pr diff | git apply` and commit
   it as yourself, and never use `git commit --reset-author` on their commits.
2. **Name the repository on every `gh` command:** `-R Taxuspt/garmin_mcp`.
   In a clone with several remotes, `gh` can silently pick another one.
   `gh api` has no `-R`; it takes the repository in the path
   (`repos/Taxuspt/garmin_mcp/...`).
3. **Use the right git remote.** Run `git remote -v` and find the remote whose
   URL contains `Taxuspt/garmin_mcp`. This file calls it `<up>`; write its real
   name (often `origin` or `upstream`) wherever you see `<up>`. Never use a
   remote that points at a fork, and never push to `<up>`'s `main`.
4. **Merge with GitHub's default message.** For a PR a contributor opened:
   `gh pr merge <PR> -R Taxuspt/garmin_mcp --squash --match-head-commit <sha>`.
   For a re-land PR you opened: `--rebase` instead of `--squash` (see Re-land).
   Never pass `--body` or `--body-file`: the default message carries the
   `Co-authored-by:` lines, and a custom body drops them. `<sha>` is the
   current head of the PR you are about to merge (for a PR you just opened,
   that is the new PR):
   `gh pr view <PR> -R Taxuspt/garmin_mcp --json headRefOid --jq .headRefOid`.
   Read it again after any push.
5. **One approval per batch.** Change nothing before the maintainer approves
   the plan in step 2, and do nothing that isn't in the approved plan.
6. **If a step fails or a PR isn't in the state the plan expected, stop work on
   that PR** and put it in the report with the error. Don't work around it: no
   force-push, no `gh pr merge --admin`, no rebasing a contributor's branch, no
   dropping `--match-head-commit`. One exception: if a merge fails with "Base
   branch was modified", or `mergeable` shows `UNKNOWN`, re-read `mergeable`
   and `<sha>` and try once more.
7. **Never close a contributor's PR without a comment** that thanks them and
   says where their work went. Post it with `gh pr comment` before
   `gh pr close`; `gh pr close --comment` skips the comment if the PR is
   already closed.
8. **Leave `.github/workflows/` changes to the maintainer.** The gh token
   usually lacks the `workflow` scope. Never approve CI runs for, push to, or
   merge a PR that changes workflow files, Dependabot ones included. Give the
   click steps from step 3 instead.
9. **No AI attribution.** Don't add a `Co-authored-by` or "Generated with" line
   for Claude or any other AI to commits, PR bodies or comments; `project.md`
   forbids it. If Claude Code keeps adding one, the maintainer can set
   `"attribution": {"commit": "", "pr": ""}` in Claude Code's settings.

## Step 1: Gather (read-only)

```bash
git remote -v
git fetch <up>
gh pr list -R Taxuspt/garmin_mcp --state open --limit 100 \
  --json number,title,author,isDraft,isCrossRepository,maintainerCanModify,mergeable,headRefOid,headRefName,files
gh issue list -R Taxuspt/garmin_mcp --state open --limit 50 --json number,title,author,labels
```

`closingIssuesReferences` isn't a valid field for `gh pr list`/`gh pr view --json`
(it only exists in GitHub's GraphQL schema), so fetch it for every open PR in one
batched call instead:

```bash
gh api graphql -f query='
  query { repository(owner: "Taxuspt", name: "garmin_mcp") {
    pullRequests(states: OPEN, first: 100) {
      nodes { number closingIssuesReferences(first: 10) { nodes { number } } }
    }
  } }' --jq '.data.repository.pullRequests.nodes[] | select(.closingIssuesReferences.nodes != []) | {number, closes: [.closingIssuesReferences.nodes[].number]}'
```

For each PR that isn't skipped:

- **Read the diff** (`gh pr diff <N> -R Taxuspt/garmin_mcp`) and note in one
  line what it does. Several stop-and-ask items in `project.md` can only be
  spotted in the diff (version bumps, changed tool behavior, competing
  implementations).
- **Read the conversation:**

  ```bash
  gh pr view <N> -R Taxuspt/garmin_mcp --json comments,reviews,commits \
    --jq '{comments: [.comments[] | {who: .author.login, at: .createdAt, body: .body[0:300]}], reviews: [.reviews[] | {who: .author.login, state, at: .submittedAt}], lastCommit: .commits[-1].committedDate}'
  ```

  If the maintainer asked for changes (a comment or a "changes requested"
  review) and `lastCommit` is older than that request, the contributor hasn't
  pushed since.
- **Find the CI state:**

  ```bash
  gh pr checks <N> -R Taxuspt/garmin_mcp --json name,bucket
  gh run list -R Taxuspt/garmin_mcp --commit <headRefOid> --status action_required --json databaseId,workflowName
  ```

  - `bucket` is `pass`, `fail`, `pending`, `skipping` or `cancel`. `skipping`
    counts as passing.
  - If `gh pr checks` prints `no checks reported` and exits with code 1, that
    is not an error: the PR has no checks yet. Then, if `gh run list` lists
    runs, CI is **waiting for approval**. GitHub holds CI for contributors who
    have never had a commit or PR merged here; once one of their PRs is merged,
    their later PRs run without approval. If there are no such runs, the PR
    usually has merge conflicts (GitHub runs no CI on conflicted PRs).
  - If `gh run list` itself fails (for example a rate limit), say so in the
    plan and mark PRs with no checks as "probably waiting for approval".
- If `mergeable` is `UNKNOWN`, ask again with
  `gh pr view <N> -R Taxuspt/garmin_mcp --json mergeable`.

For each issue, check whether an open PR already fixes it (its
`closingIssuesReferences` lists the issue, or it clearly does the same thing).

## Step 2: Plan, then one approval

Check the groups from top to bottom and put each PR in the **first** one that
matches:

| # | Group | When | Action |
|---|---|---|---|
| 1 | Skip | On the skip list in `project.md`, or a draft | Nothing |
| 2 | Ask | On the design-decision list, or matches a stop-and-ask item in `project.md` other than "Touches `.github/workflows/`" | Put the question to the maintainer |
| 3 | Workflow files | Changes anything under `.github/workflows/` | Click steps for the maintainer |
| 4 | Duplicate | Another open PR has the same approach and the same user-visible result (same tool names, parameters and dependencies). Different approaches are competing implementations: Ask | Merge one, credit and close the others |
| 5 | Waiting | Checks still `pending`; or you asked the contributor for changes and they haven't pushed since | Nothing this run; list under "Waiting on contributors" |
| 6 | Approve CI | CI waiting for approval | Approve the runs after reading the diff; merge when green |
| 7 | Fix | A check failed or was cancelled, merge conflicts, or the diff has a problem | Fix it if you may push to the branch, otherwise ask the author |
| 8 | Ready | Every check passes, `mergeable` is `MERGEABLE`, and the diff is fine | Merge the PR itself |

Dependabot PRs and the maintainer's own PRs use the same groups.

Show the plan as a table, one line per PR: number, title, author, group, what
the diff does, the planned action, and how the contributor will be credited
(for example "author of the merged commit"). Then:

- the full text of every comment you would post;
- the issues you would fix, and the ones already covered by a PR ("covered by #N");
- the questions for the maintainer. For a fork PR in Fix that you can't push
  to, offer "ask the contributor" or "re-land", and re-land only if the
  maintainer picks it.

In preview mode, stop here. Otherwise ask once: "Go ahead with this plan?"

## Step 3: Act

First run `git status --porcelain`. If it prints anything, stop and ask: there
is uncommitted work in this clone. Handle all PRs before any issue.

After finishing each PR or issue, return to a clean state. If you stopped in
the middle of a merge or rebase, run `git merge --abort` or `git rebase --abort`
first (nothing has been pushed yet). Then:

```bash
git fetch <up>
git switch --detach <up>/main
```

Right before approving runs for a PR or merging it, check that `<sha>` (rule 4)
is still the head whose diff you read. If it changed, read the diff again, and
if it now does something different from the plan, stop work on that PR and
report it.

### Waiting for CI

Use this wherever a step says "wait for CI". Run
`gh pr checks <PR> -R Taxuspt/garmin_mcp --watch --fail-fast` in the
background, or in the foreground with the Bash tool's 10-minute maximum
timeout. Don't use a foreground `sleep`.

- `no checks reported` (exit code 1): the checks haven't started yet. Run it
  again a little later. This is not a failure.
- Exit code 0: every check passed.
- Any other result after checks have appeared: a check failed. Handle the PR
  as Fix (for your own new PR, stop and report it).

### Ready

Wait for CI first if any check is still running. Then:

```bash
gh pr merge <N> -R Taxuspt/garmin_mcp --squash --match-head-commit <sha>
gh pr comment <N> -R Taxuspt/garmin_mcp --body "Merged, thanks @<login>!"
```

If this is their first merged PR here, say so and welcome them. Skip the
comment for bots (`dependabot[bot]`, `app/...`) and for the maintainer's own
PRs. For the maintainer's own PRs, test before merging:
`gh pr checkout <N> -R Taxuspt/garmin_mcp`, run the test command, then return
to a clean state.

### Approve CI

Approved runs execute the PR's code on GitHub's runners. Read the whole diff
first, especially scripts and build files. If anything looks off (unexpected
network calls, obfuscated code), don't approve it; report it. Otherwise:

```bash
gh run list -R Taxuspt/garmin_mcp --commit <sha> --status action_required --json databaseId --jq '.[].databaseId'
gh api -X POST repos/Taxuspt/garmin_mcp/actions/runs/<run-id>/approve   # once per run id
```

Then wait for CI. When every check passes, handle the PR as Ready.

### Fix

Who may push depends on the PR:

- **Fork PR** (`isCrossRepository` true): only if `maintainerCanModify` is
  true and the fix is small.
- **The maintainer's own PR:** yes.
- **Dependabot PR:** never; see Dependabot.

When you may push, the work still lands through the original PR:

```bash
gh pr checkout <N> -R Taxuspt/garmin_mcp
git merge <up>/main            # only if mergeable is CONFLICTING; resolve, then commit
# make the fix and run the test command
git commit -m "fix: <what you changed>"   # skip if the merge commit was the only change
git -c push.default=upstream push         # updates the PR's branch, even one named main
```

Add your own commits on top; don't rewrite theirs. Never run
`git push <url> HEAD`: that makes a new branch on their fork instead of
updating the PR. If the push is refused with a message about the `workflow`
scope, stop: put the PR under "Needs your click" and tell the maintainer to
click **Resolve conflicts** or **Update branch** on GitHub, then run
/babysit-prs again.

After a successful push, read the new `<sha>`, approve any waiting runs as in
Approve CI, and wait for CI. Merge as Ready only when every check passes.

If you may not push, or the fix is more than small, post the change request
from the plan and leave the PR open. Don't post it again on a later run unless
the author has pushed since.

### Re-land (only when the maintainer chose it in the plan)

Start from the contributor's commits, not from their diff:

```bash
git fetch <up> +pull/<N>/head:pr-<N>
git switch -c reland/<N>-<slug> pr-<N>
git rebase <up>/main           # resolve conflicts; their commits keep their author
gh pr view <N> -R Taxuspt/garmin_mcp --json commits --jq '.commits[].authors[].login'
```

If that last command doesn't list the PR opener's login (an empty login means
the commit email isn't linked to any account), add them as co-author on the top
contributor commit, before any fix-up commits. `--amend` without
`--reset-author` keeps the commit's author. The trailer is described under
"Crediting someone in a commit".

```bash
git commit --amend --no-edit --trailer "<the Co-authored-by line>"
```

Then add your own fix-up commits if needed, run the test command, and:

```bash
git push <up> reland/<N>-<slug>
gh pr create -R Taxuspt/garmin_mcp --head reland/<N>-<slug> --title "<original title>" \
  --body "Re-lands #<N> by @<login> on top of current main."   # add "Closes #<issue>" for each issue #N closes
```

Wait for CI on the new PR, then:

```bash
gh pr merge <new-pr> -R Taxuspt/garmin_mcp --rebase --match-head-commit <sha>
gh pr comment <N> -R Taxuspt/garmin_mcp --body "<thank-you text>"
gh pr close <N> -R Taxuspt/garmin_mcp
```

Thank-you text: "Thanks @<login>! This landed in #<new-pr> with your commits."
If you had to add them as co-author: "Thanks @<login>! This landed in
#<new-pr>, and you're credited as co-author."

Use `--rebase`, not `--squash`, here. A squash of a PR you opened would make
you the author. `Closes #<N>` would not help either: closing keywords close
issues, not pull requests.

### Duplicate

Pick one PR: the more complete and cleaner one, or the earliest one when
they're equal. Handle the chosen PR through Waiting, Approve CI, Fix or Ready
as if it were the only one. If you reuse code or ideas from the others and you
may push to the chosen PR (see Fix), add a credit commit there before merging.
The squash keeps its lines.

```bash
git commit --allow-empty -m "chore: credit #<K>" -m "<the Co-authored-by line for #K's author>"
git -c push.default=upstream push
```

Only after the chosen PR is merged, post one of these on each of the others and
close it:

- If you added a credit commit for them: "Thanks @<login>! Closing as a
  duplicate of #<M>, which is now merged. Your <what> is in it, and you're
  credited as co-author."
- Otherwise: "Thanks @<login>! Closing as a duplicate of #<M>, which is now merged."

```bash
gh pr comment <K> -R Taxuspt/garmin_mcp --body "<one of the texts above>"
gh pr close <K> -R Taxuspt/garmin_mcp
```

### Workflow files (maintainer clicks)

Tell the maintainer, for each PR:

1. Open `https://github.com/Taxuspt/garmin_mcp/pull/<N>` and read **Files changed**.
2. If GitHub says the branch has conflicts, don't merge. Click **Resolve
   conflicts** if it's offered, or ask the contributor to update the branch.
3. If a box says workflows are awaiting approval, click **Approve and run** and
   wait for the checks.
4. In the merge box, pick **Squash and merge** from the button's menu.
5. Leave the commit message GitHub fills in, including the `Co-authored-by:`
   lines, and click **Confirm squash and merge**.

### Dependabot

Ready ones merge like any other PR. If one conflicts, comment
`@dependabot rebase` once. Version bumps on the stop-and-ask list in
`project.md` go to the maintainer.

### Issues

If an open PR already fixes the issue, don't write your own fix: handle that PR
instead. An issue is **not** straightforward, so it goes under "Needs your
decision", when:

- it matches the issue stop-and-ask list in `project.md`;
- the reporter says the fix is unverified or needs testing on a device;
- the fix would change a file that an open PR also changes.

If the reporter proposed the exact fix, offer "ask them to open a PR" as an
option. For a straightforward issue:

```bash
git fetch <up>
git switch -c fix/<issue>-<slug> <up>/main
# make the fix, add tests as project.md describes, run the test command
git commit -m "fix(<scope>): <subject>"
git push <up> fix/<issue>-<slug>
gh pr create -R Taxuspt/garmin_mcp --head fix/<issue>-<slug> --title "fix(<scope>): <subject>" --body "Closes #<issue>"
```

Wait for CI on the new PR, then
`gh pr merge <new-pr> -R Taxuspt/garmin_mcp --squash --match-head-commit <sha>`.
If you used code or a patch from the issue, add the Co-authored-by line for its
author to the commit. Thank the reporter in a comment.

### Crediting someone in a commit

The line is `Co-authored-by: NAME <EMAIL>`, and the `<` `>` around the email
must be really there. Example for the GitHub user octocat, whose account id is
583231:

```
Co-authored-by: octocat <583231+octocat@users.noreply.github.com>
```

Replace both `octocat`s and the number with their login and the id from
`gh api users/<login> --jq .id`. This address always links to their GitHub
account.

## Step 4: Report

Write a short plain-language report:

- **Merged:** PR number, one line on what it does, and who is credited.
- **Waiting on contributors:** what was asked, and when.
- **Needs your decision:** each question, with the options.
- **Needs your click:** workflow PRs and refused pushes, with links and steps.
- **Stopped:** PRs where a step failed, with the error.
- **Issues closed.**
