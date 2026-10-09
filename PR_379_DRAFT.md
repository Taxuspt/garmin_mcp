# PR #379 draft

## Summary

- Keep training-load metrics when Garmin device discovery is unavailable or no primary tracker is declared; mark tracker identity unknown instead of treating the reporting device as non-primary.
- Select malformed-device-safe, deterministic reporting records, preferring Garmin's `primaryTrainingDevice` marker when the verified primary tracker is absent.
- Use Training Readiness acute load only when its `deviceId` matches the verified primary tracker and its `calendarDate` matches the requested date. Mismatches and optional readiness failures are reported as unavailable metadata without discarding status data.
- Preserve load provenance by requiring device-keyed load-balance data to match the reporting device.

## Tests

- Added regression coverage for device lookup failure, missing primary flags, zero-valued device IDs, malformed device records, deterministic fallback selection, readiness exceptions, wrong-device/wrong-date readiness rows, matching tracker rows, and mismatched-only rows.
- Full unit and integration suite passes without live Garmin credentials.
