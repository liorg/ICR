"""
POST /incoming — ה-callback היחיד שה-HostAgent מכיר.

חוזה ה-payload (WebhookDispatchPayload, PascalCase מ-JsonSerializer):
    { "MessageId": "...", "PhoneId": "...", "ContactId": "...", "Direction": true }

Direction נגזר מ-`DispatchAsync(..., isIncoming)` ב-WebhookController:
    Direction == true  →  הודעה נכנסת
    Direction == false →  הודעה יוצאת (fromMe)

ה-HostAgent כבר עשה AddMessageAsync — השורה קיימת ב-messages.
לכן כאן קוראים אותה ולא כותבים אותה שוב.
"""
import uuid, json, logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from dependencies import get_supabase

router = APIRouter(prefix="/incoming", tags=["incoming"])
log = logging.getLogger("spine.incoming")


def _now():
    return datetime.now(timezone.utc).isoformat()


class DispatchPayload(BaseModel):
    """PascalCase מה-HostAgent; alias מאפשר גם snake_case לבדיקות ידניות."""
    model_config = {"populate_by_name": True}

    message_id: str  = Field(alias="MessageId")
    phone_id:   str  = Field(alias="PhoneId")
    contact_id: str  = Field(alias="ContactId")
    direction:  bool = Field(alias="Direction")


# ── legacy: נתיב פר-טלפון. נשמר לתאימות, מנתב לאותו handler. ────────────────
@router.post("/{phone_id}")
async def handle_incoming_legacy(phone_id: str, p: DispatchPayload):
    return await handle_incoming(p)


@router.post("")
async def handle_incoming(p: DispatchPayload):
    db = get_supabase()

    # ── Gate 1: הודעה יוצאת → drop ────────────────────────────────────────
    # SaveMessage ב-HostAgent מפעיל DispatchAsync גם על fromMe.
    # בלי הסינון הזה — כל הודעה שה-worker שולח מדליקה תרחיש חדש = לולאה אינסופית.
    if not p.direction:
        log.debug("[GATE] outgoing message — dropped | msg=%s", p.message_id)
        return {"ok": True, "routed": False, "reason": "outgoing"}

    # ── שליפת ההודעה שה-HostAgent כבר שמר ─────────────────────────────────
    msg = db.table("messages") \
        .select("id, content, message_type, whatsapp_message_id, metadata") \
        .eq("id", p.message_id).maybe_single().execute().data

    if not msg:
        log.warning("[INCOMING] message %s not found in DB", p.message_id)
        return {"ok": False, "routed": False, "reason": "message_not_found"}

    # ── Gate 2: contact חייב להיות ACTIVE ─────────────────────────────────
    # HostAgent יוצר draft contact לכל מספר לא מוכר. draft לא מריץ תרחישים.
    contact = db.table("contacts") \
        .select("id, phone, name, tag") \
        .eq("id", p.contact_id).maybe_single().execute().data

    if not contact:
        log.warning("[INCOMING] contact %s not found", p.contact_id)
        return {"ok": False, "routed": False, "reason": "contact_not_found"}

    if contact.get("tag") != "active":
        log.info("[GATE] contact %s tag=%s (not active) — dropped",
                 p.contact_id, contact.get("tag"))
        return {"ok": True, "routed": False, "reason": "contact_not_active"}

    content = msg.get("content")
    ctx = {
        "phone_id":      p.phone_id,
        "contact_id":    p.contact_id,
        "contact_phone": contact.get("phone") or "",
        "contact_name":  contact.get("name") or "",
        "message_id":    msg.get("whatsapp_message_id") or p.message_id,
        "message_type":  msg.get("message_type") or "text",
        "content":       content,
        "metadata":      msg.get("metadata"),
    }

    # ── שיחה פעילה → העברה ל-Worker הרץ ───────────────────────────────────
    active_call = db.table("calls").select("id") \
        .eq("phone_id", p.phone_id).eq("contact_id", p.contact_id) \
        .eq("status", "running").order("started_at", desc=True) \
        .limit(1).execute().data

    if active_call:
        worker = _worker(db, p.phone_id)
        if worker:
            delivered = await _forward_to_worker(worker, ctx)
            log.info("[ROUTE] → worker | phone=%s contact=%s call=%s delivered=%s",
                     p.phone_id, p.contact_id, active_call[0]["id"], delivered)
            return {"ok": True, "routed": True,
                    "call_id": active_call[0]["id"], "delivered": delivered}
        log.warning("[ROUTE] active call but no running worker | phone=%s", p.phone_id)

    # ── אין שיחה פעילה → auto-trigger לפי תוכן ────────────────────────────
    created = _auto_trigger(db, ctx)
    if created:
        return {"ok": True, "routed": True, "triggered": len(created), "calls": created}

    log.info("[ROUTE] stored, no trigger | phone=%s contact=%s", p.phone_id, p.contact_id)
    return {"ok": True, "routed": False, "call_id": None}


# ── Triggers ──────────────────────────────────────────────────────────────
def _auto_trigger(db, ctx):
    """תרחישים עם auto_trigger שמותאם לתוכן ההודעה → תור לפי priority."""
    content = ctx.get("content")
    if not content:
        return None

    scenarios = db.table("scenarios") \
        .select("id, config, priority, auto_trigger") \
        .eq("phone_id", ctx["phone_id"]) \
        .eq("auto_trigger_enabled", True) \
        .order("priority") \
        .execute().data or []

    matched = [
        sc for sc in scenarios
        if sc.get("auto_trigger") and sc["auto_trigger"].lower() in content.lower()
    ]
    if not matched:
        return None

    return _queue_scenarios(db, ctx, matched)


def _queue_scenarios(db, ctx, scenarios):
    """הראשון רץ, השאר queued — pop_next_queued_call ידחוף אותם בסיום."""
    calls = []
    for i, sc in enumerate(scenarios):
        call_id  = f"call-{uuid.uuid4().hex[:12]}"
        status   = "running" if i == 0 else "queued"
        config   = sc.get("config") or {}
        snapshot = config if isinstance(config, dict) else {}

        db.table("calls").insert({
            "id":                call_id,
            "scenario_id":       sc["id"],
            "scenario_snapshot": snapshot,
            "phone_id":          ctx["phone_id"],
            "contact_id":        ctx["contact_id"],
            "status":            status,
            "priority":          sc.get("priority", i),
            "started_at":        _now() if status == "running" else None,
        }).execute()

        calls.append({"call_id": call_id, "scenario_id": sc["id"],
                      "status": status, "priority": sc.get("priority", i)})

    worker = _worker(db, ctx["phone_id"])
    if worker:
        cfg = scenarios[0].get("config") or {}
        import asyncio
        asyncio.ensure_future(_post(worker, {
            "typeEvent":     "init",
            "call_id":       calls[0]["call_id"],
            "contact_id":    ctx["contact_id"],
            "contact_phone": ctx["contact_phone"],
            "contact_name":  ctx["contact_name"],
            "scenario_id":   scenarios[0]["id"],
            "scenario_json": json.dumps(cfg) if isinstance(cfg, dict) else str(cfg),
            "first_message": {"type": ctx["message_type"],
                              "data": {"text": ctx["content"]}} if ctx["content"] else None,
        }))
    else:
        log.warning("[TRIGGER] no running worker for phone=%s — calls queued only", ctx["phone_id"])

    log.info("[TRIGGER] %d calls | phone=%s contact=%s first=%s",
             len(calls), ctx["phone_id"], ctx["contact_id"], calls[0]["call_id"])
    return calls


# ── Worker plane ──────────────────────────────────────────────────────────
def _worker(db, phone_id) -> Optional[str]:
    res = db.table("phone_workers").select("service_name") \
        .eq("phone_id", phone_id).eq("status", "running") \
        .limit(1).execute().data
    return f"http://{res[0]['service_name']}:9000" if res else None


async def _forward_to_worker(worker_url, ctx) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            resp = await c.post(f"{worker_url}/webhook/event", json={
                "typeEvent":  "entryMessage",
                "contact_id": ctx["contact_id"],
                "message_id": ctx["message_id"],
                "payload": {
                    "type": ctx["message_type"],
                    "data": {"text": ctx["content"]} if ctx["message_type"] == "text"
                            else (ctx["metadata"] or {}),
                },
            })
            return resp.json().get("delivered", False)
    except Exception as e:
        log.error("[FORWARD] failed | %s: %s", worker_url, e)
        return False


async def _post(worker_url, payload):
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"{worker_url}/webhook/event", json=payload)
    except Exception as e:
        log.error("[DISPATCH] failed | %s: %s", worker_url, e)
