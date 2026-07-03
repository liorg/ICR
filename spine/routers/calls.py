"""
POST /calls           — create new call (+ scenario snapshot + runtime entry)
GET  /calls/{call_id} — get call with leaves
PATCH /calls/{call_id} — update status
POST /calls/{call_id}/summary — Worker sends summary at end
"""
import uuid, logging, json
from datetime import datetime, timezone
from typing import Optional, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from dependencies import get_supabase

router = APIRouter(prefix="/calls", tags=["calls"])
log = logging.getLogger("spine.calls")

def _now():
    return datetime.now(timezone.utc).isoformat()

class CreateCallReq(BaseModel):
    scenario_id: str
    scenario_json: Any             # scenario snapshot
    phone_id: str
    contact_id: str
    contact_phone: Optional[str] = None
    contact_name: Optional[str] = None
    worker_service: Optional[str] = None

class UpdateCallReq(BaseModel):
    status: Optional[str] = None
    last_step_id: Optional[str] = None
    variables: Optional[dict] = None

class SummaryReq(BaseModel):
    status: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: int = 0
    last_step_id: str = ""
    variables: Optional[dict] = None
    leaves: Optional[list] = None
    sender_count: int = 0
    expected_count: int = 0
    mismatch_count: int = 0


@router.post("")
def create_call(req: CreateCallReq):
    db = get_supabase()
    call_id = f"call-{uuid.uuid4().hex[:12]}"

    # Snapshot scenario JSON
    snapshot = req.scenario_json if isinstance(req.scenario_json, dict) else json.loads(req.scenario_json) if isinstance(req.scenario_json, str) else {}

    db.table("spine_calls").insert({
        "call_id": call_id, "scenario_id": req.scenario_id,
        "scenario_snapshot": snapshot,
        "phone_id": req.phone_id, "contact_id": req.contact_id,
        "contact_phone": req.contact_phone, "contact_name": req.contact_name,
        "status": "running", "started_at": _now(),
    }).execute()

    # Create runtime entry
    db.table("spine_runtime").insert({
        "call_id": call_id, "phone_id": req.phone_id,
        "contact_id": req.contact_id, "status": "active",
        "worker_service": req.worker_service, "updated_at": _now(),
    }).execute()

    log.info("Call created | call=%s phone=%s contact=%s scenario=%s",
             call_id, req.phone_id, req.contact_id, req.scenario_id)
    return {"ok": True, "call_id": call_id}


@router.get("/{call_id}")
def get_call(call_id: str):
    db = get_supabase()
    call = db.table("spine_calls").select("*").eq("call_id", call_id).maybe_single().execute().data
    if not call:
        raise HTTPException(404, "Call not found")
    leaves = db.table("spine_leaves").select("*").eq("call_id", call_id).order("timestamp").execute().data or []
    return {"call": call, "leaves": leaves}


@router.patch("/{call_id}")
def update_call(call_id: str, req: UpdateCallReq):
    db = get_supabase()
    patch = {}
    if req.status:
        patch["status"] = req.status
        if req.status in ("completed", "failed", "expired"):
            patch["finished_at"] = _now()
    if req.last_step_id:
        patch["last_step_id"] = req.last_step_id
    if req.variables:
        patch["variables"] = req.variables
    if patch:
        db.table("spine_calls").update(patch).eq("call_id", call_id).execute()
        # Update runtime
        rt_patch = {"updated_at": _now()}
        if req.status:
            rt_patch["status"] = "completed" if req.status in ("completed", "failed", "expired") else "active"
        if req.last_step_id:
            rt_patch["current_step"] = req.last_step_id
        if req.variables:
            rt_patch["variables"] = req.variables
        db.table("spine_runtime").update(rt_patch).eq("call_id", call_id).execute()
    return {"ok": True}


@router.post("/{call_id}/summary")
def ingest_summary(call_id: str, s: SummaryReq):
    db = get_supabase()
    db.table("spine_calls").update({
        "status": s.status, "finished_at": s.finished_at or _now(),
        "duration_seconds": s.duration_seconds, "last_step_id": s.last_step_id,
        "variables": s.variables, "sender_count": s.sender_count,
        "expected_count": s.expected_count, "mismatch_count": s.mismatch_count,
    }).eq("call_id", call_id).execute()

    # Mark runtime completed
    db.table("spine_runtime").update({"status": "completed", "updated_at": _now()}).eq("call_id", call_id).execute()

    # Bulk upsert leaves
    if s.leaves:
        rows = [{
            "leaf_id": lf.get("leaf_id") or lf.get("leafId"),
            "call_id": call_id,
            "step_id": lf.get("step_id") or lf.get("stepId"),
            "type": lf.get("type", ""),
            "content": lf.get("content"),
            "wa_type": lf.get("wa_type") or lf.get("waType"),
            "status": lf.get("status", ""),
            "timestamp": lf.get("timestamp"),
        } for lf in s.leaves if isinstance(lf, dict)]
        if rows:
            db.table("spine_leaves").upsert(rows, on_conflict="leaf_id").execute()

    log.info("Summary | call=%s status=%s dur=%ds", call_id, s.status, s.duration_seconds)
    return {"ok": True}
