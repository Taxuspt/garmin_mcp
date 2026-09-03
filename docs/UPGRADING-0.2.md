# Upgrading to 0.2.0 beta 1

This beta preserves existing tool names, arguments, JSON-string results, and
legacy write defaults. New intent-level tools use structured output and place
human confirmation in front of closed-loop Garmin writes.

Python 3.12 or newer is required. Python 3.10 and 3.11 cannot use the patched
`garminconnect 0.3.5` dependency; upgrade the runtime before installing this
beta. The DXT and container already select Python 3.12.

## Before upgrading

1. Back up the Garmin token directory, normally `~/.garminconnect`.
2. Ensure the directory is private (`0700`) and token files are private
   (`0600`) on POSIX systems.
3. Run `garmin-mcp-auth` once if the token session is expired.
4. Stop any older MCP server process before replacing its package or DXT.

This release requires `garminconnect 0.3.5`, which fixes insecure token-store
permissions. Existing loose files should still be tightened before the first
run; if they were exposed on a shared host, re-authenticate to rotate tokens.

## Configuration changes

- Startup is now offline. The first Garmin-backed tool call performs a locked
  login; `check_garmin_auth(verify=false)` checks local configuration only.
- `GARMIN_DATA_DIR` opts into the SQLite physiology/coaching store. Leaving it
  unset keeps streams and inline analysis stateless.
- `GARMIN_ALLOWED_UPLOAD_DIRS` is mandatory for FIT upload over HTTP/SSE. Use a
  dedicated staging directory, never a home directory or filesystem root.
- `GARMIN_REQUEST_TIMEOUT_SECONDS` defaults to 15 seconds.
- `GARMIN_REQUEST_BUDGET_PER_MINUTE` defaults to 120 remote requests.

No database migration is needed when SQLite was never enabled. When it is
enabled, the server applies explicit `PRAGMA user_version` migrations on open.
Back up the database before moving it between beta versions.

## Behavioral changes

- New workout, profile-sync, FIT-upload, plan-apply, and adaptation-apply flows
  default to `dry_run=true` and require an MCP client capable of confirmation.
- Scheduled jobs may generate pending plans or adaptations but cannot bypass
  human confirmation to write Garmin.
- Raw stream pagination uses opaque cursors bound to activity, fields,
  resolution, and time basis. Do not construct or reuse cursors across queries.
- Threshold estimates are candidates only. Conflicting evidence is never
  averaged or activated automatically.
- `set_heart_rate_zones` retains its historical write default; pass
  `dry_run=true` when a preview is required.

## Rollback

The Python package can be downgraded to `0.1.0` without changing Garmin data.
Do not reuse a physiology SQLite database with an older server that does not
recognize its schema. Keep or archive the database separately, and restore the
previous DXT or package version.

Any apply operation that reports `recovery_required` includes exact generated
workout and schedule identifiers. Reconcile only those identifiers; never
delete an existing user workout by name or a guessed ID.
