"""
POST /send/{phone_id} — שליחה דרך ה-HostAgent, ושמירה ב-messages.

שינוי מהותי: אין יותר spine_webhooks.agent_url.
יש HostAgent אחד שמנתב לפי phoneId (PortHashCalculator) — כלומר קבוע
קונפיגורציה, בדיוק כמו SPINE_URL בכיוון ההפוך. env: HOST_AGENT_URL.
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

# ← הנתיב בקונטרולר השליחה של ה-HostAgent. {phone_id} ו-{type} מוחלפים.
#   אם החוזה שונה — לשנות env בלבד, בלי נגיעה בקוד.
SEND_PATH = os.getenv("HOST_AGENT_SEND_PATH", "/api/messages/{phone_id}/send/{type}")


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

    payload = {"phone": req.contact_phone}
    if req.message_type == "text":
        payload["message"] = req.content or ""
    elif req.message_type == "buttons":
        payload["message"] = req.content or ""
        payload["buttons"] = (req.metadata or {}).get("buttons", [])
    elif req.message_type in ("image", "file", "audio"):
        payload["url"]     = (req.metadata or {}).get("url", "")
        payload["caption"] = req.content or ""

    url = HOST_AGENT_URL.rstrip("/") + SEND_PATH.format(phone_id=phone_id, type=req.message_type)

    wa_id, status = None, "failed"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp   = await c.post(url, json=payload)
            result = resp.json() if resp.content else {}
            wa_id  = result.get("messageId") or (result.get("key") or {}).get("id")
            status = "sent" if resp.status_code == 200 else "failed"
            if status == "failed":
                log.error("Send rejected | phone=%s status=%s body=%s",
                          phone_id, resp.status_code, resp.text[:200])
    except Exception as e:
        log.error("Send failed | phone=%s url=%s: %s", phone_id, url, e)

    # ── direction: מיושר לקונבנציה של ה-HostAgent ─────────────────────────
    # WebhookController: AddMessageAsync(direction: isIncoming)
    #   → True = נכנסת, False = יוצאת.
    # הקוד הקודם כתב כאן True על הודעה יוצאת — היפוך מול ה-HostAgent,
    # שגרם לבועות להתרנדר בצד ההפוך במסך השיחות.
    msg_res = db.table("messages").insert({
        "phone_id":            phone_id,
        "contact_id":          req.contact_id,
        "direction":           False,          # ← יוצאת
        "content":             req.content,
        "message_type":        req.message_type,
        "whatsapp_message_id": wa_id,
        "status":              status,
        "metadata":            req.metadata,
        "call_id":             req.call_id,
    }).execute()

    msg_id = msg_res.data[0]["id"] if msg_res.data else None

    if req.leaf_id and msg_id:
        try:
            db.table("spine_leaf_messages").insert(
                {"leaf_id": req.leaf_id, "message_id": msg_id}).execute()
        except Exception:
            pass

    if status == "failed":
        raise HTTPException(502, f"HostAgent send failed for phone {phone_id}")

    return {"ok": True, "message_id": msg_id, "wa_message_id": wa_id, "status": status}
