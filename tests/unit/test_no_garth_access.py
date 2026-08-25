"""Guards against reintroducing `.garth` access anywhere in the source tree.

garminconnect >= 0.3 dropped garth entirely: `Garmin` no longer exposes a
`.garth` attribute and garth is not an installed dependency. Any surviving
`client.garth.*` call therefore raises AttributeError at runtime -- and it
does so on the token-persist path, which only runs when the cached session
has actually died. So the break stays invisible until the exact moment the
service needs to re-authenticate, then strands it.

That is not hypothetical: this repo carried exactly that bug in
workout_builders.py before it was fixed (see the regression test in
tests/integration/test_workout_builders_tools.py), and `.garth` keeps
resurfacing in LLM-agent-generated garminconnect code industry-wide -- it's
a strong prior from older training data/tutorials that predates the >=0.3
API change, so an agent (or a human copying agent output) reaching for it
again without being told otherwise is the expected failure mode, not an
edge case.

Ported from the identical guard in garmin-scale-sync/hevy2garmin-lite
(/Users/mr13/workspace/hevva2/tests/test_no_garth_access.py) -- same
account, same fleet, same hazard.
"""

import importlib.util
import re
from pathlib import Path

from garminconnect import Garmin

_GARTH_ACCESS = re.compile(r"\.garth\b")

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "garmin_mcp"


def _source_files() -> list[Path]:
    return sorted(_SRC_ROOT.rglob("*.py"))


def test_garth_is_not_installed():
    """garth must not be reachable -- if it reappears, something pulled in a
    dependency carrying a second Garmin auth stack."""
    assert importlib.util.find_spec("garth") is None, (
        "garth is installed. It ships its own SSO login path against the same "
        "rate-limited Garmin account this fleet shares; remove the dependency "
        "that pulled it in."
    )


def test_garmin_exposes_no_garth_attribute():
    """Pins the library contract. If this fails, garminconnect was moved to a
    version where `.garth` exists again -- check for version skew across the
    services sharing this token store before relying on `.garth`."""
    assert not hasattr(Garmin, "garth"), (
        "garminconnect's Garmin object exposes `.garth` again -- the installed "
        "version differs from the >=0.3 API this codebase targets."
    )


def test_scan_covers_the_source_tree():
    """Fails loudly if a path refactor leaves the scan below looking at
    nothing, which would let it pass vacuously forever."""
    found = _source_files()
    assert len(found) >= 3, (
        f"Expected to scan the source tree, found only {len(found)} module(s) "
        f"under {_SRC_ROOT}. Fix _SRC_ROOT before trusting this guard."
    )


def test_no_source_file_accesses_garth():
    """The actual guard: no module may touch `.garth`. Use `.client` instead
    (e.g. `client.client.dump(path)`, not `client.garth.dump(path)`)."""
    offenders = []
    for path in _source_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _GARTH_ACCESS.search(line):
                offenders.append(f"  {path.relative_to(_SRC_ROOT)}:{lineno}: {line.strip()}")

    assert not offenders, (
        "`.garth` access found -- this raises AttributeError on garminconnect "
        ">=0.3. Use the `.client` attribute instead:\n" + "\n".join(offenders)
    )
