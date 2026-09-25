from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.agent.errors import ResumeConflictError, ThreadNotFoundError
from app.core.config import settings
from app.core.origin_security import has_exact_local_origin
from app.models.chat import ChatResponse, ChatResumeRequest, ChatRunRequest
from app.storage.graph_checkpoint import TurnConflictError, TurnInProgressError


router = APIRouter(prefix="/chat", tags=["Career chat"])
CHECKPOINT_PATH = Path(settings.langgraph_checkpoint_path)


def require_chat_origin(request: Request) -> None:
    if not has_exact_local_origin(request):
        raise HTTPException(status_code=403, detail="Open the localhost dashboard to run career actions.")


def _runtime():
    # LangGraph is optional; load it only when a chat endpoint is used.
    from app.agent.runtime import CareerGraphRuntime
    return CareerGraphRuntime(checkpoint_path=CHECKPOINT_PATH)


@router.post("/run", response_model=ChatResponse)
async def run_chat(payload: ChatRunRequest, request: Request):
    require_chat_origin(request)
    try:
        return await _runtime().run(payload)
    except (TurnConflictError, TurnInProgressError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="The chat request is invalid.") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="The requested career action is unavailable.") from exc


@router.post("/resume", response_model=ChatResponse)
async def resume_chat(payload: ChatResumeRequest, request: Request):
    require_chat_origin(request)
    try:
        return await _runtime().resume(payload)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ResumeConflictError, TurnConflictError, TurnInProgressError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="The draft update is invalid.") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="The send action could not be completed safely.") from exc

