"""POST /incoming/{phone_id} — incoming message → queue scenarios by priority"""
import uuid, json, logging
from typing import Optional, Any
from fastapi import APIRouter
from pydantic import BaseModel
import httpx
from dependencies import get_supabase

router = APIRouter(prefix="/incoming", tags=["incoming"])
log = logging.getLogger("spine.incoming")

def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class IncomingMsg(BaseModel):
    contact_phone: str
    contact_id: Optional[str] = None
    contact_name: Optional[str] = None
    message_id: Optional[str] = None
    message_type: str = "text"
    content: Optional[str] = None
    metadata: Optional[Any] = None
    trigger_type: Optional[str] = None   # if set, triggers matching scenarios


@router.post("/{phone_id}")
async def handle_incoming(phone_id: str, msg: IncomingMsg):
    db = get_supabase()
    contact_id = msg.contact_id or _resolve_contact(db, phone_id, msg.contact_phone)

    # Store message
    db.table("messages").insert({
        "phone_id": phone_id, "contact_id": contact_id or "",
        "direction": False, "content": msg.content,
        "message_type": msg.message_type,
        "whatsapp_message_id": msg.message_id,
        "status": "received", "metadata": msg.metadata,
    }).execute()

    # ── Check active call → forward to Worker ──────────────────────────────
    active_call = db.table("calls").select("id") \
        .eq("phone_id", phone_id).eq("contact_id", contact_id) \
        .eq("status", "running").order("started_at", desc=True) \
        .limit(1).maybe_single().execute().data

    if active_call:
        worker = db.table("phone_workers").select("service_name") \
            .eq("phone_id", phone_id).eq("status", "running") \
            .maybe_single().execute().data

        if worker:
            delivered = await _forward_to_worker(worker["service_name"], contact_id, msg)
            log.info("Incoming routed | phone=%s contact=%s call=%s", phone_id, contact_id, active_call["id"])
            return {"ok": True, "routed": True, "call_id": active_call["id"], "delivered": delivered}

    # ── No active call — check for trigger scenarios ───────────────────────
    if msg.trigger_type:
        calls = _create_triggered_calls(db, phone_id, contact_id, msg)
        if calls:
            return {"ok": True, "routed": True, "triggered": len(calls), "calls": calls}

    # ── No trigger match — check all scenarios with matching trigger ────────
    # Try matching by message content (auto-trigger)
    calls = _auto_trigger(db, phone_id, contact_id, msg)
    if calls:
        return {"ok": True, "routed": True, "triggered": len(calls), "calls": calls}

    log.info("Incoming stored | phone=%s contact=%s (no active call, no trigger)", phone_id, contact_id)
    return {"ok": True, "routed": False, "call_id": None}


def _create_triggered_calls(db, phone_id, contact_id, msg):
    """Find scenarios matching trigger_type, create calls sorted by priority."""
    scenarios = db.table("scenarios") \
        .select("id, config, priority") \
        .eq("phone_id", phone_id) \
        .eq("trigger_type", msg.trigger_type) \
        .order("priority") \
        .execute().data or []

    if not scenarios:
        return None

    return _queue_scenarios(db, phone_id, contact_id, scenarios, msg)


def _auto_trigger(db, phone_id, contact_id, msg):
    """Find scenarios with auto_trigger matching message content."""
    scenarios = db.table("scenarios") \
        .select("id, config, priority, auto_trigger") \
        .eq("phone_id", phone_id) \
        .eq("auto_trigger_enabled", True) \
        .order("priority") \
        .execute().data or []

    if not scenarios:
        return None

    # Filter by content match
    matched = []
    for sc in scenarios:
        trigger = sc.get("auto_trigger", "")
        if trigger and msg.content and trigger.lower() in msg.content.lower():
            matched.append(sc)

    if not matched:
        return None

    return _queue_scenarios(db, phone_id, contact_id, matched, msg)


def _queue_scenarios(db, phone_id, contact_id, scenarios, msg):
    """Create calls for each scenario — first runs, rest queued."""
    calls = []
    for i, sc in enumerate(scenarios):
        call_id = f"call-{uuid.uuid4().hex[:12]}"
        status = "running" if i == 0 else "queued"
        config = sc.get("config", {})
        snapshot = config if isinstance(config, dict) else {}

        db.table("calls").insert({
            "id": call_id,
            "scenario_id": sc["id"],
            "scenario_snapshot": snapshot,
            "phone_id": phone_id,
            "contact_id": contact_id,
            "status": status,
            "priority": sc.get("priority", i),
            "started_at": _now() if status == "running" else None,
        }).execute()

        calls.append({"call_id": call_id, "scenario_id": sc["id"], "status": status, "priority": sc.get("priority", i)})

    # Dispatch first to Worker
    if calls:
        first_sc = scenarios[0]
        worker = db.table("phone_workers").select("service_name") \
            .eq("phone_id", phone_id).eq("status", "running") \
            .maybe_single().execute().data

        if worker:
            config = first_sc.get("config", {})
            scenario_json = json.dumps(config) if isinstance(config, dict) else str(config)

            import asyncio
            asyncio.ensure_future(_dispatch_async(
                f"http://{worker['service_name']}:9000",
                {
                    "typeEvent": "init",
                    "call_id": calls[0]["call_id"],
                    "contact_id": contact_id,
                    "contact_phone": msg.contact_phone,
                    "contact_name": msg.contact_name or "",
                    "scenario_id": first_sc["id"],
                    "scenario_json": scenario_json,
                    "first_message": {"type": msg.message_type, "data": {"text": msg.content}} if msg.content else None,
                },
            ))

    log.info("Triggered %d calls | phone=%s contact=%s first=%s",
             len(calls), phone_id, contact_id, calls[0]["call_id"] if calls else "none")

    return calls


async def _forward_to_worker(svc, contact_id, msg):
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            resp = await c.post(f"http://{svc}:9000/webhook/event", json={
                "typeEvent": "entryMessage", "contact_id": contact_id,
                "message_id": msg.message_id or "",
                "payload": {"type": msg.message_type, "data": {"text": msg.content} if msg.message_type == "text" else msg.metadata or {}},
            })
            return resp.json().get("delivered", False)
    except:
        return False


async def _dispatch_async(worker_url, payload):
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"{worker_url}/webhook/event", json=payload)
    except Exception as e:
        log.error("Dispatch failed: %s", e)


def _resolve_contact(db, phone_id, phone):
    clean = phone.replace("@s.whatsapp.net", "").replace("@c.us", "")
    res = db.table("contacts").select("id").eq("phone_id", phone_id).or_(f"phone.eq.{clean},lid.eq.{phone}").limit(1).execute()
    return res.data[0]["id"] if res.data else clean