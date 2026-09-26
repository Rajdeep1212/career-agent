"""Offline test environment shared by run_tests.py and pytest (tests/conftest.py).

Storage goes to a disposable directory, every credential is blank, and live
HTTP, TCP and DNS raise. TestClient's in-process transport still works.
"""
import os
from pathlib import Path
from unittest.mock import patch

BLANK_SETTINGS = (
    "RAPIDAPI_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "JOOBLE_API_KEY",
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET",
    "TOKEN_ENCRYPTION_KEY",
    "ALERTS_IMAP_HOST", "ALERTS_IMAP_USER", "ALERTS_IMAP_APP_PASSWORD",
)


def configure_environment(directory: str) -> None:
    """Must run before any `app` module is imported."""
    os.environ.update({"DATA_DIR": directory, "UPLOAD_DIR": str(Path(directory) / "uploads"),
                       "JOB_PROVIDERS": "jsearch,adzuna,jooble", "DEMO_MODE": "false",
                       "LEGACY_HISTORY_PATH": "", "SEARCH_CACHE_HOURS": "0",
                       **{name: "" for name in BLANK_SETTINGS}})


def network_guards() -> list:
    return [
        patch("httpx.HTTPTransport.handle_request", side_effect=AssertionError("Live HTTP forbidden in tests")),
        patch("httpcore._backends.auto.AutoBackend.connect_tcp", side_effect=AssertionError("Live TCP forbidden in tests")),
        patch("httpcore._backends.auto.AutoBackend.connect_unix_socket", side_effect=AssertionError("Live sockets forbidden in tests")),
        patch("socket.getaddrinfo", side_effect=AssertionError("Live DNS forbidden in tests")),
    ]
