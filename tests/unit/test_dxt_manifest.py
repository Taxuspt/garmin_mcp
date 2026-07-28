"""Regression tests for the Desktop Extension manifest."""

import json
from pathlib import Path
from zipfile import ZipFile


def test_token_path_default_does_not_require_nested_interpolation():
    repo_root = Path(__file__).parents[2]
    manifest_path = repo_root / "dxt" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    with ZipFile(repo_root / "garmin-mcp.dxt") as bundle:
        bundled_manifest = json.loads(bundle.read("manifest.json"))

    assert manifest["user_config"]["token_path"]["default"] == "~/.garminconnect"
    assert bundled_manifest == manifest
