"""IMAP ingestion from the dedicated alerts inbox, against a fake server (no network, no real credentials)."""
import imaplib
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.sources.alerts import imap
from app.storage import alert_store, db, radar_store

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "alerts"
PASSWORD = "abcd efgh ijkl mnop"   # fake app password
ALLOWED = {"login", "select", "search", "fetch", "store", "logout"}


class FakeImap:
    """Serves fixture emails; records every command; refuses anything but the allowed set."""

    instances: list["FakeImap"] = []

    def __init__(self, host, port, *, ssl_context=None, timeout=None):
        self.host, self.port, self.ssl_context, self.timeout = host, port, ssl_context, timeout
        self.commands: list[tuple] = []
        self.readonly = None
        self.messages = {b"11": (FIXTURES / "linkedin_alert.eml").read_bytes(),
                         b"12": (FIXTURES / "naukri_alert.eml").read_bytes()}
        self.seen: set[bytes] = set()
        self.fail_login = False
        FakeImap.instances.append(self)

    def login(self, user, password):
        self.commands.append(("login", user))
        if self.fail_login:
            raise imaplib.IMAP4.error(f"[AUTHENTICATIONFAILED] Invalid credentials for {user}")
        return "OK", [b"logged in"]

    def select(self, mailbox="INBOX", readonly=False):
        self.commands.append(("select", mailbox, readonly))
        self.readonly = readonly
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command, *args):
        command = command.lower()
        self.commands.append((command, *args))
        assert command in ALLOWED, command
        if command == "search":
            unseen = [uid for uid in self.messages if uid not in self.seen]
            return "OK", [b" ".join(unseen)]
        if command == "fetch":
            uid, spec = args
            assert spec == "(BODY.PEEK[])", "fetching must not mark messages read"
            return "OK", [(uid + b" (UID " + uid + b" BODY[] {1}", self.messages[uid]), b")"]
        if command == "store":
            assert not self.readonly, "flags changed on a read-only mailbox"
            uid, action, flags = args
            assert (action, flags) == ("+FLAGS", "(\\Seen)"), (action, flags)
            self.seen.add(uid)
            return "OK", [b""]
        raise AssertionError(command)

    def logout(self):
        self.commands.append(("logout",))
        return "BYE", [b""]

    def __getattr__(self, name):   # expunge, copy, move, append, delete... are never allowed
        raise AssertionError(f"IMAP command not allowed: {name}")


class ImapIngestTests(unittest.TestCase):
    def setUp(self):
        FakeImap.instances = []
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for target, values in ((alert_store, {"DB_PATH": root / "alerts.sqlite3"}),
                               (radar_store, {"DB_PATH": root / "radar.sqlite3"}),
                               (settings, {"alerts_imap_host": "imap.gmail.com", "alerts_imap_user": "alerts.inbox@example.com",
                                           "alerts_imap_app_password": PASSWORD, "alerts_imap_folder": "INBOX"})):
            patcher = patch.multiple(target, **values)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)

    def test_unread_alerts_are_fetched_with_peek_stored_then_marked_read(self):
        outcome = imap.ingest_imap(factory=FakeImap)
        server = FakeImap.instances[0]
        self.assertEqual((server.host, server.port), ("imap.gmail.com", 993))
        self.assertIsNotNone(server.ssl_context)
        self.assertEqual((outcome.status, outcome.messages, outcome.new), ("ok", 2, 5))
        self.assertEqual(server.seen, {b"11", b"12"})
        search = next(command for command in server.commands if command[0] == "search")
        self.assertEqual(search[1:3], (None, "UNSEEN"))
        self.assertIn("SINCE", search)
        self.assertLessEqual({command[0] for command in server.commands}, ALLOWED)

    def test_second_run_finds_nothing_new(self):
        imap.ingest_imap(factory=FakeImap)
        server = FakeImap.instances[0]

        def reuse(*args, **kwargs):
            return server
        again = imap.ingest_imap(factory=reuse)
        self.assertEqual((again.messages, again.new), (0, 0))

    def test_dry_run_is_read_only(self):
        with patch("builtins.print"):
            outcome = imap.ingest_imap(factory=FakeImap, dry_run=True)
        server = FakeImap.instances[0]
        self.assertTrue(server.readonly)
        self.assertEqual(server.seen, set())
        self.assertEqual(outcome.parsed_jobs, 5)
        self.assertFalse(alert_store.DB_PATH.exists())

    def test_skipped_silently_without_an_app_password(self):
        for missing in ("alerts_imap_app_password", "alerts_imap_user", "alerts_imap_host"):
            with self.subTest(missing=missing), patch.object(settings, missing, ""):
                outcome = imap.ingest_imap(factory=FakeImap)
                self.assertEqual(outcome.status, "skipped")
        self.assertEqual(FakeImap.instances, [])
        with patch.object(settings, "demo_mode", True):
            self.assertEqual(imap.ingest_imap(factory=FakeImap).status, "skipped")

    def test_login_failure_never_reveals_credentials(self):
        def failing(*args, **kwargs):
            server = FakeImap(*args, **kwargs)
            server.fail_login = True
            return server
        with self.assertLogs("app.sources.alerts.imap", level=logging.WARNING) as logs:
            outcome = imap.ingest_imap(factory=failing)
        self.assertEqual(outcome.status, "error")
        text = outcome.note + " ".join(logs.output)
        self.assertIn("login failed", text)
        for secret in (PASSWORD, "alerts.inbox@example.com"):
            self.assertNotIn(secret, text)

    def test_host_must_be_a_bare_hostname(self):
        with patch.object(settings, "alerts_imap_host", "https://imap.gmail.com/"):
            outcome = imap.ingest_imap(factory=FakeImap)
        self.assertEqual(outcome.status, "error")
        self.assertEqual(FakeImap.instances, [])

    def test_a_message_that_fails_to_store_stays_unread(self):
        with patch("app.sources.alerts.ingest.alert_store.upsert", side_effect=RuntimeError("disk full")):
            outcome = imap.ingest_imap(factory=FakeImap)
        self.assertEqual(FakeImap.instances[0].seen, set())
        self.assertIn("2 messages left unread", outcome.note)


if __name__ == "__main__":
    unittest.main()
