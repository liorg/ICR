# scheduler/main.py
import logging
import os
from datetime import timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from config import (
    DEFAULT_TIMEZONE,
    POLL_SECONDS,
    SLA_CHECK_SECONDS,
    SPINE_ENSURE_PATH,
    SPINE_URL,
)
from schedule_service import poll_due_schedules
from sweep_service import sweep_stale_calls

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(name)s | "
        "%(message)s"
    ),
)

log = logging.getLogger("scheduler.main")


def main() -> None:
    log.info(
        "Scheduler starting | poll=%ss sweep=%ss spine=%s%s timezone=%s",
        POLL_SECONDS,
        SLA_CHECK_SECONDS,
        SPINE_URL,
        SPINE_ENSURE_PATH,
        DEFAULT_TIMEZONE,
    )

    scheduler = BlockingScheduler(
        timezone=timezone.utc,
    )

    # ── תזמונים ─────────────────────────────────────────────────────
    scheduler.add_job(
        poll_due_schedules,
        trigger="interval",
        seconds=POLL_SECONDS,
        id="poll-due-schedules",
        # מונע משתי בדיקות לרוץ במקביל באותו Container
        max_instances=1,
        # אם ה-Service היה תקוע, לא מריץ עשרות בדיקות ישנות
        coalesce=True,
        misfire_grace_time=max(
            POLL_SECONDS * 2,
            10,
        ),
    )

    # ── sweep: סגירת calls תקועים ───────────────────────────────────
    #
    # מחליף את שני ה-Jobs הקודמים — promote_queued_calls ו-
    # expire_stale_calls — ומבטל את הצורך בשניהם:
    #
    #   הקידום עבר ל-spine_complete_call, שרץ תחת אותו advisory lock
    #   שסוגר את ה-call. אין יותר חלון שבו call נסגר והתור עוד לא
    #   התקדם, ולכן אין מה לסרוק.
    #
    #   פקיעת SLA כבר לא UPDATE ישיר: POST /calls/sweep סוגר דרך
    #   spine_complete_call, מקדם את הבא בתור ושולח לו init. UPDATE
    #   ישיר היה משאיר שורות queued יתומות והקונטקט נשאר חסום.
    #
    # תדירות נמוכה — זו רשת ביטחון, לא נתיב חם.
    scheduler.add_job(
        sweep_stale_calls,
        trigger="interval",
        seconds=SLA_CHECK_SECONDS,
        id="sweep-stale-calls",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=max(
            SLA_CHECK_SECONDS,
            30,
        ),
    )

    # בדיקה מיד בעליית ה-Container
    poll_due_schedules()
    sweep_stale_calls()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped")


if __name__ == "__main__":
    main()
