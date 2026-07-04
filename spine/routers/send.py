"""POST /send/{phone_id} — send via agent, store in messages"""
import logging
from typing import Optional, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx
from dependencies import get_supabase

router = APIRouter(prefix="/send", tags=["send"])
log = logging.getLogger("spine.send")

class SendReq(BaseModel):
    contact_id: str
    contact_phone: str
    message_type: str = "text"
    content: Optional[str] = None
    metadata: Optional[Any] = None
    leaf_id: Optional[str] = None
    call_id: Optional[str] = None

@router.post("/{phone_id}")
async def send_message(phone_id: str, req: SendReq):
    db = get_supabase()
    agent_url = _resolve_agent(db, phone_id)
    if not agent_url:
        raise HTTPException(404, f"No agent for phone {phone_id}")

    payload = {"phone": req.contact_phone}
    if req.message_type == "text":
        payload["message"] = req.content or ""
    elif req.message_type == "buttons":
        payload["message"] = req.content or ""
        payload["buttons"] = (req.metadata or {}).get("buttons", [])
    elif req.message_type in ("image", "file", "audio"):
        payload["url"] = (req.metadata or {}).get("url", "")
        payload["caption"] = req.content or ""

    wa_id, status = None, "failed"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.post(f"{agent_url}/send/{req.message_type}", json=payload)
            result = resp.json()
            wa_id = result.get("messageId") or result.get("key", {}).get("id")
            status = "sent" if resp.status_code == 200 else "failed"
    except Exception as e:
        log.error("Send failed | phone=%s: %s", phone_id, e)

    msg_res = db.table("messages").insert({
        "phone_id": phone_id, "contact_id": req.contact_id,
        "direction": True, "content": req.content,
        "message_type": req.message_type, "whatsapp_message_id": wa_id,
        "status": status, "metadata": req.metadata, "call_id": req.call_id,
    }).execute()
    msg_id = msg_res.data[0]["id"] if msg_res.data else None

    if req.leaf_id and msg_id:
        try: db.table("spine_leaf_messages").insert({"leaf_id": req.leaf_id, "message_id": msg_id}).execute()
        except: pass

    return {"ok": True, "message_id": msg_id, "wa_message_id": wa_id, "status": status}

def _resolve_agent(db, phone_id):
    res = db.table("spine_webhooks").select("agent_url").eq("phone_id", phone_id).eq("status", "active").limit(1).execute()
    return res.data[0]["agent_url"] if res.data else None
