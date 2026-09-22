import base64
from email.message import EmailMessage
from pathlib import Path

from app.core.config import settings
from app.storage.attachment_store import get_attachment
from app.storage.oauth_state import create_state, consume_state
from app.storage.token_store import save_token, load_token, TokenStoreError


# Least privilege: send only.
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


class GmailNotConfiguredError(RuntimeError):
    pass


class GmailNotConnectedError(RuntimeError):
    pass


class GmailDependencyError(RuntimeError):
    pass


def _google_imports():
    """
    Import Google/Gmail dependencies only when Gmail functionality is used.
    This lets the rest of the dashboard start even before optional integrations
    are configured/installed.
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import Flow
        from googleapiclient.discovery import build
        return Request, Credentials, Flow, build
    except ImportError as exc:
        raise GmailDependencyError(
            "Google Gmail dependencies are not installed. "
            "Run: pip install -r requirements.txt"
        ) from exc


def _client_config():
    if not settings.google_client_id or not settings.google_client_secret:
        raise GmailNotConfiguredError(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not configured."
        )

    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_redirect_uri],
        }
    }


def build_authorization_url() -> str:
    _, _, Flow, _ = _google_imports()
    state = create_state()

    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        state=state,
    )
    flow.redirect_uri = settings.google_redirect_uri

    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return authorization_url


def exchange_callback(code: str, state: str) -> dict:
    _, _, Flow, _ = _google_imports()

    if not consume_state(state):
        raise ValueError("OAuth state is invalid or expired.")

    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        state=state,
    )
    flow.redirect_uri = settings.google_redirect_uri
    flow.fetch_token(code=code)

    credentials = flow.credentials

    data = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
        "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
    }
    save_token("gmail", data)
    return {"connected": True, "scopes": list(credentials.scopes or SCOPES)}


def _load_credentials():
    Request, Credentials, _, _ = _google_imports()

    data = load_token("gmail")
    if not data:
        raise GmailNotConnectedError("Gmail has not been connected yet.")

    credentials = Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes"),
    )

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        save_token(
            "gmail",
            {
                "token": credentials.token,
                "refresh_token": credentials.refresh_token,
                "token_uri": credentials.token_uri,
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
                "scopes": credentials.scopes,
                "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
            },
        )

    if not credentials.valid:
        raise GmailNotConnectedError("Stored Gmail credentials are not valid.")

    return credentials


def gmail_status() -> dict:
    configured = bool(settings.google_client_id and settings.google_client_secret)

    if not configured:
        return {
            "configured": False,
            "connected": False,
            "scope": SCOPES,
        }

    try:
        credentials = _load_credentials()
        return {
            "configured": True,
            "connected": bool(credentials.valid),
            "scope": SCOPES,
        }
    except (GmailNotConnectedError, TokenStoreError, GmailDependencyError):
        return {
            "configured": True,
            "connected": False,
            "scope": SCOPES,
        }


def build_message(
    recipient: str,
    subject: str,
    body: str,
    attachment_id: str | None = None,
) -> dict:
    message = EmailMessage()
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    if attachment_id:
        attachment = get_attachment(attachment_id)
        if not attachment:
            raise ValueError("Attachment not found.")

        path = Path(attachment["stored_path"])
        if not path.exists():
            raise ValueError("Stored attachment file is missing.")

        mime = attachment["mime_type"] or "application/octet-stream"
        maintype, subtype = mime.split("/", 1)

        message.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=attachment["original_name"],
        )

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    return {"raw": raw}


def send_approved_email(
    recipient: str,
    subject: str,
    body: str,
    attachment_id: str | None = None,
) -> dict:
    _, _, _, build = _google_imports()
    credentials = _load_credentials()
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)

    payload = build_message(
        recipient=recipient,
        subject=subject,
        body=body,
        attachment_id=attachment_id,
    )

    return service.users().messages().send(
        userId="me",
        body=payload,
    ).execute()
