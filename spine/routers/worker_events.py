"""
Worker → Spine. הנתיבים כאן חייבים להתאים *בדיוק* למה שה-Worker קורא:

    NotificationService   → POST  {SPINE_URL}/events
    RuntimeTreeService    → POST  {SPINE_URL}/leaves
                          → PATCH {SPINE_URL}/leaves/{leafId}/status
    WorkerRegistry        → POST  {SPINE_URL}/workers/heartbeat      ← היה /heartbeat = 404
    SessionSummaryService → POST  {SPINE_URL}/calls/{callId}/summary ← לא היה קיים = 404

הקובץ הזה מחליף את worker_events.py הישן וגם את webhook.py (שהיה נכון אך
לא רשום ב-main.py). איחוד: הנתיבים של webhook.py + קישור ה-leaf↔message
של worker_events.py.

ה-summary הוא אות הסיום של התרחיש — ולכן הוא גם מה שסוגר את ה-call
ומקדם את הבא בתור. ה-Worker לא צריך לדעת על התור בכלל.
"""
import json, logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from dependencies import get_supabase

router = APIRouter(tags=["worker-events"])
log = logging.getLogger("spine.worker")


def _now():
    return datetime.now(timezone.utc).isoformat()


# ── Models ────────────────────────────────────────────────────────────
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
    message_id: Optional[str] = None
    content: Optional[str] = None
    wa_type: Optional[str] = None
    status: str = "Pending"
    timestamp: Optional[str] = None
    meta: Optional[dict] = None


class LeafStatusIn(BaseModel):
    leaf_id: Optional[str] = None
    call_id: Optional[str] = None
    status: str
    message_id: Optional[str] = None


class HeartbeatIn(BaseModel):
    phone_id: str
    service_name: str
    port: int = 9000
    status: str = "online"
    updated_at: Optional[str] = None


class SummaryIn(BaseModel):
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


# ── Events / Leaves ───────────────────────────────────────────────────
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
    db.table("spine_leaves").upsert({
        "leaf_id": leaf.leaf_id, "call_id": leaf.call_id,
        "step_id": leaf.step_id, "type": leaf.type,
        "message_id": leaf.message_id, "content": leaf.content,
        "wa_type": leaf.wa_type, "status": leaf.status,
        "timestamp": leaf.timestamp or _now(), "meta": leaf.meta,
    }, on_conflict="leaf_id").execute()
    return {"ok": True}


@router.patch("/leaves/{leaf_id}/status")
def update_leaf(leaf_id: str, body: LeafStatusIn):
    db = get_supabase()
    patch = {"status": body.status}
    if body.message_id:
        patch["message_id"] = body.message_id
    db.table("spine_leaves").update(patch).eq("leaf_id", leaf_id).execute()

    # קישור leaf ↔ messages (היה רק ב-worker_events, נשמר)
    if body.message_id:
        res = db.table("messages").select("id") \
                .eq("whatsapp_message_id", body.message_id).limit(1).execute()
        if res.data:
            try:
                db.table("spine_leaf_messages").insert({
                    "leaf_id": leaf_id, "message_id": res.data[0]["id"],
                }).execute()
            except Exception:
                pass
    return {"ok": True}


# ── Heartbeat ─────────────────────────────────────────────────────────
# הנתיב הוא /workers/heartbeat — בדיוק כמו ב-WorkerRegistry.ReportAsync.
# הנתיב הישן (/heartbeat) החזיר 404, ולכן phone_workers.status לא עודכן
# ל-running, ולכן _worker_url החזיר None, ולכן שום dispatch לא הגיע.
@router.post("/workers/heartbeat")
def heartbeat(hb: HeartbeatIn):
    db = get_supabase()
    db.table("phone_workers").update({
        "status": "running" if hb.status == "online" else hb.status,
        "updated_at": hb.updated_at or _now(),
    }).eq("service_name", hb.service_name).execute()
    return {"ok": True}


# ── Summary = אות הסיום ───────────────────────────────────────────────
@router.post("/calls/{call_id}/summary")
async def ingest_summary(call_id: str, s: SummaryIn):
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

    if s.leaves:
        rows = [{
            "leaf_id":    lf.get("leaf_id")    or lf.get("leafId"),
            "call_id":    call_id,
            "step_id":    lf.get("step_id")    or lf.get("stepId"),
            "type":       lf.get("type", ""),
            "message_id": lf.get("message_id") or lf.get("messageId"),
            "content":    lf.get("content"),
            "wa_type":    lf.get("wa_type")    or lf.get("waType"),
            "status":     lf.get("status", ""),
            "timestamp":  lf.get("timestamp"),
        } for lf in s.leaves if isinstance(lf, dict)]
        if rows:
            db.table("spine_leaves").upsert(rows, on_conflict="leaf_id").execute()

    # ── סגירת ה-call + קידום הבא בתור ─────────────────────────────────
    # בלי זה status נשאר 'running' לנצח, ה-partial unique index חוסם כל
    # call עתידי לאותו contact, והתור לא מתקדם. ה-Worker לא יודע על התור.
    try:
        res = db.rpc("spine_complete_call", {
            "p_call_id": call_id,
            "p_status":  s.status or "completed",
        }).execute().data or {}
    except Exception as e:
        log.error("[SUMMARY] complete_call failed | call=%s: %s", call_id, e)
        res = {}

    log.info("[SUMMARY] call=%s status=%s dur=%ds → %s next=%s",
             call_id, s.status, s.duration_seconds,
             res.get("code"), res.get("next_call_id"))

    nxt = res.get("next_call_id")
    if nxt:
        await _init_next(db, nxt)

    return {"ok": True, "code": res.get("code"), "next_call_id": nxt}


async def _init_next(db, call_id: str):
    """דוחף ל-worker את ה-call שקודם מ-queued ל-running."""
    row = db.table("calls") \
        .select("id, phone_id, contact_id, scenario_id, scenario_snapshot") \
        .eq("id", call_id).maybe_single().execute().data
    if not row:
        return

    ct = db.table("contacts").select("id, phone, name") \
           .eq("id", row["contact_id"]).maybe_single().execute().data or {}

    w = db.table("phone_workers").select("service_name") \
          .eq("phone_id", row["phone_id"]).eq("status", "running") \
          .limit(1).execute().data
    if not w:
        log.warning("[SUMMARY] next call %s promoted but no worker | phone=%s",
                    call_id, row["phone_id"])
        return

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"http://{w[0]['service_name']}:9000/webhook/event", json={
                "typeEvent":     "init",
                "call_id":       row["id"],
                "contact_id":    row["contact_id"],
                "contact_phone": ct.get("phone") or "",
                "contact_name":  ct.get("name") or "",
                "scenario_id":   row["scenario_id"],
                "scenario_json": json.dumps(row.get("scenario_snapshot") or {}),
                "first_message": None,
            })
        log.info("[SUMMARY] next call dispatched | call=%s", call_id)
    except Exception as e:
        log.error("[SUMMARY] next dispatch failed | call=%s: %s", call_id, e)
