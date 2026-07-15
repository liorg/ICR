"""
POST /send/{phone_id} — שליחה דרך ה-HostAgent, ושמירה ב-messages.

חוזה ה-HostAgent (אומת מול Swagger):
    POST /api/phones/{phoneId}/send/{type}
    SendTextRequest → { "jid": "...", "text": "..." }

הסוגים הנתמכים בפועל:
    text · buttons · list · button-response · list-response · ping · status
אין image/file/audio — כל שליחת מדיה תחזיר 404.

אין spine_webhooks. יש HostAgent אחד שמנתב לפי phoneId → env.
"""
import os, logging
from typing import Optional, Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dependencies import get_supabase

router = APIRouter(prefix="/send", tags=["send"])
log = logging.getLogger("spine.send")

HOST_AGENT_URL = os.getenv("HOST_AGENT_URL", "http://10.186.0.2:5000")
SEND_PATH      = os.getenv("HOST_AGENT_SEND_PATH", "/api/phones/{phone_id}/send/{type}")

# מה שה-HostAgent באמת חושף. כל השאר → 404.
SUPPORTED = {"text", "buttons", "list", "button-response", "list-response", "ping", "status"}


class SendReq(BaseModel):
    contact_id:    str
    contact_phone: str                      # ה-jid: lid אם קיים, אחרת number
    message_type:  str = "text"
    content:       Optional[str] = None
    metadata:      Optional[Any] = None
    leaf_id:       Optional[str] = None
    call_id:       Optional[str] = None


@router.post("/{phone_id}")
async def send_message(phone_id: str, req: SendReq):
    db = get_supabase()

    if req.message_type not in SUPPORTED:
        raise HTTPException(400, f"unsupported message_type '{req.message_type}' "
                                 f"(HostAgent supports: {', '.join(sorted(SUPPORTED))})")

    # ── SendTextRequest: jid + text. לא phone/message. ─────────────────
    payload = {"jid": req.contact_phone, "text": req.content or ""}

    meta = req.metadata or {}
    if req.message_type == "buttons":
        payload["buttons"] = meta.get("buttons", [])
    elif req.message_type == "list":
        payload["sections"] = meta.get("sections", [])

    url = HOST_AGENT_URL.rstrip("/") + SEND_PATH.format(
        phone_id=phone_id, type=req.message_type)

    wa_id, status = None, "failed"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp   = await c.post(url, json=payload)
            result = resp.json() if resp.content else {}
            wa_id  = result.get("messageId") or (result.get("key") or {}).get("id")
            status = "sent" if resp.status_code == 200 else "failed"
            if status == "failed":
                log.error("Send rejected | phone=%s url=%s status=%s body=%s",
                          phone_id, url, resp.status_code, resp.text[:200])
    except Exception as e:
        log.error("Send failed | phone=%s url=%s: %s", phone_id, url, e)


    if req.leaf_id and req.call_id and wa_id:
        db.table("spine_leaf_messages").upsert(
            {
                "scenario_id": req.scenario_id,
                "call_id": req.call_id,
                "leaf_id": req.leaf_id,
                "whatsapp_message_id": wa_id,
                "message_id": None,
            },
            on_conflict="scenario_id,call_id,leaf_id,whatsapp_message_id",
        ).execute()

    if status == "failed":
        raise HTTPException(502, f"HostAgent send failed for phone {phone_id}")

    return { "ok": True,  "message_id": wa_id,  "wa_message_id": wa_id,"status": status}
    
