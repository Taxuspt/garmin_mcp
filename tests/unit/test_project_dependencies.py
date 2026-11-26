"""Project metadata regression tests."""

from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import Specifier

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


def test_project_caps_mcp_to_v1_series() -> None:
    """The project should explicitly stay on the MCP v1 series for compatibility."""
    repo_root = Path(__file__).resolve().parents[2]
    with (repo_root / "pyproject.toml").open("rb") as pyproject_file:
        project = tomllib.load(pyproject_file)["project"]

    requirements = [Requirement(dependency) for dependency in project["dependencies"]]
    mcp_requirement = next((req for req in requirements if req.name.lower() == "mcp"), None)

    assert mcp_requirement is not None
    assert Specifier("<2") in set(mcp_requirement.specifier)
