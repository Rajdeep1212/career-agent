"""Every PowerShell script parses (checked on Windows, including the Windows CI job)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted(ROOT.glob("*.ps1")) + sorted((ROOT / "scripts").glob("*.ps1"))
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


@unittest.skipUnless(POWERSHELL, "PowerShell is not available")
class PowerShellScriptTests(unittest.TestCase):
    def test_scripts_parse_without_errors(self):
        self.assertIn("03_SCHEDULE_DAILY_SYNC.ps1", [path.name for path in SCRIPTS])
        paths = json.dumps([str(path) for path in SCRIPTS])
        command = ("$errors = @(); foreach ($p in (ConvertFrom-Json '" + paths.replace("'", "''") + "')) { $e = $null; "
                   "[System.Management.Automation.Language.Parser]::ParseFile($p, [ref]$null, [ref]$e) | Out-Null; "
                   "foreach ($x in $e) { $errors += \"${p}: $($x.Message)\" } }; $errors -join \"`n\"")
        result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
