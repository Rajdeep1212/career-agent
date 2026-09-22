"""Backend-only LinkedIn OAuth connection using the OIDC UserInfo endpoint.

This connects the local owner's account; it does not add multi-user app login.
ID tokens are not decoded, trusted, or persisted. Profile claims come only from
the fixed HTTPS UserInfo endpoint using the server-exchanged access token.
"""
import math
import time
from urllib.parse import urlencode, urlsplit

import httpx

from app.core.config import settings
from app.storage.token_store import load_token, save_token, validate_encryption_key

ORIGIN = "http://localhost:8010"
CALLBACK = ORIGIN + "/auth/linkedin/callback"
SCOPES = "openid profile email"
AUTHORIZATION_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"


class LinkedInConfigurationError(ValueError):
    pass


def validate_configuration():
    if (not settings.linkedin_client_id or not settings.linkedin_client_id.strip()
            or not settings.linkedin_client_secret or not settings.linkedin_client_secret.strip()
            or settings.linkedin_redirect_uri != CALLBACK):
        raise LinkedInConfigurationError("LinkedIn configuration is incomplete.")
    validate_encryption_key()


def authorization_url(state: str) -> str:
    return AUTHORIZATION_URL + "?" + urlencode({
        "response_type": "code", "client_id": settings.linkedin_client_id,
        "redirect_uri": settings.linkedin_redirect_uri, "scope": SCOPES, "state": state,
    })


def _profile(payload) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("sub"), str) or not payload["sub"].strip():
        raise ValueError("Invalid LinkedIn member response.")
    profile = {"sub": payload["sub"]}
    for key in ("name", "email", "picture"):
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or len(value) > 2048:
            raise ValueError("Invalid LinkedIn profile field.")
        if key == "picture":
            url = urlsplit(value)
            if url.scheme != "https" or not url.hostname or url.username or url.password:
                continue
        profile[key] = value
    return profile


async def exchange_code(code: str, generation: int) -> None:
    validate_configuration()
    # TLS validation cannot be disabled by the job-search VERIFY_SSL setting.
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds,
                                 verify=True, follow_redirects=False, trust_env=False) as client:
        response = await client.post(TOKEN_URL, data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": settings.linkedin_redirect_uri,
            "client_id": settings.linkedin_client_id,
            "client_secret": settings.linkedin_client_secret,
        }, headers={"Accept": "application/json"})
        response.raise_for_status()
        token = response.json()
        if not isinstance(token, dict):
            raise ValueError("Invalid LinkedIn token response.")
        access = token.get("access_token")
        lifetime = token.get("expires_in")
        if (not isinstance(access, str) or not access.strip()
                or any(c.isspace() for c in access)
                or isinstance(lifetime, bool) or not isinstance(lifetime, (int, float))
                or not math.isfinite(lifetime) or lifetime <= 0
                or str(token.get("token_type", "Bearer")).lower() != "bearer"):
            raise ValueError("Invalid LinkedIn token response.")
        issued_at = time.time()
        response = await client.get(USERINFO_URL, headers={
            "Authorization": "Bearer " + access, "Accept": "application/json",
        })
        response.raise_for_status()
        profile = _profile(response.json())
    # Allowlist fields; never persist the entire provider response or ID token.
    data = {"access_token": access, "expires_at": issued_at + lifetime, "profile": profile}
    refresh = token.get("refresh_token")
    if isinstance(refresh, str) and refresh:
        data["refresh_token"] = refresh
    save_token("linkedin", data, expected_generation=generation)


def connection_status() -> dict:
    try:
        validate_configuration()
    except (ValueError, RuntimeError):
        return {"configured": False, "connected": False}
    result = {"configured": True, "connected": False}
    try:
        data = load_token("linkedin")
        if not data or not data.get("access_token") or data.get("expires_at", 0) <= time.time():
            return result
        profile = _profile(data.get("profile"))
    except Exception:
        # Corrupt storage/key rotation must not reveal decrypted data or errors.
        return {**result, "error": "storage_unavailable"}
    return {**result, "connected": True,
            **{key: profile[key] for key in ("name", "email", "picture") if key in profile}}
