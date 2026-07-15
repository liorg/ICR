# scheduler/main.py

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from supabase import Client, create_client


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

log = logging.getLogger("scenario.scheduler")


# ─────────────────────────────────────────────────────────────────────────────
# Environment
# ─────────────────────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

SPINE_URL = os.getenv(
    "SPINE_URL",
    "http://scenario_data-spine:8000",
).rstrip("/")

SPINE_DISPATCH_PATH = os.getenv(
    "SPINE_DISPATCH_PATH",
    "/dispatch",
)

DEFAULT_TIMEZONE = os.getenv(
    "DEFAULT_TIMEZONE",
    "Asia/Jerusalem",
)

POLL_SECONDS = max(
    int(os.getenv("POLL_SECONDS", "5")),
    1,
)

DISPATCH_TIMEOUT_SECONDS = max(
    int(os.getenv("DISPATCH_TIMEOUT_SECONDS", "20")),
    1,
)

MAX_SCHEDULES_PER_POLL = max(
    int(os.getenv("MAX_SCHEDULES_PER_POLL", "50")),
    1,
)


db: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY,
)


# ─────────────────────────────────────────────────────────────────────────────
# Time helpers
# ─────────────────────────────────────────────────────────────────────────────

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_timezone(timezone_name: Optional[str]) -> ZoneInfo:
    name = timezone_name or DEFAULT_TIMEZONE

    try:
        return ZoneInfo(name)
    except Exception:
        log.warning(
            "Invalid timezone '%s'; using '%s'",
            name,
            DEFAULT_TIMEZONE,
        )
        return ZoneInfo(DEFAULT_TIMEZONE)


def compute_next_run(
    schedule_type: Optional[str],
    cron_expr: Optional[str],
    run_at: Optional[str],
    timezone_name: Optional[str],
    *,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """
    מחשב את זמן הריצה הבא ומחזיר ISO UTC.

    once:
        מחזיר את run_at ב-UTC.

    cron:
        מחשב את זמן ה-Cron הבא אחרי now.
    """

    schedule_type_normalized = str(
        schedule_type or ""
    ).strip().lower()

    if schedule_type_normalized == "once":
        if not run_at:
            return None

        try:
            parsed = datetime.fromisoformat(
                run_at.replace("Z", "+00:00")
            )
        except ValueError:
            log.warning("Invalid run_at: %s", run_at)
            return None

        if parsed.tzinfo is None:
            zone = parse_timezone(timezone_name)
            parsed = parsed.replace(tzinfo=zone)

        return parsed.astimezone(timezone.utc).isoformat()

    if schedule_type_normalized == "cron":
        if not cron_expr or not cron_expr.strip():
            return None

        zone = parse_timezone(timezone_name)

        current_utc = now or utc_now()

        if current_utc.tzinfo is None:
            current_utc = current_utc.replace(
                tzinfo=timezone.utc
            )

        current_local = current_utc.astimezone(zone)

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
                "Failed calculating next run | cron=%s timezone=%s",
                cron_expr,
                timezone_name,
            )
            return None

    log.warning(
        "Unsupported schedule_type: %s",
        schedule_type,
    )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────────────────

def get_due_schedules() -> list[dict[str, Any]]:
    """
    מביא תזמונים פעילים שה-next_run_at שלהם הגיע.
    """

    now = utc_now_iso()

    result = (
        db.table("schedules")
        .select(
            "id,"
            "phone_id,"
            "contact_id,"
            "scenario_id,"
            "schedule_name,"
            "schedule_type,"
            "cron_expr,"
            "run_at,"
            "timezone,"
            "next_run_at,"
            "status"
        )
        .eq("status", "active")
        .not_.is_("next_run_at", "null")
        .lte("next_run_at", now)
        .order("next_run_at")
        .limit(MAX_SCHEDULES_PER_POLL)
        .execute()
    )

    return result.data or []


def update_schedule(
    schedule_id: str,
    values: dict[str, Any],
) -> None:
    (
        db.table("schedules")
        .update(values)
        .eq("id", schedule_id)
        .execute()
    )


# ─────────────────────────────────────────────────────────────────────────────
# Spine dispatch
# ─────────────────────────────────────────────────────────────────────────────

def dispatch_to_spine(
    schedule: dict[str, Any],
) -> dict[str, Any]:
    """
    שולח ל-Spine בקשה להפעיל Scenario.
    """

    url = f"{SPINE_URL}{SPINE_DISPATCH_PATH}"

    payload = {
        "phone_id": schedule["phone_id"],
        "contact_id": schedule["contact_id"],
        "scenario_id": schedule["scenario_id"],

        # מידע נוסף שמאפשר לדעת מה מקור ההפעלה
        "source": "scheduler",
        "schedule_id": schedule["id"],
        "schedule_name": schedule.get("schedule_name"),
    }

    response = requests.post(
        url,
        json=payload,
        timeout=DISPATCH_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    if not response.content:
        return {"ok": True}

    try:
        return response.json()
    except ValueError:
        return {
            "ok": True,
            "response": response.text,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Schedule processing
# ─────────────────────────────────────────────────────────────────────────────

def process_once_schedule(
    schedule: dict[str, Any],
) -> None:
    schedule_id = schedule["id"]

    dispatch_result = dispatch_to_spine(schedule)

    update_schedule(
        schedule_id,
        {
            "status": "completed",
            "next_run_at": None,
            "last_run_at": utc_now_iso(),
        },
    )

    log.info(
        "Once schedule completed | id=%s result=%s",
        schedule_id,
        dispatch_result,
    )


def process_cron_schedule(
    schedule: dict[str, Any],
) -> None:
    schedule_id = schedule["id"]

    dispatch_result = dispatch_to_spine(schedule)

    # מחשבים רק אחרי שההפעלה הנוכחית הצליחה.
    next_run_at = compute_next_run(
        schedule_type="cron",
        cron_expr=schedule.get("cron_expr"),
        run_at=None,
        timezone_name=schedule.get("timezone"),
        now=utc_now(),
    )

    if next_run_at is None:
        update_schedule(
            schedule_id,
            {
                "status": "error",
                "next_run_at": None,
                "last_run_at": utc_now_iso(),
                "last_error": "Could not calculate next_run_at",
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
        "Cron schedule dispatched | id=%s next=%s result=%s",
        schedule_id,
        next_run_at,
        dispatch_result,
    )


def process_schedule(
    schedule: dict[str, Any],
) -> None:
    schedule_id = schedule.get("id")
    schedule_type = str(
        schedule.get("schedule_type") or ""
    ).strip().lower()

    log.info(
        "Processing schedule | id=%s type=%s planned=%s",
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

    log.error(
        "Unsupported schedule type | id=%s type=%s",
        schedule_id,
        schedule_type,
    )


def poll_due_schedules() -> None:
    """
    הפונקציה ש-APScheduler מפעיל כל כמה שניות.
    """

    try:
        schedules = get_due_schedules()
    except Exception:
        log.exception("Failed reading due schedules")
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

        except requests.RequestException as exc:
            log.exception(
                "Spine dispatch failed | schedule=%s",
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
                    "Failed saving dispatch error | schedule=%s",
                    schedule_id,
                )

        except Exception as exc:
            log.exception(
                "Schedule processing failed | schedule=%s",
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
                    "Failed saving schedule error | schedule=%s",
                    schedule_id,
                )


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info(
        "Scheduler starting | poll=%ss spine=%s%s timezone=%s",
        POLL_SECONDS,
        SPINE_URL,
        SPINE_DISPATCH_PATH,
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
        max_instances=1,
        coalesce=True,
        misfire_grace_time=max(POLL_SECONDS * 2, 10),
    )

    # בדיקה ראשונה מיד בעליית ה-Container.
    poll_due_schedules()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped")


if __name__ == "__main__":
    main()
