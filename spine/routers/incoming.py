"""
POST /incoming — ה-callback היחיד שה-HostAgent מכיר.

חוזה ה-payload (WebhookDispatchPayload, PascalCase מ-JsonSerializer):
    { "MessageId": "...", "PhoneId": "...", "ContactId": "...", "Direction": true }

Direction נגזר מ-`DispatchAsync(..., isIncoming)` ב-WebhookController:
    true  → נכנסת   |   false → יוצאת (fromMe)

ה-HostAgent כבר עשה AddMessageAsync — השורה קיימת ב-messages.
לכן כאן קוראים אותה, לא כותבים אותה שוב.

יצירת calls עוברת אך ורק דרך dispatch.ensure_core → spine_ensure_call.
אין כאן INSERT ל-calls. מסלול יצירה שני היה שובר את ה-invariant.
"""
import logging
from typing import Optional

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from dependencies import get_supabase
from routers.dispatch import ensure_core, EnsureReq

router = APIRouter(prefix="/incoming", tags=["incoming"])
log = logging.getLogger("spine.incoming")


class DispatchPayload(BaseModel):
    """PascalCase מה-HostAgent; alias מאפשר גם snake_case לבדיקות ידניות."""
    model_config = {"populate_by_name": True}

    message_id: str  = Field(alias="MessageId")
    phone_id:   str  = Field(alias="PhoneId")
    contact_id: str  = Field(alias="ContactId")
    direction:  bool = Field(alias="Direction")


# legacy: נתיב פר-טלפון. נשמר לתאימות, מנתב לאותו handler.
@router.post("/{phone_id}")
async def handle_incoming_legacy(phone_id: str, p: DispatchPayload):
    return await handle_incoming(p)


@router.post("")
async def handle_incoming(p: DispatchPayload):
    db = get_supabase()

    # ── Gate 1: הודעה יוצאת → drop ────────────────────────────────────
    # SaveMessage ב-HostAgent מפעיל DispatchAsync גם על fromMe.
    # בלי זה, כל הודעה שה-worker שולח מדליקה תרחיש = לולאה אינסופית.
    if not p.direction:
        log.debug("[GATE] outgoing — dropped | msg=%s", p.message_id)
        return {"ok": True, "routed": False, "reason": "outgoing"}

    # ── ההודעה שה-HostAgent כבר שמר ───────────────────────────────────
    msg = db.table("messages") \
        .select("id, content, message_type, whatsapp_message_id, metadata") \
        .eq("id", p.message_id).maybe_single().execute().data
    if not msg:
        log.warning("[INCOMING] message %s not in DB", p.message_id)
        return {"ok": False, "routed": False, "reason": "message_not_found"}

    content   = msg.get("content")
    msg_type  = msg.get("message_type") or "text"
    wa_msg_id = msg.get("whatsapp_message_id") or p.message_id

    # ── שיחה פעילה → מעבירים את ההודעה, לא יוצרים call ────────────────
    active = db.table("calls").select("id") \
        .eq("phone_id", p.phone_id).eq("contact_id", p.contact_id) \
        .eq("status", "running").limit(1).execute().data

    if active:
        worker = _worker(db, p.phone_id)
        if not worker:
            log.warning("[ROUTE] active call, no worker | phone=%s", p.phone_id)
            return {"ok": True, "routed": False, "reason": "no_worker",
                    "call_id": active[0]["id"]}

        delivered = await _forward(worker, p.contact_id, wa_msg_id,
                                   msg_type, content, msg.get("metadata"))
        log.info("[ROUTE] → worker | call=%s delivered=%s", active[0]["id"], delivered)
        return {"ok": True, "routed": True,
                "call_id": active[0]["id"], "delivered": delivered}

    # ── אין שיחה פעילה → auto-trigger לפי תוכן ────────────────────────
    if not content:
        return {"ok": True, "routed": False, "reason": "no_content"}

    scenarios = db.table("scenarios") \
        .select("id, priority, auto_trigger") \
        .eq("phone_id", p.phone_id) \
        .eq("auto_trigger_enabled", True) \
        .order("priority").execute().data or []

    matched = [sc for sc in scenarios
               if sc.get("auto_trigger") and sc["auto_trigger"].lower() in content.lower()]

    if not matched:
        log.info("[ROUTE] no trigger match | phone=%s contact=%s", p.phone_id, p.contact_id)
        return {"ok": True, "routed": False, "reason": "no_trigger_match"}

    # ── כל התאמה → ensure_core. ה-unique index מכריע מי running ומי queued.
    # אין כאן "הראשון רץ" — ה-DB עושה את זה, אטומית, גם מול טריגר מקביל.
    first_message = {"type": msg_type, "data": {"text": content}}
    created = []

    for sc in matched:
        code, body = await ensure_core(EnsureReq(
            phone_id=p.phone_id,
            contact_id=p.contact_id,
            scenario_id=sc["id"],
            priority=sc.get("priority"),
            source="trigger",
            first_message=first_message if not created else None,
        ))

        if code == 409:                       # contact לא active → אין טעם להמשיך
            log.info("[GATE] %s | contact=%s", body.get("code"), p.contact_id)
            return {"ok": True, "routed": False, "reason": "contact_not_active"}
        if code not in (201, 202):
            log.error("[TRIGGER] ensure failed | scenario=%s code=%s %s",
                      sc["id"], code, body)
            continue

        created.append(body)

    if not created:
        return {"ok": False, "routed": False, "reason": "ensure_failed"}

    log.info("[TRIGGER] %d calls | phone=%s contact=%s", len(created), p.phone_id, p.contact_id)
    return {"ok": True, "routed": True, "triggered": len(created), "calls": created}


# ── Worker plane ──────────────────────────────────────────────────────
def _worker(db, phone_id: str) -> Optional[str]:
    r = db.table("phone_workers").select("service_name") \
          .eq("phone_id", phone_id).eq("status", "running") \
          .limit(1).execute().data
    return f"http://{r[0]['service_name']}:9000" if r else None


async def _forward(worker_url, contact_id, message_id, msg_type, content, metadata) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(f"{worker_url}/webhook/event", json={
                "typeEvent":  "entryMessage",
                "contact_id": contact_id,
                "message_id": message_id,
                "payload": {
                    "type": msg_type,
                    "data": {"text": content} if msg_type == "text" else (metadata or {}),
                },
            })
            return r.json().get("delivered", False)
    except Exception as e:
        log.error("[FORWARD] %s: %s", worker_url, e)
        return False
