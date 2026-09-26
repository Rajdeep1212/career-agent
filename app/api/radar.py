"""Company Radar endpoints: sync status, a background "Sync now", and today's new jobs."""
import asyncio
import logging
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.origin_security import has_exact_local_origin
from app.sources.digest import build_digest
from app.sources.registry import RadarConfigError, read_config, syncable
from app.storage import radar_store

router = APIRouter(prefix="/radar", tags=["Company Radar"])
logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_STATE: dict = {"running": False, "started_at": None, "finished_at": None, "result": None, "error": None}


def _run_in_background() -> None:
    from app.sources.sync import run_cycle   # imported lazily: the sync pulls in every adapter
    try:
        result = asyncio.run(run_cycle())
        error = None
    except Exception as exc:   # reported in /radar/status, never raised into the server
        logger.exception("Company Radar sync failed")
        result, error = None, type(exc).__name__
    with _LOCK:
        _STATE.update(running=False, finished_at=datetime.now(timezone.utc).isoformat(), result=result, error=error)


@router.get("/status")
def status():
    try:
        companies = len(syncable(read_config()))
    except RadarConfigError:
        companies = 0
    last = radar_store.last_sync() if radar_store.DB_PATH.exists() else None
    with _LOCK:
        manual = dict(_STATE)
    return {"companies": companies, "last_sync": last, "manual_sync": manual}


@router.post("/sync")
def sync_now(request: Request):
    if not has_exact_local_origin(request):
        raise HTTPException(status_code=403, detail="Open the localhost dashboard to start a sync.")
    with _LOCK:
        if _STATE["running"]:
            raise HTTPException(status_code=409, detail="A Company Radar sync is already running.")
        _STATE.update(running=True, started_at=datetime.now(timezone.utc).isoformat(), finished_at=None, result=None, error=None)
    threading.Thread(target=_run_in_background, name="radar-sync", daemon=True).start()
    return JSONResponse(status_code=202, content={"started": True})


@router.get("/new-today")
def new_today():
    return build_digest().to_dict()
