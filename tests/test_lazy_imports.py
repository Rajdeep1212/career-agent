"""Optional chat dependencies load only when the chat endpoints are used."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LazyImportTests(unittest.TestCase):
    def test_app_starts_without_importing_langgraph(self):
        code = "import sys, app.main; print(any(m.split('.')[0] in ('langgraph', 'langchain_core', 'langchain_ollama') for m in sys.modules))"
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(result.stdout.strip(), "False")


if __name__ == "__main__":
    unittest.main()
