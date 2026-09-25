from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.origin_security import has_exact_local_origin
from app.api.linkedin import router as linkedin_router
from app.api.career import router as career_router
from app.api.chat import router as chat_router
from app.core.oauth_logging import install_oauth_log_filter
from app.core.preferences import DEFAULT_JOB_PREFERENCES
from app.models.schemas import (
    CandidateProfile,
    JobSearchPreferences,
    RankJobsRequest,
    CreateEmailDraftRequest,
    AgentSearchRequest,
    PrepareJobEmailRequest,
    CandidateProfileUpdate,
    JobSearchPreferencesUpdate,
)
from app.services.cv_parser import extract_pdf_text, parse_profile_from_text
from app.services.ranker import rank_jobs
from app.providers.mock_provider import MockJobProvider
from app.services.search_pipeline import search_verify_rank
from app.services.career_agent import CareerAgent
from app.services.gmail_service import (
    build_authorization_url,
    exchange_callback,
    gmail_status,
    send_approved_email,
    GmailNotConfiguredError,
    GmailNotConnectedError,
    GmailDependencyError,
)
from app.services.email_send_boundary import (
    DraftNotClaimableError,
    DraftNotFoundError,
    SendResultUncertainError,
    send_draft_once,
)
from app.services.outreach_workflow import (
    OutreachConflictError,
    OutreachInputError,
    OutreachNotFoundError,
    draft_with_outreach_linkage,
    prepare_linked_outreach,
)
from app.storage.token_store import delete_token, TokenStoreError
from app.storage.attachment_store import save_attachment
from app.storage.email_store import (
    create_draft,
    get_draft,
    list_drafts,
    approve_draft,
    cancel_draft,
)
from app.storage.profile_store import load_profile, save_profile
from app.storage.preference_store import preferences_for, save_preferences


install_oauth_log_filter()
app = FastAPI(title=settings.app_name)
app.include_router(linkedin_router)
app.include_router(career_router)
app.include_router(chat_router)
career_agent = CareerAgent()

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root():
    return RedirectResponse(url="/app/")


@app.get("/app/")
def dashboard(request: Request):
    if request.url.hostname == "127.0.0.1":
        return RedirectResponse("http://localhost:8010/app/", status_code=303)
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/connections/search/status")
def search_connection_status():
    # Configuration only; no paid provider call just to render Connections.
    return {"configured": bool(settings.rapidapi_key)}


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name}


@app.get("/profile/current", response_model=CandidateProfile)
def current_profile():
    return load_profile()


@app.put("/profile/current", response_model=CandidateProfile)
def update_current_profile(update: CandidateProfileUpdate, request: Request):
    require_local_origin(request)
    data = load_profile().model_dump()
    data.update(update.model_dump(exclude_unset=True))
    try:
        profile = CandidateProfile.model_validate(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Profile update is invalid.") from exc
    return save_profile(profile)


@app.get("/profile/default", response_model=CandidateProfile)
def default_profile():
    return load_profile()


@app.get("/preferences/default", response_model=JobSearchPreferences)
def default_preferences():
    return DEFAULT_JOB_PREFERENCES


@app.get("/preferences/current", response_model=JobSearchPreferences)
def current_preferences():
    return preferences_for(load_profile())


@app.put("/preferences/current", response_model=JobSearchPreferences)
def update_current_preferences(update: JobSearchPreferencesUpdate, request: Request):
    require_local_origin(request)
    data = preferences_for(load_profile()).model_dump()
    data.update(update.model_dump(exclude_unset=True))
    try:
        preferences = JobSearchPreferences.model_validate(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Preference update is invalid.") from exc
    return save_preferences(preferences)


@app.post("/cv/parse", response_model=CandidateProfile)
async def parse_cv(request: Request, file: UploadFile = File(...)):
    require_local_origin(request)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF CVs are supported.")

    with NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        parsed = parse_profile_from_text(extract_pdf_text(tmp_path))
        baseline = load_profile()
        parsed.preferred_locations = baseline.preferred_locations
        parsed.preferred_roles = baseline.preferred_roles
        return parsed
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.post("/cv/upload")
async def upload_cv(request: Request, file: UploadFile = File(...)):
    require_local_origin(request)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF CVs are supported.")

    content = await file.read()

    with NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        parsed = parse_profile_from_text(extract_pdf_text(tmp_path))
        baseline = load_profile()
        parsed.preferred_locations = baseline.preferred_locations
        parsed.preferred_roles = baseline.preferred_roles
        save_profile(parsed)
        attachment = save_attachment(file.filename, content)

        return {
            "profile": parsed,
            "attachment": {
                "id": attachment["id"],
                "original_name": attachment["original_name"],
                "size_bytes": attachment["size_bytes"],
            },
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.post("/jobs/rank")
def rank(request: RankJobsRequest):
    return rank_jobs(
        request.profile,
        request.jobs,
        request.preferences or DEFAULT_JOB_PREFERENCES,
    )


@app.get("/jobs/demo-search")
async def demo_search(query: str = "AI ML Python fresher India"):
    return await MockJobProvider().search(query)


@app.get("/jobs/demo-search-and-rank")
async def demo_search_and_rank(query: str = "AI ML Python fresher India"):
    jobs = await MockJobProvider().search(query)
    return {
        "query": query,
        "profile": load_profile(),
        "preferences": DEFAULT_JOB_PREFERENCES,
        "results": rank_jobs(load_profile(), jobs, DEFAULT_JOB_PREFERENCES),
    }


@app.get("/jobs/search-and-rank")
async def real_search_and_rank(
    query: str = "AI ML Python fresher India",
    include_seen: bool = False,
):
    try:
        return await search_verify_rank(query, include_seen=include_seen)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/agent/search")
async def agent_search(request: AgentSearchRequest):
    try:
        result = await career_agent.search(
            request.query,
            include_seen=request.include_seen,
            session_id=request.session_id,
            strict_mode=request.strict_mode,
        )
        return {
            "agent_action": "search_verify_rank",
            **result,
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/attachments")
async def upload_attachment(request: Request, file: UploadFile = File(...)):
    require_local_origin(request)
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required.")

    try:
        attachment = save_attachment(file.filename, await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Never expose the local filesystem path to the browser.
    return {key: attachment[key] for key in ("id", "original_name", "mime_type", "size_bytes")}


# -------------------------
# Gmail OAuth
# -------------------------

@app.get("/auth/google/status")
def google_status():
    return gmail_status()


@app.get("/auth/google/login")
def google_login():
    try:
        url = build_authorization_url()
    except (GmailNotConfiguredError, TokenStoreError, GmailDependencyError) as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return RedirectResponse(url=url)


@app.get("/auth/google/callback")
def google_callback(code: str, state: str):
    try:
        exchange_callback(code=code, state=state)
        return RedirectResponse(url="/app/?gmail=connected")
    except (ValueError, GmailNotConfiguredError, TokenStoreError, GmailDependencyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/auth/google/disconnect")
def google_disconnect(request: Request):
    require_local_origin(request)
    delete_token("gmail")
    return {"connected": False}


# -------------------------
# Email drafting / approval
# -------------------------

def require_local_origin(request: Request) -> None:
    if not has_exact_local_origin(request):
        raise HTTPException(
            status_code=403,
            detail="Open the localhost dashboard to make this change.",
        )


@app.post("/agent/prepare-email")
def prepare_job_email(request: PrepareJobEmailRequest, http_request: Request):
    require_local_origin(http_request)
    try:
        return prepare_linked_outreach(request)
    except OutreachNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OutreachConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OutreachInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/email/drafts")
def create_email_draft(request: CreateEmailDraftRequest, http_request: Request):
    require_local_origin(http_request)
    return create_draft(
        recipient=request.recipient,
        subject=request.subject,
        body=request.body,
        attachment_id=request.attachment_id,
    )


@app.get("/email/drafts")
def get_email_drafts(limit: int = 50):
    limit = max(1, min(limit, 100))
    return [draft_with_outreach_linkage(draft) for draft in list_drafts(limit=limit)]


@app.get("/email/drafts/{draft_id}")
def get_email_draft(draft_id: int):
    draft = get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return draft_with_outreach_linkage(draft)


@app.post("/email/drafts/{draft_id}/approve")
def approve_email_draft(draft_id: int, request: Request):
    require_local_origin(request)
    draft = approve_draft(draft_id)
    if not draft:
        raise HTTPException(
            status_code=409,
            detail="Draft could not be approved. It may already be approved/sent/cancelled.",
        )
    return {
        **draft,
        "message": "Approved. Sending still requires a separate Send action.",
    }


@app.post("/email/drafts/{draft_id}/cancel")
def cancel_email_draft(draft_id: int, request: Request):
    require_local_origin(request)
    draft = cancel_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=409, detail="Draft could not be cancelled.")
    return draft


@app.post("/email/drafts/{draft_id}/send")
def send_email_draft(draft_id: int, request: Request):
    require_local_origin(request)
    try:
        return send_draft_once(draft_id, sender=send_approved_email)
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DraftNotClaimableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SendResultUncertainError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "The Gmail send result is uncertain. This draft is locked and "
                "will not be retried automatically. Create and approve a new draft if needed."
            ),
        ) from exc
