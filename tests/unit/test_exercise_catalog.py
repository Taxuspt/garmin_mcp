import json
import os
from pathlib import Path

import pytest
import requests

from garmin_mcp import exercise_catalog as module
from garmin_mcp.exercise_catalog import (
    CatalogError,
    ExerciseCatalog,
    GarminExercise,
    normalize_text,
    parse_exercises,
    parse_properties,
    resolve_strength_exercises,
)


FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture
def source_texts():
    return (
        (FIXTURES / "exercises_catalog_sample.json").read_text(),
        (FIXTURES / "exercise_types_sample.properties").read_text(),
    )


@pytest.fixture
def catalog(source_texts):
    raw, labels = source_texts
    return ExerciseCatalog(parse_exercises(json.loads(raw), parse_properties(labels)))


@pytest.fixture(autouse=True)
def reset_memory_cache():
    module.clear_memory_cache()
    yield
    module.clear_memory_cache()


def test_valid_catalog_parsing_and_uppercase_normalization():
    result = parse_exercises({"categories": {"crunch": {"exercises": {"reverse_crunch": {}}}}})
    assert (result[0].category, result[0].exercise_name) == ("CRUNCH", "REVERSE_CRUNCH")


def test_properties_parser_preserves_values():
    result = parse_properties("# c\n! c\n A = L'été = bien \nEMPTY=\nNO_SEPARATOR\n")
    assert result == {"A": "L'été = bien", "EMPTY": ""}


def test_display_name_fallback_and_missing_muscles():
    exercise = parse_exercises({"categories": {"BENCH_PRESS": {"exercises": ["DUMBBELL_BENCH_PRESS"]}}})[0]
    assert exercise.display_name == "Dumbbell Bench Press"
    assert exercise.category_display_name == "Bench Press"
    assert exercise.primary_muscles == exercise.secondary_muscles == ()


@pytest.mark.parametrize("bad", [{}, {"categories": []}, {"categories": {"BAD": {}}}, {"categories": {}}])
def test_invalid_or_empty_catalog_rejected(bad):
    with pytest.raises(CatalogError):
        parse_exercises(bad)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Pull-up", "pull up"),
        ("PULL_UP", "pull_up"),
        ("élévation", "elevation"),
        ("pull-ups", "pull up"),
    ],
)
def test_text_normalization(left, right):
    assert normalize_text(left) == normalize_text(right)


def test_numbers_are_preserved():
    assert normalize_text("3-way calf raises") == "3 way calf raise"


@pytest.mark.parametrize("query", ["CRUNCH/REVERSE_CRUNCH", "CRUNCH:REVERSE_CRUNCH", "CRUNCH_REVERSE_CRUNCH"])
def test_exact_pair_matching(catalog, query):
    result = catalog.match(query)
    assert result["status"] == "exact"
    assert result["confidence"] == 1.0
    assert result["match"]["exercise_name"] == "REVERSE_CRUNCH"


def test_exact_key_and_human_name_matching(catalog):
    assert catalog.match("REVERSE_CRUNCH")["match"]["category"] == "CRUNCH"
    assert catalog.match("Reverse Crunch")["match"]["exercise_name"] == "REVERSE_CRUNCH"


def test_category_filtering_accepts_label(catalog):
    exercises, error, resolved = catalog.list("Crunch")
    assert error is None and resolved == "CRUNCH"
    assert {e.exercise_name for e in exercises} == {"REVERSE_CRUNCH", "SEATED_LEG_U"}


def test_unknown_category_has_suggestions(catalog):
    result = catalog.match("reverse", "Crnch")
    assert result["status"] == "error"
    assert "CRUNCH" in result["closest_categories"]


def test_ambiguous_and_no_match(catalog):
    ambiguous = catalog.match("leg raise")
    assert ambiguous["status"] == "ambiguous"
    assert len(ambiguous["alternatives"]) >= 2
    assert catalog.match("completely unknown exercise name")["status"] == "no_match"


def test_valid_and_invalid_aliases(catalog, caplog):
    custom = ExerciseCatalog(catalog.exercises, aliases={"abdominal invertido": ("CRUNCH", "REVERSE_CRUNCH"), "bad": ("NO", "PAIR")})
    assert custom.match("abdominal invertido")["confidence"] == 0.99
    assert "bad" not in custom.aliases
    assert "Ignoring exercise alias" in caplog.text


def test_search_pagination_and_deterministic_order(catalog):
    first, _, _ = catalog.list(search="leg raise")
    second, _, _ = catalog.list(search="LEG_RAISE")
    assert [(e.category, e.exercise_name) for e in first] == [(e.category, e.exercise_name) for e in second]
    assert first == sorted(first, key=lambda e: (e.category, e.display_name.casefold(), e.exercise_name))


def test_batch_resolver_preserves_metadata_and_resolves_once(monkeypatch, catalog, mocker):
    load = mocker.Mock(return_value=catalog)
    monkeypatch.setattr(module, "load_catalog", load)
    result = resolve_strength_exercises([
        {"name": "Reverse Crunch", "sets": 3, "reps": 12, "note": "slow"},
        {"exercise_name": "REVERSE_CRUNCH", "rest_seconds": 30},
    ])
    assert result["status"] == "ready"
    assert result["resolved_exercises"][0] == {
        "name": "Reverse Crunch", "sets": 3, "reps": 12, "note": "slow",
        "category": "CRUNCH", "exercise_name": "REVERSE_CRUNCH",
    }
    assert all(item["status"] == "exact" for item in result["items"])
    load.assert_called_once_with()


def test_batch_resolver_blocks_invalid_pair_and_validates_limit(monkeypatch, catalog):
    monkeypatch.setattr(module, "load_catalog", lambda: catalog)
    result = resolve_strength_exercises([
        {"name": "Keep description", "category": "CRUNCH", "exercise_name": "NOT_REAL"}
    ])
    assert result["status"] == "needs_review"
    assert result["items"][0]["status"] == "invalid_pair"
    assert resolve_strength_exercises([], 0)["error"] == "invalid_limit"


def test_batch_resolver_catalog_failure(monkeypatch):
    def unavailable():
        raise CatalogError("unavailable")
    monkeypatch.setattr(module, "load_catalog", unavailable)
    assert resolve_strength_exercises([{"name": "Squat"}])["status"] == "catalog_unavailable"


class Response:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _configure_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("GARMIN_EXERCISE_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GARMIN_EXERCISE_CACHE_TTL_SECONDS", "60")


def _write_cache(tmp_path, source_texts, age=0):
    raw, labels = source_texts
    (tmp_path / "Exercises.json").write_text(raw)
    (tmp_path / "exercise_types.properties").write_text(labels)
    timestamp = module.time.time() - age
    os.utime(tmp_path / "Exercises.json", (timestamp, timestamp))
    os.utime(tmp_path / "exercise_types.properties", (timestamp, timestamp))


def test_fresh_cache_makes_no_request(monkeypatch, tmp_path, source_texts, mocker):
    _configure_cache(monkeypatch, tmp_path)
    _write_cache(tmp_path, source_texts)
    get = mocker.patch.object(module.requests, "get")
    assert module.load_catalog().exercises
    get.assert_not_called()


def test_expired_cache_is_refreshed(monkeypatch, tmp_path, source_texts, mocker):
    _configure_cache(monkeypatch, tmp_path)
    _write_cache(tmp_path, source_texts, age=120)
    raw, labels = source_texts
    get = mocker.patch.object(module.requests, "get", side_effect=[Response(raw), Response(labels)])
    assert module.load_catalog().cache_status == "fresh"
    assert get.call_count == 2


@pytest.mark.parametrize("failure", [requests.ConnectionError("offline"), Response("server", 500)])
def test_network_failure_uses_stale_cache(monkeypatch, tmp_path, source_texts, mocker, failure):
    _configure_cache(monkeypatch, tmp_path)
    _write_cache(tmp_path, source_texts, age=120)
    get = mocker.patch.object(module.requests, "get")
    if isinstance(failure, Exception):
        get.side_effect = failure
    else:
        get.return_value = failure
    assert module.load_catalog().cache_status == "stale"


def test_invalid_json_does_not_replace_valid_cache(monkeypatch, tmp_path, source_texts, mocker):
    _configure_cache(monkeypatch, tmp_path)
    _write_cache(tmp_path, source_texts, age=120)
    original = (tmp_path / "Exercises.json").read_text()
    mocker.patch.object(module.requests, "get", side_effect=[Response("<html>bad</html>"), Response("")])
    assert module.load_catalog().cache_status == "stale"
    assert (tmp_path / "Exercises.json").read_text() == original


def test_no_network_and_no_cache_is_safe_error(monkeypatch, tmp_path, mocker):
    _configure_cache(monkeypatch, tmp_path)
    mocker.patch.object(module.requests, "get", side_effect=requests.ConnectionError("secret details"))
    with pytest.raises(CatalogError, match="unavailable") as exc:
        module.load_catalog()
    assert "secret details" not in str(exc.value)


def test_atomic_write_uses_replace_and_leaves_complete_file(tmp_path, mocker):
    replace = mocker.spy(module.os, "replace")
    target = tmp_path / "catalog.json"
    module._atomic_write(target, "complete")
    assert target.read_text() == "complete"
    replace.assert_called_once()
    assert not list(tmp_path.glob(".catalog.json.*"))


def test_two_calls_use_memory_cache(monkeypatch, tmp_path, source_texts, mocker):
    _configure_cache(monkeypatch, tmp_path)
    raw, labels = source_texts
    get = mocker.patch.object(module.requests, "get", side_effect=[Response(raw), Response(labels)])
    assert module.load_catalog() is module.load_catalog()
    assert get.call_count == 2
