"""scripts/run_daily_sync.ps1, the scheduled task's runner: Windows only, run against a stub project, never a real sync.

The stub's sync prints one line per company. The runner is copied into a temporary project, so the real data\logs and
databases are never touched.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell")
STUB_SYNC = """import os, sys, time
print("alpha ok", flush=False)          # -u must put this on disk at once
time.sleep(float(os.environ.get("STUB_PAUSE", "0")))
print("beta ok")
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
"""


@unittest.skipUnless(sys.platform == "win32" and POWERSHELL, "the runner is for Windows PowerShell")
class DailySyncRunnerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.project = Path(directory.name)
        (self.project / "scripts").mkdir()
        for name in ("run_daily_sync.ps1", "python_env.ps1"):
            shutil.copy(ROOT / "scripts" / name, self.project / "scripts" / name)
        (self.project / "scripts" / "tracker_admin.py").write_text("print('Backed up the tracker (stub).')\n", encoding="utf-8")
        package = self.project / "app" / "sources"
        package.mkdir(parents=True)
        (self.project / "app" / "__init__.py").write_text("", encoding="utf-8")
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "sync.py").write_text(STUB_SYNC, encoding="utf-8")
        self.logs = self.project / "data" / "logs"

    def start(self, **env) -> subprocess.Popen:
        environment = {**os.environ, "CAREER_AGENT_PYTHON": sys.executable, **env}
        return subprocess.Popen([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                                 str(self.project / "scripts" / "run_daily_sync.ps1")], env=environment,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def main_log(self) -> str:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in self.logs.glob("sync-????-??-??.log"))

    def company_log(self) -> str:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in self.logs.glob("sync-*.out.log"))

    def wait_for(self, text: str, read, seconds: float = 30) -> None:
        deadline = time.monotonic() + seconds
        while text not in read():
            if time.monotonic() > deadline:
                self.fail(f"{text!r} never appeared in:\n{read()}")
            time.sleep(0.2)

    def test_a_finished_run_logs_its_start_each_company_and_its_exit(self):
        self.assertEqual(self.start(STUB_EXIT="3").wait(timeout=120), 3)
        self.assertIn("Starting Company Radar sync", self.main_log())
        self.assertIn("Finished with exit code 3", self.main_log())
        self.assertIn("alpha ok", self.company_log())
        self.assertIn("beta ok", self.company_log())

    def test_a_stopped_host_loses_no_company_and_the_next_run_says_it_did_not_finish(self):
        runner = self.start(STUB_PAUSE="4")
        self.wait_for("alpha ok", self.company_log)       # written while the run is still going
        runner.kill()                                      # what the scheduler does when the laptop goes on battery
        runner.wait(timeout=30)
        self.wait_for("beta ok", self.company_log)        # the sync carries on and its output still reaches the file
        self.assertNotIn("Finished with exit code", self.main_log())
        self.assertEqual(self.start().wait(timeout=120), 0)
        self.assertIn("did not record its exit", self.main_log())
        self.assertEqual(self.main_log().count("did not record its exit"), 1)

    def test_a_run_after_a_finished_one_reports_nothing_unusual(self):
        self.assertEqual(self.start().wait(timeout=120), 0)
        self.assertEqual(self.start().wait(timeout=120), 0)
        self.assertNotIn("did not record its exit", self.main_log())


if __name__ == "__main__":
    unittest.main()
