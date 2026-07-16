"""
spine/routers/calls.py — GET /calls/{id}

⚠️ רק אם הקובץ חסר אצלך בשרת: main.py מייבא `routers.calls`
אבל ב-ZIP ששלחת הוא לא קיים → ImportError בעליית ה-Spine.
אם יש לך גרסה קיימת בשרת — השאר אותה, אל תדרוס בזה.

מינימלי: פרטי call + ההודעות וה-leaves שלו.
"""
import logging

from fastapi import APIRouter, HTTPException

from dependencies import get_supabase

router = APIRouter(prefix="/calls", tags=["calls"])
log = logging.getLogger("spine.calls")


@router.get("/{call_id}")
def get_call(call_id: str):
    db = get_supabase()

    call = db.table("calls").select("*") \
        .eq("id", call_id).maybe_single().execute().data
    if not call:
        raise HTTPException(404, "CALL_NOT_FOUND")

    messages = db.table("messages") \
        .select("id, content, message_type, direction, status, created_at") \
        .eq("call_id", call_id).order("created_at").execute().data or []

    leaves = db.table("spine_leaves") \
        .select("leaf_id, step_id, type, wa_type, status, content, created_at") \
        .eq("call_id", call_id).order("created_at").execute().data or []

    return {"call": call, "messages": messages, "leaves": leaves}
