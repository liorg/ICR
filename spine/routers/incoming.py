"""
POST /incoming/{phone_id} — agent (Baileys) calls here on incoming message

1. Store message in spine_messages
2. Find active call (spine_runtime) for this contact
3. If exists → forward to Worker
4. If not → create new call and dispatch to Worker
"""
import logging, json, uuid
from typing import Optional, Any
from datetime import datetime, timezone
from fastapi import APIRouter
from pydantic import BaseModel
import httpx
from dependencies import get_supabase

router = APIRouter(prefix="/incoming", tags=["incoming"])
log = logging.getLogger("spine.incoming")

def _now():
    return datetime.now(timezone.utc).isoformat()


class IncomingMessage(BaseModel):
    contact_phone: str           # 972501234567@s.whatsapp.net
    contact_id: Optional[str] = None
    contact_name: Optional[str] = None
    message_id: Optional[str] = None   # Baileys wa message id
    message_type: str = "text"
    content: Optional[str] = None
    metadata: Optional[Any] = None


@router.post("/{phone_id}")
async def handle_incoming(phone_id: str, msg: IncomingMessage):
    db = get_supabase()

    # Resolve contact_id from phone if not provided
    contact_id = msg.contact_id
    if not contact_id:
        contact_id = _resolve_contact(db, phone_id, msg.contact_phone)

    # 1. Store incoming message
    msg_row = db.table("spine_messages").insert({
        "phone_id": phone_id, "contact_id": contact_id or "",
        "direction": False,  # incoming
        "content": msg.content, "message_type": msg.message_type,
        "wa_message_id": msg.message_id, "status": "received",
        "metadata": msg.metadata,
    }).execute()
    stored_msg_id = msg_row.data[0]["id"] if msg_row.data else None

    # 2. Find active call for this contact
    runtime = db.table("spine_runtime").select("call_id, worker_service") \
        .eq("phone_id", phone_id).eq("contact_id", contact_id) \
        .eq("status", "active").maybe_single().execute().data

    if runtime:
        # Active call exists → forward to Worker
        call_id = runtime["call_id"]
        worker_svc = runtime["worker_service"]
        delivered = await _forward_to_worker(worker_svc, contact_id, msg, stored_msg_id)

        log.info("Incoming routed | phone=%s contact=%s call=%s delivered=%s",
                 phone_id, contact_id, call_id, delivered)
        return {"ok": True, "routed": True, "call_id": call_id, "delivered": delivered}

    else:
        # No active call → store message, notify
        _create_notification(db, phone_id, contact_id, msg)

        log.info("Incoming stored | phone=%s contact=%s (no active call)",
                 phone_id, contact_id)
        return {"ok": True, "routed": False, "call_id": None, "message_id": stored_msg_id}


async def _forward_to_worker(worker_service: str, contact_id: str, msg: IncomingMessage, stored_msg_id: int) -> bool:
    if not worker_service:
        return False
    url = f"http://{worker_service}:9000/webhook/event"
    envelope = {
        "typeEvent": "entryMessage",
        "contact_id": contact_id,
        "message_id": msg.message_id or "",
        "payload": {
            "type": msg.message_type,
            "data": {"text": msg.content} if msg.message_type == "text" else msg.metadata or {},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(url, json=envelope)
            return resp.json().get("delivered", False)
    except Exception as e:
        log.warning("Forward to worker %s failed: %s", worker_service, e)
        return False


def _resolve_contact(db, phone_id: str, contact_phone: str) -> str:
    """Find contact_id by phone number, or return phone as fallback."""
    clean = contact_phone.replace("@s.whatsapp.net", "").replace("@c.us", "")
    res = db.table("contacts").select("id") \
        .eq("phone_id", phone_id) \
        .or_(f"phone.eq.{clean},lid.eq.{contact_phone}") \
        .limit(1).execute()
    if res.data:
        return res.data[0]["id"]
    return clean


def _create_notification(db, phone_id, contact_id, msg):
    try:
        db.table("spine_notifications").insert({
            "phone_id": phone_id, "contact_id": contact_id,
            "type": "incoming_no_call",
            "title": f"הודעה נכנסת מ-{msg.contact_name or contact_id}",
            "body": (msg.content or "")[:100],
        }).execute()
    except Exception:
        pass
