"""Calls — uses EXISTING calls table. No spine_runtime."""
import uuid, json, logging
from datetime import datetime, timezone
from typing import Optional, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from dependencies import get_supabase

router = APIRouter(prefix="/calls", tags=["calls"])
log = logging.getLogger("spine.calls")
def _now(): return datetime.now(timezone.utc).isoformat()

class CreateCallReq(BaseModel):
    scenario_id: str
    scenario_json: Any
    phone_id: str
    contact_id: str
    contact_phone: Optional[str] = None
    contact_name: Optional[str] = None
    worker_service: Optional[str] = None

class SummaryReq(BaseModel):
    status: str = ""
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
    snapshot = req.scenario_json if isinstance(req.scenario_json, dict) else json.loads(req.scenario_json) if isinstance(req.scenario_json, str) else {}

    db.table("calls").insert({
        "id": call_id, "scenario_id": req.scenario_id,
        "scenario_snapshot": snapshot,
        "phone_id": req.phone_id, "contact_id": req.contact_id,
        "status": "running", "started_at": _now(),
    }).execute()

    log.info("Call created | %s phone=%s contact=%s", call_id, req.phone_id, req.contact_id)
    return {"ok": True, "call_id": call_id}

@router.get("/{call_id}")
def get_call(call_id: str):
    db = get_supabase()
    call = db.table("calls").select("*").eq("id", call_id).maybe_single().execute().data
    if not call: raise HTTPException(404, "Call not found")
    leaves = db.table("spine_leaves").select("*").eq("call_id", call_id).order("timestamp").execute().data or []
    messages = db.table("messages").select("*").eq("call_id", call_id).order("created_at").execute().data or []
    return {"call": call, "leaves": leaves, "messages": messages}

@router.post("/{call_id}/summary")
def ingest_summary(call_id: str, s: SummaryReq):
    db = get_supabase()
    db.table("calls").update({
        "status": s.status, "finished_at": s.finished_at or _now(),
        "duration_seconds": s.duration_seconds,
    }).eq("id", call_id).execute()

    if s.leaves:
        rows = [{
            "leaf_id": lf.get("leaf_id") or lf.get("leafId"), "call_id": call_id,
            "step_id": lf.get("step_id") or lf.get("stepId"), "type": lf.get("type", ""),
            "content": lf.get("content"), "wa_type": lf.get("wa_type") or lf.get("waType"),
            "status": lf.get("status", ""), "timestamp": lf.get("timestamp"),
        } for lf in s.leaves if isinstance(lf, dict)]
        if rows:
            db.table("spine_leaves").upsert(rows, on_conflict="leaf_id").execute()

    return {"ok": True}
