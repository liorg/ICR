
# services/scheduler.py
"""
חישוב next_run_at בצד ה-API — לוגיקה זהה ל-Scheduler עצמאי.

cron_expr הוא ביטוי Linux cron אמיתי (5 שדות):
    30 20 * * *       כל יום ב-20:30
    30 19 * * 0,3     ראשון ורביעי ב-19:30
    15 8-23/2 * * *   כל שעתיים מ-8:15
    0 10 15 * *       בכל 15 לחודש ב-10:00

schedule_type:
    once  → next_run_at = run_at
    cron  → next_run_at מחושב מ-CronTrigger באזור הזמן של המשתמש,
            נשמר ב-UTC.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger("api.scheduler")

DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "Asia/Jerusalem")


def _parse_timezone(timezone_name: Optional[str]) -> ZoneInfo:
    name = timezone_name or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except Exception:
        log.warning(
            "Invalid timezone '%s' — using default '%s'",
            name,
            DEFAULT_TIMEZONE,
        )
        return ZoneInfo(DEFAULT_TIMEZONE)


def validate_cron(cron_expr: str) -> bool:
    """בדיקת תקינות בלבד — משמש את ה-Router לפני שמירה."""
    try:
        CronTrigger.from_crontab(cron_expr.strip())
        return True
    except Exception:
        return False


def compute_next_run(
    schedule_type: Optional[str],
    cron_expr: Optional[str],
    run_at: Optional[str],
    timezone_name: Optional[str] = None,
) -> Optional[str]:
    """
    מחזיר next_run_at כ-ISO ב-UTC, או None אם אין מה לחשב.

    once  → run_at כמו שהוא (מנורמל ל-UTC אם יש tz).
    cron  → הירי הבא לפי CronTrigger.
    """

    stype = str(schedule_type or "").strip().lower()

    if stype == "once":
        if not run_at:
            return None
        try:
            dt = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                # run_at בלי tz מתפרש כזמן מקומי של המשתמש
                dt = dt.replace(tzinfo=_parse_timezone(timezone_name))
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            log.warning("Invalid run_at '%s'", run_at)
            return None

    if stype == "cron":
        if not cron_expr or not cron_expr.strip():
            return None

        zone = _parse_timezone(timezone_name)
        now_local = datetime.now(timezone.utc).astimezone(zone)

        try:
            trigger = CronTrigger.from_crontab(cron_expr.strip(), timezone=zone)
            next_local = trigger.get_next_fire_time(
                previous_fire_time=None,
                now=now_local,
            )
            if next_local is None:
                return None
            return next_local.astimezone(timezone.utc).isoformat()

        except Exception:
            log.exception(
                "Failed computing next run | cron=%s timezone=%s",
                cron_expr,
                timezone_name,
            )
            return None

    log.warning("Unsupported schedule_type '%s'", schedule_type)
    return None
