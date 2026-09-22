import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.services import linkedin_service as linkedin
from app.storage.oauth_state import create_bound_state, consume_bound_state, clear_bound_states
from app.storage.token_store import delete_token, token_generation, TokenStoreError

router = APIRouter(prefix="/auth/linkedin", tags=["LinkedIn"])
COOKIE = "linkedin_oauth"
COOKIE_PATH = "/auth/linkedin"
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def _result(indicator):
    response = RedirectResponse(linkedin.ORIGIN + "/app/?linkedin=" + indicator,
                                status_code=303, headers=HEADERS)
    response.delete_cookie(COOKIE, path=COOKIE_PATH, httponly=True, samesite="lax")
    return response


def _local(request):
    return str(request.base_url).rstrip("/") == linkedin.ORIGIN


@router.get("/login")
def login(request: Request):
    if str(request.base_url).rstrip("/") == "http://127.0.0.1:8010":
        return RedirectResponse(linkedin.ORIGIN + "/auth/linkedin/login", status_code=303, headers=HEADERS)
    if not _local(request):
        return JSONResponse({"detail": "Use the localhost dashboard."}, status_code=400, headers=HEADERS)
    try:
        linkedin.validate_configuration()
        browser = secrets.token_urlsafe(32)
        state = create_bound_state("linkedin", browser)
        response = RedirectResponse(linkedin.authorization_url(state), status_code=303, headers=HEADERS)
        # HTTP is supported only for this fixed loopback origin. A hosted version
        # needs HTTPS, Secure cookies and per-user authorization/storage.
        response.set_cookie(COOKIE, browser, max_age=600, httponly=True,
                            samesite="lax", path=COOKIE_PATH)
        return response
    except (linkedin.LinkedInConfigurationError, TokenStoreError):
        return _result("not_configured")
    except Exception:
        return _result("failed")


@router.get("/callback")
async def callback(request: Request):
    if not _local(request):
        return _result("invalid_state")
    try:
        # Read manually: framework validation errors must not echo callback data.
        params = request.query_params
        state = params.get("state", "")
        browser = request.cookies.get(COOKIE, "")
        # Capture before claiming state. A later disconnect invalidates this
        # generation atomically with deleting the token, including in-flight I/O.
        generation = token_generation("linkedin")
        if (len(params.getlist("state")) != 1 or not state or len(state) > 256
                or not browser or len(browser) > 256
                or not consume_bound_state("linkedin", state, browser)):
            return _result("invalid_state")
        if "error" in params:
            return _result("denied" if params["error"] == "access_denied" else "failed")
        code = params.get("code", "")
        if len(params.getlist("code")) != 1 or not code or len(code) > 8192:
            return _result("failed")
        await linkedin.exchange_code(code, generation)
        return _result("connected")
    except (linkedin.LinkedInConfigurationError, TokenStoreError):
        return _result("not_configured")
    except Exception:
        # No raw provider body, exception, code, or credential is logged/returned.
        return _result("failed")


@router.get("/status")
def status(request: Request):
    if not _local(request):
        return JSONResponse({"detail": "Use the localhost dashboard."}, status_code=400, headers=HEADERS)
    return JSONResponse(linkedin.connection_status(), headers=HEADERS)


@router.post("/disconnect")
def disconnect(request: Request):
    # Origin is mandatory for this browser mutation; no permissive CORS.
    if not _local(request) or request.headers.get("origin") != linkedin.ORIGIN:
        return JSONResponse({"detail": "Open Connections on the localhost dashboard."}, status_code=403, headers=HEADERS)
    try:
        clear_bound_states("linkedin")
        delete_token("linkedin", invalidate_pending=True)
    except Exception:
        return JSONResponse({"detail": "Could not disconnect LinkedIn. Please try again."}, status_code=503, headers=HEADERS)
    response = JSONResponse({"connected": False}, headers=HEADERS)
    response.delete_cookie(COOKIE, path=COOKIE_PATH, httponly=True, samesite="lax")
    return response
