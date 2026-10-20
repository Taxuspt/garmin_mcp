"""Run workflow log steps offline under GitHub Actions' fail-fast shell."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _step(filename, name):
    workflow = (REPO_ROOT / ".github" / "workflows" / filename).read_text()
    match = re.search(
        rf"^    - name: {re.escape(name)}\n(.*?)(?=^    - name: |\Z)",
        workflow,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"Missing workflow step: {name}"
    return match.group(1)


def _run_block(step):
    lines = step.split("      run: |\n", 1)[1].splitlines()
    commands = []
    for line in lines:
        if line and not line.startswith("        "):
            break
        commands.append(line[8:])
    return "\n".join(commands)


def _run_shell(commands, **kwargs):
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", commands],
        capture_output=True,
        text=True,
        timeout=10,
        **kwargs,
    )


@pytest.mark.parametrize("outcome", ["success", "failure", "skipped", "cancelled"])
def test_ci_summary_reports_the_test_step_outcome(outcome):
    summary = _step("ci.yml", "Test summary")
    commands = _run_block(summary)
    commands = commands.replace("${{ matrix.python-version }}", "3.13")
    commands = commands.replace("${{ steps.tests.outcome }}", outcome)
    assert "${{" not in commands, "Unexpected unexpanded workflow expression"

    result = _run_shell(commands)

    assert result.returncode == 0, result.stderr
    if outcome == "success":
        assert "All tests passed" in result.stdout
    else:
        assert "All tests passed" not in result.stdout
        assert outcome in result.stdout
    tests = _step("ci.yml", "Run integration tests")
    assert re.search(r"^      id: tests$", tests, re.MULTILINE)
    assert re.search(r"^      if: always\(\)$", summary, re.MULTILINE)


@pytest.mark.parametrize("lock_status", [0, 71])
def test_lock_check_prints_repair_hint_and_still_fails(tmp_path, lock_status):
    calls = tmp_path / "calls.json"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(calls)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        f"raise SystemExit({lock_status})\n"
    )
    uv.chmod(0o755)
    commands = _run_block(_step("security.yml", "Verify lock file is up to date"))

    result = _run_shell(
        commands,
        cwd=tmp_path,
        env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]},
    )

    assert json.loads(calls.read_text()) == ["lock", "--check"]
    if lock_status:
        assert result.returncode != 0
        assert "Lock file is out of sync" in result.stdout
        assert "Run 'uv lock' locally and commit the updated uv.lock" in result.stdout
    else:
        assert result.returncode == 0, result.stderr
        assert "out of sync" not in result.stdout
