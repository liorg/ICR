# scheduler/main.py

import logging
import os
from datetime import timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from config import (
    DEFAULT_TIMEZONE,
    POLL_SECONDS,
    SPINE_ENSURE_PATH,
    SPINE_URL,
)
from schedule_service import poll_due_schedules


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
        "Scheduler starting | poll=%ss spine=%s%s timezone=%s",
        POLL_SECONDS,
        SPINE_URL,
        SPINE_ENSURE_PATH,
        DEFAULT_TIMEZONE,
    )

    scheduler = BlockingScheduler(
        timezone=timezone.utc,
    )

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

    # בדיקה מיד בעליית ה-Container
    poll_due_schedules()

    try:
        scheduler.start()

    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped")


if __name__ == "__main__":
    main()
