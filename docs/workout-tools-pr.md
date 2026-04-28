# Garmin Workout Tools PR Notes

This document summarizes the changes in PR #1 for the `codex/garmin-strength-workouts` branch.

## Summary

The PR expands Garmin workout support for strength training and calendar management. It adds validation for Garmin-compatible strength payloads, exercise catalog lookup, recurring workout scheduling, and tools for removing scheduled workout instances from the Garmin calendar.

## New MCP Tools

### `search_exercise_catalog`

Searches Garmin's public exercise catalog and translation files to find the `category` and `exerciseName` values required by Garmin strength workouts.

- Supports catalog keys such as `AIR_SQUAT`.
- Supports English display names.
- Supports Spanish display names from Garmin's public translation file.
- Returns category, exercise name, English display name, Spanish display name, and a match score.

### `validate_workout_payload`

Performs a dry-run validation of workout JSON before upload.

- Normalizes common heart-rate zone mistakes by converting zone-like `targetValueOne` values into `zoneNumber`.
- Validates strength training payloads before they are sent to Garmin.
- Returns a summary of the workout shape, including segment counts, top-level step counts, and strength exercise steps.
- Returns the normalized payload when validation succeeds.

### `unschedule_workout`

Removes a scheduled workout instance from the Garmin calendar by `scheduledWorkoutId`.

- Deletes only the calendar entry.
- Does not delete the workout from the user's workout library.
- Uses Garmin's `workout-service/schedule/{scheduledWorkoutId}` delete endpoint.

### `unschedule_workout_on_date`

Removes a specific workout from a specific date without requiring the caller to already know the scheduled instance ID.

- Queries the schedule for the requested date.
- Finds the matching `workoutId`.
- Resolves `scheduledWorkoutId`.
- Deletes that scheduled calendar instance.
- Returns `not_found` if the workout/date pair is not scheduled.

### `schedule_workout_recurring`

Schedules the same workout across a generated set of dates.

- Accepts `start_date`, `end_date`, `weekdays`, and `interval_weeks`.
- Supports English and Spanish weekday names, for example `monday`, `thursday`, `lunes`, and `jueves`.
- Supports `exclude_dates` for skipped calendar dates.
- Supports `dry_run` so callers can preview generated dates before creating Garmin calendar entries.

## Strength Workout Validation

The PR adds guardrails for strength training uploads because Garmin can accept malformed payloads while dropping important exercise metadata.

Validation currently enforces these rules for `strength_training` workouts:

- A strength exercise step with `exerciseName` must also include `category`.
- A strength exercise step with `category` must also include `exerciseName`.
- Strength exercise steps must use `reps`, not `distance`, as the exercise end condition.

The existing `upload_workout` flow now runs this validation before calling Garmin. Invalid strength payloads fail locally and are not uploaded.

## Workout Detail Curation

Workout detail responses now preserve more useful structure for strength workouts and repeat groups.

- Handles `targetType: null` values returned by Garmin.
- Includes nested steps inside repeat groups.
- Includes repeat group `step_count`.
- Includes strength fields such as `category`, `exercise_name`, `weight_value`, and `weight_unit`.
- Includes `scheduled_workout_id` in scheduled workout summaries so callers can unschedule by instance ID.

## Template Updates

The strength circuit template was updated from a generic timed circuit to a Garmin-compatible strength workout example.

- Uses `sportTypeId` 5 with `sportTypeKey` `strength_training`.
- Uses a repeat group with Garmin repeat metadata.
- Demonstrates a real strength exercise with `category: SQUAT` and `exerciseName: AIR_SQUAT`.
- Uses `reps` for the exercise step.
- Uses time-based rest and lap-button warmup/cooldown steps.

The workout structure reference was also updated to document `reps` as an end condition and the corrected strength training sport type.

## Test Coverage

The integration tests were expanded to cover:

- Curating nested strength workout steps and null `targetType` values.
- Searching the exercise catalog with Spanish queries.
- Validating successful strength payloads.
- Reporting validation errors for malformed strength payloads.
- Preventing invalid strength uploads from reaching Garmin.
- Preserving strength metadata on valid uploads.
- Returning `scheduled_workout_id` in scheduled workout summaries.
- Unscheduling directly by scheduled workout instance ID.
- Unscheduling by workout ID and date.
- Recurring scheduling date generation with dry-run mode.
- Posting one Garmin scheduling request per generated recurring date.

## Files Changed

- `src/garmin_mcp/workouts.py`
- `src/garmin_mcp/workout_templates.py`
- `tests/integration/test_workouts_tools.py`
- `docs/workout-tools-pr.md`
