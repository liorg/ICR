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

    update_schedule(
        schedule_id,
        {
            "status": "completed",
            "next_run_at": None,
            "last_run_at": utc_now_iso(),
            "last_error": None,
        },
    )

    log.info(
        "Once schedule completed | id=%s status=%s",
        schedule_id,
        result.http_status,
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
                "next_run_at": None,
                "last_run_at": utc_now_iso(),
                "last_error": (
                    "Call accepted but failed calculating next_run_at"
                ),
            },
        )

        raise RuntimeError(
            f"Could not calculate next_run_at "
            f"for schedule {schedule_id}"
        )

    update_schedule(
        schedule_id,
        {
            "next_run_at": next_run_at,
            "last_run_at": utc_now_iso(),
            "last_error": None,
        },
    )

    log.info(
        "Cron schedule accepted | id=%s status=%s next=%s",
        schedule_id,
        result.http_status,
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
        schedule.get("next_run_at"),
    )

    if schedule_type == "once":
        process_once_schedule(schedule)
        return

    if schedule_type == "cron":
        process_cron_schedule(schedule)
        return

    update_schedule(
        schedule_id,
        {
            "status": "error",
            "next_run_at": None,
            "last_error": (
                f"Unsupported schedule_type: {schedule_type}"
            ),
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

            try:
                update_schedule(
                    schedule_id,
                    {
                        "last_error": str(exc)[:1000],
                    },
                )
            except Exception:
                log.exception(
                    "Failed saving schedule error | id=%s",
                    schedule_id,
                )
