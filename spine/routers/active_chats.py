"""
spine/routers/active_chats.py — שיחות של אנשי קשר ACTIVE.

    GET /active-chats/{phone_id}/contacts
        → { contacts: [ { id, name, whatsapp_name, phone, lid,
                          call_count, last_message } ] }
        ממוין לפי ההודעה האחרונה (חדש → ישן).
        RPC אחד (spine_active_chats) — לא N+1 מהפייתון.

    GET /active-chats/{phone_id}/contacts/{contact_id}/messages?limit=200
        → { messages: [...] }  כרונולוגי (ישן → חדש), N האחרונות.

קונבנציה (ERD): messages.direction = TRUE → נכנסת (מאיש הקשר).
ה-API מחזיר את הערך כמו שהוא ב-DB — ההיפוך ב-UI תוקן ב-ActiveChatsScreen.
"""
import logging

from fastapi import APIRouter, HTTPException, Query

from dependencies import get_supabase

router = APIRouter(prefix="/active-chats", tags=["active-chats"])
log = logging.getLogger("spine.active_chats")

MSG_FIELDS = "id, content, message_type, direction, status, metadata, created_at"


@router.get("/{phone_id}/contacts")
def list_active_contacts(phone_id: str):
    """
    contacts.tag = 'active' בלבד — לא scenarios.status.
    אותה מילה, טבלאות שונות (ראה ERD).
    """
    db = get_supabase()
    try:
        contacts = db.rpc("spine_active_chats", {"p_phone_id": phone_id}) \
                     .execute().data or []
    except Exception as e:
        log.error("[ACTIVE-CHATS] rpc failed | phone=%s: %s", phone_id, e)
        raise HTTPException(500, "spine_active_chats rpc failed")

    return {"contacts": contacts, "count": len(contacts)}


@router.get("/{phone_id}/contacts/{contact_id}/messages")
def contact_messages(
    phone_id: str,
    contact_id: str,
    limit: int = Query(200, ge=1, le=500),
):
    """
    N ההודעות האחרונות, מוחזרות כרונולוגית (ישן → חדש) —
    כמו שה-UI מציג. desc+limit ואז reverse, לא offset.
    """
    db = get_supabase()

    rows = db.table("messages").select(MSG_FIELDS) \
        .eq("phone_id", phone_id).eq("contact_id", contact_id) \
        .order("created_at", desc=True).limit(limit) \
        .execute().data or []

    rows.reverse()
    return {"messages": rows, "count": len(rows)}
