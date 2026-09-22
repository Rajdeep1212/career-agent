import json
import gc
import logging
from pathlib import Path
import sqlite3
import tempfile
import time
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.storage import oauth_state, token_store


class LinkedInTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(gc.collect)
        self.db = Path(self.temp.name) / "agent.sqlite3"
        for module in (oauth_state, token_store):
            p = patch.object(module, "DB_PATH", self.db)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(settings.__dict__, {
            "linkedin_client_id": "test-client-id", "linkedin_client_secret": "test-client-secret",
            "linkedin_redirect_uri": "http://localhost:8010/auth/linkedin/callback",
            "token_encryption_key": Fernet.generate_key().decode(),
        })
        p.start()
        self.addCleanup(p.stop)
        self.client = TestClient(app, base_url="http://localhost:8010", follow_redirects=False)
        self.addCleanup(self.client.close)

    def login(self):
        response = self.client.get("/auth/linkedin/login")
        self.assertEqual(response.status_code, 303)
        return parse_qs(urlsplit(response.headers["location"]).query)["state"][0], response

    def callback(self, state=None, **kwargs):
        params = {"code": "test-auth-code", **kwargs}
        if state is not None:
            params["state"] = state
        return self.client.get("/auth/linkedin/callback", params=params)

    def assert_result(self, response, result):
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], f"http://localhost:8010/app/?linkedin={result}")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        for secret in ("test-auth-code", "test-client-secret", "test-access-token", "test-refresh-token", "test-id-token"):
            self.assertNotIn(secret, response.text + str(response.headers))

    def provider(self, token=None, profile=None, failure=None, pause=None):
        token = token if token is not None else {
            "access_token": "test-access-token", "expires_in": 3600,
            "token_type": "Bearer", "refresh_token": "test-refresh-token", "id_token": "test-id-token",
        }
        profile = profile if profile is not None else {
            "sub": "member-123", "name": "Test Member", "email": "member@example.com",
            "email_verified": True, "picture": "https://example.com/photo.png",
        }
        def handler(request):
            if failure:
                raise httpx.ReadTimeout("test-access-token", request=request)
            if str(request.url) == "https://www.linkedin.com/oauth/v2/accessToken":
                if pause:
                    pause[0].set()
                    if not pause[1].wait(10):
                        raise AssertionError("Callback coordination timed out")
                form = parse_qs(request.content.decode())
                self.assertEqual(form["grant_type"], ["authorization_code"])
                self.assertEqual(form["client_secret"], ["test-client-secret"])
                self.assertEqual(form["redirect_uri"], ["http://localhost:8010/auth/linkedin/callback"])
                self.assertEqual(form["code"], ["test-auth-code"])
                return httpx.Response(200, json=token)
            self.assertEqual(str(request.url), "https://api.linkedin.com/v2/userinfo")
            self.assertEqual(request.headers["authorization"], "Bearer test-access-token")
            return httpx.Response(200, json=profile)
        original = httpx.AsyncClient
        def factory(**kwargs):
            self.assertIs(kwargs["verify"], True)
            self.assertIs(kwargs["follow_redirects"], False)
            return original(transport=httpx.MockTransport(handler), **kwargs)
        return patch("app.services.linkedin_service.httpx.AsyncClient", side_effect=factory)

    def test_status_not_configured(self):
        settings.__dict__["linkedin_client_secret"] = ""
        r = self.client.get("/auth/linkedin/status")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"configured": False, "connected": False})

    def test_status_configured_disconnected(self):
        self.assertEqual(self.client.get("/auth/linkedin/status").json(), {"configured": True, "connected": False})

    def test_login_scope_state_and_cookie(self):
        state, r = self.login()
        query = parse_qs(urlsplit(r.headers["location"]).query)
        self.assertEqual(query["scope"], ["openid profile email"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["client_id"], ["test-client-id"])
        self.assertEqual(query["redirect_uri"], ["http://localhost:8010/auth/linkedin/callback"])
        self.assertGreaterEqual(len(state), 43)
        self.assertIn("HttpOnly", r.headers["set-cookie"])
        self.assertIn("SameSite=lax", r.headers["set-cookie"])
        self.assertIn("Max-Age=600", r.headers["set-cookie"])
        self.assertNotEqual(self.login()[0], state)

    def test_callback_missing_state(self):
        self.assert_result(self.callback(), "invalid_state")

    def test_callback_wrong_state(self):
        self.login()
        self.assert_result(self.callback("incorrect"), "invalid_state")

    def test_callback_requires_same_browser(self):
        state, _ = self.login()
        self.client.cookies.clear()
        self.assert_result(self.callback(state), "invalid_state")

    def test_denial_is_safe_and_consumes_state(self):
        state, _ = self.login()
        cookie = self.client.cookies.get("linkedin_oauth")
        self.assert_result(self.callback(state, error="access_denied", error_description="test-client-secret"), "denied")
        self.client.cookies.set("linkedin_oauth", cookie)
        self.assert_result(self.callback(state), "invalid_state")

    def test_success_encrypted_storage_safe_status_and_replay(self):
        state, _ = self.login()
        cookie = self.client.cookies.get("linkedin_oauth")
        with self.provider():
            self.assert_result(self.callback(state), "connected")
        status = self.client.get("/auth/linkedin/status")
        self.assertEqual(status.json(), {"configured": True, "connected": True,
            "name": "Test Member", "email": "member@example.com", "picture": "https://example.com/photo.png"})
        self.assertNotIn("token", status.text)
        self.assertNotIn("secret", status.text)
        raw = self.db.read_bytes()
        for value in (b"test-access-token", b"test-refresh-token", b"test-id-token", b"member@example.com"):
            self.assertNotIn(value, raw)
        saved = token_store.load_token("linkedin")
        self.assertEqual(saved["access_token"], "test-access-token")
        self.assertNotIn("id_token", saved)
        self.client.cookies.set("linkedin_oauth", cookie)
        self.assert_result(self.callback(state), "invalid_state")

    def test_invalid_key_prevents_login(self):
        settings.__dict__["token_encryption_key"] = "bad-key"
        self.assert_result(self.client.get("/auth/linkedin/login"), "not_configured")

    def test_invalid_redirect_prevents_login(self):
        settings.__dict__["linkedin_redirect_uri"] = "http://127.0.0.1:8010/auth/linkedin/callback"
        self.assert_result(self.client.get("/auth/linkedin/login"), "not_configured")

    def test_provider_failure_is_safe(self):
        state, _ = self.login()
        with self.provider(failure=True):
            self.assert_result(self.callback(state), "failed")
        self.assertIsNone(token_store.load_token("linkedin"))

    def test_malformed_provider_responses_not_saved(self):
        cases = [( [], None), ({"access_token": "", "expires_in": 3600}, None),
                 ({"access_token": "test-access-token", "expires_in": -1}, None),
                 (None, {}), (None, {"sub": 123}), (None, {"sub": "member", "name": []})]
        for token, profile in cases:
            with self.subTest(token=type(token).__name__, profile=type(profile).__name__):
                state, _ = self.login()
                with self.provider(token=token, profile=profile):
                    self.assert_result(self.callback(state), "failed")
                self.assertIsNone(token_store.load_token("linkedin"))

    def test_optional_email_not_required(self):
        state, _ = self.login()
        with self.provider(profile={"sub": "member"}):
            self.assert_result(self.callback(state), "connected")
        self.assertTrue(self.client.get("/auth/linkedin/status").json()["connected"])

    def test_disconnect_clears_only_linkedin_and_pending_state(self):
        token_store.save_token("linkedin", {"access_token": "test-access-token"})
        token_store.save_token("gmail", {"token": "gmail-test-token"})
        state, _ = self.login()
        cookie = self.client.cookies.get("linkedin_oauth")
        r = self.client.post("/auth/linkedin/disconnect", headers={"Origin": "http://localhost:8010"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"connected": False})
        self.assertIsNone(token_store.load_token("linkedin"))
        self.assertEqual(token_store.load_token("gmail"), {"token": "gmail-test-token"})
        self.client.cookies.set("linkedin_oauth", cookie)
        self.assert_result(self.callback(state), "invalid_state")

    def test_disconnect_rejects_cross_site_and_missing_origin(self):
        for origin in (None, "https://attacker.example", "null"):
            r = self.client.post("/auth/linkedin/disconnect", headers={"Origin": origin} if origin else {})
            self.assertEqual(r.status_code, 403)

    def test_disconnect_cancels_callback_already_exchanging_code(self):
        state, _ = self.login()
        requested, resume = threading.Event(), threading.Event()
        with self.provider(pause=(requested, resume)), ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.callback, state)
            try:
                self.assertTrue(requested.wait(5))
                response = self.client.post("/auth/linkedin/disconnect", headers={"Origin": "http://localhost:8010"})
                self.assertEqual(response.status_code, 200)
            finally:
                resume.set()
            self.assert_result(pending.result(timeout=5), "failed")
        self.assertIsNone(token_store.load_token("linkedin"))

    def test_loopback_login_redirects_before_setting_cookie(self):
        r = self.client.get("http://127.0.0.1:8010/auth/linkedin/login")
        self.assertEqual(r.headers["location"], "http://localhost:8010/auth/linkedin/login")
        self.assertNotIn("set-cookie", r.headers)

    def test_host_rebinding_rejected(self):
        self.assertEqual(self.client.get("http://attacker.example/auth/linkedin/status").status_code, 400)

    def test_expired_connection_is_disconnected(self):
        token_store.save_token("linkedin", {"access_token": "old-token", "expires_at": time.time() - 10,
            "profile": {"sub": "member", "name": "Old Member"}})
        self.assertFalse(self.client.get("/auth/linkedin/status").json()["connected"])

    def test_health_unchanged(self):
        self.assertEqual(self.client.get("/health").json()["status"], "ok")

    def test_expired_state_cannot_be_consumed(self):
        state, _ = self.login()
        from contextlib import closing
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE oauth_bound_states SET expires_at=?", (time.time() - 1,))
        self.assert_result(self.callback(state), "invalid_state")

    def test_concurrent_state_consumption_is_single_use(self):
        state = oauth_state.create_bound_state("linkedin", "browser")
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: oauth_state.consume_bound_state("linkedin", state, "browser"), range(6)))
        self.assertEqual(sum(results), 1)

    def test_provider_states_are_isolated_and_gmail_still_works(self):
        state = oauth_state.create_bound_state("linkedin", "browser")
        self.assertFalse(oauth_state.consume_bound_state("gmail", state, "browser"))
        self.assertTrue(oauth_state.consume_bound_state("linkedin", state, "browser"))
        google = oauth_state.create_state()
        self.assertTrue(oauth_state.consume_state(google))
        self.assertFalse(oauth_state.consume_state(google))

    def test_callback_missing_code_consumes_state(self):
        state, _ = self.login()
        self.assert_result(self.client.get("/auth/linkedin/callback", params={"state": state}), "failed")

    def test_unsafe_picture_omitted(self):
        state, _ = self.login()
        with self.provider(profile={"sub": "member", "picture": "javascript:alert(1)"}):
            self.assert_result(self.callback(state), "connected")
        self.assertNotIn("picture", self.client.get("/auth/linkedin/status").json())

    def test_storage_error_does_not_expose_exception(self):
        with patch("app.services.linkedin_service.load_token", side_effect=RuntimeError("test-access-token")):
            r = self.client.get("/auth/linkedin/status")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"configured": True, "connected": False, "error": "storage_unavailable"})

    def test_logging_redacts_callback_query(self):
        from app.core.oauth_logging import OAuthQueryFilter
        access = logging.LogRecord("uvicorn.access", logging.INFO, "", 0,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1", "GET", "/auth/linkedin/callback?code=test-auth-code&state=private-state", "1.1", 303), None)
        OAuthQueryFilter().filter(access)
        self.assertNotIn("test-auth-code", access.getMessage())
        self.assertNotIn("private-state", access.getMessage())
        http = logging.LogRecord("httpx", logging.INFO, "", 0, 'HTTP %s',
            ("http://localhost:8010/auth/linkedin/callback?code=test-auth-code&state=private-state",), None)
        OAuthQueryFilter().filter(http)
        self.assertNotIn("test-auth-code", http.getMessage())
        self.assertNotIn("private-state", http.getMessage())


if __name__ == "__main__":
    unittest.main()
