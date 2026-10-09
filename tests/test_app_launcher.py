"""The desktop launcher (scripts/start_app.ps1, install_app.ps1): Windows only, and nothing real is started or installed.

`-PlanOnly` prints what the launcher would do and exits; the shortcuts are written to temporary folders.
"""
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell")


def run(script: str, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                           str(ROOT / "scripts" / script), *arguments], capture_output=True, text=True, timeout=120)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@unittest.skipUnless(sys.platform == "win32" and POWERSHELL, "the launcher is for Windows PowerShell")
class LauncherTests(unittest.TestCase):
    def plan(self, api: int, web: int) -> dict:
        result = run("start_app.ps1", "-PlanOnly", "-ApiPort", str(api), "-WebPort", str(web))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_a_server_that_is_already_running_is_skipped(self):
        with socket.socket() as running:
            running.bind(("127.0.0.1", 0))
            running.listen()
            plan = self.plan(running.getsockname()[1], free_port())
        self.assertEqual((plan["api"], plan["web"]), ("skip", "start"))

    def test_both_are_started_when_nothing_is_running(self):
        plan = self.plan(free_port(), free_port())
        self.assertEqual((plan["api"], plan["web"]), ("start", "start"))

    def test_both_servers_are_bound_to_this_computer_only(self):
        plan = self.plan(free_port(), free_port())
        for name in ("api_command", "web_command"):
            self.assertIn("127.0.0.1", plan[name], name)
            self.assertNotIn("0.0.0.0", plan[name], name)
        self.assertRegex(plan["window_url"], r"^http://localhost:\d+/app$")   # the board, and never 127.0.0.1

    def test_shortcuts_are_created_and_removed_for_this_user_only(self):
        with tempfile.TemporaryDirectory() as startup, tempfile.TemporaryDirectory() as desktop:
            folders = ("-StartupFolder", startup, "-DesktopFolder", desktop)
            result = run("install_app.ps1", "-AutoStart", "on", "-DesktopShortcut", "on", *folders)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual([path.name for path in Path(startup).iterdir()], ["Career Agent (start at login).lnk"])
            self.assertEqual([path.name for path in Path(desktop).iterdir()], ["Career Agent.lnk"])
            result = run("install_app.ps1", "-AutoStart", "off", "-DesktopShortcut", "off", *folders)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(list(Path(startup).iterdir()) + list(Path(desktop).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
