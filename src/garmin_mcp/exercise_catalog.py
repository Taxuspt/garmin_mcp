"""MCP tools: Garmin exercise catalog (valid categories + exercise names).

Lets the client discover which ExerciseCategory values and exercise names
Garmin accepts BEFORE creating a strength workout, instead of guessing a
category and risking a "400 - Invalid category" upload error.

Source: FIT profile (garmin-fit-sdk), generated into exercise_catalog_data.py
by scripts/generate_exercise_catalog.py (static data, no runtime dependency).
"""
import json

from garmin_mcp.exercise_catalog_data import CATALOG


def register_tools(app):
    @app.tool()
    async def list_exercise_categories() -> str:
        """List all valid exercise categories (Garmin ExerciseCategory) with the
        number of exercises per category. When creating a strength workout, use
        exactly one of these category names; the concrete exercise names come
        from list_exercises(category)."""
        data = [
            {"category": cat, "exercise_count": len(names)}
            for cat, names in sorted(CATALOG.items())
        ]
        return json.dumps(
            {
                "total_categories": len(CATALOG),
                "total_exercises": sum(len(v) for v in CATALOG.values()),
                "categories": data,
            },
            indent=2,
        )

    @app.tool()
    async def list_exercises(category: str = "") -> str:
        """List exercise names (Garmin ExerciseName) for one category. Without an
        argument the COMPLETE catalog (all categories -> exercise names) is
        returned. Valid category names come from list_exercise_categories
        (e.g. 'squat', 'bench_press', 'deadlift', 'lunge')."""
        if not category:
            return json.dumps(CATALOG, ensure_ascii=False)
        key = category.strip().lower()
        if key not in CATALOG:
            return json.dumps(
                {
                    "error": f"Unknown category '{category}'.",
                    "valid_categories": sorted(CATALOG.keys()),
                },
                indent=2,
            )
        return json.dumps({key: CATALOG[key]}, ensure_ascii=False, indent=2)

    return app
