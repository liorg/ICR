# scheduler/queue_service.py

"""
סריקת התור וקידום calls ממתינים.

חלוקת אחריות:
    ה-Scheduler מחליט *מה* לקדם — הוא היוזם.
    ה-Spine מאמת ומבצע — הוא הממשק שמדבר עם ה-Workers.

הסריקה סדרתית בכוונה: אין threads ואין קריאות מקבילות. שני calls
לאותו (phone_id, contact_id) לא יכולים לרוץ יחד, ולכן אין טעם
לנסות את שניהם — הראשון תופס והשני היה מקבל 409 ממילא.

הסדר: priority asc, created_at asc — בדיוק כמו ב-spine_complete_call.
next_run אינו רלוונטי כאן; הוא שייך ל-schedules בלבד.
"""

import logging
from typing import Any

from database import db
from config import MAX_SCHEDULES_PER_POLL
from spine_client import promote_call


log = logging.getLogger("scheduler.queue")


def get_queued_calls() -> list[dict[str, Any]]:
    result = (
        db.table("calls")
        .select("id, phone_id, contact_id, scenario_id, priority, created_at")
        .eq("status", "queued")
        .order("priority")
        .order("created_at")
        .limit(MAX_SCHEDULES_PER_POLL)
        .execute()
    )

    return result.data or []


def promote_queued_calls() -> None:
    """
    רץ אחרי poll_due_schedules בכל סבב.

    תזמונים מקבלים עדיפות על התור: אם שניהם רוצים את אותו איש קשר,
    התזמון כבר תפס אותו והקידום יקבל 409 וידלג.
    """
    try:
        queued = get_queued_calls()
    except Exception:
        log.exception("Failed loading queued calls")
        return

    if not queued:
        return

    log.info("Found %d queued call(s)", len(queued))

    seen: set[tuple[str, str]] = set()
    promoted = 0

    for call in queued:
        key = (call["phone_id"], call["contact_id"])

        # רק הראשון לכל זוג — השאר יידחו ב-409 ממילא.
        if key in seen:
            continue

        seen.add(key)

        call_id = call["id"]

        try:
            result = promote_call(call_id)
        except Exception:
            log.exception("Promote request failed | call=%s", call_id)
            continue

        if result.promoted:
            promoted += 1

            log.info(
                "Call promoted | call=%s queued=%ss delivered=%s",
                call_id,
                result.body.get("queued_seconds"),
                result.body.get("delivered"),
            )

        elif result.busy:
            log.debug(
                "Call skipped | call=%s code=%s",
                call_id,
                result.body.get("code"),
            )

        else:
            log.warning(
                "Promote rejected | call=%s status=%s body=%s",
                call_id,
                result.http_status,
                result.body,
            )

    if promoted:
        log.info("Promoted %d call(s) from queue", promoted)
