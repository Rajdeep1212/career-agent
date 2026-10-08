"""Exact-origin checks for browser-triggered mutations."""
from fastapi import Request

from app.core.config import settings


def has_tracker_origin(request: Request) -> bool:
    """For /api/v1 only: the dashboard's origin or the tracker web app's (WEB_ORIGIN), each compared exactly.

    The web app reaches this server through its own proxy, so no CORS header is sent and no other route trusts it.
    """
    return (
        str(request.base_url).rstrip("/") == settings.app_origin
        and request.headers.get("origin") in (settings.app_origin, settings.web_origin)
    )


def has_exact_local_origin(request: Request) -> bool:
    origin = settings.app_origin
    return (
        str(request.base_url).rstrip("/") == origin
        and request.headers.get("origin") == origin
    )
