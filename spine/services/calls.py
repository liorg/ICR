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
import json, logging, os
from dataclasses import dataclass, field
from typing import Optional

import httpx

log = logging.getLogger("spine.services.calls")

WORKER_PORT = 9000

# מקור אמת משותף ל-Summary ול-webhook.
CALL_END_EMOJI = os.getenv("CALL_END_EMOJI", "🏁🏁").strip() or "🏁🏁"
CALL_END_TYPE = "call_end"


async def _rpc(fn: str, params: dict) -> dict:
    """
    קריאת RPC ישירות מול PostgREST, בעקיפת postgrest-py.

    למה: postgrest 0.17/2.x נכשל בפרסור תשובות שמכילות UTF-8 (עברית)
    ומחזיר APIError עם code=200 — למרות שה-RPC הצליח וה-HTTP היה 200.
    התרחישים כאן מלאים עברית, אז מפרסרים את הבייטים בעצמנו.
    """
    base = os.environ["SUPABASE_URL"].rstrip("/")
    key  = os.environ["SUPABASE_SERVICE_KEY"]

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{base}/rest/v1/rpc/{fn}",
            json=params,
            headers={
                "apikey":        key,
                "Authorization": f"Bearer {key}",
                "Content-Type":  "application/json",
                "Accept":        "application/json",
            },
        )

    if r.status_code >= 400:
        log.error("[RPC] %s failed | status=%s body=%s", fn, r.status_code, r.text[:300])
        raise RuntimeError(f"{fn} failed with HTTP {r.status_code}")

    # decode מפורש — לא לסמוך על ניחוש הקידוד של הקליינט
    return json.loads(r.content.decode("utf-8")) or {}


# ══════════════════════════════════════════════════════════════════════
@dataclass
class CallResult:
    http_status: int
    code:        str
    body:        dict            = field(default_factory=dict)
    call_id:     Optional[str]   = None
    phone_id:    Optional[str]   = None
    worker_payload: Optional[dict] = None   # None → אין מה לשלוח ל-Worker
    event_id:    Optional[str]   = None     # GUID משותף לכל הבאטץ'

    @property
    def needs_worker(self) -> bool:
        return self.worker_payload is not None

    def with_delivery(self, delivered: bool) -> "CallResult":
        self.body["delivered"] = delivered
        return self




async def send_call_end_marker(
    db,
    call_id: str,
    phone_id: str,
    contact_id: str,
    final_status: str,
) -> dict:
    """
    משגר חותמת סיום דרך endpoint השליחה של Spine.

    קריאה ישירה לפונקציה ולא HTTP ל-SPINE_SELF_URL: זה אותו תהליך,
    אז round trip דרך ה-overlay רק מוסיף latency, תלות במשתנה סביבה
    נוסף, וסיכון ש-Spine עמוס ימתין לעצמו.

    אין SELECT או בדיקות מקדימות — שולחים as-is. אישור אמיתי מתקבל
    רק מה-webhook שמעדכן את last_send_time / last_whatsapp_id /
    last_message_id.

    contact_phone נשלף כאן כי SendReq דורש אותו כ-jid. זו שאילתה
    אחת קצרה, לא בדיקה מקדימה: בלעדיה הבקשה נדחית ב-422.
    """
    # import מקומי: routers.send מייבא מ-services.calls, ואימפורט
    # ברמת המודול היה יוצר מעגל.
    from routers.send import SendReq, send_message

    try:
        contact = (
            db.table("contacts")
            .select("number, lid")
            .eq("id", contact_id)
            .limit(1)
            .execute()
            .data
        )

        if not contact:
            log.warning(
                "[CALL-END] contact not found | call=%s contact=%s",
                call_id,
                contact_id,
            )
            return {
                "ok": False,
                "code": "END_MARKER_CONTACT_NOT_FOUND",
            }

        contact_phone = (
            contact[0].get("lid")
            or contact[0].get("number")
            or ""
        )

        await send_message(
            phone_id,
            SendReq(
                contact_id=contact_id,
                contact_phone=contact_phone,
                message_type="text",
                content=CALL_END_EMOJI,
                metadata={
                    "system_type": CALL_END_TYPE,
                    "final_status": final_status,
                },
                call_id=call_id,
            ),
        )

        log.info(
            "[CALL-END] marker dispatched | call=%s status=%s",
            call_id,
            final_status,
        )

        return {
            "ok": True,
            "code": "END_MARKER_DISPATCHED",
        }

    except Exception as exc:
        log.exception(
            "[CALL-END] dispatch failed | call=%s status=%s error=%s",
            call_id,
            final_status,
            exc,
        )
        return {
            "ok": False,
            "code": "END_MARKER_DISPATCH_FAILED",
        }


def is_call_end_marker(message: dict) -> bool:
    """
    מזהה webhook של חותמת הסיום לפי אותה הגדרה משותפת.

    מאחר שה-HostAgent עשוי לשמור content כ-dict, JSON string או טקסט,
    הבדיקה תומכת בשלושת המצבים.
    """
    metadata = message.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}

    if isinstance(metadata, dict):
        marker_type = str(
            metadata.get("system_type")
            or metadata.get("type")
            or ""
        ).strip().lower()

        if marker_type == CALL_END_TYPE:
            return True

    content = message.get("content")

    if isinstance(content, dict):
        text = (
            content.get("text")
            or content.get("caption")
            or ""
        )
        return str(text).strip() == CALL_END_EMOJI

    if isinstance(content, str):
        raw = content.strip()

        if raw == CALL_END_EMOJI:
            return True

        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False

        if isinstance(parsed, dict):
            text = (
                parsed.get("text")
                or parsed.get("caption")
                or ""
            )
            return str(text).strip() == CALL_END_EMOJI

    return False


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




def entry_payload(
    call_id: str,
    scenario_id: Optional[str],
    contact_id: str,
    message_id: str,
    whatsapp_message_id: Optional[str],
    msg_type: str,
    content: Optional[str],
    metadata: Optional[dict] = None,
) -> dict:
    """
    typeEvent=entryMessage.

    message_id:
        מזהה פנימי של הרשומה בטבלת messages.

    whatsapp_message_id:
        מזהה ההודעה המקורי של WhatsApp.

    שני המזהים עוברים ל-Worker בנפרד לאורך כל ה-flow.
    """

    data = (
        {"text": content or ""}
        if msg_type == "text"
        else (metadata or {})
    )

    return {
        "typeEvent": "entryMessage",
        "call_id": call_id,
        "scenario_id": scenario_id,
        "contact_id": contact_id,

        # מזהה פנימי בטבלת messages
        "message_id": message_id,

        # מזהה ההודעה ב-WhatsApp
        "whatsapp_message_id": whatsapp_message_id,

        "payload": {
            "type": msg_type,
            "data": data,
        },
    }
# ══════════════════════════════════════════════════════════════════════
# ensure — נקודת היצירה היחידה
# ══════════════════════════════════════════════════════════════════════
async def ensure_call(
    db,
    phone_id: str,
    contact_id: str,
    scenario_id: Optional[str] = None,
    priority: Optional[int] = None,
    source: str = "trigger",
    schedule_id: Optional[str] = None,
    first_message: Optional[dict] = None,
) -> CallResult:
    """
    עוטף את spine_ensure_call. שני מצבי הפעלה, אותו תהליך עסקי:

      scenario_id = None → מצב trigger. ה-RPC בוחר בעצמו את כל תרחישי
                           ה-trigger הפעילים; הראשון לפי priority running,
                           השאר queued. source נכפה ל-'trigger'.

      scenario_id = uuid → תרחיש בודד (scheduler / api / manual).

    מול call פתוח (running או queued):

      trigger                  → 409 denied. לא נרשמת שורה.
      scheduler/api/manual     → 409 aborted, ולא נכנס לתור. בנוסף
                                 מרוקן את התור: כל ה-queued הופכים
                                 ל-aborted / PREEMPTED_BY_<SOURCE>.

    ה-running לא מופסק בשום מקרה — עד summary או עד expired.
    SLA מתחיל רק במעבר ל-running, לא בכניסה לתור.
    """

    res = await _rpc(
        "spine_ensure_call",
        {
            "p_phone_id": phone_id,
            "p_contact_id": contact_id,
            "p_scenario_id": scenario_id,
            "p_priority": priority,
            "p_source": source,
            "p_schedule_id": schedule_id,
            "p_first_message": first_message,
        },
    )

    if not isinstance(res, dict):
        return CallResult(
            500,
            "INVALID_RPC_RESPONSE",
            {
                "status": "error",
                "code": "INVALID_RPC_RESPONSE",
                "message": "spine_ensure_call returned a non-object response",
                "raw": res,
            },
        )

    code     = res.get("code", "UNKNOWN")
    status   = res.get("status")
    call_id  = res.get("call_id")
    event_id = res.get("event_id")

    log.info(
        "[ENSURE] %s | phone=%s contact=%s scenario=%s source=%s "
        "status=%s call=%s event=%s",
        code, phone_id, contact_id, scenario_id or "*", source,
        status, call_id, event_id,
    )

    if code in ("CONTACT_NOT_FOUND", "SCENARIO_NOT_FOUND_OR_INACTIVE"):
        return CallResult(404, code, res)

    if status == "denied":
        # לא נרשמה שורה. זה מצב תקין, לא שגיאה.
        return CallResult(409, code, res, res.get("active_call_id"), phone_id)

    if status == "aborted":
        # נרשמה שורה לתיעוד, אבל שום דבר לא רץ ולא ירוץ.
        log.info(
            "[ENSURE] aborted | call=%s source=%s reason=%s cancelled_queued=%s",
            call_id, source, res.get("status_reason"), res.get("cancelled_queued", 0),
        )
        return CallResult(409, code, res, call_id, phone_id, event_id=event_id)

    if status == "empty":
        return CallResult(200, code, res, None, phone_id)

    if status != "running" or not call_id:
        body = dict(res)
        body["code"] = "UNEXPECTED_ENSURE_RESULT"
        body["message"] = (
            "spine_ensure_call did not return running, denied, aborted or empty"
        )
        return CallResult(500, "UNEXPECTED_ENSURE_RESULT", body)

    contact = {
        "id":     res.get("contact_id"),
        "number": res.get("contact_number") or "",
        "lid":    res.get("contact_lid"),
        "name":   res.get("contact_name") or "",
        "tag":    "active",
    }

    return CallResult(
        201,
        code,
        res,
        call_id,
        phone_id,
        worker_payload=init_payload(
            call_id,
            contact,
            res.get("scenario_id") or scenario_id,
            res.get("scenario_json") or {},
            first_message,
        ),
        event_id=event_id,
    )

# ══════════════════════════════════════════════════════════════════════
# complete — סוגר ומקדם את הבא בתור
# ══════════════════════════════════════════════════════════════════════
async def complete_call(db, call_id: str, status: str = "completed") -> CallResult:

    res = await _rpc("spine_complete_call", {
        "p_call_id": call_id,
        "p_status":  status,
    })

    code = res.get("code")
    log.info("[COMPLETE] %s | call=%s next=%s", code, call_id, res.get("next_call_id"))

    if code in ("CALL_NOT_FOUND", "INVALID_STATUS"):
        return CallResult(400, code, res, call_id)

    nxt = res.get("next_call_id")
    if not nxt:
        return CallResult(200, code, res, call_id)

    # ── הבא בתור קודם ל-running → הקורא ישלח לו init ─────────────────
    # first_message נשמר על השורה בזמן היצירה — ה-call המקודם מקבל
    # את אותה הודעה נכנסת שיצרה אותו, לא None.
    row = db.table("calls") \
        .select("id, phone_id, contact_id, scenario_id, scenario_snapshot, first_message") \
        .eq("id", nxt).maybe_single().execute().data
    if not row:
        log.error("[COMPLETE] promoted call %s not found", nxt)
        return CallResult(200, code, res, call_id)

    contact = db.table("contacts").select("id, number, lid, name") \
                .eq("id", row["contact_id"]).maybe_single().execute().data or {}

    return CallResult(
        200, code, res, call_id, row["phone_id"],
        worker_payload=init_payload(row["id"], contact, row["scenario_id"],
                                    row.get("scenario_snapshot"),
                                    row.get("first_message")),
    )
