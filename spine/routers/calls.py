"""
POST /api/calls/ensure — נקודת הכניסה היחידה ליצירת call.

משמשת את שלושת מקורות הטריגר:
    incoming message  ·  scheduler  ·  API endpoint ישיר

ה-invariant (call פעיל אחד לכל phone+contact) נאכף ב-DB דרך
partial unique index — לא בקוד. שני טריגרים מקבילים לא ייצרו כפילות.

קודים:
    201 CALL_CREATED  — נוצר ורץ, נשלח ל-worker
    202 CALL_QUEUED   — יש call פעיל, נכנס לתור לפי priority
    404 NO_SCENARIO / CONTACT_NOT_ACTIVE
"""
import os, json, logging
from typing import Optional

import httpx
from fastapi import APIRouter, Response, HTTPException
from pydantic import BaseModel

from dependencies import get_supabase

router = APIRouter(prefix="/calls", tags=["calls"])
log = logging.getLogger("spine.calls.ensure")

REQUIRE_ACTIVE_CONTACT = os.getenv("REQUIRE_ACTIVE_CONTACT", "true").lower() == "true"


class EnsureReq(BaseModel):
    phone_id:    str
    contact_id:  str
    scenario_id: Optional[str] = None      # None → בחירה לפי priority
    priority:    Optional[int] = None
    source:      str = "trigger"           # trigger | scheduler | api
    first_message: Optional[dict] = None


@router.post("/ensure")
async def ensure_call(req: EnsureReq, response: Response):
    db = get_supabase()

    # ── contact חייב להיות active ──────────────────────────────────────
    if REQUIRE_ACTIVE_CONTACT:
        c = db.table("contacts").select("id, phone, name, tag") \
              .eq("id", req.contact_id).maybe_single().execute().data
        if not c:
            raise HTTPException(404, "contact not found")
        if c.get("tag") != "active":
            response.status_code = 409
            return {"code": "CONTACT_NOT_ACTIVE", "message": f"contact tag={c.get('tag')}"}
    else:
        c = db.table("contacts").select("id, phone, name") \
              .eq("id", req.contact_id).maybe_single().execute().data or {}

    # ── בחירת התרחיש ───────────────────────────────────────────────────
    if req.scenario_id:
        sc = db.table("scenarios").select("id, config, priority") \
               .eq("id", req.scenario_id).maybe_single().execute().data
    else:
        rows = db.table("scenarios").select("id, config, priority") \
                 .eq("phone_id", req.phone_id).eq("is_published", True) \
                 .order("priority").limit(1).execute().data
        sc = rows[0] if rows else None

    if not sc:
        raise HTTPException(404, "NO_SCENARIO")

    snapshot = sc.get("config") or {}
    priority = req.priority if req.priority is not None else sc.get("priority", 100)

    # ── ה-RPC האטומי. כאן נסגרת ה-race. ────────────────────────────────
    res = db.rpc("spine_ensure_call", {
        "p_phone_id":    req.phone_id,
        "p_contact_id":  req.contact_id,
        "p_scenario_id": sc["id"],
        "p_snapshot":    snapshot,
        "p_priority":    priority,
        "p_source":      req.source,
    }).execute().data

    log.info("[ENSURE] %s | phone=%s contact=%s call=%s",
             res["code"], req.phone_id, req.contact_id, res["call_id"])

    # ── queued → לא שולחים ל-worker. הוא יורם ב-spine_complete_call. ───
    if res["status"] == "queued":
        response.status_code = 202
        return res

    # ── running → dispatch ל-worker ────────────────────────────────────
    worker = _worker(db, req.phone_id)
    if not worker:
        log.warning("[ENSURE] no running worker | phone=%s — call stays running, undelivered",
                    req.phone_id)
        res["delivered"] = False
    else:
        res["delivered"] = await _post(worker, {
            "typeEvent":     "init",
            "call_id":       res["call_id"],
            "contact_id":    req.contact_id,
            "contact_phone": c.get("phone", ""),
            "contact_name":  c.get("name", ""),
            "scenario_id":   sc["id"],
            "scenario_json": json.dumps(snapshot),
            "first_message": req.first_message,
        })

    response.status_code = 201
    return res


# ── סגירת call + קידום הבא בתור. ה-Worker קורא לזה בסיום. ─────────────
class CompleteReq(BaseModel):
    status: str = "completed"      # completed | failed | aborted


@router.post("/{call_id}/complete")
async def complete_call(call_id: str, req: CompleteReq):
    db = get_supabase()

    res = db.rpc("spine_complete_call", {
        "p_call_id": call_id,
        "p_status":  req.status,
    }).execute().data

    log.info("[COMPLETE] %s | call=%s next=%s",
             res["code"], call_id, res.get("next_call_id"))

    # הבא בתור קודם ל-running → צריך לדחוף אותו ל-worker
    nxt = res.get("next_call_id")
    if nxt:
        row = db.table("calls").select(
            "id, phone_id, contact_id, scenario_id, scenario_snapshot"
        ).eq("id", nxt).maybe_single().execute().data

        if row:
            ct = db.table("contacts").select("phone, name") \
                   .eq("id", row["contact_id"]).maybe_single().execute().data or {}
            worker = _worker(db, row["phone_id"])
            if worker:
                res["delivered"] = await _post(worker, {
                    "typeEvent":     "init",
                    "call_id":       row["id"],
                    "contact_id":    row["contact_id"],
                    "contact_phone": ct.get("phone", ""),
                    "contact_name":  ct.get("name", ""),
                    "scenario_id":   row["scenario_id"],
                    "scenario_json": json.dumps(row.get("scenario_snapshot") or {}),
                    "first_message": None,
                })
    return res


# ── Worker plane ──────────────────────────────────────────────────────
def _worker(db, phone_id: str) -> Optional[str]:
    r = db.table("phone_workers").select("service_name") \
          .eq("phone_id", phone_id).eq("status", "running") \
          .limit(1).execute().data
    return f"http://{r[0]['service_name']}:9000" if r else None


async def _post(worker_url: str, payload: dict) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{worker_url}/webhook/event", json=payload)
            return r.status_code == 200
    except Exception as e:
        log.error("[DISPATCH] failed | %s: %s", worker_url, e)
        return False
