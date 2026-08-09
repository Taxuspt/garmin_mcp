#!/usr/bin/env python3
"""Regenerate src/garmin_mcp/exercise_catalog_data.py from the FIT SDK.

The catalog maps Garmin ExerciseCategory names to the ExerciseName values of
that category, both lowercase. It is the same data Garmin Connect validates
strength-workout steps against, taken from the official FIT profile that
ships with the garmin-fit-sdk package.

Usage (no install needed with uv):
    uv run --with garmin-fit-sdk scripts/generate_exercise_catalog.py

Re-run after a garmin-fit-sdk release to pick up newly added exercises.
"""
from pathlib import Path

from garmin_fit_sdk import Profile

OUT = Path(__file__).resolve().parent.parent / "src" / "garmin_mcp" / "exercise_catalog_data.py"

# In the FIT profile every exercise category has its own "<category>_exercise_name"
# type whose values are the exercise names of that category.
SUFFIX = "_exercise_name"


def main() -> None:
    types = Profile["types"]
    catalog: dict[str, list[str]] = {}

    for type_name, values in sorted(types.items()):
        if not type_name.endswith(SUFFIX):
            continue
        category = type_name[: -len(SUFFIX)]
        # values maps the numeric FIT id to the exercise name string.
        names = sorted(v for v in values.values() if isinstance(v, str))
        if names:
            catalog[category] = names

    total = sum(len(v) for v in catalog.values())
    with OUT.open("w") as f:
        f.write(
            "# Auto-generated from garmin-fit-sdk (FIT profile) by\n"
            "# scripts/generate_exercise_catalog.py - do not edit by hand.\n"
            f"# {len(catalog)} categories, {total} exercises.\n"
        )
        f.write(f"CATALOG = {catalog!r}\n")

    print(f"Wrote {OUT.name}: {len(catalog)} categories, {total} exercises")


if __name__ == "__main__":
    main()
