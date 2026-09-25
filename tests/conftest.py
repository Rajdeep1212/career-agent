"""pytest uses the same offline environment as run_tests.py."""
import shutil
import tempfile

from _offline import configure_environment, network_guards

_state = {}


def pytest_configure(config):
    directory = tempfile.mkdtemp(prefix="job-agent-tests-")
    configure_environment(directory)
    guards = network_guards()
    for guard in guards:
        guard.start()
    _state.update(directory=directory, guards=guards)


def pytest_unconfigure(config):
    for guard in _state.get("guards", []):
        guard.stop()
    shutil.rmtree(_state.get("directory", ""), ignore_errors=True)
