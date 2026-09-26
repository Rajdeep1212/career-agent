"""Read job-alert emails from the dedicated alerts inbox over IMAP (TLS, port 993).

Credentials come only from .env (ALERTS_IMAP_HOST, ALERTS_IMAP_USER,
ALERTS_IMAP_APP_PASSWORD) and are never logged or returned. Without them the
feature is skipped silently. Unread messages from the last ALERTS_LOOKBACK_DAYS
are fetched with BODY.PEEK[] (which does not mark them read), parsed and
stored; each is then flagged \\Seen. That flag is the only change made to the
mailbox: nothing is deleted, moved, copied or expunged. A dry run opens the
mailbox read-only and changes nothing.
"""
import imaplib
import logging
import re
import ssl
from datetime import date, timedelta

from app.core.config import settings
from app.sources.alerts.ingest import IngestOutcome, ingest_raw

logger = logging.getLogger(__name__)
PORT = 993
TIMEOUT_SECONDS = 30
_HOSTNAME = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", re.I)


def configured() -> bool:
    return bool(settings.alerts_imap_host and settings.alerts_imap_user and settings.alerts_imap_app_password)


def _raw_message(data) -> bytes | None:
    for part in data or []:
        if isinstance(part, tuple) and len(part) == 2 and isinstance(part[1], bytes):
            return part[1]
    return None


def ingest_imap(*, dry_run: bool = False, factory=imaplib.IMAP4_SSL, today: date | None = None) -> IngestOutcome:
    if settings.demo_mode or not configured():
        return IngestOutcome(status="skipped", note="alerts inbox is not configured")
    host = str(settings.alerts_imap_host).strip()
    if not _HOSTNAME.match(host):
        return IngestOutcome(status="error", note="ALERTS_IMAP_HOST must be a host name such as imap.gmail.com")
    since = ((today or date.today()) - timedelta(days=settings.alerts_lookback_days)).strftime("%d-%b-%Y")
    try:
        client = factory(host, PORT, ssl_context=ssl.create_default_context(), timeout=TIMEOUT_SECONDS)
    except (OSError, imaplib.IMAP4.error) as exc:
        logger.warning("alerts inbox: could not connect to the IMAP server (%s)", type(exc).__name__)
        return IngestOutcome(status="error", note=f"could not connect to the IMAP server ({type(exc).__name__})")
    outcome = IngestOutcome()
    try:
        try:
            client.login(settings.alerts_imap_user, settings.alerts_imap_app_password)
        except imaplib.IMAP4.error:
            # The server's reply can echo the user name; it is not passed on.
            logger.warning("alerts inbox: IMAP login failed")
            return IngestOutcome(status="error", note="IMAP login failed; check ALERTS_IMAP_USER and the app password in .env")
        status, _ = client.select(settings.alerts_imap_folder, readonly=dry_run)
        if status != "OK":
            return IngestOutcome(status="error", note=f"IMAP folder '{settings.alerts_imap_folder}' could not be opened")
        status, found = client.uid("search", None, "UNSEEN", "SINCE", since)
        uids = (found[0] or b"").split() if status == "OK" and found else []
        uids = uids[-settings.alerts_max_messages:]
        unread_left = 0
        for uid in uids:
            status, data = client.uid("fetch", uid, "(BODY.PEEK[])")
            raw = _raw_message(data) if status == "OK" else None
            if raw is None:
                unread_left += 1
                continue
            try:
                result = ingest_raw([("imap", raw)], dry_run=dry_run)
            except Exception as exc:   # left unread, so the next run tries again
                logger.warning("alerts inbox: a message could not be stored (%s)", type(exc).__name__)
                unread_left += 1
                continue
            outcome.merge(result)
            if not dry_run:
                client.uid("store", uid, "+FLAGS", "(\\Seen)")
        if unread_left:
            outcome.note = f"{unread_left} messages left unread for the next run"
        return outcome
    except (OSError, imaplib.IMAP4.error) as exc:
        logger.warning("alerts inbox: IMAP error (%s)", type(exc).__name__)
        return IngestOutcome(status="error", note=f"IMAP error ({type(exc).__name__}); nothing else was changed")
    finally:
        try:
            client.logout()
        except (OSError, imaplib.IMAP4.error):
            pass
