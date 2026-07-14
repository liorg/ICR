"""
POST /incoming — ה-callback היחיד שה-HostAgent מכיר.

payload (WebhookDispatchPayload, PascalCase):
    { "MessageId", "PhoneId", "ContactId", "Direction" }

Direction נגזר מ-DispatchAsync(..., isIncoming):
    true → נכנסת   |   false → יוצאת (fromMe)

ה-HostAgent כבר עשה AddMessageAsync — ההודעה קיימת. כאן קוראים, לא כותבים.
יצירת calls עוברת רק דרך services.calls.ensure_call.
"""
import os, logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from dependencies import get_supabase
from services.calls import ensure_call, send_to_worker, entry_payload

router = APIRouter(prefix="/incoming", tags=["incoming"])
log = logging.getLogger("spine.incoming")

# scenarios.status — 'draft' (ברירת מחדל) | 'active' (מפורסם).
# לא אותו דבר כמו contacts.tag == 'active'. אותה מילה, טבלאות שונות.
PUBLISHED = os.getenv("SCENARIO_PUBLISHED_STATUS", "active")


class DispatchPayload(BaseModel):
    model_config = {"populate_by_name": True}
    message_id: str  = Field(alias="MessageId")
    phone_id:   str  = Field(alias="PhoneId")
    contact_id: str  = Field(alias="ContactId")
    direction:  bool = Field(alias="Direction")


@router.post("/{phone_id}")
async def handle_incoming_legacy(phone_id: str, p: DispatchPayload):
    return await handle_incoming(p)


@router.post("")
async def handle_incoming(p: DispatchPayload):
    db = get_supabase()

    # ── Gate 1: יוצאת → drop ──────────────────────────────────────────
    # SaveMessage מפעיל DispatchAsync גם על fromMe. בלי הסינון, כל הודעה
    # שה-worker שולח מדליקה תרחיש חדש = לולאה אינסופית.
    if not p.direction:
        log.debug("[GATE] outgoing — dropped | msg=%s", p.message_id)
        return {"ok": True, "routed": False, "reason": "outgoing"}

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
    active = db.table("calls").select("id, scenario_id") \
        .eq("phone_id", p.phone_id).eq("contact_id", p.contact_id) \
        .eq("status", "running").limit(1).execute().data

    if active:
        delivered = await send_to_worker(db, p.phone_id, entry_payload(
            active[0]["id"], active[0].get("scenario_id"), p.contact_id,
            wa_msg_id, msg_type, content, msg.get("metadata"),
        ))
        log.info("[ROUTE] → worker | call=%s delivered=%s", active[0]["id"], delivered)
        return {"ok": True, "routed": True,
                "call_id": active[0]["id"], "delivered": delivered}

    # ── אין שיחה פעילה → התרחישים של איש הקשר הזה ─────────────────────
    # scenarios קשורה ישירות ל-(phone_id, contact_id). אין חיפוש לפי תוכן.
    scenarios = db.table("scenarios") \
        .select("id, priority") \
        .eq("phone_id", p.phone_id) \
        .eq("contact_id", p.contact_id) \
        .eq("status", PUBLISHED) \
        .eq("event_type", "trigger") \
        .order("priority").execute().data or []

    if not scenarios:
        log.info("[ROUTE] no published trigger scenario | phone=%s contact=%s",
                 p.phone_id, p.contact_id)
        return {"ok": True, "routed": False, "reason": "no_trigger_scenario"}

    first_message = {"type": msg_type, "data": {"text": content}} if content else None
    created = []

    for sc in scenarios:
        res = await ensure_call(
            db, p.phone_id, p.contact_id, sc["id"],
            priority=sc.get("priority"), source="trigger",
            first_message=first_message if not created else None,
        )

        if res.code == "CONTACT_NOT_ACTIVE":
            log.info("[GATE] CONTACT_NOT_ACTIVE | contact=%s", p.contact_id)
            return {"ok": True, "routed": False, "reason": "contact_not_active"}

        if res.http_status not in (201, 202):
            log.error("[TRIGGER] ensure failed | scenario=%s %s", sc["id"], res.body)
            continue

        # 201 → יש worker_payload. 202 (queued) → אין; יורם ב-complete_call.
        if res.needs_worker:
            res.with_delivery(await send_to_worker(db, res.phone_id, res.worker_payload))

        log.info("[TRIGGER] %s | scenario=%s call=%s delivered=%s",
                 res.code, sc["id"], res.call_id, res.body.get("delivered"))
        created.append(res.body)

    if not created:
        return {"ok": False, "routed": False, "reason": "ensure_failed"}

    running = sum(1 for b in created if b.get("status") == "running")
    log.info("[TRIGGER] %d calls (%d running, %d queued) | phone=%s contact=%s",
             len(created), running, len(created) - running, p.phone_id, p.contact_id)
    return {"ok": True, "routed": True, "triggered": len(created), "calls": created}
