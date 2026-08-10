"""Garmin's strength exercise catalog, read from the FIT profile at import time.

The catalog maps ExerciseCategory names to the ExerciseName values of that
category, both lowercase. It is the same data Garmin Connect validates
strength-workout steps against, taken from the official FIT profile that ships
with garmin-fit-sdk (published by Garmin International, Inc.).

Reading it from the SDK rather than embedding a copy keeps the exercise data on
Garmin's own distribution channel and picks up exercises added in later SDK
releases automatically. Import cost is a few milliseconds.
"""
from garmin_fit_sdk import Profile

# In the FIT profile every exercise category has its own "<category>_exercise_name"
# type whose values map the numeric FIT id to the exercise name string.
_SUFFIX = "_exercise_name"


def _build_catalog() -> dict[str, list[str]]:
    catalog: dict[str, list[str]] = {}

    for type_name, values in Profile["types"].items():
        if not type_name.endswith(_SUFFIX):
            continue
        category = type_name[: -len(_SUFFIX)]
        names = sorted(v for v in values.values() if isinstance(v, str))
        if names:
            catalog[category] = names

    return catalog


def _profile_version() -> str:
    v = Profile.get("version", {})
    parts = (v.get("major"), v.get("minor"), v.get("patch"))
    if any(p is None for p in parts):
        return "unknown"
    return ".".join(str(p) for p in parts)


CATALOG = _build_catalog()
FIT_PROFILE_VERSION = _profile_version()

# An empty catalog would make every exercise silently fall back to a free-text
# step instead of linking to Garmin's catalog entry. Fail loudly at import
# instead, so a renamed FIT profile structure surfaces here and not as workouts
# that upload but never link.
if not CATALOG:
    raise RuntimeError(
        "No exercise categories found in the FIT profile "
        f"(garmin-fit-sdk profile version {FIT_PROFILE_VERSION}). "
        f"Expected types named '<category>{_SUFFIX}'."
    )
