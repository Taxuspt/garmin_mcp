# Agent contribution instructions

## Required preflight: search before implementation

Read the request only far enough to identify useful search terms. Before deeper
investigation, editing files, or preparing an issue or pull request, check
whether the work is already tracked or implemented. Do not make code changes
until this check is complete.

1. Identify several distinctive search terms from the request, such as an unusual
   token from an error message, feature name, module, function, dependency, and
   common synonyms.
2. Search this repository's open **and closed** issues and pull requests.

   Run the following command once per search term. Use **one distinctive term per
   query**: GitHub combines multiple words restrictively, so one wording mismatch
   can hide relevant results without producing an error. Prefer an unusual token
   such as `fastmcp`, `ModuleNotFoundError`, or `mcp.server.fastmcp` over a phrase.
   An empty result is not enough to conclude that no duplicate exists; try the
   other identified terms first.

   ```bash
   # Issues and PRs, open, closed, and merged; omitting --state is intentional.
   gh search issues "TERM" --repo Taxuspt/garmin_mcp --include-prs --limit 1000
   ```

   If a result set reaches the limit, it is truncated and the missing entries are
   the lowest-ranked ones; narrow the term and search again.
   If the command exits non-zero or `gh` is unavailable, the search did not run.
   Report the failure and use the GitHub web interface, API, or another available
   repository search tool instead; never treat a failed search as an empty result.

3. Open likely matches and inspect their descriptions, comments, linked items,
   status, and diffs. Similarity cannot be judged from titles alone. Useful
   inspection commands include:

   ```bash
   gh issue view ISSUE_NUMBER --repo Taxuspt/garmin_mcp --comments

   # PRs GitHub recognizes as closing the issue; this is not exhaustive. Mere
   # references may appear only in comments or in the term search.
   gh issue view ISSUE_NUMBER --repo Taxuspt/garmin_mcp --json closedByPullRequestsReferences

   gh pr view PR_NUMBER --repo Taxuspt/garmin_mcp --comments
   gh pr diff PR_NUMBER --repo Taxuspt/garmin_mcp
   ```

4. Check the default branch and recent commit history in case the change already
   landed without a matching issue or pull request. These commands fetch and
   inspect the canonical repository rather than relying on a potentially stale
   fork remote:

   ```bash
   # A depth is required because a plain fetch in a shallow clone can succeed
   # while leaving only one commit of history.
   git fetch --depth=100 https://github.com/Taxuspt/garmin_mcp.git main
   git log --oneline -30 FETCH_HEAD
   git log --oneline -20 FETCH_HEAD -- path/to/relevant/file
   ```

   If the first `git log` returns far fewer than 30 commits, the history may still
   be truncated and cannot establish that a change is absent. Report the limited
   history instead of drawing that conclusion.

5. Before making code changes, report to the user every term searched and the
   relevant issue and pull request numbers found for each term. Note any term
   that returned nothing, any truncated result, and any search failure.

If equivalent work exists:

- When an issue already tracks the request, use it as the source of truth and
  link any authorized implementation to it.
- When an active pull request already implements the same outcome, do not create
  a competing implementation or pull request. Review, test, or contribute to the
  existing work, or ask the user or maintainers how to proceed.
- When a matching pull request is **merged**, the change may already be on the
  default branch. Verify the current behavior of `main` before assuming the
  problem still exists. If it no longer reproduces, stop. If it still reproduces,
  link the merged pull request and explain how the remaining problem differs from
  what that pull request fixed.
- When a matching issue or pull request is **closed without merging**, read the
  closing discussion, report why it was rejected or abandoned, and do not
  propose the same work again unless a distinct gap remains.
- When the overlap is partial or uncertain, explain the difference and get
  direction before proceeding.

Immediately before publishing an issue or pull request, rerun the step 2 search
for every term from step 1 because the repository may have changed while the
work was in progress. Then scan the full open pull request list so this final
check does not rely only on matching terms:

```bash
gh pr list --repo Taxuspt/garmin_mcp --state open --limit 1000
```

Open any entry whose title or branch name is plausibly related and apply the
step 3 inspection commands before dismissing it.

If this final check finds a match that the first did not, stop and apply the
rules above rather than publishing. A newly opened pull request that covers the
same outcome makes the proposed work a duplicate. If no new match appears,
reference any related issue in the new item or state why the proposed work is
distinct. Never publish an issue, comment, branch, or pull request unless the
user has authorized that remote action.
