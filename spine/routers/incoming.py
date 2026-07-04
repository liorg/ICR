"""POST /incoming/{phone_id} — agent sends here. No spine_runtime — queries calls directly."""
import logging
from typing import Optional, Any
from fastapi import APIRouter
from pydantic import BaseModel
import httpx
from dependencies import get_supabase

router = APIRouter(prefix="/incoming", tags=["incoming"])
log = logging.getLogger("spine.incoming")

class IncomingMsg(BaseModel):
    contact_phone: str
    contact_id: Optional[str] = None
    contact_name: Optional[str] = None
    message_id: Optional[str] = None
    message_type: str = "text"
    content: Optional[str] = None
    metadata: Optional[Any] = None

@router.post("/{phone_id}")
async def handle_incoming(phone_id: str, msg: IncomingMsg):
    db = get_supabase()
    contact_id = msg.contact_id or _resolve_contact(db, phone_id, msg.contact_phone)

    # Find active call — directly from calls table, no spine_runtime
    active_call = db.table("calls") \
        .select("id, phone_id") \
        .eq("phone_id", phone_id).eq("contact_id", contact_id).eq("status", "running") \
        .order("started_at", desc=True).limit(1).maybe_single().execute().data

    call_id = active_call["id"] if active_call else None

    # Find worker for this phone
    worker = db.table("phone_workers") \
        .select("service_name") \
        .eq("phone_id", phone_id).eq("status", "running") \
        .maybe_single().execute().data

    # Store in existing messages table
    db.table("messages").insert({
        "phone_id": phone_id, "contact_id": contact_id or "",
        "direction": False, "content": msg.content,
        "message_type": msg.message_type,
        "whatsapp_message_id": msg.message_id,
        "status": "received", "metadata": msg.metadata,
        "call_id": call_id,
    }).execute()

    if active_call and worker:
        delivered = await _forward_to_worker(worker["service_name"], contact_id, msg)
        log.info("Incoming routed | phone=%s contact=%s call=%s", phone_id, contact_id, call_id)
        return {"ok": True, "routed": True, "call_id": call_id, "delivered": delivered}

    # No active call — notification
    db.table("spine_notifications").insert({
        "phone_id": phone_id, "contact_id": contact_id,
        "type": "incoming_no_call",
        "payload": {"contact_name": msg.contact_name or contact_id, "content": (msg.content or "")[:100]},
    }).execute()
    return {"ok": True, "routed": False, "call_id": None}

async def _forward_to_worker(svc, contact_id, msg):
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            resp = await c.post(f"http://{svc}:9000/webhook/event", json={
                "typeEvent": "entryMessage", "contact_id": contact_id,
                "message_id": msg.message_id or "",
                "payload": {"type": msg.message_type, "data": {"text": msg.content} if msg.message_type == "text" else msg.metadata or {}},
            })
            return resp.json().get("delivered", False)
    except: return False

def _resolve_contact(db, phone_id, phone):
    clean = phone.replace("@s.whatsapp.net", "").replace("@c.us", "")
    res = db.table("contacts").select("id").eq("phone_id", phone_id).or_(f"phone.eq.{clean},lid.eq.{phone}").limit(1).execute()
    return res.data[0]["id"] if res.data else clean
