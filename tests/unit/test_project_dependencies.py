"""Project metadata regression tests."""

from pathlib import Path
import tomllib


def test_project_caps_mcp_to_v1_series() -> None:
    """The project should explicitly stay on the MCP v1 series for compatibility."""
    repo_root = Path(__file__).resolve().parents[2]
    pyproject_text = (repo_root / "pyproject.toml").read_text()

    assert '"mcp>=1.28.1,<2"' in pyproject_text


def test_project_requires_security_fixed_runtime_and_garmin_client() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((repo_root / "pyproject.toml").read_text())["project"]

    assert project["requires-python"] == ">=3.12"
    assert "garminconnect==0.3.5" in project["dependencies"]
