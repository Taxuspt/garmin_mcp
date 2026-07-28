"""Structured strength-activity tools for Garmin Connect.

Garmin stores completed strength sets separately from planned workouts.  This
module owns the validation, exercise matching, timestamp handling, API payload
construction, and read-after-write verification for that activity endpoint.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from garminconnect import exercises as garmin_exercises


garmin_client = None

_IDENTIFIER_RE = re.compile(r"^[A-Z0-9_]+$")

# Conservative matching thresholds favor rejecting near-collisions over guessing.
_MIN_EXERCISE_MATCH_SCORE = 0.82
_MIN_EXERCISE_MATCH_MARGIN = 0.06
_READBACK_RETRY_DELAY_SECONDS = 1.0

ExerciseCatalogEntry = Tuple[str, str, str]
ExerciseCatalog = Tuple[ExerciseCatalogEntry, ...]


class StrengthTrainingError(ValueError):
    """Raised when a structured strength request is unsafe or invalid."""


def configure(client) -> None:
    """Configure the module with the shared Garmin client."""
    global garmin_client
    garmin_client = client


def _json_result(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _normalise_label(value: str) -> str:
    """Return a comparison label independent of punctuation and accents."""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.lower().replace("_", " ").replace("-", " ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _normalise_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StrengthTrainingError(f"{field} must be a non-empty string")
    identifier = value.strip().upper().replace(" ", "_").replace("-", "_")
    if not _IDENTIFIER_RE.fullmatch(identifier):
        raise StrengthTrainingError(
            f"{field} must contain only letters, digits, and underscores"
        )
    return identifier


def _catalog_entries_from_rows(rows: Any) -> ExerciseCatalog:
    """Validate the catalog shape bundled with python-garminconnect."""
    if not isinstance(rows, list):
        raise StrengthTrainingError("Bundled Garmin exercise catalog is not a list")

    entries = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            category = _normalise_identifier(
                row.get("category"), "catalog category"
            )
            exercise_name = _normalise_identifier(
                row.get("exercise"), "catalog exercise name"
            )
        except StrengthTrainingError:
            continue
        display_name = row.get("name")
        if not isinstance(display_name, str) or not display_name.strip():
            display_name = exercise_name.replace("_", " ").title()
        entries.add((category, exercise_name, display_name.strip()))

    if not entries:
        raise StrengthTrainingError("Bundled Garmin exercise catalog is empty")
    return tuple(sorted(entries))


@lru_cache(maxsize=1)
def _load_garmin_exercise_catalog() -> ExerciseCatalog:
    """Load the same bundled catalog exposed by get_exercise_types."""
    return _catalog_entries_from_rows(garmin_exercises.EXERCISES)


def _match_score(query: str, candidate: str) -> float:
    query_tokens = set(query.split())
    candidate_tokens = set(candidate.split())
    if not query_tokens or not candidate_tokens:
        return 0.0
    shared = len(query_tokens & candidate_tokens)
    if shared == 0:
        return 0.0
    token_f1 = (2.0 * shared) / (len(query_tokens) + len(candidate_tokens))
    ordered_similarity = SequenceMatcher(None, query, candidate).ratio()
    return (0.65 * ordered_similarity) + (0.35 * token_f1)


def _exercise_suggestions(
    query: str,
    catalog: Iterable[ExerciseCatalogEntry],
    limit: int = 5,
) -> List[Dict[str, Any]]:
    scored = []
    for category, name, display_name in catalog:
        display_name_label = _normalise_label(display_name)
        score = _match_score(query, display_name_label)
        if score > 0:
            scored.append((score, category, name, display_name))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [
        {
            "category": category,
            "name": name,
            "exercise_name": name,
            "display_name": display_name,
            "score": round(score, 3),
        }
        for score, category, name, display_name in scored[:limit]
    ]


def _resolve_exercise_query(
    value: Any,
    catalog_loader: Optional[Callable[[], ExerciseCatalog]] = None,
) -> Dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        raise StrengthTrainingError("exercise must be a non-empty string")

    query = _normalise_label(value)
    catalog = (catalog_loader or _load_garmin_exercise_catalog)()
    exact = []
    for category, name, display_name in catalog:
        if query == _normalise_label(display_name):
            exact.append((category, name, display_name))

    if len(exact) == 1:
        category, name, display_name = exact[0]
        return {
            "input": value,
            "category": category,
            "name": name,
            "exercise_name": name,
            "display_name": display_name,
            "match_type": "catalog_exact",
            "score": 1.0,
        }
    if len(exact) > 1:
        options = ", ".join(
            f"{category}/{name}" for category, name, _ in exact[:8]
        )
        raise StrengthTrainingError(
            f"Exercise '{value}' is ambiguous. Use category/exercise_name. "
            f"Matches: {options}"
        )

    suggestions = _exercise_suggestions(query, catalog)
    if not suggestions:
        raise StrengthTrainingError(
            f"No Garmin exercise matches '{value}'. "
            "Use exact category/exercise_name identifiers."
        )

    best = suggestions[0]
    runner_up_score = suggestions[1]["score"] if len(suggestions) > 1 else 0.0
    if (
        best["score"] < _MIN_EXERCISE_MATCH_SCORE
        or best["score"] - runner_up_score < _MIN_EXERCISE_MATCH_MARGIN
    ):
        options = ", ".join(
            f"{item['category']}/{item['name']} ({item['score']:.3f})"
            for item in suggestions
        )
        raise StrengthTrainingError(
            f"No confident Garmin exercise match for '{value}'. "
            f"Closest candidates: {options}"
        )

    return {
        "input": value,
        "category": best["category"],
        "name": best["name"],
        "exercise_name": best["name"],
        "display_name": best["display_name"],
        "match_type": "catalog_fuzzy",
        "score": best["score"],
    }


def _resolve_exercise_spec(
    item: Dict[str, Any],
    index: int,
    catalog_loader: Optional[Callable[[], ExerciseCatalog]] = None,
) -> Dict[str, Any]:
    exercise = item.get("exercise")
    if exercise not in (None, ""):
        return _resolve_exercise_query(exercise, catalog_loader)

    provided_name = item.get("name")
    provided_exercise_name = item.get("exercise_name")
    if (
        provided_name not in (None, "")
        and provided_exercise_name not in (None, "")
        and _normalise_identifier(
            provided_name, f"sets[{index}].name"
        )
        != _normalise_identifier(
            provided_exercise_name, f"sets[{index}].exercise_name"
        )
    ):
        raise StrengthTrainingError(
            f"sets[{index}].name and exercise_name must identify the same exercise"
        )
    raw_name = (
        provided_exercise_name
        if provided_exercise_name not in (None, "")
        else provided_name
    )
    if item.get("category") in (None, "") or raw_name in (None, ""):
        raise StrengthTrainingError(
            f"sets[{index}] must include exercise or exact "
            "category/exercise_name identifiers"
        )

    category = _normalise_identifier(item["category"], f"sets[{index}].category")
    name = _normalise_identifier(raw_name, f"sets[{index}].exercise_name")

    catalog = (catalog_loader or _load_garmin_exercise_catalog)()
    display_name = next(
        (
            catalog_display_name
            for catalog_category, catalog_name, catalog_display_name in catalog
            if (catalog_category, catalog_name) == (category, name)
        ),
        None,
    )
    if display_name is None:
        category_entries = [
            entry for entry in catalog if entry[0] == category
        ]
        nearby = _exercise_suggestions(
            _normalise_label(name),
            category_entries,
        )
        options = ", ".join(suggestion["name"] for suggestion in nearby) or "none"
        raise StrengthTrainingError(
            f"sets[{index}] contains unknown Garmin exercise "
            f"{category}/{name}. Closest names in that category: {options}"
        )

    return {
        "input": f"{category}/{name}",
        "category": category,
        "name": name,
        "exercise_name": name,
        "display_name": display_name,
        "match_type": "provided",
        "score": 1.0,
    }


def _finite_number(
    value: Any,
    field: str,
    *,
    minimum: Optional[float] = None,
    allow_none: bool = False,
) -> Optional[float]:
    if value in (None, "") and allow_none:
        return None
    if isinstance(value, bool):
        raise StrengthTrainingError(f"{field} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StrengthTrainingError(f"{field} must be a number") from exc
    if not math.isfinite(number):
        raise StrengthTrainingError(f"{field} must be finite")
    if minimum is not None and number < minimum:
        raise StrengthTrainingError(f"{field} must be at least {minimum:g}")
    return number


def _integer_value(value: Any, field: str, requirement: str) -> int:
    error = f"{field} must be a {requirement}"
    if isinstance(value, bool):
        raise StrengthTrainingError(error)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return int(value)
        raise StrengthTrainingError(error)
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value)
    raise StrengthTrainingError(error)


def _positive_integer(value: Any, field: str, default: int = 1) -> int:
    if value in (None, ""):
        return default
    number = _integer_value(value, field, "positive integer")
    if number <= 0:
        raise StrengthTrainingError(f"{field} must be a positive integer")
    return number


def _non_negative_integer(
    value: Any,
    field: str,
    *,
    allow_none: bool = True,
) -> Optional[int]:
    if value in (None, "") and allow_none:
        return None
    number = _integer_value(value, field, "non-negative integer")
    if number < 0:
        raise StrengthTrainingError(f"{field} must be a non-negative integer")
    return number


def _zone_info(time_zone: str) -> ZoneInfo:
    try:
        return ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise StrengthTrainingError(f"Unknown IANA time zone '{time_zone}'") from exc


def _parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO timestamp using the Python 3.12+ runtime contract."""
    return datetime.fromisoformat(value.strip())


def _parse_local_datetime(value: Any, time_zone: str, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise StrengthTrainingError(f"{field} must be a non-empty ISO date-time")
    zone = _zone_info(time_zone)
    try:
        parsed = _parse_iso_datetime(value)
    except ValueError as exc:
        raise StrengthTrainingError(f"{field} must be an ISO date-time") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _parse_gmt_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise StrengthTrainingError(f"{field} is missing")
    try:
        parsed = _parse_iso_datetime(value)
    except ValueError as exc:
        raise StrengthTrainingError(f"{field} is not an ISO date-time") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_set_start_time(value: Any, activity_start: datetime) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise StrengthTrainingError("start_time must be a non-empty string")
    raw = value.strip()

    clock_match = re.fullmatch(
        r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?",
        raw,
    )
    if clock_match:
        hour = int(clock_match.group("hour"))
        minute = int(clock_match.group("minute"))
        second = int(clock_match.group("second") or 0)
        if hour > 23 or minute > 59 or second > 59:
            raise StrengthTrainingError(
                "start_time clock must be HH:MM or HH:MM:SS"
            )
        return activity_start.replace(
            hour=hour,
            minute=minute,
            second=second,
            microsecond=0,
        )

    try:
        parsed = _parse_iso_datetime(raw)
    except ValueError as exc:
        raise StrengthTrainingError(
            "start_time must be ISO date-time, HH:MM, or HH:MM:SS"
        ) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=activity_start.tzinfo)
    return parsed.astimezone(activity_start.tzinfo)


def _garmin_utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0")


def _garmin_local_timestamp(value: datetime) -> str:
    return value.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S.000")


def _activity_type_key(activity: Dict[str, Any]) -> Optional[str]:
    for key in ("activityTypeDTO", "activityType"):
        value = activity.get(key)
        if isinstance(value, dict) and value.get("typeKey"):
            return str(value["typeKey"])
    return None


def _activity_time_zone(
    activity: Dict[str, Any],
    provided_time_zone: Optional[str],
) -> str:
    if provided_time_zone is not None:
        return provided_time_zone

    summary = activity.get("summaryDTO")
    containers = [activity]
    if isinstance(summary, dict):
        containers.append(summary)
    for container in containers:
        time_zone_dto = container.get("timeZoneUnitDTO")
        if not isinstance(time_zone_dto, dict):
            continue
        unit_key = time_zone_dto.get("unitKey")
        if isinstance(unit_key, str) and unit_key.strip():
            return unit_key.strip()
    return "UTC"


def _activity_start(
    activity: Dict[str, Any],
    provided_start: Optional[str],
    time_zone: str,
) -> datetime:
    if provided_start:
        return _parse_local_datetime(
            provided_start, time_zone, "activity_start_datetime"
        )

    summary = activity.get("summaryDTO")
    if not isinstance(summary, dict):
        summary = {}

    gmt_value = summary.get("startTimeGMT") or activity.get("startTimeGMT")
    if gmt_value:
        return _parse_gmt_datetime(
            gmt_value, "activity startTimeGMT"
        ).astimezone(_zone_info(time_zone))

    local_value = summary.get("startTimeLocal") or activity.get("startTimeLocal")
    if local_value:
        return _parse_local_datetime(
            str(local_value).replace(" ", "T"),
            time_zone,
            "activity startTimeLocal",
        )

    raise StrengthTrainingError(
        "Activity start time is unavailable. Provide activity_start_datetime."
    )


def _completed_activity_exercise_name(match: Dict[str, Any]) -> Optional[str]:
    """Translate a workout-catalog match to the activity endpoint encoding."""
    if match["name"] == match["category"]:
        return None
    return match["name"]


def _prepare_strength_sets(
    set_specs: List[Dict[str, Any]],
    activity_start: datetime,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not isinstance(set_specs, list) or not set_specs:
        raise StrengthTrainingError("sets must be a non-empty list")
    if activity_start.tzinfo is None:
        raise StrengthTrainingError("activity_start must include a time zone")

    exercise_sets: List[Dict[str, Any]] = []
    matches: List[Dict[str, Any]] = []
    activity_start_utc = activity_start.astimezone(timezone.utc)
    cursor = activity_start_utc
    previous_end: Optional[datetime] = None
    catalog_value: Optional[ExerciseCatalog] = None

    def load_catalog_once() -> ExerciseCatalog:
        nonlocal catalog_value
        if catalog_value is None:
            catalog_value = _load_garmin_exercise_catalog()
        return catalog_value

    for index, raw_item in enumerate(set_specs):
        if not isinstance(raw_item, dict):
            raise StrengthTrainingError(f"sets[{index}] must be an object")
        item = dict(raw_item)

        raw_set_type = item.get("set_type")
        set_type = (
            "ACTIVE"
            if raw_set_type in (None, "")
            else str(raw_set_type).strip().upper()
        )
        if set_type not in {"ACTIVE", "REST"}:
            raise StrengthTrainingError(
                f"sets[{index}].set_type must be ACTIVE or REST"
            )

        repeat_count = _positive_integer(
            item.get("sets"), f"sets[{index}].sets"
        )
        repetitions = _non_negative_integer(
            item.get("repetitions"),
            f"sets[{index}].repetitions",
        )
        weight_kg = _finite_number(
            item.get("weight_kg"),
            f"sets[{index}].weight_kg",
            minimum=0,
            allow_none=True,
        )
        default_duration = 90.0 if set_type == "REST" else 30.0
        raw_duration = item.get("duration_seconds")
        duration_seconds = _finite_number(
            default_duration if raw_duration in (None, "") else raw_duration,
            f"sets[{index}].duration_seconds",
            minimum=0.001,
        )
        default_rest = 0.0 if set_type == "REST" else 90.0
        raw_rest = item.get("rest_seconds")
        rest_seconds = _finite_number(
            default_rest if raw_rest in (None, "") else raw_rest,
            f"sets[{index}].rest_seconds",
            minimum=0,
        )

        if set_type == "ACTIVE":
            match = _resolve_exercise_spec(item, index, load_catalog_once)
            matches.append({**match, "input_index": index})
        else:
            if any(
                item.get(field) not in (None, "")
                for field in ("exercise", "category", "name", "exercise_name")
            ):
                raise StrengthTrainingError(
                    f"sets[{index}] is REST and must not specify an exercise"
                )
            if repetitions is not None or weight_kg is not None:
                raise StrengthTrainingError(
                    f"sets[{index}] is REST and must not specify repetitions or weight_kg"
                )
            match = None

        if (
            item.get("offset_seconds") not in (None, "")
            and item.get("offset_minutes") not in (None, "")
        ):
            raise StrengthTrainingError(
                f"sets[{index}] cannot specify both offset_seconds and offset_minutes"
            )
        if item.get("start_time") not in (None, "") and any(
            item.get(field) not in (None, "")
            for field in ("offset_seconds", "offset_minutes")
        ):
            raise StrengthTrainingError(
                f"sets[{index}] cannot combine start_time with an offset"
            )

        if item.get("start_time") not in (None, ""):
            first_start = _parse_set_start_time(
                item["start_time"], activity_start
            ).astimezone(timezone.utc)
        elif item.get("offset_seconds") not in (None, ""):
            offset_seconds = _finite_number(
                item["offset_seconds"],
                f"sets[{index}].offset_seconds",
                minimum=0,
            )
            first_start = activity_start_utc + timedelta(seconds=offset_seconds)
        elif item.get("offset_minutes") not in (None, ""):
            offset_minutes = _finite_number(
                item["offset_minutes"],
                f"sets[{index}].offset_minutes",
                minimum=0,
            )
            first_start = activity_start_utc + timedelta(minutes=offset_minutes)
        else:
            first_start = cursor

        for repeat_index in range(repeat_count):
            set_start = first_start + timedelta(
                seconds=repeat_index * (duration_seconds + rest_seconds)
            )
            if set_start < activity_start_utc:
                raise StrengthTrainingError(
                    f"sets[{index}] starts before the activity"
                )
            if previous_end is not None and set_start < previous_end:
                raise StrengthTrainingError(
                    f"sets[{index}] overlaps or precedes the previous set"
                )

            if set_type == "ACTIVE":
                # The workout-editor catalog represents category-only entries
                # by repeating the category as the exercise identifier. The
                # completed-activity endpoint uses a null sub-category for the
                # same selection and rejects the repeated value with HTTP 400.
                exercises = [
                    {
                        "category": match["category"],
                        "name": _completed_activity_exercise_name(match),
                        "probability": 100.0,
                    }
                ]
                internal_weight = (
                    float(weight_kg) * 1000.0
                    if weight_kg is not None and weight_kg > 0
                    else -1.0
                )
            else:
                exercises = []
                internal_weight = None

            exercise_sets.append(
                {
                    "exercises": exercises,
                    "duration": float(duration_seconds),
                    "repetitionCount": (
                        repetitions if set_type == "ACTIVE" else None
                    ),
                    "weight": internal_weight,
                    "setType": set_type,
                    "startTime": _garmin_utc_timestamp(set_start),
                    "wktStepIndex": None,
                    "messageIndex": None,
                }
            )
            cursor = max(
                cursor,
                set_start + timedelta(seconds=duration_seconds + rest_seconds),
            )
            previous_end = set_start + timedelta(seconds=duration_seconds)

    return exercise_sets, matches


def _validate_sets_within_activity(
    activity: Dict[str, Any],
    activity_start: datetime,
    exercise_sets: List[Dict[str, Any]],
) -> None:
    summary = activity.get("summaryDTO")
    if not isinstance(summary, dict):
        summary = {}
    raw_duration = summary.get("duration")
    if raw_duration in (None, ""):
        raw_duration = activity.get("duration")
    if raw_duration in (None, ""):
        return
    duration = _finite_number(
        raw_duration,
        "activity duration",
        minimum=0.001,
    )
    activity_end = activity_start.astimezone(timezone.utc) + timedelta(
        seconds=duration
    )
    for index, item in enumerate(exercise_sets):
        set_start = _parse_gmt_datetime(
            item.get("startTime"),
            f"exerciseSets[{index}].startTime",
        )
        set_end = set_start + timedelta(seconds=float(item["duration"]))
        if set_end > activity_end + timedelta(seconds=1):
            overrun = (set_end - activity_end).total_seconds()
            raise StrengthTrainingError(
                f"exerciseSets[{index}] ends {overrun:.1f}s after the activity. "
                "Adjust set timing/rests or the activity duration."
            )


def _display_exercise_sets(exercise_sets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    displayed = []
    for item in exercise_sets:
        converted = dict(item)
        internal_weight = converted.pop("weight", None)
        if converted.get("setType") == "ACTIVE":
            converted["weightKg"] = (
                internal_weight / 1000.0
                if isinstance(internal_weight, (int, float))
                and internal_weight > 0
                else None
            )
        displayed.append(converted)
    return displayed


def _extract_readback_sets(value: Any) -> Optional[List[Dict[str, Any]]]:
    if isinstance(value, dict):
        sets = value.get("exerciseSets")
        if isinstance(sets, list):
            return sets
        if "exerciseSets" in value and sets is None:
            return []
    if isinstance(value, list):
        return value
    return None


def _display_readback_sets(value: Any) -> Optional[List[Dict[str, Any]]]:
    exercise_sets = _extract_readback_sets(value)
    if exercise_sets is None:
        return None
    return _display_exercise_sets(exercise_sets)


def _numbers_equal(left: Any, right: Any, tolerance: float = 0.01) -> bool:
    if left is None or right is None:
        return left is None and right is None
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def _weight_key(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.inf
    return None if number <= 0 else round(number, 3)


def _weights_equal(left: Any, right: Any, tolerance_grams: float = 1.0) -> bool:
    left_key = _weight_key(left)
    right_key = _weight_key(right)
    if left_key is None or right_key is None:
        return left_key is None and right_key is None
    return _numbers_equal(left_key, right_key, tolerance=tolerance_grams)


def _repetition_key(value: Any) -> Any:
    """Treat Garmin's zero and null encodings as the same absent count."""
    if value is None:
        return None
    try:
        return None if float(value) == 0 else value
    except (TypeError, ValueError):
        return value


def _timestamp_key(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = _parse_iso_datetime(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _verify_exercise_sets(
    expected: List[Dict[str, Any]],
    readback: Any,
) -> Tuple[bool, List[str]]:
    actual = _extract_readback_sets(readback)
    if actual is None:
        return False, ["read-back response does not contain exerciseSets"]
    if len(actual) != len(expected):
        return False, [
            f"set count differs: expected {len(expected)}, got {len(actual)}"
        ]

    differences = []
    for index, (wanted, received) in enumerate(zip(expected, actual)):
        prefix = f"exerciseSets[{index}]"
        if not isinstance(received, dict):
            differences.append(f"{prefix} is not an object")
            continue
        if received.get("setType") != wanted.get("setType"):
            differences.append(
                f"{prefix}.setType expected {wanted.get('setType')}, "
                f"got {received.get('setType')}"
            )
        if not _numbers_equal(received.get("duration"), wanted.get("duration")):
            differences.append(f"{prefix}.duration differs")
        if _repetition_key(received.get("repetitionCount")) != _repetition_key(
            wanted.get("repetitionCount")
        ):
            differences.append(f"{prefix}.repetitionCount differs")
        if not _weights_equal(received.get("weight"), wanted.get("weight")):
            differences.append(f"{prefix}.weight differs")
        if _timestamp_key(received.get("startTime")) != _timestamp_key(
            wanted.get("startTime")
        ):
            differences.append(f"{prefix}.startTime differs")

        wanted_exercises = wanted.get("exercises") or []
        received_exercises = received.get("exercises") or []
        if wanted_exercises:
            wanted_pair = (
                wanted_exercises[0].get("category"),
                wanted_exercises[0].get("name"),
            )
            received_pairs = {
                (item.get("category"), item.get("name"))
                for item in received_exercises
                if isinstance(item, dict)
            }
            if wanted_pair not in received_pairs:
                differences.append(
                    f"{prefix}.exercises does not contain "
                    f"{wanted_pair[0]}/{wanted_pair[1]}"
                )
        elif received_exercises:
            differences.append(f"{prefix}.exercises expected an empty list")

    return not differences, differences


def _response_summary(response: Any) -> Any:
    if response is None or isinstance(
        response, (str, int, float, bool, dict, list)
    ):
        return response
    status_code = getattr(response, "status_code", None)
    return {"status_code": status_code} if status_code is not None else str(response)


def _find_activity_id(value: Any) -> Optional[int]:
    """Read the positive top-level activityId returned by Garmin."""
    if not isinstance(value, dict):
        return None
    for key in ("activityId", "activity_id"):
        candidate = value.get(key)
        if isinstance(candidate, bool):
            continue
        try:
            parsed = int(candidate)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _replace_activity_strength_sets(
    activity_id: int,
    exercise_sets: List[Dict[str, Any]],
) -> Any:
    payload = {"activityId": activity_id, "exerciseSets": exercise_sets}
    return garmin_client.set_activity_exercise_sets(activity_id, payload)


async def _read_and_verify_strength_sets(
    activity_id: int,
    expected_sets: List[Dict[str, Any]],
) -> Tuple[Any, bool, List[str]]:
    """Retry one stale read after a short delay, then return verification."""
    readback = garmin_client.get_activity_exercise_sets(activity_id)
    verified, differences = _verify_exercise_sets(expected_sets, readback)
    if verified:
        return readback, verified, differences

    await asyncio.sleep(_READBACK_RETRY_DELAY_SECONDS)
    readback = garmin_client.get_activity_exercise_sets(activity_id)
    verified, differences = _verify_exercise_sets(expected_sets, readback)
    return readback, verified, differences


def _validate_activity_id(activity_id: Union[int, str]) -> int:
    if isinstance(activity_id, bool):
        raise StrengthTrainingError("activity_id must be a positive integer")
    try:
        parsed = int(activity_id)
    except (TypeError, ValueError) as exc:
        raise StrengthTrainingError("activity_id must be a positive integer") from exc
    if parsed <= 0:
        raise StrengthTrainingError("activity_id must be a positive integer")
    return parsed


def register_tools(app):
    """Register structured strength-activity tools."""

    @app.tool()
    async def set_activity_strength_exercise_sets(
        activity_id: Union[int, str],
        sets: List[Dict[str, Any]],
        activity_start_datetime: Optional[str] = None,
        time_zone: Optional[str] = None,
        confirm: bool = False,
        dry_run: bool = False,
        rollback_on_failure: bool = True,
    ) -> str:
        """Replace all structured sets on a completed Garmin strength activity.

        This is a full replacement, not a partial patch. Use dry_run=true first
        to validate exercise matching, weights, repetitions, and timestamps
        without writing. A real update requires confirm=true and is verified by
        reading the saved sets back from Garmin. Before writing, the existing
        sets are saved and restored by default if the write cannot be verified.
        The activity start is normally read from Garmin;
        activity_start_datetime is only an explicit override.

        Each item in sets may contain:
          - exercise: an English display name matched against Garmin's bundled
            catalog; or
          - category and exercise_name: exact identifiers returned by
            get_exercise_types (`name` is accepted as an activity-API alias)
          - sets: repeat count within this item (default 1); distinct from the
            top-level sets list
          - repetitions: non-negative repetition count
          - weight_kg: external load in kilograms; omit for bodyweight
          - duration_seconds: set duration (default 30; REST default 90)
          - rest_seconds: spacing before the next automatic set (default 90)
          - set_type: ACTIVE or REST (default ACTIVE)
          - start_time: ISO date-time, HH:MM, or HH:MM:SS; clock-only values
            use the activity's start date, so use full ISO after midnight
          - offset_seconds or offset_minutes: offset from activity start

        The same English catalog bundled with garminconnect and exposed by
        get_exercise_types is used for name matching. Localized aliases are
        intentionally not maintained; callers should translate localized input
        to English or pass exact category/exercise_name identifiers. Ambiguous
        matches are rejected instead of guessed.

        Args:
            activity_id: Existing Garmin strength activity ID
            sets: Complete replacement list of strength set specifications
            activity_start_datetime: Optional ISO start time used for offsets
            time_zone: Optional IANA override for naive/local times; otherwise
                derived from the activity, falling back to UTC
            confirm: Must be true for a write
            dry_run: Validate and preview without changing Garmin
            rollback_on_failure: Restore the previous sets after a failed write
        """
        parsed_activity_id: Optional[int] = None
        try:
            parsed_activity_id = _validate_activity_id(activity_id)
            if not dry_run and not confirm:
                raise StrengthTrainingError(
                    "confirm=true is required because this replaces all "
                    "exercise sets on the activity"
                )

            activity = garmin_client.get_activity(parsed_activity_id)
            if not isinstance(activity, dict) or not activity:
                raise StrengthTrainingError(
                    f"Activity {parsed_activity_id} was not found"
                )
            type_key = _activity_type_key(activity)
            if type_key != "strength_training":
                raise StrengthTrainingError(
                    f"Activity {parsed_activity_id} has type "
                    f"'{type_key or 'unknown'}', "
                    "not 'strength_training'"
                )

            resolved_time_zone = _activity_time_zone(activity, time_zone)
            start = _activity_start(
                activity,
                activity_start_datetime,
                resolved_time_zone,
            )
            exercise_sets, matches = _prepare_strength_sets(sets, start)
            _validate_sets_within_activity(activity, start, exercise_sets)
            preview = {
                "activityId": parsed_activity_id,
                "timeZone": resolved_time_zone,
                "exerciseSets": _display_exercise_sets(exercise_sets),
            }

            if dry_run:
                return _json_result(
                    {
                        "success": True,
                        "dry_run": True,
                        "activity_id": parsed_activity_id,
                        "replacement_set_count": len(exercise_sets),
                        "matches": matches,
                        "preview": preview,
                        "warning": (
                            "No changes were made. A confirmed call replaces "
                            "the activity's complete exercise-set list."
                        ),
                    }
                )

            previous_readback = garmin_client.get_activity_exercise_sets(
                parsed_activity_id
            )
            previous_sets = _extract_readback_sets(previous_readback)
            if previous_sets is None:
                raise StrengthTrainingError(
                    "Could not save the activity's current exercise sets; "
                    "the replacement was not attempted"
                )

            write_completed = False
            response: Any = None
            readback: Any = None
            try:
                response = _replace_activity_strength_sets(
                    parsed_activity_id,
                    exercise_sets,
                )
                write_completed = True
                (
                    readback,
                    verified,
                    differences,
                ) = await _read_and_verify_strength_sets(
                    parsed_activity_id,
                    exercise_sets,
                )
                if verified:
                    return _json_result(
                        {
                            "success": True,
                            "written": True,
                            "verified": True,
                            "activity_id": parsed_activity_id,
                            "replacement_set_count": len(exercise_sets),
                            "matches": matches,
                            "api_response": _response_summary(response),
                            "exercise_sets": _display_readback_sets(readback),
                        }
                    )
                failure = {
                    "success": False,
                    "written": True,
                    "verified": False,
                    "activity_id": parsed_activity_id,
                    "replacement_set_count": len(exercise_sets),
                    "matches": matches,
                    "api_response": _response_summary(response),
                    "exercise_sets": _display_readback_sets(readback),
                    "verification_errors": differences,
                    "error": (
                        "Garmin accepted the update, but read-back "
                        "verification failed"
                    ),
                }
            except Exception as update_error:
                failure = {
                    "success": False,
                    "written": write_completed,
                    "verified": False,
                    "activity_id": parsed_activity_id,
                    "replacement_set_count": len(exercise_sets),
                    "matches": matches,
                    "api_response": _response_summary(response),
                    "exercise_sets": _display_readback_sets(readback),
                    "error": str(update_error),
                }
                if not write_completed:
                    failure["write_status"] = (
                        "unknown because the replacement request raised an error"
                    )

            failure["previous_sets"] = _display_exercise_sets(previous_sets)
            if rollback_on_failure:
                try:
                    rollback_response = _replace_activity_strength_sets(
                        parsed_activity_id,
                        previous_sets,
                    )
                    (
                        rollback_readback,
                        restored,
                        rollback_differences,
                    ) = await _read_and_verify_strength_sets(
                        parsed_activity_id,
                        previous_sets,
                    )
                    failure["rolled_back"] = restored
                    failure["rollback_api_response"] = _response_summary(
                        rollback_response
                    )
                    failure["rollback_exercise_sets"] = (
                        _display_readback_sets(rollback_readback)
                    )
                    if rollback_differences:
                        failure["rollback_verification_errors"] = (
                            rollback_differences
                        )
                except Exception as rollback_error:
                    failure["rolled_back"] = False
                    failure["rollback_error"] = str(rollback_error)
            else:
                failure["rolled_back"] = False
            return _json_result(failure)
        except Exception as exc:
            return _json_result(
                {
                    "success": False,
                    "activity_id": (
                        parsed_activity_id
                        if parsed_activity_id is not None
                        else activity_id
                    ),
                    "error": str(exc),
                }
            )

    @app.tool()
    async def create_strength_training_activity(
        activity_name: str,
        start_datetime: str,
        time_zone: str,
        duration_minutes: float,
        sets: List[Dict[str, Any]],
        confirm: bool = False,
        dry_run: bool = False,
        rollback_on_failure: bool = True,
    ) -> str:
        """Create a completed manual strength activity with structured sets.

        This creates an activity record, not a planned workout for a watch.
        Use dry_run=true first to validate the exercise matches and complete
        timeline without changing Garmin. A real create requires confirm=true.
        start_datetime is required because a new activity has no stored start
        time to derive from Garmin.

        The activity is created as private `strength_training`, then its full
        exercise-set list is written and read back for verification. If the set
        write or verification fails, rollback_on_failure=true removes the new
        empty/incomplete activity.

        Set fields are identical to set_activity_strength_exercise_sets:
        exercise or exact category/exercise_name, sets, repetitions,
        weight_kg, duration_seconds, rest_seconds, set_type, start_time, and
        offsets. `name` is accepted as an alias for exercise_name.
        Clock-only start_time values use the new activity's start date; sets
        after midnight require a full ISO date-time with the following date.

        Args:
            activity_name: Name shown in Garmin Connect
            start_datetime: ISO local or timezone-aware activity start
            time_zone: IANA time zone, e.g. Europe/Warsaw
            duration_minutes: Positive activity duration in minutes
            sets: Complete structured set specification
            confirm: Must be true for a real create
            dry_run: Validate and preview without changing Garmin
            rollback_on_failure: Delete a newly created incomplete activity
        """
        created_activity_id: Optional[int] = None
        activity_response: Any = None
        create_request_started = False
        matches: List[Dict[str, Any]] = []
        try:
            if not isinstance(activity_name, str):
                raise StrengthTrainingError(
                    "activity_name must be a non-empty string"
                )
            name = activity_name.strip()
            if not name:
                raise StrengthTrainingError(
                    "activity_name must be a non-empty string"
                )
            duration = _finite_number(
                duration_minutes,
                "duration_minutes",
                minimum=0.001,
            )
            if not dry_run and not confirm:
                raise StrengthTrainingError(
                    "confirm=true is required to create a Garmin activity"
                )

            local_start = _parse_local_datetime(
                start_datetime,
                time_zone,
                "start_datetime",
            )
            exercise_sets, matches = _prepare_strength_sets(sets, local_start)
            _validate_sets_within_activity(
                {"summaryDTO": {"duration": duration * 60.0}},
                local_start,
                exercise_sets,
            )
            preview = {
                "activityName": name,
                "activityType": "strength_training",
                "startTimeLocal": _garmin_local_timestamp(local_start),
                "timeZone": time_zone,
                "durationMinutes": duration,
                "exerciseSets": _display_exercise_sets(exercise_sets),
            }

            if dry_run:
                return _json_result(
                    {
                        "success": True,
                        "dry_run": True,
                        "set_count": len(exercise_sets),
                        "matches": matches,
                        "preview": preview,
                        "warning": "No activity was created.",
                    }
                )

            create_request_started = True
            activity_response = garmin_client.create_manual_activity(
                start_datetime=_garmin_local_timestamp(local_start),
                time_zone=time_zone,
                type_key="strength_training",
                distance_km=0.0,
                duration_min=duration,
                activity_name=name,
            )
            created_activity_id = _find_activity_id(activity_response)
            if created_activity_id is None:
                raise StrengthTrainingError(
                    "Garmin created an activity response without an activityId; "
                    "structured sets could not be attached"
                )

            try:
                write_response = _replace_activity_strength_sets(
                    created_activity_id,
                    exercise_sets,
                )
                (
                    readback,
                    verified,
                    differences,
                ) = await _read_and_verify_strength_sets(
                    created_activity_id,
                    exercise_sets,
                )
                if not verified:
                    details = "; ".join(differences)
                    raise StrengthTrainingError(
                        "Read-back verification failed after creating the "
                        f"activity: {details}"
                    )
            except Exception as attach_error:
                failure = {
                    "success": False,
                    "activity_created": True,
                    "activity_id": created_activity_id,
                    "error": str(attach_error),
                    "matches": matches,
                }
                if rollback_on_failure:
                    try:
                        garmin_client.delete_activity(created_activity_id)
                        failure["rolled_back"] = True
                    except Exception as rollback_error:
                        failure["rolled_back"] = False
                        failure["rollback_error"] = str(rollback_error)
                else:
                    failure["rolled_back"] = False
                return _json_result(failure)

            return _json_result(
                {
                    "success": True,
                    "activity_created": True,
                    "verified": True,
                    "activity_id": created_activity_id,
                    "set_count": len(exercise_sets),
                    "matches": matches,
                    "activity_response": _response_summary(activity_response),
                    "set_write_response": _response_summary(write_response),
                    "exercise_sets": _display_readback_sets(readback),
                }
            )
        except Exception as exc:
            failure = {
                "success": False,
                "activity_id": created_activity_id,
                "activity_response": _response_summary(activity_response),
                "error": str(exc),
            }
            if create_request_started and created_activity_id is None:
                failure.update(
                    {
                        "activity_may_exist": True,
                        "manual_cleanup_may_be_required": True,
                        "warning": (
                            "The create request may have reached Garmin without "
                            "returning an activity ID. Check Garmin Connect for "
                            "an incomplete activity."
                        ),
                    }
                )
            return _json_result(failure)

    return app
