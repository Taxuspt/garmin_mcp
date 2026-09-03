"""Regression tests for the Desktop Extension manifest."""

import json
import tomllib
from pathlib import Path
from zipfile import ZipFile


REPO_ROOT = Path(__file__).parents[2]
MANIFEST_PATH = REPO_ROOT / "dxt" / "manifest.json"
BUNDLE_PATH = REPO_ROOT / "garmin-mcp.dxt"


def _read_manifest():
    return json.loads(MANIFEST_PATH.read_text())


def test_token_path_default_does_not_require_nested_interpolation():
    manifest = _read_manifest()
    assert manifest["user_config"]["token_path"]["default"] == "~/.garminconnect"


def test_user_config_defaults_do_not_contain_template_variables():
    manifest = _read_manifest()

    for config in manifest.get("user_config", {}).values():
        default = config.get("default")
        values = default if isinstance(default, list) else [default]
        assert all("${" not in value for value in values if isinstance(value, str))


def test_extension_does_not_request_password_or_mfa_in_configuration_form():
    manifest = _read_manifest()
    config = manifest.get("user_config", {})
    assert "garmin_password" not in config
    assert "mfa" not in config
    assert "GARMIN_PASSWORD" not in manifest["server"]["mcp_config"]["env"]


def test_bundled_manifest_matches_source_manifest():
    manifest = _read_manifest()
    with ZipFile(BUNDLE_PATH) as bundle:
        assert bundle.namelist() == ["manifest.json"]
        bundled_manifest = json.loads(bundle.read("manifest.json"))

    assert bundled_manifest == manifest


def test_extension_version_matches_python_project():
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert _read_manifest()["version"] == project["project"]["version"]
