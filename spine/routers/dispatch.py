"""
Dispatch — נקודת הכניסה של Scheduler ו-API ישיר.
הלוגיקה עצמה ב-services/calls.py. כאן רק HTTP.

    POST /api/calls/ensure          201 CREATED | 202 QUEUED | 409 BLOCKED/ABORTED
    POST /api/calls/{id}/complete
    POST /api/calls/sweep           סוגר calls תקועים לפי expected_end
    POST /api/dispatch/message
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from dependencies import get_supabase
from services.calls import ensure_call, complete_call, send_to_worker, entry_payload

router = APIRouter(tags=["dispatch"])
log = logging.getLogger("spine.dispatch")


# מול call פתוח:
#   trigger   → 409 blocked, בלי שורה
#   scheduler → 202 queued (ומבטל triggers ממתינים), או
#               409 aborted אם כבר קיים instance של scheduler
#   api       → 409 aborted תמיד
# ה-running לא מופסק בשום מקרה.
class EnsureReq(BaseModel):
    phone_id:      str
    contact_id:    str
    scenario_id:   str                    # חובה. אין בחירה אוטומטית.
    priority:      Optional[int] = None
    source:        str = "api"            # trigger | scheduler | api
    first_message: Optional[dict] = None
    schedule_id:   Optional[str] = None



class ForwardReq(BaseModel):
    phone_id:            str
    contact_id:          str
    call_id:             Optional[str] = None
    message_id:          Optional[str] = None
    whatsapp_message_id: Optional[str] = None
    payload:             Optional[dict] = None


class CompleteReq(BaseModel):
    status: str = "completed"


class SweepReq(BaseModel):
    # ברירת מחדל: expired. אפשר timeout אם מבדילים בדוחות.
    status: str = "expired"
    limit:  int = 50


# ── ensure ────────────────────────────────────────────────────────────
@router.post("/calls/ensure")
async def ensure(req: EnsureReq, response: Response):
    db  = get_supabase()
    res = await ensure_call(
            db,
            phone_id=req.phone_id,
            contact_id=req.contact_id,
            scenario_id=req.scenario_id,
            priority=req.priority,
            source=req.source,
            schedule_id=req.schedule_id,
            first_message=req.first_message,
        )

    if res.http_status in (404,):
        raise HTTPException(404, res.code)

    # ה-Worker מופעל מה-response — לא מתוך ה-service.
    if res.needs_worker:
        res.with_delivery(await send_to_worker(db, res.phone_id, res.worker_payload))

    response.status_code = res.http_status
    return res.body


# ── complete ──────────────────────────────────────────────────────────
@router.post("/calls/{call_id}/complete")
async def complete(call_id: str, req: CompleteReq, response: Response):
    db  = get_supabase()
    res = await complete_call(db, call_id, req.status)

    if res.needs_worker:
        res.with_delivery(await send_to_worker(db, res.phone_id, res.worker_payload))

    response.status_code = res.http_status
    return res.body



# ── sweep ─────────────────────────────────────────────────────────────
@router.post("/calls/sweep")
async def sweep(req: SweepReq):
    """
    סוגר calls שנתקעו ב-running ולא הגיע להם Summary.

    למה זה חייב להתקיים: ה-Worker הוא היחיד שסוגר call (דרך
    /calls/{id}/summary). אם הוא קרס, נהרג או איבד קשר — ה-call נשאר
    running לנצח, וכל הודעה נכנסת מהקונטקט הזה מנותבת ל-Worker שלא
    קיים. גם trigger חדש נדחה. הקונטקט חסום.

    expected_end כבר מחושב בכל יצירה וקידום (estimated_time + buffer),
    ופשוט לא נקרא ע"י אף אחד. זה הקורא.

    הסגירה עוברת דרך complete_call, כך שגם כאן ה-queued הבא מקודם
    ומקבל init — בדיוק כמו בסיום תקין.

    ה-Scheduler קורא לזה מחזורית. Spine מגיב, לא יוזם.
    """
    db  = get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    stuck = (
        db.table("calls")
        .select("id, phone_id, contact_id, expected_end")
        .eq("status", "running")
        .lt("expected_end", now)
        .order("expected_end")
        .limit(max(1, min(req.limit, 200)))
        .execute()
        .data
        or []
    )

    if not stuck:
        return {"ok": True, "swept": 0, "calls": []}

    results = []

    for row in stuck:
        res = await complete_call(db, row["id"], req.status)

        delivered = False
        if res.needs_worker:
            delivered = await send_to_worker(db, res.phone_id, res.worker_payload)

        log.warning(
            "[SWEEP] closed stuck call | call=%s contact=%s expected_end=%s "
            "code=%s next=%s delivered=%s",
            row["id"], row.get("contact_id"), row.get("expected_end"),
            res.code, res.body.get("next_call_id"), delivered,
        )

        results.append({
            "call_id":      row["id"],
            "contact_id":   row.get("contact_id"),
            "expected_end": row.get("expected_end"),
            "code":         res.code,
            "next_call_id": res.body.get("next_call_id"),
            "delivered":    delivered,
        })

    return {"ok": True, "swept": len(results), "calls": results}


# ── forward incoming message ─────────────────────────────────────────
@router.post("/dispatch/message")
async def forward_message(req: ForwardReq):
    db = get_supabase()
    p  = req.payload or {"type": "text", "data": {"text": ""}}

    # ה-Worker (entryMessage) דורש whatsapp_message_id — 400 בלעדיו.
    # אם הקורא לא שלח, משלימים מטבלת messages לפי message_id.
    wa_id = req.whatsapp_message_id
    if not wa_id and req.message_id:
        row = db.table("messages").select("whatsapp_message_id") \
            .eq("id", req.message_id).maybe_single().execute().data
        wa_id = (row or {}).get("whatsapp_message_id")

    if not wa_id:
        raise HTTPException(422, "whatsapp_message_id required (not provided and not found in messages)")

    ok = await send_to_worker(db, req.phone_id, entry_payload(
        call_id=req.call_id,
        scenario_id=None,
        contact_id=req.contact_id,
        message_id=req.message_id or "",
        whatsapp_message_id=wa_id,
        msg_type=p.get("type", "text"),
        content=(p.get("data") or {}).get("text"),
        metadata=p.get("data"),
    ))
    return {"ok": ok, "delivered": ok}
