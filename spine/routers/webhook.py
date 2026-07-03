"""
Worker → Spine endpoints.
Matches what WorkerScenarioRuntime already calls on SPINE_URL.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import APIRouter
from pydantic import BaseModel
from dependencies import get_supabase

router = APIRouter(tags=["webhook"])
log = logging.getLogger("spine.webhook")

def _now():
    return datetime.now(timezone.utc).isoformat()

# ── Models ─────────────────────────────────────────────────────────────────

class Event(BaseModel):
    call_id: str = ""
    phone_id: Optional[str] = None
    event_type: str = ""
    step_id: Optional[str] = None
    step_type: Optional[str] = None
    data: Optional[Any] = None
    timestamp: str = ""

class Leaf(BaseModel):
    leaf_id: str = ""
    call_id: str = ""
    step_id: str = ""
    type: str = ""
    message_id: Optional[str] = None
    content: Optional[str] = None
    wa_type: Optional[str] = None
    status: str = "Pending"
    timestamp: Optional[str] = None
    meta: Optional[dict] = None

class LeafStatus(BaseModel):
    leaf_id: str
    call_id: str
    status: str
    message_id: Optional[str] = None

class Summary(BaseModel):
    call_id: str = ""
    scenario_id: str = ""
    phone_id: str = ""
    contact_id: str = ""
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

class Heartbeat(BaseModel):
    phone_id: str
    service_name: str
    port: int = 9000
    status: str = "online"
    updated_at: Optional[str] = None

# ── Endpoints ──────────────────────────────────────────────────────────────

@router.post("/events")
def ingest_event(ev: Event):
    db = get_supabase()
    db.table("spine_events").insert({
        "call_id": ev.call_id, "phone_id": ev.phone_id,
        "event_type": ev.event_type, "step_id": ev.step_id,
        "step_type": ev.step_type, "data": ev.data,
        "timestamp": ev.timestamp or _now(),
    }).execute()
    return {"ok": True}

@router.post("/leaves")
def ingest_leaf(leaf: Leaf):
    db = get_supabase()
    db.table("spine_leaves").insert({
        "leaf_id": leaf.leaf_id, "call_id": leaf.call_id,
        "step_id": leaf.step_id, "type": leaf.type,
        "message_id": leaf.message_id, "content": leaf.content,
        "wa_type": leaf.wa_type, "status": leaf.status,
        "timestamp": leaf.timestamp or _now(), "meta": leaf.meta,
    }).execute()
    return {"ok": True}

@router.patch("/leaves/{leaf_id}/status")
def update_leaf(leaf_id: str, u: LeafStatus):
    db = get_supabase()
    patch = {"status": u.status}
    if u.message_id:
        patch["message_id"] = u.message_id
    db.table("spine_leaves").update(patch).eq("leaf_id", leaf_id).execute()
    return {"ok": True}

@router.post("/calls/{call_id}/summary")
def ingest_summary(call_id: str, s: Summary):
    db = get_supabase()
    db.table("spine_calls").upsert({
        "call_id": call_id, "scenario_id": s.scenario_id,
        "phone_id": s.phone_id, "contact_id": s.contact_id,
        "status": s.status, "started_at": s.started_at,
        "finished_at": s.finished_at, "duration_seconds": s.duration_seconds,
        "last_step_id": s.last_step_id, "variables": s.variables,
        "sender_count": s.sender_count, "expected_count": s.expected_count,
        "mismatch_count": s.mismatch_count,
    }, on_conflict="call_id").execute()

    # Bulk upsert leaves from summary
    if s.leaves:
        rows = [{
            "leaf_id": lf.get("leaf_id") or lf.get("leafId"),
            "call_id": call_id,
            "step_id": lf.get("step_id") or lf.get("stepId"),
            "type": lf.get("type", ""),
            "message_id": lf.get("message_id") or lf.get("messageId"),
            "content": lf.get("content"),
            "wa_type": lf.get("wa_type") or lf.get("waType"),
            "status": lf.get("status", ""),
            "timestamp": lf.get("timestamp"),
        } for lf in s.leaves if isinstance(lf, dict)]
        if rows:
            db.table("spine_leaves").upsert(rows, on_conflict="leaf_id").execute()

    log.info("Summary | call=%s status=%s dur=%ds", call_id, s.status, s.duration_seconds)
    return {"ok": True}

@router.post("/workers/heartbeat")
def heartbeat(hb: Heartbeat):
    db = get_supabase()
    db.table("phone_workers").update({
        "status": "running" if hb.status == "online" else hb.status,
        "updated_at": hb.updated_at or _now(),
    }).eq("service_name", hb.service_name).execute()
    return {"ok": True}
