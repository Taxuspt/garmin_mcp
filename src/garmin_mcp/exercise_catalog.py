"""Public Garmin strength-exercise catalog, search, and MCP tools.

The canonical identifiers come exclusively from Garmin's ``Exercises.json``.
Labels and the small alias table are presentation/search aids and never create
new valid exercise pairs.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher, get_close_matches
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Iterable, Mapping
import unicodedata

import requests


EXERCISES_URL = "https://connect.garmin.com/web-data/exercises/Exercises.json"
EXERCISE_LABELS_URL = (
    "https://connect.garmin.com/"
    "web-translations/exercise_types/exercise_types.properties"
)
DEFAULT_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
REQUEST_TIMEOUT = (5, 20)

# These are conservative search conveniences, not part of Garmin's catalog.
EXERCISE_ALIASES: dict[str, tuple[str, str]] = {
    "reverse crunch": ("CRUNCH", "REVERSE_CRUNCH"),
    "abdominal invertido": ("CRUNCH", "REVERSE_CRUNCH"),
}

logger = logging.getLogger(__name__)


class CatalogError(ValueError):
    """A safe, client-presentable catalog validation/loading error."""


@dataclass(frozen=True)
class GarminExercise:
    category: str
    exercise_name: str
    display_name: str
    category_display_name: str | None
    primary_muscles: tuple[str, ...]
    secondary_muscles: tuple[str, ...]


def humanize(identifier: str) -> str:
    return " ".join(part.capitalize() for part in identifier.split("_") if part)


def _singularize_token(token: str) -> str:
    """Normalize only uncomplicated English plurals used in exercise queries."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 2 and token.endswith("s") and not token.endswith(
        ("ss", "us", "is")
    ):
        return token[:-1]
    return token


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value).casefold())
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    separated = re.sub(r"[_\-/\\]+", " ", without_marks)
    alphanumeric = re.sub(r"[^\w\s]", " ", separated, flags=re.UNICODE)
    return " ".join(_singularize_token(token) for token in alphanumeric.split())


def parse_properties(content: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "!")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        properties[key.strip()] = value.strip()
    return properties


def _identifier(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip().upper()
    # Keep Garmin identifiers intact, but reject values that are clearly labels.
    if not re.fullmatch(r"[A-Z0-9_]+", candidate):
        return None
    return candidate


def _muscles(metadata: Mapping[str, Any], key: str) -> tuple[str, ...]:
    values = metadata.get(key, [])
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(
        muscle
        for item in values
        if (muscle := _identifier(item)) is not None
    )


def _exercise_entries(raw: Any) -> Iterable[tuple[Any, Any]]:
    if isinstance(raw, dict):
        yield from raw.items()
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                yield item, {}
            elif isinstance(item, dict):
                name = item.get("exerciseName") or item.get("key") or item.get("name")
                yield name, item


def parse_exercises(data: Any, properties: Mapping[str, str] | None = None) -> tuple[GarminExercise, ...]:
    if not isinstance(data, dict) or not isinstance(data.get("categories"), dict):
        raise CatalogError("Exercise catalog has no valid categories object.")
    labels = properties or {}
    exercises: list[GarminExercise] = []
    for raw_category, category_data in data["categories"].items():
        category = _identifier(raw_category)
        if category is None or not isinstance(category_data, dict):
            continue
        raw_exercises = category_data.get("exercises")
        if not isinstance(raw_exercises, (dict, list)):
            continue
        category_label = labels.get(f"category_type_{category}") or humanize(category)
        for raw_name, raw_metadata in _exercise_entries(raw_exercises):
            exercise_name = _identifier(raw_name)
            if exercise_name is None:
                continue
            metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
            exercises.append(
                GarminExercise(
                    category=category,
                    exercise_name=exercise_name,
                    display_name=labels.get(f"{category}_{exercise_name}")
                    or humanize(exercise_name),
                    category_display_name=category_label,
                    primary_muscles=_muscles(metadata, "primaryMuscles"),
                    secondary_muscles=_muscles(metadata, "secondaryMuscles"),
                )
            )
    if not exercises:
        raise CatalogError("Exercise catalog contains no valid exercises.")
    unique = {(e.category, e.exercise_name): e for e in exercises}
    return tuple(sorted(unique.values(), key=lambda e: (e.category, e.display_name.casefold(), e.exercise_name)))


def _without_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_without_none(item) for item in value]
    return value


def _serialize(payload: dict[str, Any]) -> str:
    return json.dumps(_without_none(payload), indent=2, ensure_ascii=False)


class ExerciseCatalog:
    def __init__(
        self,
        exercises: Iterable[GarminExercise],
        *,
        cache_status: str = "fresh",
        aliases: Mapping[str, tuple[str, str]] | None = None,
    ) -> None:
        self.exercises = tuple(sorted(exercises, key=lambda e: (e.category, e.display_name.casefold(), e.exercise_name)))
        self.cache_status = cache_status
        self._pairs = {(e.category, e.exercise_name): e for e in self.exercises}
        self._categories = {e.category: e.category_display_name for e in self.exercises}
        self.aliases: dict[str, tuple[str, str]] = {}
        for alias, pair in (aliases if aliases is not None else EXERCISE_ALIASES).items():
            normalized_pair = (str(pair[0]).upper(), str(pair[1]).upper())
            if normalized_pair in self._pairs:
                self.aliases[normalize_text(alias)] = normalized_pair
            else:
                logger.warning("Ignoring exercise alias %r with invalid Garmin pair", alias)

    def validate_pair(self, category: str, exercise_name: str) -> GarminExercise | None:
        return self._pairs.get((category.strip().upper(), exercise_name.strip().upper()))

    def resolve_category(self, category: str) -> str | None:
        normalized = normalize_text(category)
        matches = [
            key for key, label in self._categories.items()
            if normalized in {normalize_text(key), normalize_text(label or "")}
        ]
        return matches[0] if len(matches) == 1 else None

    def category_error(self, category: str) -> dict[str, Any]:
        choices = list(self._categories)
        normalized_map = {normalize_text(key): key for key in choices}
        normalized_map.update({normalize_text(label or ""): key for key, label in self._categories.items()})
        close = get_close_matches(normalize_text(category), list(normalized_map), n=5, cutoff=0.35)
        return {
            "status": "error",
            "error": "unknown_category",
            "message": f"Unknown Garmin exercise category: {category}",
            "closest_categories": list(dict.fromkeys(normalized_map[item] for item in close)),
        }

    @staticmethod
    def exercise_dict(exercise: GarminExercise, include_muscles: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "category": exercise.category,
            "exercise_name": exercise.exercise_name,
            "display_name": exercise.display_name,
            "category_display_name": exercise.category_display_name,
        }
        if include_muscles:
            result.update(primary_muscles=list(exercise.primary_muscles), secondary_muscles=list(exercise.secondary_muscles))
        return _without_none(result)

    def list(self, category: str | None = None, search: str | None = None) -> tuple[list[GarminExercise] | None, dict[str, Any] | None, str | None]:
        resolved = None
        candidates = list(self.exercises)
        if category is not None:
            resolved = self.resolve_category(category)
            if resolved is None:
                return None, self.category_error(category), None
            candidates = [e for e in candidates if e.category == resolved]
        if search and (query := normalize_text(search)):
            query_tokens = set(query.split())
            filtered = []
            for exercise in candidates:
                fields = (
                    exercise.exercise_name,
                    exercise.display_name,
                    exercise.category,
                    exercise.category_display_name or "",
                    *exercise.primary_muscles,
                    *exercise.secondary_muscles,
                )
                combined = normalize_text(" ".join(fields))
                if query in combined or query_tokens.issubset(set(combined.split())):
                    filtered.append(exercise)
            candidates = filtered
        return candidates, None, resolved

    def _exact_pair(self, query: str, candidates: list[GarminExercise]) -> GarminExercise | None:
        compact = query.strip().upper()
        for exercise in candidates:
            forms = {
                f"{exercise.category}/{exercise.exercise_name}",
                f"{exercise.category}:{exercise.exercise_name}",
                f"{exercise.category}_{exercise.exercise_name}",
            }
            if compact in forms:
                return exercise
        return None

    @staticmethod
    def _score(query: str, exercise: GarminExercise) -> float:
        candidate_forms = [
            normalize_text(exercise.display_name),
            normalize_text(exercise.exercise_name),
            normalize_text(f"{exercise.category} {exercise.display_name}"),
            normalize_text(f"{exercise.category} {exercise.exercise_name}"),
        ]
        query_tokens = set(query.split())
        best = 0.0
        for form in candidate_forms:
            candidate_tokens = set(form.split())
            union = query_tokens | candidate_tokens
            intersection = query_tokens & candidate_tokens
            sequence = SequenceMatcher(None, query, form).ratio()
            jaccard = len(intersection) / len(union) if union else 0.0
            coverage = len(intersection) / len(query_tokens) if query_tokens else 0.0
            best = max(best, 0.50 * sequence + 0.30 * jaccard + 0.20 * coverage)
        return round(best, 4)

    def match(self, query: str, category: str | None = None, limit: int = 5) -> dict[str, Any]:
        normalized_query = normalize_text(query)
        candidates = list(self.exercises)
        if category is not None:
            resolved = self.resolve_category(category)
            if resolved is None:
                return self.category_error(category)
            candidates = [e for e in candidates if e.category == resolved]

        pair = self._exact_pair(query, candidates)
        if pair:
            return self._exact_response(query, pair, 1.0)

        key_matches = [e for e in candidates if normalize_text(e.exercise_name) == normalized_query]
        if len(key_matches) == 1:
            return self._exact_response(query, key_matches[0], 1.0)
        if len(key_matches) > 1:
            return self._ambiguous(query, [(e, 1.0) for e in key_matches[:limit]])

        name_matches = [e for e in candidates if normalize_text(e.display_name) == normalized_query]
        if len(name_matches) == 1:
            return self._exact_response(query, name_matches[0], 1.0)
        if len(name_matches) > 1:
            return self._ambiguous(query, [(e, 1.0) for e in name_matches[:limit]])

        alias_pair = self.aliases.get(normalized_query)
        if alias_pair and (alias_match := self._pairs.get(alias_pair)) in candidates:
            return self._exact_response(query, alias_match, 0.99)

        ranked = sorted(
            ((exercise, self._score(normalized_query, exercise)) for exercise in candidates),
            key=lambda item: (-item[1], item[0].category, item[0].display_name.casefold(), item[0].exercise_name),
        )
        top_score = ranked[0][1] if ranked else 0.0
        alternatives = ranked[:limit]
        margin = top_score - ranked[1][1] if len(ranked) > 1 else top_score
        if top_score >= 0.82 and margin >= 0.08:
            match = alternatives[0][0]
            return {
                "status": "matched", "query": query, "confidence": top_score,
                "match": self.exercise_dict(match),
                "alternatives": [self._scored(e, score) for e, score in alternatives[1:]],
            }
        if top_score >= 0.65:
            return self._ambiguous(query, alternatives)
        return {
            "status": "no_match", "query": query, "confidence": top_score,
            "message": "No confident Garmin exercise match was found.", "alternatives": [],
        }

    def _exact_response(self, query: str, exercise: GarminExercise, confidence: float) -> dict[str, Any]:
        return {"status": "exact", "query": query, "confidence": confidence, "match": self.exercise_dict(exercise), "alternatives": []}

    def _scored(self, exercise: GarminExercise, score: float) -> dict[str, Any]:
        return {**self.exercise_dict(exercise), "score": score}

    def _ambiguous(self, query: str, ranked: list[tuple[GarminExercise, float]]) -> dict[str, Any]:
        return {
            "status": "ambiguous", "query": query,
            "confidence": ranked[0][1] if ranked else 0.0,
            "message": "Multiple Garmin exercises are similarly plausible.",
            "match": None,
            "alternatives": [self._scored(e, score) for e, score in ranked],
        }


@dataclass(frozen=True)
class _CacheConfig:
    directory: Path
    ttl_seconds: int
    exercises_url: str
    labels_url: str


_memory_catalogs: dict[_CacheConfig, ExerciseCatalog] = {}


def _cache_config() -> _CacheConfig:
    ttl_raw = os.getenv("GARMIN_EXERCISE_CACHE_TTL_SECONDS", str(DEFAULT_CACHE_TTL_SECONDS))
    try:
        ttl = max(0, int(ttl_raw))
    except ValueError:
        ttl = DEFAULT_CACHE_TTL_SECONDS
    return _CacheConfig(
        Path(os.path.expanduser(os.getenv("GARMIN_EXERCISE_CACHE_DIR", "~/.cache/garmin_mcp/exercise_catalog/"))),
        ttl,
        os.getenv("GARMIN_EXERCISES_URL", EXERCISES_URL),
        os.getenv("GARMIN_EXERCISE_LABELS_URL", EXERCISE_LABELS_URL),
    )


def clear_memory_cache() -> None:
    """Clear process cache (primarily useful for tests and explicit reloads)."""
    _memory_catalogs.clear()


def _read_cached(config: _CacheConfig) -> tuple[ExerciseCatalog, float] | None:
    exercises_path = config.directory / "Exercises.json"
    labels_path = config.directory / "exercise_types.properties"
    try:
        exercises_text = exercises_path.read_text(encoding="utf-8")
        labels_text = labels_path.read_text(encoding="utf-8")
        exercises = parse_exercises(json.loads(exercises_text), parse_properties(labels_text))
        age = max(time.time() - exercises_path.stat().st_mtime, time.time() - labels_path.stat().st_mtime)
        return ExerciseCatalog(exercises), age
    except (OSError, ValueError, json.JSONDecodeError, CatalogError):
        return None


def _download(config: _CacheConfig) -> tuple[str, str, ExerciseCatalog]:
    exercise_response = requests.get(config.exercises_url, timeout=REQUEST_TIMEOUT)
    exercise_response.raise_for_status()
    labels_response = requests.get(config.labels_url, timeout=REQUEST_TIMEOUT)
    labels_response.raise_for_status()
    exercises_text = exercise_response.text
    labels_text = labels_response.text
    exercises = parse_exercises(json.loads(exercises_text), parse_properties(labels_text))
    return exercises_text, labels_text, ExerciseCatalog(exercises)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def load_catalog() -> ExerciseCatalog:
    config = _cache_config()
    if config in _memory_catalogs:
        return _memory_catalogs[config]
    cached = _read_cached(config)
    if cached and cached[1] <= config.ttl_seconds:
        _memory_catalogs[config] = cached[0]
        return cached[0]
    try:
        exercises_text, labels_text, catalog = _download(config)
        _atomic_write(config.directory / "Exercises.json", exercises_text)
        _atomic_write(config.directory / "exercise_types.properties", labels_text)
        _memory_catalogs[config] = catalog
        return catalog
    except (requests.RequestException, OSError, ValueError, json.JSONDecodeError, CatalogError) as exc:
        if cached:
            stale = ExerciseCatalog(cached[0].exercises, cache_status="stale")
            _memory_catalogs[config] = stale
            logger.warning("Using stale Garmin exercise catalog cache: %s", type(exc).__name__)
            return stale
        raise CatalogError("Garmin exercise catalog is unavailable and no usable cache exists.") from None


def _load_error(exc: Exception) -> str:
    return _serialize({"status": "error", "source": "garmin_exercise_catalog", "error": "catalog_unavailable", "message": str(exc)})


def resolve_strength_exercises(
    exercises: list[dict[str, Any]], limit: int = 5
) -> dict[str, Any]:
    """Resolve a complete set of caller inputs to canonical Garmin identifiers.

    This is deliberately independent of the authenticated Garmin client.  The
    catalog is loaded once and each returned exercise retains all caller fields.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10:
        return {
            "status": "error", "error": "invalid_limit",
            "message": "limit must be between 1 and 10.",
            "resolved_exercises": [], "items": [],
        }
    if not isinstance(exercises, list):
        return {
            "status": "error", "error": "invalid_exercises",
            "message": "exercises must be a list of objects.",
            "resolved_exercises": [], "items": [],
        }
    try:
        catalog = load_catalog()
    except CatalogError as exc:
        return {
            "status": "catalog_unavailable", "error": "catalog_unavailable",
            "message": str(exc), "resolved_exercises": [], "items": [],
        }

    resolved: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for index, exercise in enumerate(exercises):
        if not isinstance(exercise, dict):
            items.append({
                "index": index, "status": "invalid", "confidence": 0.0,
                "message": "Exercise must be an object.", "alternatives": [],
            })
            continue

        category = exercise.get("category")
        exercise_name = exercise.get("exercise_name")
        name = exercise.get("name")
        match: dict[str, Any]

        if category is not None and (
            not isinstance(category, str) or not category.strip()
        ):
            match = {"status": "invalid", "confidence": 0.0,
                     "message": "category must be a non-empty string.", "alternatives": []}
        elif exercise_name is not None and (
            not isinstance(exercise_name, str) or not exercise_name.strip()
        ):
            match = {"status": "invalid", "confidence": 0.0,
                     "message": "exercise_name must be a non-empty string.", "alternatives": []}
        elif category is not None and exercise_name is not None:
            exact = catalog.validate_pair(category, exercise_name)
            if exact is None:
                # A supplied pair is authoritative: never silently repair one half.
                suggestions = catalog.match(exercise_name, category, limit)
                match = {
                    "status": "invalid_pair", "confidence": 0.0,
                    "message": "category and exercise_name are not a valid Garmin catalog pair.",
                    "alternatives": suggestions.get("alternatives", []),
                }
            else:
                match = catalog._exact_response(exercise_name, exact, 1.0)
        elif exercise_name is not None:
            match = catalog.match(exercise_name, category, limit)
        elif isinstance(name, str) and name.strip():
            match = catalog.match(name, category, limit)
        else:
            match = {"status": "invalid", "confidence": 0.0,
                     "message": "name or exercise_name is required.", "alternatives": []}

        item = {
            "index": index,
            "status": match.get("status", "error"),
            "confidence": match.get("confidence", 0.0),
            "alternatives": match.get("alternatives", []),
        }
        if match.get("message"):
            item["message"] = match["message"]
        canonical = match.get("match")
        if match.get("status") in {"exact", "matched"} and canonical:
            output = dict(exercise)
            output["category"] = canonical["category"]
            output["exercise_name"] = canonical["exercise_name"]
            resolved.append(output)
            item["resolved_exercise"] = output
        items.append(item)

    ready = len(resolved) == len(exercises)
    return {
        "status": "ready" if ready else "needs_review",
        "source": "garmin_exercise_catalog", "cache_status": catalog.cache_status,
        "resolved_exercises": resolved, "items": items,
        "unresolved_items": [item for item in items if item["status"] not in {"exact", "matched"}],
    }


def register_tools(app):
    @app.tool(
        description=(
            "List Garmin strength exercises and their exact category/exerciseName "
            "identifiers. Supports category filtering, text search, and pagination. "
            "Use the returned category and exercise_name fields when creating a "
            "strength workout."
        )
    )
    async def list_strength_exercises(
        category: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
        include_muscles: bool = False,
    ) -> str:
        if not 1 <= limit <= 200:
            return _serialize({"status": "error", "error": "invalid_limit", "message": "limit must be between 1 and 200."})
        if offset < 0:
            return _serialize({"status": "error", "error": "invalid_offset", "message": "offset must not be negative."})
        try:
            catalog = load_catalog()
        except CatalogError as exc:
            return _load_error(exc)
        matches, error, resolved = catalog.list(category, search)
        if error:
            return _serialize(error)
        assert matches is not None
        page = matches[offset : offset + limit]
        total = len(matches)
        return _serialize({
            "status": "success", "source": "garmin_exercise_catalog",
            "cache_status": catalog.cache_status, "total": total, "offset": offset,
            "limit": limit, "has_more": offset + len(page) < total,
            "next_offset": offset + len(page) if offset + len(page) < total else None,
            "filters": {"category": resolved, "search": search},
            "exercises": [catalog.exercise_dict(e, include_muscles) for e in page],
        })

    @app.tool(
        description=(
            "Match a human-friendly exercise description to Garmin's exercise "
            "catalog. Returns the exact category and exercise_name identifiers, "
            "a confidence score, and alternatives. It never creates or modifies "
            "a workout."
        )
    )
    async def match_strength_exercise(query: str, category: str | None = None, limit: int = 5) -> str:
        if not isinstance(query, str) or not query.strip():
            return _serialize({"status": "error", "error": "invalid_query", "message": "query is required and must not be empty."})
        if not 1 <= limit <= 10:
            return _serialize({"status": "error", "error": "invalid_limit", "message": "limit must be between 1 and 10."})
        try:
            catalog = load_catalog()
        except CatalogError as exc:
            return _load_error(exc)
        return _serialize(catalog.match(query, category, limit))

    @app.tool(
        name="resolve_strength_exercises",
        description=(
            "Resolve a batch of strength exercise inputs to exact Garmin category "
            "and exercise_name identifiers without creating a workout."
        )
    )
    async def resolve_strength_exercises_tool(
        exercises: list[dict[str, Any]], limit: int = 5
    ) -> str:
        return _serialize(resolve_strength_exercises(exercises, limit))

    return app
