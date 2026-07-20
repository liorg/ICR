"""
Worker → Spine.

הנתיבים כאן חייבים להתאים בדיוק למה שה-Worker קורא:

    NotificationService   → POST  {SPINE_URL}/events
    RuntimeTreeService    → POST  {SPINE_URL}/leaves
                          → PATCH {SPINE_URL}/leaves/status
    WorkerRegistry        → POST  {SPINE_URL}/workers/heartbeat
    SessionSummaryService → POST  {SPINE_URL}/calls/{callId}/summary

זהות Leaf מורכבת משלושה שדות:

    scenario_id + call_id + leaf_id

לכן כל יצירה, upsert ועדכון של Leaf מתבצעים לפי שלושת השדות יחד.

ה-Summary אינו מסנכרן Leaves מחדש.
Leaves נשלחים בזמן אמת דרך POST /leaves ומתעדכנים דרך PATCH /leaves/status.

ה-Summary הוא אות הסיום של התרחיש:
הוא שומר נתוני סיכום, סוגר את ה-call ומקדם את הבא בתור.
ה-Worker אינו צריך לדעת על ניהול התור.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dependencies import get_supabase
from services.calls import complete_call, send_to_worker


router = APIRouter(tags=["worker-events"])
log = logging.getLogger("spine.worker")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Models ────────────────────────────────────────────────────────────

class EventIn(BaseModel):
    call_id: str
    phone_id: Optional[str] = None
    event_type: str
    step_id: Optional[str] = None
    step_type: Optional[str] = None
    data: Optional[Any] = None
    timestamp: Optional[str] = None


class LeafIn(BaseModel):
    # הזהות המלאה של ה-Leaf
    scenario_id: str
    call_id: str
    leaf_id: str

    step_id: str = ""
    type: str = ""
    message_id: Optional[str] = None
    whatsapp_message_id: Optional[str] = None
    content: Optional[str] = None
    wa_type: Optional[str] = None
    status: str = "Pending"
    timestamp: Optional[str] = None
    meta: Optional[dict] = None


class LeafStatusIn(BaseModel):
    # עדכון Leaf דורש את כל הזהות
    scenario_id: str
    call_id: str
    leaf_id: str

    status: str
    message_id: Optional[str] = None
    whatsapp_message_id: Optional[str] = None


class HeartbeatIn(BaseModel):
    phone_id: str
    service_name: str
    port: int = 9000
    status: str = "online"
    updated_at: Optional[str] = None


class SummaryIn(BaseModel):
    call_id: str
    scenario_id: str
    phone_id: str = ""
    contact_id: str = ""
    status: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: int = 0
    last_step_id: str = ""
    variables: Optional[dict] = None
    sender_count: int = 0
    expected_count: int = 0
    mismatch_count: int = 0


# ── Events ────────────────────────────────────────────────────────────

@router.post("/events")
def ingest_event(ev: EventIn):
    db = get_supabase()

    db.table("spine_events").insert({
        "call_id": ev.call_id,
        "phone_id": ev.phone_id,
        "event_type": ev.event_type,
        "step_id": ev.step_id,
        "step_type": ev.step_type,
        "data": ev.data,
        "timestamp": ev.timestamp or _now(),
    }).execute()

    return {"ok": True}


# ── Leaves ────────────────────────────────────────────────────────────

@router.post("/leaves")
def ingest_leaf(leaf: LeafIn):
    """
    יוצר Leaf חדש או מעדכן Leaf קיים.

    Conflict מתרחש רק כאשר שלושת השדות זהים:

        scenario_id + call_id + leaf_id
    """

    db = get_supabase()

    result = (
        db.table("spine_leaves")
        .upsert(
            {
                "scenario_id": leaf.scenario_id,
                "call_id": leaf.call_id,
                "leaf_id": leaf.leaf_id,
                "step_id": leaf.step_id,
                "type": leaf.type,
                "message_id": leaf.message_id,
                "whatsapp_message_id": leaf.whatsapp_message_id,
                "content": leaf.content,
                "wa_type": leaf.wa_type,
                "status": leaf.status,
                "timestamp": leaf.timestamp or _now(),
                "meta": leaf.meta,
            },
            on_conflict="scenario_id,call_id,leaf_id",
        )
        .execute()
    )

    # הודעה נכנסת מגיעה מה-Worker עם שני המזהים שכבר ידועים.
    # לכן אפשר ליצור מיד את הקישור רבים-לרבים בלי חיפוש נוסף ב-messages.
    if leaf.message_id:
        try:
            (
                db.table("spine_leaf_messages")
                .upsert(
                    {
                        "scenario_id": leaf.scenario_id,
                        "call_id": leaf.call_id,
                        "leaf_id": leaf.leaf_id,
                        "message_id": leaf.message_id,
                        "whatsapp_message_id": leaf.whatsapp_message_id,
                    },
                    on_conflict="leaf_id,message_id",
                )
                .execute()
            )
        except Exception:
            log.exception(
                "Failed linking incoming leaf to message | "
                "scenario=%s call=%s leaf=%s message=%s whatsapp=%s",
                leaf.scenario_id,
                leaf.call_id,
                leaf.leaf_id,
                leaf.message_id,
                leaf.whatsapp_message_id,
            )

    if not result.data:
        log.warning(
            "Leaf upsert returned no data | scenario=%s call=%s leaf=%s",
            leaf.scenario_id,
            leaf.call_id,
            leaf.leaf_id,
        )

    return {
        "ok": True,
        "scenario_id": leaf.scenario_id,
        "call_id": leaf.call_id,
        "leaf_id": leaf.leaf_id,
    }


@router.patch("/leaves/status")
def update_leaf(body: LeafStatusIn):
    """
    מעדכן Leaf לפי הזהות המלאה שנשלחת ב-body:

        scenario_id + call_id + leaf_id
    """

    db = get_supabase()

    patch = {
        "status": body.status,
    }

    # מזהים מתעדכנים רק אם נשלח ערך.
    # שליחת null אינה מוחקת ערך קיים.
    if body.message_id is not None:
        patch["message_id"] = body.message_id

    if body.whatsapp_message_id is not None:
        patch["whatsapp_message_id"] = body.whatsapp_message_id

    result = (
        db.table("spine_leaves")
        .update(patch)
        .eq("scenario_id", body.scenario_id)
        .eq("call_id", body.call_id)
        .eq("leaf_id", body.leaf_id)
        .execute()
    )

    if not result.data:
        log.warning(
            "Leaf not found for status update | scenario=%s call=%s leaf=%s",
            body.scenario_id,
            body.call_id,
            body.leaf_id,
        )

        raise HTTPException(
            status_code=404,
            detail="Leaf not found for the supplied scenario, call and leaf IDs",
        )

    # בהודעה נכנסת Worker מעביר message_id פנימי.
    # לכן מקשרים ישירות לטבלת רבים-לרבים.
    #
    # בהודעה יוצאת בדרך כלל קיים רק whatsapp_message_id בשלב זה;
    # הקישור ל-message_id הפנימי מושלם מאוחר יותר ב-incoming.py
    # לאחר webhook של HostAgent.
    if body.message_id:
        try:
            (
                db.table("spine_leaf_messages")
                .upsert(
                    {
                        "scenario_id": body.scenario_id,
                        "call_id": body.call_id,
                        "leaf_id": body.leaf_id,
                        "message_id": body.message_id,
                        "whatsapp_message_id": body.whatsapp_message_id,
                    },
                    on_conflict="leaf_id,message_id",
                )
                .execute()
            )
        except Exception:
            log.exception(
                "Failed linking leaf to message | "
                "scenario=%s call=%s leaf=%s message=%s whatsapp=%s",
                body.scenario_id,
                body.call_id,
                body.leaf_id,
                body.message_id,
                body.whatsapp_message_id,
            )

    return {
        "ok": True,
        "scenario_id": body.scenario_id,
        "call_id": body.call_id,
        "leaf_id": body.leaf_id,
        "status": body.status,
    }


# ── Heartbeat ─────────────────────────────────────────────────────────

@router.post("/workers/heartbeat")
def heartbeat(hb: HeartbeatIn):
    """
    הנתיב הוא /workers/heartbeat,
    בדיוק כמו WorkerRegistry.ReportAsync.
    """

    db = get_supabase()

    result = (
        db.table("phone_workers")
        .update({
            "status": "running" if hb.status == "online" else hb.status,
            "updated_at": hb.updated_at or _now(),
        })
        .eq("service_name", hb.service_name)
        .execute()
    )

    if not result.data:
        log.warning(
            "Heartbeat worker not found | phone=%s service=%s",
            hb.phone_id,
            hb.service_name,
        )

    return {
        "ok": True,
        "phone_id": hb.phone_id,
        "service_name": hb.service_name,
    }


# ── Summary = אות הסיום ───────────────────────────────────────────────

@router.post("/calls/{call_id}/summary")
async def ingest_summary(call_id: str, summary: SummaryIn):
    """
    ה-Summary הוא אות הסיום של התרחיש.

    הוא אחראי רק על:

    1. שמירת נתוני הסיכום על ה-call.
    2. סגירת ה-call.
    3. קידום ה-call הבא בתור.
    4. שליחת ה-call הבא ל-Worker.

    הוא אינו שומר או מעדכן Leaves.
    """

    if call_id != summary.call_id:
        raise HTTPException(
            status_code=400,
            detail="call_id in URL does not match call_id in request body",
        )

    db = get_supabase()

    call_result = (
        db.table("calls")
        .update({
            "duration_seconds": summary.duration_seconds,
            "last_step_id": summary.last_step_id,
            "variables": summary.variables,
            "sender_count": summary.sender_count,
            "expected_count": summary.expected_count,
            "mismatch_count": summary.mismatch_count,
        })
        .eq("id", call_id)
        .eq("scenario_id", summary.scenario_id)
        .execute()
    )

    if not call_result.data:
        log.warning(
            "Call not found for summary | scenario=%s call=%s",
            summary.scenario_id,
            call_id,
        )

        raise HTTPException(
            status_code=404,
            detail="Call not found for the supplied call_id and scenario_id",
        )

    # ── סגירת ה-call + קידום הבא בתור ────────────────────────────────
    #
    # ה-Summary הוא אות הסיום של התרחיש.
    #
    # בלעדיו ה-call יישאר running, ה-partial unique index יחסום
    # calls עתידיים לאותו contact והתור לא יתקדם.
    #
    # ה-Worker אינו יודע שקיים תור.

    completion_status = summary.status or "completed"

    result = await complete_call(
        db,
        call_id,
        completion_status,
    )

    # הפעלת ה-Worker נעשית לפי התוצאה של complete_call.
    if result.needs_worker:
        delivery = await send_to_worker(
            db,
            result.phone_id,
            result.worker_payload,
        )

        result.with_delivery(delivery)

        log.info(
            "[SUMMARY] next call dispatched | call=%s delivered=%s",
            result.body.get("next_call_id"),
            result.body.get("delivered"),
        )

    log.info(
        "[SUMMARY] scenario=%s call=%s status=%s duration=%ds result=%s",
        summary.scenario_id,
        call_id,
        completion_status,
        summary.duration_seconds,
        result.code,
    )

    return {
        "ok": True,
        "code": result.code,
        "next_call_id": result.body.get("next_call_id"),
    }
