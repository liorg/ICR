"""
React-facing API.

GET /api/phones/{phone_id}/active                         — active contacts + last call
GET /api/phones/{phone_id}/contacts/{contact_id}/calls    — all calls
GET /api/calls/{call_id}                                  — call + leaves
GET /api/calls/{call_id}/leaves                           — leaves (live poll)
GET /api/calls/{call_id}/messages                         — messages linked to leaves
GET /api/workers                                          — workers list
"""
from fastapi import APIRouter, Query
from dependencies import get_supabase

router = APIRouter(tags=["conversations"])


@router.get("/phones/{phone_id}/active")
def active_contacts(phone_id: str):
    db = get_supabase()
    contacts = db.table("contacts").select("id, name, phone, lid, tag") \
        .eq("phone_id", phone_id).eq("tag", "active").order("name").execute().data or []
    if not contacts:
        return {"contacts": []}

    cids = [c["id"] for c in contacts]
    calls = db.table("spine_calls") \
        .select("contact_id, call_id, scenario_id, status, started_at, duration_seconds, sender_count, expected_count, mismatch_count") \
        .eq("phone_id", phone_id).in_("contact_id", cids).order("started_at", desc=True).execute().data or []

    latest, counts = {}, {}
    for c in calls:
        cid = c["contact_id"]
        counts[cid] = counts.get(cid, 0) + 1
        if cid not in latest:
            latest[cid] = c

    return {"contacts": [{**c, "call_count": counts.get(c["id"], 0), "last_call": latest.get(c["id"])} for c in contacts]}


@router.get("/phones/{phone_id}/contacts/{contact_id}/calls")
def contact_calls(phone_id: str, contact_id: str, limit: int = Query(20), offset: int = Query(0)):
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
    return {"leaves": db.table("spine_leaves").select("*").eq("call_id", call_id).order("timestamp").execute().data or []}


@router.get("/calls/{call_id}/messages")
def call_messages(call_id: str):
    """Messages linked to this call's leaves via spine_leaf_messages."""
    db = get_supabase()
    leaves = db.table("spine_leaves").select("leaf_id").eq("call_id", call_id).execute().data or []
    if not leaves:
        return {"messages": []}
    leaf_ids = [l["leaf_id"] for l in leaves]
    links = db.table("spine_leaf_messages").select("leaf_id, message_id").in_("leaf_id", leaf_ids).execute().data or []
    if not links:
        return {"messages": []}
    msg_ids = list(set(l["message_id"] for l in links))
    msgs = db.table("spine_messages").select("*").in_("id", msg_ids).order("created_at").execute().data or []

    link_map = {}
    for l in links:
        link_map.setdefault(l["message_id"], []).append(l["leaf_id"])
    for m in msgs:
        m["linked_leaves"] = link_map.get(m["id"], [])

    return {"messages": msgs}


@router.get("/workers")
def list_workers():
    db = get_supabase()
    return {"workers": db.table("phone_workers").select("*").order("updated_at", desc=True).execute().data or []}
