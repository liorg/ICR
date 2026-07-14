"""
Dispatch — נקודת הכניסה של Scheduler ו-API ישיר.
הלוגיקה עצמה ב-services/calls.py. כאן רק HTTP.

    POST /api/calls/ensure          201 CREATED | 202 QUEUED | 409 BLOCKED
    POST /api/calls/{id}/complete
    POST /api/dispatch              legacy → ensure
    POST /api/dispatch/message
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from dependencies import get_supabase
from services.calls import ensure_call, complete_call, send_to_worker, entry_payload

router = APIRouter(tags=["dispatch"])
log = logging.getLogger("spine.dispatch")


class EnsureReq(BaseModel):
    phone_id:      str
    contact_id:    str
    scenario_id:   str                    # חובה. אין בחירה אוטומטית.
    priority:      Optional[int] = None
    source:        str = "api"            # trigger | scheduler | api
    first_message: Optional[dict] = None


class DispatchReq(BaseModel):
    phone_id:      str
    contact_id:    str
    scenario_id:   str
    scenario_json: Optional[str] = None   # מתעלמים — ה-config נשלף מה-DB
    contact_phone: Optional[str] = None
    contact_name:  Optional[str] = None
    first_message: Optional[dict] = None


class ForwardReq(BaseModel):
    phone_id:   str
    contact_id: str
    call_id:    Optional[str] = None
    message_id: Optional[str] = None
    payload:    Optional[dict] = None


class CompleteReq(BaseModel):
    status: str = "completed"


# ── ensure ────────────────────────────────────────────────────────────
@router.post("/calls/ensure")
async def ensure(req: EnsureReq, response: Response):
    db  = get_supabase()
    res = await ensure_call(db, req.phone_id, req.contact_id, req.scenario_id,
                            req.priority, req.source, req.first_message)

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


# ── legacy ────────────────────────────────────────────────────────────
@router.post("/dispatch")
async def dispatch(req: DispatchReq, response: Response):
    return await ensure(EnsureReq(
        phone_id=req.phone_id, contact_id=req.contact_id,
        scenario_id=req.scenario_id, source="api",
        first_message=req.first_message,
    ), response)


@router.post("/dispatch/message")
async def forward_message(req: ForwardReq):
    db = get_supabase()
    p  = req.payload or {"type": "text", "data": {"text": ""}}
    ok = await send_to_worker(db, req.phone_id, entry_payload(
        req.call_id, None, req.contact_id, req.message_id or "",
        p.get("type", "text"), (p.get("data") or {}).get("text"), p.get("data"),
    ))
    return {"ok": ok, "delivered": ok}
