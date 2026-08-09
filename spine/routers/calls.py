"""
spine/routers/calls.py — קריאה ויצירה של calls.

מה הוסר מהגרסה הקודמת ולמה:

  POST /calls/{id}/summary
      היה handler שני על אותו נתיב כמו worker_events.ingest_summary.
      calls.router נרשם ראשון ב-main.py, ולכן הוא זה שקיבל בפועל את
      כל הסיכומים מה-Worker — והשני לא רץ מעולם.
      הישן סגר ב-UPDATE ישיר, כתב finished_at, וקידם דרך
      pop_next_queued_call: קידום מקביל שלא קובע expected_end ולא
      מכיר first_message. worker_events הוא הקנוני.

  _dispatch_to_worker / _dispatch_async
      שכפול של send_to_worker, כולל asyncio.get_event_loop() בתוך
      פונקציה סינכרונית — מסלול שנוטה לשגיאות בהקשר של FastAPI.
      services/calls.py הוא המקור היחיד לשליחה ל-Worker.

  לוגיקת ה-queue ב-POST ""
      יצרה calls ישירות, בלי advisory lock, בלי event_id ובלי SLA.
      עכשיו היא מאצילה ל-ensure_call כמו כל שאר המסלולים.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Optional

from dependencies import get_supabase
from services.calls import ensure_call, send_to_worker

router = APIRouter(prefix="/calls", tags=["calls"])
log = logging.getLogger("spine.calls")


class CreateCallReq(BaseModel):
    scenario_id: str
    phone_id: str
    contact_id: str
    priority: Optional[int] = None

    # נשמרים לתאימות לאחור עם קוראים קיימים. ה-RPC שולף את התרחיש
    # ואת פרטי הקונטקט מה-DB, אז השדות האלה לא בשימוש.
    scenario_json: Optional[Any] = None
    contact_phone: Optional[str] = None
    contact_name: Optional[str] = None


@router.post("")
async def create_call(req: CreateCallReq):
    """
    יצירה ידנית של call.

    source='manual' — מול call פעיל זה נפסל ונרשם aborted, ובנוסף
    מרוקן את התור. רק scenario אחד, מפורש.
    """
    db = get_supabase()

    result = await ensure_call(
        db,
        phone_id=req.phone_id,
        contact_id=req.contact_id,
        scenario_id=req.scenario_id,
        priority=req.priority,
        source="manual",
    )

    if result.needs_worker:
        delivered = await send_to_worker(
            db,
            result.phone_id or req.phone_id,
            result.worker_payload,
        )

        result.with_delivery(delivered)

    body = dict(result.body)
    body["ok"] = result.http_status < 400
    body["call_id"] = result.call_id

    return body


@router.get("/{call_id}")
def get_call(call_id: str):
    db = get_supabase()

    call = (
        db.table("calls")
        .select("*")
        .eq("id", call_id)
        .maybe_single()
        .execute()
        .data
    )

    if not call:
        raise HTTPException(404, "Call not found")

    leaves = (
        db.table("spine_leaves")
        .select("*")
        .eq("call_id", call_id)
        .order("timestamp")
        .execute()
        .data
        or []
    )

    messages = (
        db.table("messages")
        .select("*")
        .eq("call_id", call_id)
        .order("created_at")
        .execute()
        .data
        or []
    )

    return {"call": call, "leaves": leaves, "messages": messages}
