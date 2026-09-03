# Changelog

All notable changes to this project are documented here. This project follows
Semantic Versioning; Python build filenames use PEP 440 normalization, so
`0.2.0-beta.1` is packaged as `0.2.0b1`.

## [0.2.0-beta.1] - 2026-09-03

### Added

- First-call lazy Garmin authentication with a concurrency lock and local
  `check_garmin_auth` diagnostics.
- A centralized Garmin gateway with bounded request budgets, endpoint-aware
  caching, timeout handling, 429 backoff, and write-side cache invalidation.
- `get_briefing` with bounded fan-out and independent partial-result status.
- Pause-aware, cursor-paginated activity streams, time-weighted resampling,
  decoupling analysis, historical zone re-slicing, and polarization audits.
- Optional SQLite physiology evidence, threshold candidates, zone models,
  analysis provenance, immutable training plans, adaptations, workout links,
  and audit records.
- Generic physiology test inspection/import and deterministic threshold rules.
- Preview-first cycling workout builders with verified absolute-watt target
  encoding, plus deterministic cycling training blocks and weekly adaptation.
- Staged, hashed, preview-first FIT upload with fail-closed vendor repair rules.
- Structured output models and MCP safety annotations for new tools.
- Multi-architecture GHCR publishing workflow for version tags.

### Changed

- Minimum Python is now 3.12 because the security-fixed `garminconnect 0.3.5`
  no longer supports Python 3.10 or 3.11.
- Ordinary `pytest` runs exclude all real-account E2E tests.
- Existing tools and their default write behavior remain compatible; the full
  endpoint-oriented tool catalog is still exposed.
- `set_heart_rate_zones` accepts an optional compatible `dry_run` parameter.
- Workout cleanup now distinguishes `workoutScheduleId` from
  `scheduledWorkoutId`, verifies authoritative unschedule state, and never
  deletes a template while its calendar outcome is indeterminate.
- Live nutrition tests skip only when Garmin explicitly denies the account
  capability or omits the requested meal; other errors remain failures.

### Security

- Upgraded `garminconnect` to `0.3.5`, which creates OAuth token directories
  and files with owner-only permissions and rejects unsafe symlink writes.
- New closed-loop writes default to preview and require interactive human
  confirmation before Garmin mutation.
- Remote FIT upload requires an explicit allowed-directory list.
- Passwords, MFA codes, and tokens are never requested with form elicitation.

### Known limitations

- Garmin FTP and power-zone profile writes remain experimental and unavailable
  until fixture-backed live round-trip and rollback validation is complete.
- The first complete coaching policy targets cycling; other sports retain
  provider/domain extension points.
- China-region live-contract coverage is not included in this beta.
- HTTP transport remains local/single-account and must not be treated as a
  hosted multi-tenant OAuth service.

[0.2.0-beta.1]: https://github.com/Taxuspt/garmin_mcp/compare/v0.1.0...v0.2.0-beta.1
