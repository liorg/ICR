# scheduler/sla_service.py

"""
בדיקת SLA — סגירת calls שפג תוקפם.

call שהתחיל (started_at + expected_end נקבעו) ולא נסגר עד expected_end
נחשב תקוע: ה-worker מת, timeout שלא דווח, או summary שאבד. בלי סגירה
הוא נשאר running לנצח וחוסם את איש הקשר.

הבדיקה כולה ב-spine_expire_stale_calls (UPDATE אטומי אחד). כאן רק
מפעילים אותה ומדווחים. queued לא מושפע — אין לו expected_end.

זו פעולת תחזוקה, לא הפעלת worker — לכן ה-Scheduler עושה אותה ישירות
מול ה-DB ולא דרך ה-Spine.
"""

import logging

from database import db


log = logging.getLogger("scheduler.sla")


def expire_stale_calls() -> None:
    try:
        result = db.rpc("spine_expire_stale_calls", {}).execute()
    except Exception:
        log.exception("SLA check failed")
        return

    expired = result.data if isinstance(result.data, int) else 0

    if expired:
        log.warning("SLA: expired %d stale running call(s)", expired)
