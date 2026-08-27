# scheduler/recording_service.py

"""
פקיעת recording calls.

call מסוג recording שה-expected_end שלו עבר ועדיין active — ההשמעה
הסתיימה ואף אחד לא סגר אותו. UPDATE אטומי אחד מול ה-DB.

זו פעולת תחזוקה ולא הפעלת worker, ולכן ישירות מול ה-DB ולא דרך
ה-Spine — כמו expire_stale_calls.
"""

import logging

from database import db, utc_now_iso

log = logging.getLogger("scheduler.recording")


def expire_recording_calls() -> None:
    now = utc_now_iso()

    try:
        result = (
            db.table("calls")
            .update(
                {
                    "status": "expired",
                    "ended_at": now,
                    "last_status_updated_at": now,
                }
            )
            .eq("call_type", "recording")
            .eq("status", "active")
            .lt("expected_end", now)
            .execute()
        )
    except Exception:
        log.exception("Recording expiry failed")
        return

    expired = len(result.data or [])

    if expired:
        log.info("Expired %d recording call(s)", expired)
