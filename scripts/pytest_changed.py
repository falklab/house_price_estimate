"""Run the fast tests related to files changed in a commit.

Used by the ``pytest-changed`` pre-commit hook, which passes the staged
Python files as arguments. Each file is mapped to tests as follows:

- ``house_price_estimate/<module>.py`` runs the test files listed for that
  module in ``MODULE_TESTS``, or else ``tests/test_<module>.py``
- ``tests/test_*.py`` runs itself
- ``tests/conftest.py`` runs all tests

Other files run no tests. Tests with the markers in ``FAST_MARKERS`` are
deselected. CI runs the full suite, so this is only a quick local check.
"""

import pathlib
import subprocess
import sys

TESTS: pathlib.Path = pathlib.Path("tests")
SRC: pathlib.Path = pathlib.Path("house_price_estimate")
FAST_MARKERS: str = "not slow and not local_data"
NO_TESTS_COLLECTED: int = 5

MODULE_TESTS: dict[str, list[str]] = {
    "match": ["test_address.py"],
    "offline": ["test_offline.py", "test_estimate.py"],
    "build": ["test_estimate.py", "test_offline.py"],
    "backtest": ["test_estimate.py"],
    "cli": ["test_estimate.py"],
    "config": ["test_estimate.py"],
    "hpi": ["test_estimate.py"],
    "__main__": ["test_estimate.py"],
}
"""Test files for modules whose tests are not in ``tests/test_<module>.py``."""


def _tests_for(pth: pathlib.Path) -> list[pathlib.Path]:
    """Return the test files which cover a changed file."""
    if pth == TESTS / "conftest.py":
        return sorted(TESTS.glob("test_*.py"))
    if pth.parent == TESTS and pth.name.startswith("test_"):
        return [pth]
    if not pth.is_relative_to(SRC):
        return []
    names = MODULE_TESTS.get(pth.stem, [f"test_{pth.stem}.py"])
    return [TESTS / name for name in names]


def main(changed: list[str]) -> int:
    """Run pytest on the tests for changed files, returning the exit code."""
    tests: list[pathlib.Path] = sorted(
        {t for f in changed for t in _tests_for(pathlib.Path(f))}
    )
    tests = [t for t in tests if t.exists()]
    if not tests:
        return 0
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-cov",
            "-m",
            FAST_MARKERS,
            *(str(t) for t in tests),
        ]
    )
    if result.returncode == NO_TESTS_COLLECTED:
        return 0
    return result.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
