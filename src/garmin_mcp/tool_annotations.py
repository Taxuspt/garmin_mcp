"""MCP tool effect annotations.

The MCP specification treats annotations as hints, but omitting them makes
clients assume the most conservative risk profile. Keep this registry
explicit so a newly registered tool cannot silently become read-only merely
because its name happens to start with ``get_``.
"""

from mcp.types import ToolAnnotations


# Tools that add a record, create a resource, or otherwise perform a
# non-destructive operation. ``set_fit_download_dir`` changes local server
# configuration, so it is a write even though it does not contact Garmin.
ADDITIVE_TOOLS = frozenset(
    {
        "add_body_composition",
        "add_gear_to_activity",
        "add_hydration_data",
        "add_weigh_in",
        "add_weigh_in_with_timestamps",
        "create_custom_food",
        "create_manual_activity",
        "create_run_interval_workout",
        "create_run_workout",
        "create_strength_workout",
        "create_walk_run_workout",
        "create_z2_walk_workout",
        "log_custom_food",
        "log_food",
        "push_workout_to_device",
        "request_reload",
        "schedule_week",
        "schedule_workout",
        "schedule_workouts",
        "set_blood_pressure",
        "set_fit_download_dir",
        "upload_course",
        "upload_workout",
        "upload_workouts",
        "upsert_and_log",
    }
)


# Tools that delete, replace, overwrite, or otherwise remove existing state.
# Download tools write files and can overwrite an existing destination, so
# they are deliberately not marked read-only either.
DESTRUCTIVE_TOOLS = frozenset(
    {
        "delete_activity",
        "delete_course",
        "delete_custom_food",
        "delete_food_log",
        "delete_weigh_ins",
        "delete_workout",
        "delete_workouts",
        "download_activity_file",
        "download_course_gpx",
        "download_workout",
        "remove_gear_from_activity",
        "reschedule_workout",
        "set_activity_description",
        "set_activity_event_type",
        "set_activity_exercise_sets",
        "set_activity_feel",
        "set_activity_name",
        "set_activity_type",
        "set_heart_rate_zones",
        "set_nutrition_daily_settings",
        "set_perceived_effort",
        "unschedule_workout",
        "unschedule_workouts",
        "update_custom_food",
    }
)


# These tools are explicitly documented as safe to repeat with the same
# arguments. Batch scheduling can also upload an inline workout, so it is
# intentionally excluded even though its existing-workout path is guarded.
IDEMPOTENT_TOOLS = frozenset(
    {"reschedule_workout", "schedule_week", "schedule_workout"}
)


# This is the only registered tool whose effect is strictly local. All other
# tools either read from or write to Garmin Connect (including tools that also
# write a local download file), so they cross an external-world boundary.
LOCAL_ONLY_TOOLS = frozenset({"set_fit_download_dir"})


# The explicit read-only set is kept as a complement rather than inferred at
# runtime. The coverage test compares the union of all sets with the actual
# registered tool list and fails when upstream adds a tool.
READ_ONLY_TOOLS = frozenset(
    {
        "count_activities",
        "get_acclimation",
        "get_activities",
        "get_activities_by_date",
        "get_activities_fordate",
        "get_activity",
        "get_activity_exercise_sets",
        "get_activity_fit_data",
        "get_activity_fit_messages",
        "get_activity_gear",
        "get_activity_hr_in_timezones",
        "get_activity_power_in_timezones",
        "get_activity_split_summaries",
        "get_activity_splits",
        "get_activity_typed_splits",
        "get_activity_types",
        "get_activity_weather",
        "get_adhoc_challenges",
        "get_all_day_events",
        "get_all_day_stress",
        "get_available_badge_challenges",
        "get_badge_challenges",
        "get_blood_pressure",
        "get_body_battery",
        "get_body_battery_events",
        "get_body_composition",
        "get_calendar_events",
        "get_course_details",
        "get_courses",
        "get_custom_food_serving_units",
        "get_custom_foods",
        "get_cycling_ftp",
        "get_daily_steps",
        "get_daily_weigh_ins",
        "get_device_alarms",
        "get_device_last_used",
        "get_device_settings",
        "get_device_solar_data",
        "get_devices",
        "get_earned_badges",
        "get_endurance_score",
        "get_energy_balance",
        "get_fitnessage_data",
        "get_floors",
        "get_full_name",
        "get_garmin_coach_workouts",
        "get_gear",
        "get_goals",
        "get_heart_rate_zones",
        "get_heart_rates",
        "get_heart_rates_summary",
        "get_hill_score",
        "get_hrv_data",
        "get_hrv_trend",
        "get_hydration_data",
        "get_inprogress_virtual_challenges",
        "get_lactate_threshold",
        "get_lifestyle_logging_data",
        "get_menstrual_calendar_data",
        "get_menstrual_data_for_date",
        "get_morning_training_readiness",
        "get_non_completed_badge_challenges",
        "get_nutrition_daily_food_log",
        "get_nutrition_daily_meals",
        "get_nutrition_daily_settings",
        "get_nutrition_summary_between_dates",
        "get_personal_record",
        "get_power_duration_curve",
        "get_pregnancy_summary",
        "get_primary_training_device",
        "get_progress_summary_between_dates",
        "get_race_predictions",
        "get_recovery_time_remaining",
        "get_respiration_data",
        "get_respiration_summary",
        "get_respiration_trend",
        "get_rhr_day",
        "get_running_tolerance",
        "get_running_tolerance_trend",
        "get_scheduled_workouts",
        "get_sleep_data",
        "get_sleep_summary",
        "get_sleep_summary_range",
        "get_spo2_data",
        "get_stats",
        "get_stats_and_body",
        "get_stats_range",
        "get_steps_data",
        "get_stress_data",
        "get_stress_summary",
        "get_training_effect",
        "get_training_load_balance",
        "get_training_load_trend",
        "get_training_plan_workouts",
        "get_training_readiness",
        "get_training_status",
        "get_unit_system",
        "get_user_profile",
        "get_user_summary",
        "get_userprofile_settings",
        "get_vo2max_trend",
        "get_weekly_intensity_minutes",
        "get_weekly_steps",
        "get_weekly_stress",
        "get_weigh_ins",
        "get_workout_by_id",
        "get_workouts",
        "list_strength_exercises",
        "match_strength_exercise",
        "resolve_strength_exercises",
        "search_foods",
    }
)


ALL_ANNOTATED_TOOLS = READ_ONLY_TOOLS | ADDITIVE_TOOLS | DESTRUCTIVE_TOOLS


def annotations_for(name: str) -> ToolAnnotations | None:
    """Return annotations for a known tool, or ``None`` for a new tool."""

    if name in READ_ONLY_TOOLS:
        read_only = True
        destructive = False
    elif name in ADDITIVE_TOOLS:
        read_only = False
        destructive = False
    elif name in DESTRUCTIVE_TOOLS:
        read_only = False
        destructive = True
    else:
        return None

    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=name in IDEMPOTENT_TOOLS if not read_only else None,
        openWorldHint=name not in LOCAL_ONLY_TOOLS,
    )
