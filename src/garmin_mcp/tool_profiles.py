"""Built-in tool profiles for GARMIN_TOOL_PROFILE.

The server registers 160 tools. A profile narrows that to one area so an LLM
client carries less context, without the user having to list tool names in
GARMIN_ENABLED_TOOLS. Each profile is a set of registered tool names; a unit
test checks every name here against the tools the modules actually register,
so a renamed tool fails the suite instead of silently dropping out.
"""

# Identity and unit settings. Cheap, and every area needs them to label the
# numbers it returns (metric vs statute, the user's HR zones).
CORE_TOOLS = frozenset(
    {
        "get_full_name",
        "get_heart_rate_zones",
        "get_unit_system",
        "get_user_profile",
        "get_userprofile_settings",
    }
)

MINIMAL_TOOLS = CORE_TOOLS | frozenset(
    {
        "get_activities",
        "get_activities_by_date",
        "get_activity",
        "get_activity_splits",
        "get_body_battery",
        "get_daily_steps",
        "get_heart_rates_summary",
        "get_hrv_data",
        "get_sleep_data",
        "get_sleep_summary",
        "get_stats",
        "get_stress_summary",
        "get_training_readiness",
        "get_training_status",
        "get_user_summary",
    }
)

HEALTH_TOOLS = CORE_TOOLS | frozenset(
    {
        # health_wellness
        "get_all_day_events",
        "get_all_day_stress",
        "get_blood_pressure",
        "get_body_battery",
        "get_body_battery_events",
        "get_body_composition",
        "get_daily_steps",
        "get_energy_balance",
        "get_floors",
        "get_heart_rates",
        "get_heart_rates_summary",
        "get_hydration_data",
        "get_lifestyle_logging_data",
        "get_morning_training_readiness",
        "get_recovery_time_remaining",
        "get_respiration_data",
        "get_respiration_summary",
        "get_rhr_day",
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
        "get_training_readiness",
        "get_user_summary",
        "get_weekly_intensity_minutes",
        "get_weekly_steps",
        "get_weekly_stress",
        # weight_management
        "add_weigh_in",
        "add_weigh_in_with_timestamps",
        "delete_weigh_ins",
        "get_daily_weigh_ins",
        "get_weigh_ins",
        # data_management
        "add_body_composition",
        "add_hydration_data",
        "set_blood_pressure",
        # womens_health
        "get_menstrual_calendar_data",
        "get_menstrual_data_for_date",
        "get_pregnancy_summary",
        # health metrics that live in the training module
        "get_fitnessage_data",
        "get_hrv_data",
        "get_hrv_trend",
        "get_respiration_trend",
    }
)

ACTIVITIES_TOOLS = CORE_TOOLS | frozenset(
    {
        # activity_management
        "count_activities",
        "create_manual_activity",
        "delete_activity",
        "get_activities",
        "get_activities_by_date",
        "get_activities_fordate",
        "get_activity",
        "get_activity_exercise_sets",
        "get_activity_gear",
        "get_activity_hr_in_timezones",
        "get_activity_power_in_timezones",
        "get_activity_split_summaries",
        "get_activity_splits",
        "get_activity_typed_splits",
        "get_activity_types",
        "get_activity_weather",
        "set_activity_description",
        "set_activity_event_type",
        "set_activity_exercise_sets",
        "set_activity_feel",
        "set_activity_name",
        "set_activity_type",
        "set_perceived_effort",
        # activity_analysis
        "download_activity_file",
        "get_activity_fit_data",
        "get_activity_fit_messages",
        "get_power_duration_curve",
        "set_fit_download_dir",
        # gear_management
        "add_gear_to_activity",
        "get_gear",
        "remove_gear_from_activity",
        # courses
        "delete_course",
        "download_course_gpx",
        "get_course_details",
        "get_courses",
        "upload_course",
    }
)

WORKOUTS_TOOLS = CORE_TOOLS | frozenset(
    {
        # workouts
        "delete_workout",
        "delete_workouts",
        "download_workout",
        "get_garmin_coach_workouts",
        "get_scheduled_workouts",
        "get_training_plan_workouts",
        "get_workout_by_id",
        "get_workouts",
        "list_strength_exercises",
        "match_strength_exercise",
        "resolve_strength_exercises",
        "schedule_workout",
        "schedule_workouts",
        "unschedule_workout",
        "unschedule_workouts",
        "upload_workout",
        "upload_workouts",
        # workout_builders
        "create_run_interval_workout",
        "create_run_workout",
        "create_strength_workout",
        "create_walk_run_workout",
        "create_z2_walk_workout",
        "schedule_week",
        # calendar_events
        "get_calendar_events",
    }
)

TRAINING_TOOLS = CORE_TOOLS | frozenset(
    {
        # training
        "get_acclimation",
        "get_cycling_ftp",
        "get_endurance_score",
        "get_fitnessage_data",
        "get_hill_score",
        "get_hrv_data",
        "get_hrv_trend",
        "get_lactate_threshold",
        "get_progress_summary_between_dates",
        "get_respiration_trend",
        "get_running_tolerance",
        "get_running_tolerance_trend",
        "get_training_effect",
        "get_training_load_balance",
        "get_training_load_trend",
        "get_training_status",
        "get_vo2max_trend",
        "request_reload",
        # challenges: records, predictions, goals, badges
        "get_adhoc_challenges",
        "get_available_badge_challenges",
        "get_badge_challenges",
        "get_earned_badges",
        "get_goals",
        "get_inprogress_virtual_challenges",
        "get_non_completed_badge_challenges",
        "get_personal_record",
        "get_race_predictions",
        # readiness and recovery from health_wellness
        "get_morning_training_readiness",
        "get_recovery_time_remaining",
        "get_training_readiness",
        "get_weekly_intensity_minutes",
        # the device Garmin computes training metrics from
        "get_primary_training_device",
        # power curve from activity_analysis
        "get_power_duration_curve",
    }
)

NUTRITION_TOOLS = CORE_TOOLS | frozenset(
    {
        # nutrition
        "create_custom_food",
        "delete_custom_food",
        "delete_food_log",
        "get_custom_food_serving_units",
        "get_custom_foods",
        "get_nutrition_daily_food_log",
        "get_nutrition_daily_meals",
        "get_nutrition_daily_settings",
        "get_nutrition_summary_between_dates",
        "log_custom_food",
        "log_food",
        "search_foods",
        "set_nutrition_daily_settings",
        "update_custom_food",
        "upsert_and_log",
        # hydration and energy balance from health_wellness / data_management
        "add_hydration_data",
        "get_energy_balance",
        "get_hydration_data",
    }
)

# None means "every tool", the default.
TOOL_PROFILES = {
    "all": None,
    "minimal": MINIMAL_TOOLS,
    "health": HEALTH_TOOLS,
    "activities": ACTIVITIES_TOOLS,
    "workouts": WORKOUTS_TOOLS,
    "training": TRAINING_TOOLS,
    "nutrition": NUTRITION_TOOLS,
}


def resolve_tool_profile(value):
    """Turn a GARMIN_TOOL_PROFILE value into the set of tool names it selects.

    Accepts one profile name or several separated by commas (their union),
    case-insensitively. Unset, blank, or "all" selects every tool and returns
    None. An unknown name raises ValueError that lists the known profiles; a
    value with separators but no names is rejected the same way
    GARMIN_ENABLED_TOOLS is.
    """
    if value is None or not value.strip():
        return None
    names = [name.strip().lower() for name in value.split(",") if name.strip()]
    if not names:
        raise ValueError("Invalid GARMIN_TOOL_PROFILE: expected at least one profile name")
    unknown = [name for name in names if name not in TOOL_PROFILES]
    if unknown:
        raise ValueError(
            "Unknown GARMIN_TOOL_PROFILE "
            + ", ".join(repr(name) for name in unknown)
            + ". Known profiles: "
            + ", ".join(sorted(TOOL_PROFILES))
        )
    if "all" in names:
        return None
    return frozenset().union(*(TOOL_PROFILES[name] for name in names))
