"""Run all tests offline with disposable storage and no real credentials.

Equivalent to `python -m pytest`; kept so the suite runs without dev tools.
"""
import gc
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tests"))

from _offline import configure_environment, network_guards  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="job-agent-tests-") as directory:
        configure_environment(directory)
        with ExitStack() as stack:
            for guard in network_guards():
                stack.enter_context(guard)
            suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py",
                                                        top_level_dir=str(ROOT / "tests"))
            result = unittest.TextTestRunner(verbosity=2).run(suite)
        gc.collect()
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
