# scheduler/sweep_service.py
"""
סגירת calls תקועים — דרך Spine, לא ב-UPDATE ישיר.

למה זה חייב לעבור דרך Spine:

    ה-Worker הוא היחיד שסוגר call בזרימה תקינה (POST /calls/{id}/summary).
    אם הוא קרס או איבד קשר, ה-call נשאר running לנצח והקונטקט חסום —
    כל הודעה נכנסת מנותבת ל-Worker שלא קיים, וכל trigger נדחה.

    UPDATE ישיר ב-DB אמנם משנה את הסטטוס, אבל לא מקדם את התור ולא
    שולח init ל-call הבא. התוצאה: שורות queued יתומות, וקונטקט שנשאר
    חסום גם אחרי ה"תיקון".

    POST /calls/sweep עובר דרך spine_complete_call — סוגר, מקדם את
    ה-queued הבא תחת אותו lock, ושולח לו init.

Spine מגיב, Scheduler יוזם. הסריקה כאן.
"""

import logging

import httpx

from config import (
    SPINE_SWEEP_PATH,
    SPINE_URL,
    SWEEP_LIMIT,
    SWEEP_STATUS,
    SWEEP_TIMEOUT_SECONDS,
)

log = logging.getLogger("scheduler.sweep")


def sweep_stale_calls() -> None:
    url = f"{SPINE_URL}{SPINE_SWEEP_PATH}"

    try:
        response = httpx.post(
            url,
            json={
                "status": SWEEP_STATUS,
                "limit": SWEEP_LIMIT,
            },
            timeout=SWEEP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        log.error("[SWEEP] request failed | url=%s error=%s", url, exc)
        return

    if response.status_code != 200:
        log.error(
            "[SWEEP] unexpected status | code=%s body=%s",
            response.status_code,
            response.text[:500],
        )
        return

    try:
        body = response.json()
    except ValueError:
        log.error("[SWEEP] invalid json | body=%s", response.text[:500])
        return

    swept = body.get("swept", 0)

    if not swept:
        log.debug("[SWEEP] nothing to sweep")
        return

    # לוג לכל call בנפרד: call תקוע הוא תמיד סימן לבעיה בצד ה-Worker,
    # וכדאי שיהיה קל לאתר איזה קונטקט ואיזה תרחיש.
    for call in body.get("calls", []):
        log.warning(
            "[SWEEP] closed stuck call | call=%s contact=%s expected_end=%s "
            "code=%s next=%s delivered=%s",
            call.get("call_id"),
            call.get("contact_id"),
            call.get("expected_end"),
            call.get("code"),
            call.get("next_call_id"),
            call.get("delivered"),
        )

    log.info("[SWEEP] done | swept=%s", swept)
