"""
Worker → Spine

POST /events                   — event log
POST /leaves                   — leaf (scenario step)
PATCH /leaves/{leaf_id}/status — update leaf status + link message
POST /heartbeat                — worker alive
"""
from datetime import datetime, timezone
from typing import Optional, Any
from fastapi import APIRouter
from pydantic import BaseModel
from dependencies import get_supabase

router = APIRouter(tags=["worker-events"])

def _now():
    return datetime.now(timezone.utc).isoformat()


class EventIn(BaseModel):
    call_id: str = ""
    phone_id: Optional[str] = None
    event_type: str = ""
    step_id: Optional[str] = None
    step_type: Optional[str] = None
    data: Optional[Any] = None
    timestamp: str = ""

class LeafIn(BaseModel):
    leaf_id: str = ""
    call_id: str = ""
    step_id: str = ""
    type: str = ""
    content: Optional[str] = None
    wa_type: Optional[str] = None
    status: str = "Pending"
    timestamp: Optional[str] = None
    meta: Optional[dict] = None

class LeafStatusIn(BaseModel):
    status: str
    message_id: Optional[str] = None     # wa_message_id to link
    spine_message_id: Optional[int] = None  # spine_messages.id to link

class HeartbeatIn(BaseModel):
    phone_id: str
    service_name: str
    port: int = 9000
    status: str = "online"


@router.post("/events")
def ingest_event(ev: EventIn):
    db = get_supabase()
    db.table("spine_events").insert({
        "call_id": ev.call_id, "phone_id": ev.phone_id,
        "event_type": ev.event_type, "step_id": ev.step_id,
        "step_type": ev.step_type, "data": ev.data,
        "timestamp": ev.timestamp or _now(),
    }).execute()
    return {"ok": True}


@router.post("/leaves")
def ingest_leaf(leaf: LeafIn):
    db = get_supabase()
    db.table("spine_leaves").insert({
        "leaf_id": leaf.leaf_id, "call_id": leaf.call_id,
        "step_id": leaf.step_id, "type": leaf.type,
        "content": leaf.content, "wa_type": leaf.wa_type,
        "status": leaf.status,
        "timestamp": leaf.timestamp or _now(), "meta": leaf.meta,
    }).execute()
    return {"ok": True}


@router.patch("/leaves/{leaf_id}/status")
def update_leaf(leaf_id: str, body: LeafStatusIn):
    db = get_supabase()
    db.table("spine_leaves").update({"status": body.status}).eq("leaf_id", leaf_id).execute()

    # Link leaf to message if provided
    if body.spine_message_id:
        try:
            db.table("spine_leaf_messages").insert({
                "leaf_id": leaf_id, "message_id": body.spine_message_id,
            }).execute()
        except Exception:
            pass

    # Or find message by wa_message_id and link
    if body.message_id and not body.spine_message_id:
        res = db.table("spine_messages").select("id") \
            .eq("wa_message_id", body.message_id).limit(1).execute()
        if res.data:
            try:
                db.table("spine_leaf_messages").insert({
                    "leaf_id": leaf_id, "message_id": res.data[0]["id"],
                }).execute()
            except Exception:
                pass

    return {"ok": True}


@router.post("/heartbeat")
def heartbeat(hb: HeartbeatIn):
    db = get_supabase()
    db.table("phone_workers").update({
        "status": "running" if hb.status == "online" else hb.status,
        "updated_at": _now(),
    }).eq("service_name", hb.service_name).execute()
    return {"ok": True}
