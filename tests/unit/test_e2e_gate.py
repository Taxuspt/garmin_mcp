"""A plain `pytest` must not collect the live e2e tests.

Several e2e tests create and delete foods, food logs and workouts in the real
Garmin account, and they only skip themselves when ~/.garminconnect is missing.
"""

import ast
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _server_test_ast():
    # Read as data only: never import or execute the live-test module.
    return ast.parse((REPO_ROOT / "tests/e2e/test_server_e2e.py").read_text())


@pytest.mark.asyncio
async def test_live_server_test_tool_names_are_registered_offline():
    generator = runpy.run_path(str(REPO_ROOT / "scripts/generate_dxt_tools.py"))
    registered = {tool["name"] for tool in await generator["_collect_tools"]()}
    requested = set()
    for node in ast.walk(_server_test_ast()):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "call_tool" and node.args
                and isinstance(node.args[0], ast.Constant)):
            requested.add(node.args[0].value)
        elif (isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "tools_to_test"
                        for target in node.targets)):
            requested.update(item.elts[0].value for item in node.value.elts)
    assert requested
    assert requested <= registered, f"Live tests request unknown tools: {requested - registered}"


def test_live_server_tests_do_not_suppress_tool_assertions():
    for node in ast.walk(_server_test_ast()):
        if not isinstance(node, ast.Try):
            continue
        catches_all = any(
            handler.type is None
            or isinstance(handler.type, ast.Name) and handler.type.id == "Exception"
            for handler in node.handlers
        )
        invokes_tool = any(
            isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
            and child.func.attr == "call_tool"
            for statement in node.body for child in ast.walk(statement)
        )
        assert not (catches_all and invokes_tool), "Live tests must propagate failed tool assertions"


def _collect(*args: str) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-qq", "-p", "no:cacheprovider", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode in (0, 5), result.stdout + result.stderr
    return [line for line in result.stdout.splitlines() if "::" in line]


def test_plain_pytest_deselects_e2e_tests() -> None:
    node_ids = _collect()

    assert node_ids, "expected the default run to collect unit/integration tests"
    live = [n for n in node_ids if not n.startswith(("tests/unit/", "tests/integration/"))]
    assert live == []


def test_explicit_e2e_marker_still_selects_them() -> None:
    node_ids = _collect("tests/e2e", "-m", "e2e")

    assert node_ids
    assert all(n.startswith("tests/e2e/") for n in node_ids)
