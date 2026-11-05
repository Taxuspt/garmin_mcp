"""Project metadata regression tests."""

from pathlib import Path


def test_project_caps_mcp_to_v1_series() -> None:
    """The project should explicitly stay on the MCP v1 series for compatibility."""
    repo_root = Path(__file__).resolve().parents[2]
    pyproject_text = (repo_root / "pyproject.toml").read_text()

    assert '"mcp>=1.28.1,<2"' in pyproject_text
