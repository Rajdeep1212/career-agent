"""DEMO_MODE is isolated from real user state, credentials and outside services.

Each check runs the app in a subprocess, because settings are read once at import.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROBE = r'''
import json, socket
from unittest.mock import patch
from fastapi.testclient import TestClient
import httpx

from app.core.config import settings
from app.main import app

out = {"data_dir": settings.data_dir, "ignored": settings.demo_ignored,
       "keys_blank": all(getattr(settings, name) in (None, "") for name in ("rapidapi_key", "google_client_id", "token_encryption_key", "jooble_api_key"))}
origin = {"Origin": settings.app_origin}
with TestClient(app, base_url=settings.app_origin) as client:
    out["google_login"] = client.get("/auth/google/login", follow_redirects=False).status_code
    out["google_status"] = client.get("/auth/google/status").status_code
    out["linkedin_status"] = client.get("/auth/linkedin/status").status_code
    out["profile_name"] = client.get("/profile/current").json()["name"]
    search = client.post("/agent/search", json={"query": "Find machine learning fresher jobs"}, headers=origin).json()
    out.update(provider=search["provider"], results=search["result_count"], errors=search["diagnostics"]["errors"],
               states=sorted({job["verification_state"] for job in search["results"]}))
    draft = client.post("/email/drafts", json={"recipient": "someone@example.com", "subject": "Hi", "body": "Hello"}, headers=origin).json()
    client.post(f"/email/drafts/{draft['id']}/approve", headers=origin)
    out["send_route"] = client.post(f"/email/drafts/{draft['id']}/send", headers=origin).status_code
from app.services.email_send_boundary import send_draft_once
try:
    send_draft_once(draft["id"])
    out["direct_send"] = "sent"
except Exception as exc:
    out["direct_send"] = type(exc).__name__
# DNS fails loudly, so a missing guard shows up as "DNS reached" instead of a live request.
with patch("socket.getaddrinfo", side_effect=AssertionError("DNS reached")):
    try:
        httpx.HTTPTransport().handle_request(httpx.Request("GET", "https://example.invalid/"))
        out["outbound"] = "allowed"
    except Exception as exc:
        out["outbound"] = str(exc)
print("RESULT " + json.dumps(out))
'''


def _snapshot(folder: Path) -> dict:
    if not folder.exists():
        return {}
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def _run(env_updates: dict, code: str = PROBE):
    env = {**os.environ, **env_updates}
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)


class DemoModeIsolationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.real = self.root / "real"
        self.real.mkdir()
        (self.real / "current_profile.json").write_text('{"name": "Real Person", "skills": ["Secret"]}', encoding="utf-8")
        (self.real / "agent.sqlite3").write_bytes(b"real database bytes")
        (self.real / "legacy_history.sqlite3").write_bytes(b"legacy history bytes")
        self.demo = self.root / "demo"
        self.env = {
            "DEMO_MODE": "true", "DEMO_DATA_DIR": str(self.demo), "DATA_DIR": str(self.real),
            "UPLOAD_DIR": str(self.real / "uploads"), "LEGACY_HISTORY_PATH": str(self.real / "legacy_history.sqlite3"),
            "RAPIDAPI_KEY": "real-rapid-key", "ADZUNA_APP_ID": "id", "ADZUNA_APP_KEY": "real-adzuna-key",
            "JOOBLE_API_KEY": "real-jooble-key", "GOOGLE_CLIENT_ID": "real-client", "GOOGLE_CLIENT_SECRET": "real-secret",
            "LINKEDIN_CLIENT_ID": "real-li", "LINKEDIN_CLIENT_SECRET": "real-li-secret",
            "TOKEN_ENCRYPTION_KEY": "real-fernet-key", "JOB_PROVIDERS": "jsearch,adzuna,jooble",
        }

    def test_demo_mode_is_fully_isolated(self):
        real_before, repo_before = _snapshot(self.real), _snapshot(ROOT / "data")
        result = _run(self.env)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        out = json.loads(result.stdout.split("RESULT ", 1)[1])

        self.assertEqual(Path(out["data_dir"]).resolve(), self.demo.resolve())
        self.assertTrue(out["keys_blank"])
        self.assertIn("rapidapi_key", out["ignored"])
        self.assertNotIn("real-rapid-key", result.stdout + result.stderr)
        self.assertEqual((out["google_login"], out["google_status"], out["linkedin_status"]), (404, 404, 404))
        self.assertIn("synthetic", out["profile_name"])
        self.assertEqual(out["provider"], "Demo (synthetic)")
        self.assertGreater(out["results"], 0)
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["states"], ["UNVERIFIED"])
        self.assertEqual(out["send_route"], 404)
        self.assertEqual(out["direct_send"], "DraftNotClaimableError")
        self.assertIn("disabled in demo mode", out["outbound"])

        self.assertEqual(_snapshot(self.real), real_before, "real data directory changed")
        self.assertEqual(_snapshot(ROOT / "data"), repo_before, "repository data/ changed")
        self.assertTrue((self.demo / "current_profile.json").exists())

    def test_demo_directory_must_not_be_the_real_one(self):
        for demo_dir in (self.real, self.real / "demo-inside-real"):
            result = _run({**self.env, "DEMO_DATA_DIR": str(demo_dir)}, "import app.core.config")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("DEMO_DATA_DIR must be separate", result.stderr)


if __name__ == "__main__":
    unittest.main()
