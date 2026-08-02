# scheduler/schedule_service.py
import logging
from datetime import datetime, timezone
from typing import Any
from cron_service import compute_next_run
from database import (
    get_due_schedules,
    update_schedule,
    utc_now_iso,
)
from spine_client import ensure_call
log = logging.getLogger("scheduler.service")
def process_once_schedule(
    schedule: dict[str, Any],
) -> None:
    schedule_id = schedule["id"]
    result = ensure_call(schedule)
    if not result.accepted:
        raise RuntimeError(
            "Spine rejected once schedule | "
            f"status={result.http_status} "
            f"body={result.body}"
        )
    # 409 = איש הקשר תפוס. אין טעם לנסות שוב מאוחר יותר עבור once,
    # אבל כן מסמנים שהירייה קרתה כדי שלא תחזור בלולאה.
    update_schedule(
        schedule_id,
        {
            "status": "completed",
            "next_run": None,
            "last_run": utc_now_iso(),
        },
    )
    log.info(
        "Once schedule done | id=%s status=%s blocked=%s",
        schedule_id,
        result.http_status,
        result.blocked,
    )
def process_cron_schedule(
    schedule: dict[str, Any],
) -> None:
    schedule_id = schedule["id"]
    result = ensure_call(schedule)
    if not result.accepted:
        raise RuntimeError(
            "Spine rejected cron schedule | "
            f"status={result.http_status} "
            f"body={result.body}"
        )
    next_run_at = compute_next_run(
        cron_expr=schedule.get("cron_expr"),
        timezone_name=schedule.get("timezone"),
        now=datetime.now(timezone.utc),
    )
    if next_run_at is None:
        update_schedule(
            schedule_id,
            {
                "status": "error",
                "next_run": None,
                "last_run": utc_now_iso(),
            },
        )
        raise RuntimeError(
            f"Could not calculate next_run "
            f"for schedule {schedule_id}"
        )
    # גם כשה-call נחסם ב-409 מקדמים את next_run — אחרת התזמון
    # ינסה שוב כל POLL_SECONDS ולא יגיע לזמן הבא לעולם.
    update_schedule(
        schedule_id,
        {
            "next_run": next_run_at,
            "last_run": utc_now_iso(),
        },
    )
    log.info(
        "Cron schedule fired | id=%s status=%s blocked=%s next=%s",
        schedule_id,
        result.http_status,
        result.blocked,
        next_run_at,
    )
def process_schedule(
    schedule: dict[str, Any],
) -> None:
    schedule_id = schedule["id"]
    schedule_type = str(
        schedule.get("schedule_type") or ""
    ).strip().lower()
    log.info(
        "Processing schedule | id=%s type=%s due=%s",
        schedule_id,
        schedule_type,
        schedule.get("next_run"),
    )
    if schedule_type == "once":
        process_once_schedule(schedule)
        return
    if schedule_type == "cron":
        process_cron_schedule(schedule)
        return
    # manual (מסך הביצועים): Play הציב next_run=now דרך ה-RPC
    # schedule_run_now. מרגע שה-next_run הוצב ההתנהגות זהה ל-once —
    # יורה פעם אחת → completed.
    if schedule_type == "manual":
        process_once_schedule(schedule)
        return
    update_schedule(
        schedule_id,
        {
            "status": "error",
            "next_run": None,
        },
    )
    raise RuntimeError(
        f"Unsupported schedule_type: {schedule_type}"
    )
def poll_due_schedules() -> None:
    """
    מופעל על ידי APScheduler בכל כמה שניות.
    """
    try:
        schedules = get_due_schedules()
    except Exception:
        log.exception("Failed loading due schedules")
        return
    if not schedules:
        log.debug("No due schedules")
        return
    log.info(
        "Found %d due schedules",
        len(schedules),
    )
    for schedule in schedules:
        schedule_id = schedule.get("id")
        try:
            process_schedule(schedule)
        except Exception as exc:
            log.exception(
                "Schedule processing failed | id=%s",
                schedule_id,
            )
            # אין עמודת last_error בסכמה — השגיאה נשארת בלוג בלבד.
