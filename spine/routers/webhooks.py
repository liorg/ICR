"""
Webhook registration — Spine registers itself with agents.

POST   /webhooks              — register webhook
GET    /webhooks/{phone_id}   — list webhooks for phone
DELETE /webhooks/{id}         — remove webhook
POST   /webhooks/register-agent/{phone_id} — auto-register Spine with agent
"""
import os, logging
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx
from dependencies import get_supabase

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = logging.getLogger("spine.webhooks")

SPINE_SELF_URL = os.getenv("SPINE_SELF_URL", "http://scenario_data-spine:8000")


class WebhookReq(BaseModel):
    phone_id: str
    event_type: str = "all"       # message | status_update | connection | all
    agent_url: str                # e.g. http://host:9369


@router.post("")
def register_webhook(req: WebhookReq):
    db = get_supabase()
    callback = f"{SPINE_SELF_URL}/incoming/{req.phone_id}"
    db.table("spine_webhooks").upsert({
        "phone_id": req.phone_id, "event_type": req.event_type,
        "callback_url": callback, "agent_url": req.agent_url,
        "status": "active",
    }, on_conflict="phone_id,event_type").execute()
    return {"ok": True, "callback_url": callback}


@router.get("/{phone_id}")
def list_webhooks(phone_id: str):
    db = get_supabase()
    res = db.table("spine_webhooks").select("*").eq("phone_id", phone_id).execute()
    return {"webhooks": res.data or []}


@router.delete("/{webhook_id}")
def remove_webhook(webhook_id: int):
    db = get_supabase()
    db.table("spine_webhooks").delete().eq("id", webhook_id).execute()
    return {"ok": True}


@router.post("/register-agent/{phone_id}")
async def register_with_agent(phone_id: str, agent_url: str):
    """
    Register Spine as webhook listener with the Baileys agent.
    Agent will POST to /incoming/{phone_id} on every message.
    """
    callback = f"{SPINE_SELF_URL}/incoming/{phone_id}"

    # Register with agent's webhook endpoint
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{agent_url}/webhook/register", json={
                "url": callback,
                "events": ["message", "status_update"],
            })
            result = resp.json()
    except Exception as e:
        log.error("Failed to register with agent %s: %s", agent_url, e)
        raise HTTPException(502, f"Agent unreachable: {e}")

    # Store in DB
    db = get_supabase()
    db.table("spine_webhooks").upsert({
        "phone_id": phone_id, "event_type": "all",
        "callback_url": callback, "agent_url": agent_url,
        "status": "active",
    }, on_conflict="phone_id,event_type").execute()

    log.info("Registered with agent | phone=%s agent=%s callback=%s", phone_id, agent_url, callback)
    return {"ok": True, "callback_url": callback, "agent_result": result}
