"""Exact-origin checks for browser-triggered mutations."""
from fastapi import Request

from app.core.config import settings


def has_exact_local_origin(request: Request) -> bool:
    origin = settings.app_origin
    return (
        str(request.base_url).rstrip("/") == origin
        and request.headers.get("origin") == origin
    )
