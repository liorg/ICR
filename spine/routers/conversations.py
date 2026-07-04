"""React API — active contacts, messages, calls from EXISTING tables"""
from fastapi import APIRouter, Query
from dependencies import get_supabase

router = APIRouter(tags=["conversations"])

@router.get("/phones/{phone_id}/active")
def active_contacts(phone_id: str):
    db = get_supabase()
    contacts = db.table("contacts").select("id, name, phone, lid, tag, whatsapp_name") \
        .eq("phone_id", phone_id).eq("tag", "active").order("name").execute().data or []
    if not contacts: return {"contacts": []}

    cids = [c["id"] for c in contacts]
    all_calls = db.table("calls").select("contact_id, id, scenario_id, status, started_at, duration_seconds") \
        .eq("phone_id", phone_id).in_("contact_id", cids).order("started_at", desc=True).execute().data or []
    all_msgs = db.table("messages").select("contact_id, content, message_type, direction, created_at") \
        .eq("phone_id", phone_id).in_("contact_id", cids).order("created_at", desc=True).execute().data or []

    lc, cc, lm, mc = {}, {}, {}, {}
    for c in all_calls:
        cid = c["contact_id"]; cc[cid] = cc.get(cid, 0) + 1
        if cid not in lc: lc[cid] = c
    for m in all_msgs:
        cid = m["contact_id"]; mc[cid] = mc.get(cid, 0) + 1
        if cid not in lm: lm[cid] = m

    return {"contacts": [{**c, "call_count": cc.get(c["id"], 0), "message_count": mc.get(c["id"], 0),
        "last_call": lc.get(c["id"]), "last_message": lm.get(c["id"])} for c in contacts]}

@router.get("/phones/{phone_id}/contacts/{contact_id}/messages")
def contact_messages(phone_id: str, contact_id: str, limit: int = Query(200), offset: int = Query(0)):
    db = get_supabase()
    return {"messages": db.table("messages") \
        .select("id, contact_id, direction, content, message_type, whatsapp_message_id, status, metadata, call_id, created_at") \
        .eq("phone_id", phone_id).eq("contact_id", contact_id).order("created_at") \
        .range(offset, offset + limit - 1).execute().data or []}

@router.get("/phones/{phone_id}/contacts/{contact_id}/calls")
def contact_calls(phone_id: str, contact_id: str, limit: int = Query(30)):
    db = get_supabase()
    return {"calls": db.table("calls") \
        .select("id, scenario_id, status, started_at, finished_at, duration_seconds, scenario_snapshot") \
        .eq("phone_id", phone_id).eq("contact_id", contact_id).order("started_at", desc=True).limit(limit).execute().data or []}

@router.get("/calls/{call_id}/messages")
def call_messages(call_id: str):
    db = get_supabase()
    return {"messages": db.table("messages").select("*").eq("call_id", call_id).order("created_at").execute().data or []}

@router.get("/calls/{call_id}/leaves")
def call_leaves(call_id: str):
    db = get_supabase()
    leaves = db.table("spine_leaves").select("*").eq("call_id", call_id).order("timestamp").execute().data or []
    if not leaves: return {"leaves": []}
    lids = [l["leaf_id"] for l in leaves]
    links = db.table("spine_leaf_messages").select("leaf_id, message_id").in_("leaf_id", lids).execute().data or []
    lmap = {}
    for l in links: lmap.setdefault(l["leaf_id"], []).append(l["message_id"])
    for leaf in leaves: leaf["message_ids"] = lmap.get(leaf["leaf_id"], [])
    return {"leaves": leaves}

@router.get("/workers")
def list_workers():
    db = get_supabase()
    return {"workers": db.table("phone_workers").select("*").order("updated_at", desc=True).execute().data or []}
