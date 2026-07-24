"""
POST /api/calls/{call_id}/promote — קידום call מהתור.

ה-Spine מגיב בלבד. ה-Scheduler הוא שסורק את התור ומחליט מה לקדם;
כאן רק מבצעים ומאמתים.

האימות (אין running לאותו איש קשר, ה-call עדיין queued) נעשה בתוך
spine_promote_call תחת נעילת ה-slot — לא כאן — כדי שלא ייווצר חלון
שבו ensure_call יוצר call מתחרה.
"""
import logging

from fastapi import APIRouter, HTTPException, Response

from dependencies import get_supabase
from services.calls import _rpc, init_payload, send_to_worker

router = APIRouter(tags=["promote"])
log = logging.getLogger("spine.promote")


@router.post("/calls/{call_id}/promote")
async def promote_call(call_id: str, response: Response):
    db = get_supabase()

    res = await _rpc("spine_promote_call", {"p_call_id": call_id})

    code = res.get("code")

    if code == "CALL_NOT_FOUND":
        raise HTTPException(404, code)

    # NOT_QUEUED / CONTACT_BUSY — לא שגיאה: ה-Scheduler ידלג
    # וינסה שוב בסבב הבא.
    if code in ("NOT_QUEUED", "CONTACT_BUSY"):
        log.info("[PROMOTE] %s | call=%s", code, call_id)
        response.status_code = 409
        return res

    if code != "PROMOTED":
        log.error("[PROMOTE] unexpected rpc result | call=%s res=%s", call_id, res)
        response.status_code = 500
        return res

    # ── ה-call כבר running ב-DB; שולחים לו init ──────────────────────
    contact = {
        "id":     res.get("contact_id"),
        "number": res.get("contact_number") or "",
        "lid":    res.get("contact_lid"),
        "name":   res.get("contact_name") or "",
    }

    delivered = await send_to_worker(
        db,
        res.get("phone_id"),
        init_payload(
            call_id,
            contact,
            res.get("scenario_id"),
            res.get("scenario_json") or {},
            None,
        ),
    )

    # אם ה-init לא נמסר, ה-call יישאר running בלי שאיש מריץ אותו
    # ויחסום את איש הקשר לנצח. מחזירים אותו לתור.
    if not delivered:
        try:
            (
                db.table("calls")
                .update({"status": "queued", "started_at": None})
                .eq("id", call_id)
                .eq("status", "running")
                .execute()
            )
            log.warning(
                "[PROMOTE] init not delivered — call %s returned to queue",
                call_id,
            )
        except Exception:
            log.exception(
                "[PROMOTE] failed returning call %s to queue",
                call_id,
            )

    log.info(
        "[PROMOTE] call=%s phone=%s contact=%s queued=%ss delivered=%s",
        call_id,
        res.get("phone_id"),
        res.get("contact_id"),
        res.get("queued_seconds"),
        delivered,
    )

    res["delivered"] = delivered
    return res
