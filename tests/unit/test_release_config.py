"""Release metadata regression tests.

release-please bumps the version in pyproject.toml and, through an
``extra-files`` entry, in uv.lock. If the two drift apart, ``uv lock --check``
and the Docker build (``uv sync --locked``) both fail, so keep them pinned
together here.
"""

import json
import re
from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib")

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_toml(name: str) -> dict:
    with open(REPO_ROOT / name, "rb") as fh:
        return tomllib.load(fh)


def _project() -> dict:
    return _load_toml("pyproject.toml")["project"]


def _release_config() -> dict:
    return json.loads((REPO_ROOT / "release-please-config.json").read_text())


def _locked_project_versions(name: str) -> list[str]:
    packages = _load_toml("uv.lock")["package"]
    return [pkg["version"] for pkg in packages if pkg["name"] == name]


def test_manifest_matches_pyproject_version() -> None:
    manifest = json.loads((REPO_ROOT / ".release-please-manifest.json").read_text())

    assert manifest == {".": _project()["version"]}


def test_uv_lock_matches_pyproject_version() -> None:
    project = _project()

    assert _locked_project_versions(project["name"]) == [project["version"]]


def test_release_please_bumps_uv_lock_entry_for_this_project() -> None:
    package = _release_config()["packages"]["."]
    lock_updaters = [f for f in package.get("extra-files", []) if f["path"] == "uv.lock"]

    assert len(lock_updaters) == 1
    updater = lock_updaters[0]
    assert updater["type"] == "toml"
    # release-please's TOML parser wraps strings as {start, end, value}, so the
    # filter has to compare ``name.value``; a plain ``@.name == ...`` matches nothing.
    match = re.fullmatch(r"\$\.package\[\?\(@\.name\.value=='([^']+)'\)\]\.version", updater["jsonpath"])
    assert match, updater["jsonpath"]
    assert match.group(1) == _project()["name"]


def test_release_tags_continue_the_existing_unprefixed_scheme() -> None:
    config = _release_config()

    # The repo's first tag is ``0.1.0`` (no ``v``); release-please finds the
    # previous release by tag name, so the format has to match.
    assert config["release-type"] == "python"
    assert config["include-v-in-tag"] is False
    assert config["include-component-in-tag"] is False
