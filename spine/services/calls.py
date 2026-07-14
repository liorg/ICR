"""
spine/services/calls.py — שכבת הלוגיקה של calls.

עיקרון: הפונקציות כאן **לא מדברות עם ה-Worker**. הן מחזירות תוצאה
שכוללת `worker_payload` — ומי שקרא מחליט אם לשלוח.

למה: init ל-Worker נדרש משלושה מקומות (dispatch, incoming, summary),
וכל אחד שכפל את אותו _init_worker. עכשיו יש מקור אחד.

זרימה אצל הקורא:
    res = await ensure_call(db, ...)
    if res.worker_payload:
        res.delivered = await send_to_worker(db, res.phone_id, res.worker_payload)
    return res.http_status, res.body
"""
import json, logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

log = logging.getLogger("spine.services.calls")

WORKER_PORT = 9000


# ══════════════════════════════════════════════════════════════════════
@dataclass
class CallResult:
    http_status: int
    code:        str
    body:        dict            = field(default_factory=dict)
    call_id:     Optional[str]   = None
    phone_id:    Optional[str]   = None
    worker_payload: Optional[dict] = None   # None → אין מה לשלוח ל-Worker

    @property
    def needs_worker(self) -> bool:
        return self.worker_payload is not None

    def with_delivery(self, delivered: bool) -> "CallResult":
        self.body["delivered"] = delivered
        return self


# ══════════════════════════════════════════════════════════════════════
# Worker plane
# ══════════════════════════════════════════════════════════════════════
def worker_url(db, phone_id: str) -> Optional[str]:
    # limit(1) ולא maybe_single() — worker ישן שנשאר ב-running יזרוק שם.
    r = db.table("phone_workers").select("service_name") \
          .eq("phone_id", phone_id).eq("status", "running") \
          .limit(1).execute().data
    return f"http://{r[0]['service_name']}:{WORKER_PORT}" if r else None


async def send_to_worker(db, phone_id: str, payload: dict) -> bool:
    url = worker_url(db, phone_id)
    if not url:
        log.warning("[WORKER] none running | phone=%s event=%s",
                    phone_id, payload.get("typeEvent"))
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{url}/webhook/event", json=payload)
            if r.status_code != 200:
                log.error("[WORKER] rejected | %s status=%s body=%s",
                          url, r.status_code, r.text[:200])
                return False
            return True
    except Exception as e:
        log.error("[WORKER] failed | %s: %s", url, e)
        return False


def init_payload(call_id, contact, scenario_id, snapshot, first_message=None) -> dict:
    """typeEvent=init. WorkerEventEnvelope דורש call_id + contact_id + scenario_json."""
    return {
        "typeEvent":     "init",
        "call_id":       call_id,
        "contact_id":    contact.get("id"),
        "contact_phone": contact.get("lid") or contact.get("number") or "",
        "contact_name":  contact.get("name") or contact.get("whatsapp_name") or "",
        "scenario_id":   scenario_id,
        "scenario_json": json.dumps(snapshot or {}),   # str(dict) = JSON לא חוקי
        "first_message": first_message,
    }


def entry_payload(call_id, scenario_id, contact_id, message_id,
                  msg_type, content, metadata=None) -> dict:
    """typeEvent=entryMessage. call_id חובה — HandleEntryMessage לא מוודא אותו."""
    return {
        "typeEvent":   "entryMessage",
        "call_id":     call_id,
        "scenario_id": scenario_id,
        "contact_id":  contact_id,
        "message_id":  message_id,
        "payload": {
            "type": msg_type,
            "data": {"text": content} if msg_type == "text" else (metadata or {}),
        },
    }


# ══════════════════════════════════════════════════════════════════════
# ensure — נקודת היצירה היחידה
# ══════════════════════════════════════════════════════════════════════
async def ensure_call(db, phone_id: str, contact_id: str, scenario_id: str,
                      priority: Optional[int] = None,
                      source: str = "trigger",
                      first_message: Optional[dict] = None,
                      schedule_id: Optional[str] = None) -> CallResult:

    # contacts.number — לא "phone". lid מועדף כשקיים (ווטסאפ מזהה לפיו).
    contact = db.table("contacts").select("id, number, lid, name, tag") \
                .eq("id", contact_id).maybe_single().execute().data
    if not contact:
        return CallResult(404, "CONTACT_NOT_FOUND", {"code": "CONTACT_NOT_FOUND"})

    # draft contact לא מריץ תרחישים.
    if contact.get("tag") != "active":
        return CallResult(409, "CONTACT_NOT_ACTIVE", {
            "code": "CONTACT_NOT_ACTIVE",
            "message": f"tag={contact.get('tag')}",
        })

    # scenario_id חובה. אין בחירה אוטומטית — הקורא מחליט.
    sc = db.table("scenarios").select("id, config, priority") \
           .eq("id", scenario_id).maybe_single().execute().data
    if not sc:
        return CallResult(404, "NO_SCENARIO", {"code": "NO_SCENARIO"})

    snapshot = sc.get("config") or {}
    prio     = priority if priority is not None else sc.get("priority", 100)

    # ── ה-RPC האטומי: advisory lock + partial unique index. ───────────
    res = db.rpc("spine_ensure_call", {
        "p_phone_id":    phone_id,
        "p_contact_id":  contact_id,
        "p_scenario_id": sc["id"],
        "p_snapshot":    snapshot,
        "p_priority":    prio,
        "p_source":      source,
        "p_schedule_id": schedule_id,   # ← הקישור לטאב Calls של התזמון
    }).execute().data

    code   = res.get("code")
    status = res.get("status")

    log.info("[ENSURE] %s | phone=%s contact=%s source=%s call=%s",
             code, phone_id, contact_id, source, res.get("call_id"))

    # scheduler/api בזמן שיחה פעילה → נחסם. לא נכנס לתור.
    if status == "blocked":
        return CallResult(409, code, res, res.get("call_id"), phone_id)

    # trigger בזמן שיחה פעילה → תור. יורם ב-complete_call, לא נשלח עכשיו.
    if status == "queued":
        return CallResult(202, code, res, res.get("call_id"), phone_id)

    # running → הקורא ישלח init ל-Worker.
    return CallResult(
        201, code, res, res.get("call_id"), phone_id,
        worker_payload=init_payload(res["call_id"], contact, sc["id"],
                                    snapshot, first_message),
    )


# ══════════════════════════════════════════════════════════════════════
# complete — סוגר ומקדם את הבא בתור
# ══════════════════════════════════════════════════════════════════════
async def complete_call(db, call_id: str, status: str = "completed") -> CallResult:

    res = db.rpc("spine_complete_call", {
        "p_call_id": call_id,
        "p_status":  status,
    }).execute().data or {}

    code = res.get("code")
    log.info("[COMPLETE] %s | call=%s next=%s", code, call_id, res.get("next_call_id"))

    if code in ("CALL_NOT_FOUND", "INVALID_STATUS"):
        return CallResult(400, code, res, call_id)

    nxt = res.get("next_call_id")
    if not nxt:
        return CallResult(200, code, res, call_id)

    # ── הבא בתור קודם ל-running → הקורא ישלח לו init ─────────────────
    row = db.table("calls") \
        .select("id, phone_id, contact_id, scenario_id, scenario_snapshot") \
        .eq("id", nxt).maybe_single().execute().data
    if not row:
        log.error("[COMPLETE] promoted call %s not found", nxt)
        return CallResult(200, code, res, call_id)

    contact = db.table("contacts").select("id, number, lid, name") \
                .eq("id", row["contact_id"]).maybe_single().execute().data or {}

    return CallResult(
        200, code, res, call_id, row["phone_id"],
        worker_payload=init_payload(row["id"], contact, row["scenario_id"],
                                    row.get("scenario_snapshot"), None),
    )
