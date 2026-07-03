"""
GET  /notifications/{phone_id}      — list notifications
PATCH /notifications/{id}/read      — mark as read
"""
from fastapi import APIRouter, Query
from dependencies import get_supabase

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/{phone_id}")
def list_notifications(phone_id: str, unread_only: bool = False, limit: int = Query(50)):
    db = get_supabase()
    q = db.table("spine_notifications").select("*").eq("phone_id", phone_id)
    if unread_only:
        q = q.eq("read", False)
    res = q.order("created_at", desc=True).limit(limit).execute()
    return {"notifications": res.data or []}


@router.patch("/{notification_id}/read")
def mark_read(notification_id: int):
    db = get_supabase()
    db.table("spine_notifications").update({"read": True}).eq("id", notification_id).execute()
    return {"ok": True}
