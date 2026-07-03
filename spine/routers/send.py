"""
POST /send/{phone_id} — send WhatsApp message via agent

Spine looks up agent URL for phone_id → forwards → stores message → links to leaf.
"""
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
    contact_phone: str       # e.g. 972501234567@s.whatsapp.net
    message_type: str = "text"  # text | buttons | list | image | file
    content: Optional[str] = None
    metadata: Optional[Any] = None   # buttons array, media url, etc.
    leaf_id: Optional[str] = None    # link this message to a leaf
    call_id: Optional[str] = None


@router.post("/{phone_id}")
async def send_message(phone_id: str, req: SendReq):
    db = get_supabase()

    # 1. Find agent URL for this phone
    agent_url = _resolve_agent(db, phone_id)
    if not agent_url:
        raise HTTPException(404, f"No agent registered for phone {phone_id}")

    # 2. Build agent payload based on message_type
    agent_payload = _build_agent_payload(req)

    # 3. Send to agent
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            endpoint = f"{agent_url}/send/{req.message_type}"
            resp = await client.post(endpoint, json=agent_payload)
            result = resp.json()
    except Exception as e:
        log.error("Agent send failed | phone=%s: %s", phone_id, e)
        # Store as failed message
        msg_id = _store_message(db, phone_id, req, wa_message_id=None, status="failed")
        if req.leaf_id and msg_id:
            _link_leaf_message(db, req.leaf_id, msg_id)
        raise HTTPException(502, f"Agent unreachable: {e}")

    wa_message_id = result.get("messageId") or result.get("key", {}).get("id")
    success = result.get("success", resp.status_code == 200)
    status = "sent" if success else "failed"

    # 4. Store message
    msg_id = _store_message(db, phone_id, req, wa_message_id=wa_message_id, status=status)

    # 5. Link to leaf
    if req.leaf_id and msg_id:
        _link_leaf_message(db, req.leaf_id, msg_id)

    log.info("Sent | phone=%s contact=%s type=%s wa_id=%s", phone_id, req.contact_id, req.message_type, wa_message_id)
    return {
        "ok": True,
        "message_id": msg_id,
        "wa_message_id": wa_message_id,
        "status": status,
    }


def _resolve_agent(db, phone_id: str) -> Optional[str]:
    """Look up agent URL from webhooks table or phone_workers."""
    res = db.table("spine_webhooks").select("agent_url") \
        .eq("phone_id", phone_id).eq("status", "active") \
        .limit(1).execute()
    if res.data:
        return res.data[0]["agent_url"]
    return None


def _build_agent_payload(req: SendReq) -> dict:
    base = {"phone": req.contact_phone}
    if req.message_type == "text":
        base["message"] = req.content or ""
    elif req.message_type == "buttons":
        base["message"] = req.content or ""
        base["buttons"] = req.metadata.get("buttons", []) if req.metadata else []
        base["footer"] = req.metadata.get("footer", "") if req.metadata else ""
    elif req.message_type == "list":
        base["message"] = req.content or ""
        base["sections"] = req.metadata.get("sections", []) if req.metadata else []
        base["buttonText"] = req.metadata.get("buttonText", "בחר") if req.metadata else "בחר"
    elif req.message_type in ("image", "file", "audio"):
        base["url"] = req.metadata.get("url", "") if req.metadata else ""
        base["caption"] = req.content or ""
    return base


def _store_message(db, phone_id, req, wa_message_id, status) -> Optional[int]:
    try:
        res = db.table("spine_messages").insert({
            "phone_id": phone_id,
            "contact_id": req.contact_id,
            "direction": True,  # outgoing
            "content": req.content,
            "message_type": req.message_type,
            "wa_message_id": wa_message_id,
            "status": status,
            "metadata": req.metadata,
        }).execute()
        return res.data[0]["id"] if res.data else None
    except Exception as e:
        log.warning("Failed to store message: %s", e)
        return None


def _link_leaf_message(db, leaf_id: str, message_id: int):
    try:
        db.table("spine_leaf_messages").insert({
            "leaf_id": leaf_id, "message_id": message_id,
        }).execute()
    except Exception as e:
        log.warning("Failed to link leaf-message: %s", e)
