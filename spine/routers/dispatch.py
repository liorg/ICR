"""Dispatch scenarios and messages to Workers."""
import uuid, logging
from typing import Optional
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from dependencies import get_supabase

router = APIRouter(tags=["dispatch"])
log = logging.getLogger("spine.dispatch")

class DispatchReq(BaseModel):
    phone_id: str
    contact_id: str
    scenario_id: str
    scenario_json: str
    contact_phone: Optional[str] = None
    contact_name: Optional[str] = None
    first_message: Optional[dict] = None

class ForwardReq(BaseModel):
    phone_id: str
    contact_id: str
    message_id: Optional[str] = None
    payload: Optional[dict] = None

@router.post("/dispatch")
async def dispatch(req: DispatchReq):
    url = _worker_url(req.phone_id)
    if not url:
        raise HTTPException(404, f"No worker for phone {req.phone_id}")

    call_id = f"call-{uuid.uuid4().hex[:12]}"
    envelope = {
        "typeEvent": "init", "call_id": call_id,
        "contact_id": req.contact_id, "contact_phone": req.contact_phone or "",
        "contact_name": req.contact_name or "", "scenario_id": req.scenario_id,
        "scenario_json": req.scenario_json,
    }
    if req.first_message:
        envelope["first_message"] = req.first_message

    async with httpx.AsyncClient(timeout=10) as c:
        resp = await c.post(f"{url}/webhook/event", json=envelope)
        result = resp.json()

    if resp.status_code == 409:
        return {"ok": False, "error": "Contact already active"}
    if not result.get("ok"):
        raise HTTPException(resp.status_code, result.get("error"))

    log.info("Dispatched call=%s → %s", call_id, req.phone_id)
    return {"ok": True, "call_id": call_id}

@router.post("/dispatch/message")
async def forward_message(req: ForwardReq):
    url = _worker_url(req.phone_id)
    if not url:
        return {"ok": False, "delivered": False}

    envelope = {
        "typeEvent": "entryMessage", "contact_id": req.contact_id,
        "message_id": req.message_id or "", "payload": req.payload or {"type": "text", "data": {"text": ""}},
    }
    async with httpx.AsyncClient(timeout=5) as c:
        resp = await c.post(f"{url}/webhook/event", json=envelope)
    return {"ok": True, "delivered": resp.json().get("delivered", False)}

def _worker_url(phone_id: str) -> Optional[str]:
    """Resolve worker by service_name from phone_workers table → DNS in overlay."""
    db = get_supabase()
    res = db.table("phone_workers").select("service_name").eq("phone_id", phone_id).eq("status", "running").maybe_single().execute()
    if res.data:
        return f"http://{res.data['service_name']}:9000"
    return None
