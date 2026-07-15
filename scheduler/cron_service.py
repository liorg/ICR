# scheduler/cron_service.py

import logging
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

from config import DEFAULT_TIMEZONE


log = logging.getLogger("scheduler.cron")


def parse_timezone(
    timezone_name: Optional[str],
) -> ZoneInfo:
    name = timezone_name or DEFAULT_TIMEZONE

    try:
        return ZoneInfo(name)
    except Exception:
        log.warning(
            "Invalid timezone '%s'; using default '%s'",
            name,
            DEFAULT_TIMEZONE,
        )
        return ZoneInfo(DEFAULT_TIMEZONE)


def validate_cron(cron_expr: str) -> bool:
    try:
        CronTrigger.from_crontab(cron_expr.strip())
        return True
    except Exception:
        return False


def compute_next_run(
    cron_expr: Optional[str],
    timezone_name: Optional[str],
    *,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """
    מחזיר את זמן ה-Cron הבא בפורמט UTC ISO.
    """

    if not cron_expr or not cron_expr.strip():
        return None

    zone = parse_timezone(timezone_name)

    current_time = now or datetime.now(timezone.utc)

    if current_time.tzinfo is None:
        current_time = current_time.replace(
            tzinfo=timezone.utc
        )

    current_local = current_time.astimezone(zone)

    try:
        trigger = CronTrigger.from_crontab(
            cron_expr.strip(),
            timezone=zone,
        )

        next_local = trigger.get_next_fire_time(
            previous_fire_time=None,
            now=current_local,
        )

        if next_local is None:
            return None

        return next_local.astimezone(
            timezone.utc
        ).isoformat()

    except Exception:
        log.exception(
            "Failed computing next run | cron=%s timezone=%s",
            cron_expr,
            timezone_name,
        )
        return None
