"""
Dispatch — מסלול היצירה היחיד של calls.

הגרסה הקודמת ייצרה call_id ושלחה אותו ל-worker בלי להכניס שורה ל-`calls`.
ה-call פשוט לא היה קיים ב-DB, וה-invariant ("call פעיל אחד לכל
phone+contact") נאכף בזיכרון של ה-worker — ונעלם בכל restart שלו.

כאן הכל עובר דרך spine_ensure_call: partial unique index על
status='running' אוכף את ה-invariant ב-DB, אטומית, עמיד ל-race.

נתיבים (נרשם עם prefix="/api"):
    POST /api/calls/ensure           ← קנוני. incoming + scheduler + API
    POST /api/calls/{id}/complete    ← Worker בסיום. מקדם את הבא בתור.
    POST /api/dispatch               ← legacy. מנתב ל-ensure.
    POST /api/dispatch/message       ← העברת הודעה ל-call רץ
"""
import json, logging
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from dependencies import get_supabase

router = APIRouter(tags=["dispatch"])
log = logging.getLogger("spine.dispatch")


# ── Models ────────────────────────────────────────────────────────────
class EnsureReq(BaseModel):
    phone_id:      str
    contact_id:    str
    scenario_id:   str                       # חובה. אין בחירה אוטומטית כאן.
    priority:      Optional[int] = None
    source:        str = "trigger"           # trigger | scheduler | api
    first_message: Optional[dict] = None


class DispatchReq(BaseModel):
    """legacy — נשמר לתאימות."""
    phone_id:      str
    contact_id:    str
    scenario_id:   str
    scenario_json: Optional[str] = None      # מתעלמים: ה-config נשלף מה-DB
    contact_phone: Optional[str] = None
    contact_name:  Optional[str] = None
    first_message: Optional[dict] = None


class ForwardReq(BaseModel):
    phone_id:   str
    contact_id: str
    message_id: Optional[str] = None
    payload:    Optional[dict] = None


class CompleteReq(BaseModel):
    status: str = "completed"                # completed | failed | aborted


# ══════════════════════════════════════════════════════════════════════
# ensure_core — הלוגיקה עצמה. נקראת גם מ-incoming.py בתהליך (בלי HTTP).
# מחזירה (http_status, body).
# ══════════════════════════════════════════════════════════════════════
async def ensure_core(req: "EnsureReq") -> tuple[int, dict]:
    db = get_supabase()

    contact = db.table("contacts").select("id, phone, name, tag") \
                .eq("id", req.contact_id).maybe_single().execute().data
    if not contact:
        return 404, {"code": "CONTACT_NOT_FOUND"}

    # draft contact לא מריץ תרחישים.
    if contact.get("tag") != "active":
        return 409, {"code": "CONTACT_NOT_ACTIVE", "message": f"tag={contact.get('tag')}"}

    # ── התרחיש. scenario_id הוא חובה — הבחירה נעשית אצל הקורא.
    # (ה-else שהיה כאן בחר תרחיש שרירותי לפי priority והריץ אותו
    #  על איש קשר אמיתי. כל קריאה בלי scenario_id הייתה הפעלה עיוורת.)
    sc = db.table("scenarios").select("id, config, priority") \
           .eq("id", req.scenario_id).maybe_single().execute().data

    if not sc:
        return 404, {"code": "NO_SCENARIO"}

    snapshot = sc.get("config") or {}
    priority = req.priority if req.priority is not None else sc.get("priority", 100)

    # ── כאן נסגרת ה-race. ה-DB מכריע, לא הקוד. ───────────────────────
    res = db.rpc("spine_ensure_call", {
        "p_phone_id":    req.phone_id,
        "p_contact_id":  req.contact_id,
        "p_scenario_id": sc["id"],
        "p_snapshot":    snapshot,
        "p_priority":    priority,
        "p_source":      req.source,
    }).execute().data

    log.info("[ENSURE] %s | phone=%s contact=%s call=%s",
             res["code"], req.phone_id, req.contact_id, res.get("call_id"))

    # ── blocked → scheduler/api בזמן שיחה פעילה. לא נכנס לתור. ────────
    if res["status"] == "blocked":
        log.info("[ENSURE] %s | phone=%s contact=%s source=%s active=%s",
                 res["code"], req.phone_id, req.contact_id, req.source,
                 res.get("active_call_id"))
        return 409, res

    # queued → רק trigger. יורם ב-spine_complete_call.
    if res["status"] == "queued":
        return 202, res

    res["delivered"] = await _init_worker(db, req.phone_id, res["call_id"],
                                          contact, sc["id"], snapshot,
                                          req.first_message)
    return 201, res


# ── POST /api/calls/ensure ────────────────────────────────────────────
@router.post("/calls/ensure")
async def ensure_call(req: EnsureReq, response: Response):
    code, body = await ensure_core(req)
    if code >= 400 and code != 409:
        raise HTTPException(code, body.get("code", "ERROR"))
    response.status_code = code
    return body


# ── POST /api/calls/{call_id}/complete ────────────────────────────────
@router.post("/calls/{call_id}/complete")
async def complete_call(call_id: str, req: CompleteReq):
    db = get_supabase()

    res = db.rpc("spine_complete_call", {
        "p_call_id": call_id, "p_status": req.status,
    }).execute().data

    log.info("[COMPLETE] %s | call=%s next=%s",
             res["code"], call_id, res.get("next_call_id"))

    nxt = res.get("next_call_id")
    if not nxt:
        return res

    row = db.table("calls") \
        .select("id, phone_id, contact_id, scenario_id, scenario_snapshot") \
        .eq("id", nxt).maybe_single().execute().data
    if not row:
        return res

    contact = db.table("contacts").select("id, phone, name") \
                .eq("id", row["contact_id"]).maybe_single().execute().data or {}

    res["delivered"] = await _init_worker(
        db, row["phone_id"], row["id"], contact,
        row["scenario_id"], row.get("scenario_snapshot") or {}, None)
    return res


# ── POST /api/dispatch  (legacy) ──────────────────────────────────────
@router.post("/dispatch")
async def dispatch(req: DispatchReq, response: Response):
    return await ensure_call(EnsureReq(
        phone_id=req.phone_id, contact_id=req.contact_id,
        scenario_id=req.scenario_id, source="api",
        first_message=req.first_message,
    ), response)


# ── POST /api/dispatch/message ────────────────────────────────────────
@router.post("/dispatch/message")
async def forward_message(req: ForwardReq):
    db  = get_supabase()
    url = _worker_url(db, req.phone_id)
    if not url:
        return {"ok": False, "delivered": False, "reason": "no_running_worker"}

    try:
        async with httpx.AsyncClient(timeout=5) as c:
            resp = await c.post(f"{url}/webhook/event", json={
                "typeEvent":  "entryMessage",
                "contact_id": req.contact_id,
                "message_id": req.message_id or "",
                "payload":    req.payload or {"type": "text", "data": {"text": ""}},
            })
        return {"ok": True, "delivered": resp.json().get("delivered", False)}
    except Exception as e:
        log.error("[FORWARD] %s: %s", url, e)
        return {"ok": False, "delivered": False}


# ── Worker plane ──────────────────────────────────────────────────────
async def _init_worker(db, phone_id, call_id, contact, scenario_id,
                       snapshot, first_message) -> bool:
    url = _worker_url(db, phone_id)
    if not url:
        log.warning("[DISPATCH] no running worker | phone=%s call=%s", phone_id, call_id)
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{url}/webhook/event", json={
                "typeEvent":     "init",
                "call_id":       call_id,
                "contact_id":    contact.get("id"),
                "contact_phone": contact.get("phone") or "",
                "contact_name":  contact.get("name") or "",
                "scenario_id":   scenario_id,
                "scenario_json": json.dumps(snapshot),   # str() ייצר JSON לא חוקי
                "first_message": first_message,
            })
            return r.status_code == 200
    except Exception as e:
        log.error("[DISPATCH] init failed | %s: %s", url, e)
        return False


def _worker_url(db, phone_id: str) -> Optional[str]:
    # limit(1) ולא maybe_single() — worker ישן שלא נוקה יגרום ל-maybe_single לזרוק.
    r = db.table("phone_workers").select("service_name") \
          .eq("phone_id", phone_id).eq("status", "running") \
          .limit(1).execute().data
    return f"http://{r[0]['service_name']}:9000" if r else None
