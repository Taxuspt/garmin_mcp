"""A plain `pytest` must not collect the live e2e tests.

Several e2e tests create and delete foods, food logs and workouts in the real
Garmin account, and they only skip themselves when ~/.garminconnect is missing.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


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
