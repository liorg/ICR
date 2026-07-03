"""React-facing — active contacts, calls, leaves (chat)."""
from fastapi import APIRouter, Query
from dependencies import get_supabase

router = APIRouter(tags=["conversations"])

@router.get("/phones/{phone_id}/contacts/active")
def active_contacts(phone_id: str):
    db = get_supabase()
    contacts = db.table("contacts") \
        .select("id, name, phone, lid, tag") \
        .eq("phone_id", phone_id).eq("tag", "active") \
        .order("name").execute().data or []

    if not contacts:
        return {"contacts": []}

    cids = [c["id"] for c in contacts]
    calls = db.table("spine_calls") \
        .select("contact_id, call_id, scenario_id, status, started_at, duration_seconds, sender_count, expected_count, mismatch_count") \
        .eq("phone_id", phone_id).in_("contact_id", cids) \
        .order("started_at", desc=True).execute().data or []

    latest, counts = {}, {}
    for c in calls:
        cid = c["contact_id"]
        counts[cid] = counts.get(cid, 0) + 1
        if cid not in latest:
            latest[cid] = c

    return {"contacts": [{**c, "call_count": counts.get(c["id"], 0), "last_call": latest.get(c["id"])} for c in contacts]}

@router.get("/phones/{phone_id}/contacts/{contact_id}/calls")
def contact_calls(phone_id: str, contact_id: str, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    db = get_supabase()
    res = db.table("spine_calls").select("*") \
        .eq("phone_id", phone_id).eq("contact_id", contact_id) \
        .order("started_at", desc=True).range(offset, offset + limit - 1).execute()
    return {"calls": res.data or []}

@router.get("/calls/{call_id}")
def get_call(call_id: str):
    db = get_supabase()
    call = db.table("spine_calls").select("*").eq("call_id", call_id).maybe_single().execute().data
    leaves = db.table("spine_leaves").select("*").eq("call_id", call_id).order("timestamp").execute().data or []
    return {"call": call, "leaves": leaves}

@router.get("/calls/{call_id}/leaves")
def call_leaves(call_id: str):
    db = get_supabase()
    res = db.table("spine_leaves").select("*").eq("call_id", call_id).order("timestamp").execute()
    return {"leaves": res.data or []}

@router.get("/workers")
def list_workers():
    db = get_supabase()
    res = db.table("phone_workers").select("*").order("updated_at", desc=True).execute()
    return {"workers": res.data or []}
