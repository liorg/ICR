"""Notifications — normalized, payload as JSONB"""
from fastapi import APIRouter, Query
from dependencies import get_supabase

router = APIRouter(prefix="/notifications", tags=["notifications"])

@router.get("/{phone_id}")
def list_notifs(phone_id: str, unread_only: bool = False, limit: int = Query(50)):
    db = get_supabase()
    q = db.table("spine_notifications").select("*, contacts(name, phone)").eq("phone_id", phone_id)
    if unread_only: q = q.eq("read", False)
    return {"notifications": q.order("created_at", desc=True).limit(limit).execute().data or []}

@router.patch("/{nid}/read")
def mark_read(nid: int):
    db = get_supabase()
    db.table("spine_notifications").update({"read": True}).eq("id", nid).execute()
    return {"ok": True}

@router.patch("/read-all/{phone_id}")
def mark_all_read(phone_id: str):
    db = get_supabase()
    db.table("spine_notifications").update({"read": True}).eq("phone_id", phone_id).eq("read", False).execute()
    return {"ok": True}
