"""Calls — create, get, summary → auto-dispatch next queued"""
import uuid, json, logging
from datetime import datetime, timezone
from typing import Optional, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx
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
    priority: int = 0


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

    # Check if contact already has a running call → queue this one
    active = db.table("calls").select("id") \
        .eq("phone_id", req.phone_id).eq("contact_id", req.contact_id) \
        .eq("status", "running").limit(1).execute().data

    status = "queued" if active else "running"

    db.table("calls").insert({
        "id": call_id, "scenario_id": req.scenario_id,
        "scenario_snapshot": snapshot,
        "phone_id": req.phone_id, "contact_id": req.contact_id,
        "status": status, "priority": req.priority,
        "started_at": _now() if status == "running" else None,
    }).execute()

    log.info("Call created | %s status=%s priority=%d phone=%s contact=%s",
             call_id, status, req.priority, req.phone_id, req.contact_id)

    # Dispatch immediately only if running (not queued)
    if status == "running":
        _dispatch_to_worker(db, {
            "call_id": call_id,
            "scenario_id": req.scenario_id,
            "phone_id": req.phone_id,
            "contact_id": req.contact_id,
            "contact_phone": req.contact_phone,
            "contact_name": req.contact_name,
            "scenario_snapshot": snapshot,
        })

    return {"ok": True, "call_id": call_id, "status": status}


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

    # Mark current call done
    db.table("calls").update({
        "status": s.status,
        "finished_at": s.finished_at or _now(),
        "duration_seconds": s.duration_seconds,
    }).eq("id", call_id).execute()

    # Bulk upsert leaves
    if s.leaves:
        rows = [{
            "leaf_id": lf.get("leaf_id") or lf.get("leafId"), "call_id": call_id,
            "step_id": lf.get("step_id") or lf.get("stepId"), "type": lf.get("type", ""),
            "content": lf.get("content"), "wa_type": lf.get("wa_type") or lf.get("waType"),
            "status": lf.get("status", ""), "timestamp": lf.get("timestamp"),
        } for lf in s.leaves if isinstance(lf, dict)]
        if rows:
            db.table("spine_leaves").upsert(rows, on_conflict="leaf_id").execute()

    log.info("Summary | call=%s status=%s dur=%ds", call_id, s.status, s.duration_seconds)

    # ── Pop next queued call for same phone+contact ────────────────────────
    call = db.table("calls").select("phone_id, contact_id") \
        .eq("id", call_id).maybe_single().execute().data

    if call:
        next_call = db.rpc("pop_next_queued_call", {
            "p_phone_id": call["phone_id"],
            "p_contact_id": call["contact_id"],
        }).execute().data

        if next_call:
            log.info("Queue | next call=%s scenario=%s priority dispatched",
                     next_call.get("call_id"), next_call.get("scenario_id"))
            _dispatch_to_worker(db, next_call)
        else:
            log.info("Queue | no more queued calls for phone=%s contact=%s",
                     call["phone_id"], call["contact_id"])

    return {"ok": True}


# ── Dispatch helper ───────────────────────────────────────────────────────

async def _dispatch_async(worker_url: str, payload: dict):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{worker_url}/webhook/event", json=payload)
    except Exception as e:
        log.error("Dispatch failed: %s", e)


def _dispatch_to_worker(db, call_data: dict):
    """Send init event to Worker for this phone."""
    phone_id = call_data.get("phone_id")
    worker = db.table("phone_workers").select("service_name") \
        .eq("phone_id", phone_id).eq("status", "running") \
        .maybe_single().execute().data

    if not worker:
        log.warning("No running worker for phone %s", phone_id)
        return

    worker_url = f"http://{worker['service_name']}:9000"
    snapshot = call_data.get("scenario_snapshot", {})
    scenario_json = json.dumps(snapshot) if isinstance(snapshot, dict) else str(snapshot)

    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_dispatch_async(worker_url, {
                "typeEvent": "init",
                "call_id": call_data["call_id"],
                "contact_id": call_data["contact_id"],
                "contact_phone": call_data.get("contact_phone", ""),
                "contact_name": call_data.get("contact_name", ""),
                "scenario_id": call_data.get("scenario_id", ""),
                "scenario_json": scenario_json,
            }))
        else:
            loop.run_until_complete(_dispatch_async(worker_url, {
                "typeEvent": "init",
                "call_id": call_data["call_id"],
                "contact_id": call_data["contact_id"],
                "contact_phone": call_data.get("contact_phone", ""),
                "contact_name": call_data.get("contact_name", ""),
                "scenario_id": call_data.get("scenario_id", ""),
                "scenario_json": scenario_json,
            }))
    except Exception as e:
        log.error("Dispatch error: %s", e)

    log.info("Dispatched | call=%s → %s", call_data["call_id"], worker_url)