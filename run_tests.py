"""Run all tests offline with disposable storage and no real credentials."""
import os
import gc
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def main():
    with tempfile.TemporaryDirectory(prefix="job-agent-tests-") as directory:
        os.environ.update({
            "DATA_DIR": directory, "UPLOAD_DIR": str(Path(directory) / "uploads"),
            "RAPIDAPI_KEY": "", "GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "",
            "LINKEDIN_CLIENT_ID": "", "LINKEDIN_CLIENT_SECRET": "",
            "TOKEN_ENCRYPTION_KEY": "",
        })
        from app.storage import history
        history.DB_PATH = Path(directory) / "history.sqlite3"
        with patch("httpx.HTTPTransport.handle_request", side_effect=AssertionError("Live HTTP forbidden in tests")), \
             patch("httpcore._backends.auto.AutoBackend.connect_tcp", side_effect=AssertionError("Live TCP forbidden in tests")), \
             patch("httpcore._backends.auto.AutoBackend.connect_unix_socket", side_effect=AssertionError("Live sockets forbidden in tests")), \
             patch("socket.getaddrinfo", side_effect=AssertionError("Live DNS forbidden in tests")):
            # Guard the async network boundary so the TLS transport regression
            # can use real HTTP machinery with its explicit fake socket stream.
            # TestClient's in-process transport and event-loop socketpairs work.
            suite = unittest.defaultTestLoader.discover(".", pattern="test_*.py")
            result = unittest.TextTestRunner(verbosity=2).run(suite)
        gc.collect()
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
