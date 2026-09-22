"""Exact localhost checks for browser-triggered mutations."""
from fastapi import Request


LOCAL_ORIGIN = "http://localhost:8010"


def has_exact_local_origin(request: Request) -> bool:
    return (
        str(request.base_url).rstrip("/") == LOCAL_ORIGIN
        and request.headers.get("origin") == LOCAL_ORIGIN
    )
