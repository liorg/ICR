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
הוא שומר נתוני סיכום, סוגר את ה-call **ומקדם את הבא בתור**.

הסגירה עוברת דרך spine_complete_call ולא ב-UPDATE ישיר. אין Job חיצוני
שמטפל בתור, ולכן זו הנקודה היחידה שבה queued הופך ל-running אחרי סיום
תקין. בלעדיה שורות queued נשארות מתות והקונטקט נחסם לצמיתות:
בלי running הודעה נכנסת לא מנותבת, ועם queued כל trigger נדחה.

calls תקועים שלא מגיע להם Summary נסגרים ע"י POST /calls/sweep לפי
expected_end.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dependencies import get_supabase
from services.calls import complete_call, send_call_end_marker, send_to_worker


router = APIRouter(tags=["worker-events"])
log = logging.getLogger("spine.worker")


FINAL_CALL_STATUSES = {
    "completed",
    "failed",
    "aborted",
    "expired",
    "timeout",
}


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
    meta: Optional[dict[str, Any]] = None


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

    status: Literal[
        "completed",
        "failed",
        "aborted",
        "expired",
        "timeout",
    ] = "completed"

    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    duration_seconds: int = Field(default=0, ge=0)
    last_step_id: str = ""

    variables: dict[str, Any] = Field(default_factory=dict)

    worker_host: str = ""
    worker_port: int = Field(default=0, ge=0, le=65535)

    sender_count: int = Field(default=0, ge=0)
    expected_count: int = Field(default=0, ge=0)
    mismatch_count: int = Field(default=0, ge=0)


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

    # הודעה נכנסת מגיעה מה-Worker עם message_id פנימי שכבר ידוע.
    # לכן אפשר ליצור מיד את הקישור רבים-לרבים.
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
                    on_conflict="scenario_id,call_id,leaf_id,whatsapp_message_id",
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

    patch: dict[str, Any] = {
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

    # בהודעה נכנסת Worker מעביר message_id פנימי — מקשרים ישירות.
    #
    # בשליחה יוצאת מגיע רק whatsapp_message_id, ולכן הבלוק הזה היה מדולג
    # והקישור נשאר תלוי כולו ב-webhook. אבל ה-webhook לפעמים מקדים את
    # ה-insert של send.py, לא מוצא שורה לעדכן (completed=0), והקישור
    # נשאר חסר לתמיד.
    #
    # ה-HostAgent שומר את ההודעה ב-messages לפני שהוא יורה את ה-webhook,
    # ולכן בנקודה הזו אפשר למשוך את המזהה הפנימי ולסגור את הקישור כאן,
    # בלי תלות בסדר ההגעה.
    msg_id = body.message_id

    if not msg_id and body.whatsapp_message_id:
        try:
            row = (
                db.table("messages")
                .select("id")
                .eq("whatsapp_message_id", body.whatsapp_message_id)
                .limit(1)
                .execute()
                .data
            )
            msg_id = row[0]["id"] if row else None
        except Exception:
            log.exception(
                "Failed resolving message_id | whatsapp=%s",
                body.whatsapp_message_id,
            )

    if msg_id or body.whatsapp_message_id:
        try:
            (
                db.table("spine_leaf_messages")
                .upsert(
                    {
                        "scenario_id": body.scenario_id,
                        "call_id": body.call_id,
                        "leaf_id": body.leaf_id,
                        "message_id": msg_id,
                        "whatsapp_message_id": body.whatsapp_message_id,
                    },
                    on_conflict="scenario_id,call_id,leaf_id,whatsapp_message_id",
                )
                .execute()
            )
            log.info(
                "[LEAF-LINK] via status | leaf=%s message=%s whatsapp=%s",
                body.leaf_id,
                msg_id,
                body.whatsapp_message_id,
            )
        except Exception:
            log.exception(
                "Failed linking leaf to message | "
                "scenario=%s call=%s leaf=%s message=%s whatsapp=%s",
                body.scenario_id,
                body.call_id,
                body.leaf_id,
                msg_id,
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
            "port": hb.port,
            "updated_at": hb.updated_at or _now(),
        })
        .eq("service_name", hb.service_name)
        .eq("phone_id", hb.phone_id)
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

    הוא אחראי על:

    1. אימות שזהו ה-call הנכון.
    2. שמירת נתוני הסיכום על ה-call.
    3. סגירת ה-call וקידום ה-queued הבא, דרך spine_complete_call.

    הוא אינו שומר או מעדכן Leaves.
    """

    # אותו call_id חייב להופיע ב-URL וב-body.
    if call_id != summary.call_id:
        raise HTTPException(
            status_code=400,
            detail="call_id in URL does not match call_id in request body",
        )

    # הגנה נוספת מעבר ל-Literal של Pydantic.
    if summary.status not in FINAL_CALL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported completion status: {summary.status}",
        )

    # לא ייתכן שסיום השיחה קדם להתחלתה.
    if (
        summary.started_at is not None
        and summary.finished_at is not None
        and summary.finished_at < summary.started_at
    ):
        raise HTTPException(
            status_code=400,
            detail="finished_at cannot be earlier than started_at",
        )

    db = get_supabase()

    call_rows = (
        db.table("calls")
        .select("id, phone_id, contact_id, status")
        .eq("id", call_id)
        .limit(1)
        .execute()
        .data
    )

    if not call_rows:
        raise HTTPException(
            status_code=404,
            detail="Call not found",
        )

    call_row = call_rows[0]
    call_phone_id = summary.phone_id or call_row.get("phone_id") or ""
    call_contact_id = summary.contact_id or call_row.get("contact_id") or ""

    call_patch: dict[str, Any] = {
        "duration_seconds": summary.duration_seconds,
        "last_step_id": summary.last_step_id,
        "variables": summary.variables,
        "worker_host": summary.worker_host,
        "worker_port": summary.worker_port,
        "sender_count": summary.sender_count,
        "expected_count": summary.expected_count,
        "mismatch_count": summary.mismatch_count,
    }

    # לא שולחים None כדי לא למחוק תאריך קיים.
    if summary.started_at is not None:
        call_patch["started_at"] = summary.started_at.isoformat()

    if summary.finished_at is not None:
        call_patch["ended_at"] = summary.finished_at.isoformat()

    # מעדכנים רק כאשר כל הזהות שנשלחה תואמת ל-call.
    query = (
        db.table("calls")
        .update(call_patch)
        .eq("id", call_id)
        .eq("scenario_id", summary.scenario_id)
    )

    if summary.phone_id:
        query = query.eq("phone_id", summary.phone_id)

    if summary.contact_id:
        query = query.eq("contact_id", summary.contact_id)

    call_result = query.execute()

    if not call_result.data:
        # הסטטיסטיקות נשמרות רק כאשר כל הזהות תואמת.
        # גם במקרה של mismatch עדיין מנסים לסגור לפי call_id בלבד,
        # כדי שלא יישאר call במצב running.
        log.warning(
            "Summary identity mismatch — stats not saved, closing by call_id | "
            "scenario=%s call=%s phone=%s contact=%s",
            summary.scenario_id,
            call_id,
            summary.phone_id,
            summary.contact_id,
        )

    # ── סגירה + קידום התור ────────────────────────────────────────────
    #
    # spine_complete_call סוגר את ה-call ומקדם את ה-queued הבא לפי
    # priority תחת אותו lock. זו הנקודה היחידה שבה זה קורה בסיום תקין.
    close = await complete_call(db, call_id, summary.status)
    code = close.code
    next_call_id = close.body.get("next_call_id")

    if code == "CALL_NOT_FOUND":
        raise HTTPException(
            status_code=404,
            detail="Call not found",
        )

    if code == "CALL_NOT_RUNNING":
        existing = (
            db.table("calls")
            .select("id, status")
            .eq("id", call_id)
            .limit(1)
            .execute()
            .data
        )

        current_status = existing[0].get("status") if existing else None

        # Summary כפול הוא idempotent: אם ה-call כבר סופי מחזירים 200.
        if current_status in FINAL_CALL_STATUSES:
            code = "CALL_ALREADY_CLOSED"
        else:
            raise HTTPException(
                status_code=409,
                detail=f"Call is not running; current status: {current_status}",
            )

    # spine_complete_call כותב ended_at = now(). כשה-Worker דיווח זמן
    # סיום מדויק, הוא גובר.
    elif summary.finished_at is not None:
        db.table("calls").update(
            {"ended_at": summary.finished_at.isoformat()}
        ).eq("id", call_id).execute()

    # ── init ל-call שקודם ─────────────────────────────────────────────
    #
    # כשל בשליחה לא פותח מחדש את ה-call: ה-call המקודם כבר running
    # ו-expected_end שלו ייתפס ע"י ה-sweeper.
    promoted_delivered = False

    if close.needs_worker:
        promoted_delivered = await send_to_worker(
            db,
            close.phone_id or call_phone_id,
            close.worker_payload,
        )

        if not promoted_delivered:
            log.error(
                "[SUMMARY] promoted call not delivered | call=%s next=%s",
                call_id,
                next_call_id,
            )

    # חותמת הסיום נשלחת תמיד, ללא קשר לסיבת הסיום.
    # כשל בשליחה אינו פותח מחדש את ה-call ואינו מפעיל Worker.
    marker_result = await send_call_end_marker(
        db=db,
        call_id=call_id,
        phone_id=call_phone_id,
        contact_id=call_contact_id,
        final_status=summary.status,
    )

    log.info(
        "[SUMMARY] scenario=%s call=%s status=%s "
        "duration=%ds sender=%d expected=%d mismatches=%d result=%s",
        summary.scenario_id,
        call_id,
        summary.status,
        summary.duration_seconds,
        summary.sender_count,
        summary.expected_count,
        summary.mismatch_count,
        code,
    )

    if next_call_id:
        log.info(
            "[SUMMARY] queue promoted | call=%s next=%s delivered=%s",
            call_id,
            next_call_id,
            promoted_delivered,
        )

    return {
        "ok": True,
        "code": code,
        "call_id": call_id,
        "status": summary.status,
        "next_call_id": next_call_id,
        "next_call_delivered": promoted_delivered,
        "end_marker_sent": marker_result.get("ok", False),
        "end_marker_code": marker_result.get("code"),
    }
