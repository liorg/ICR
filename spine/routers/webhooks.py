"""Webhook registration with agents"""
import os, httpx
from fastapi import APIRouter
from pydantic import BaseModel
from dependencies import get_supabase

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
SPINE_SELF_URL = os.getenv("SPINE_SELF_URL", "http://scenario_data-spine:8000")

class WebhookReq(BaseModel):
    phone_id: str; event_type: str = "all"; agent_url: str

@router.post("")
def register(req: WebhookReq):
    db = get_supabase()
    callback = f"{SPINE_SELF_URL}/incoming/{req.phone_id}"
    db.table("spine_webhooks").upsert({
        "phone_id": req.phone_id, "event_type": req.event_type,
        "callback_url": callback, "agent_url": req.agent_url, "status": "active",
    }, on_conflict="phone_id,event_type").execute()
    return {"ok": True, "callback_url": callback}

@router.get("/{phone_id}")
def list_hooks(phone_id: str):
    db = get_supabase()
    return {"webhooks": db.table("spine_webhooks").select("*").eq("phone_id", phone_id).execute().data or []}

@router.delete("/{webhook_id}")
def remove(webhook_id: int):
    db = get_supabase()
    db.table("spine_webhooks").delete().eq("id", webhook_id).execute()
    return {"ok": True}

@router.post("/register-agent/{phone_id}")
async def register_with_agent(phone_id: str, agent_url: str):
    callback = f"{SPINE_SELF_URL}/incoming/{phone_id}"
    async with httpx.AsyncClient(timeout=10) as c:
        await c.post(f"{agent_url}/webhook/register", json={"url": callback, "events": ["message", "status_update"]})
    db = get_supabase()
    db.table("spine_webhooks").upsert({
        "phone_id": phone_id, "event_type": "all",
        "callback_url": callback, "agent_url": agent_url, "status": "active",
    }, on_conflict="phone_id,event_type").execute()
    return {"ok": True, "callback_url": callback}
