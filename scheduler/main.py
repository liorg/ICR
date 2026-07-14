"""
Scheduler — טריגר מתוזמן ללא RPC.

הנחות:

1. קיים מופע Scheduler יחיד בלבד:
       deploy:
         replicas: 1

2. cron_expr הוא ביטוי Cron אמיתי, לדוגמה:
       30 20 * * *       כל יום ב-20:30
       30 19 * * 0,3     ראשון ורביעי ב-19:30
       15 */2 * * *      כל שעתיים בדקה 15
       0 10 15 * *       בכל 15 לחודש ב-10:00

3. timezone נשמר בטבלת users.
   ברירת המחדל היא:
       Asia/Jerusalem

4. סוגי התזמון:
       once
       cron

זרימת העבודה:

    SELECT schedules שהגיע זמנם
          ↓
    UPDATE status = firing
          ↓
    POST /api/calls/ensure
          ↓
    חישוב next_run_at בעזרת CronTrigger
          ↓
    UPDATE schedules:
        once  → completed
        cron  → active + next_run_at
        error → active לצורך ניסיון נוסף
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from supabase import Client, create_client


# ── Logging ───────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("scheduler")


# ── Configuration ─────────────────────────────────────────────────────

SPINE_URL = os.getenv(
    "SPINE_URL",
    "http://scenario_data-spine:8000",
).rstrip("/")

CHECK_INTERVAL_SECONDS = max(
    int(os.getenv("CHECK_INTERVAL_SECONDS", "30")),
    1,
)

SCHEDULE_FETCH_LIMIT = max(
    int(os.getenv("SCHEDULE_FETCH_LIMIT", "50")),
    1,
)

REQUEST_TIMEOUT_SECONDS = max(
    float(os.getenv("SPINE_REQUEST_TIMEOUT_SECONDS", "15")),
    1.0,
)

DEFAULT_TIMEZONE = os.getenv(
    "DEFAULT_TIMEZONE",
    "Asia/Jerusalem",
)


# ── Clients ───────────────────────────────────────────────────────────

db: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"],
)

http = httpx.Client(
    base_url=SPINE_URL,
    timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS),
)


# ── Time helpers ──────────────────────────────────────────────────────

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
            "Invalid timezone '%s' — using default '%s'",
            name,
            DEFAULT_TIMEZONE,
        )

        return ZoneInfo(DEFAULT_TIMEZONE)


def compute_next_run(
    cron_expr: Optional[str],
    timezone_name: Optional[str],
    now: Optional[datetime] = None,
) -> Optional[str]:
    """
    מחשב את זמן ההפעלה הבא לפי ביטוי Cron אמיתי.

    cron_expr לדוגמה:
        30 20 * * *

    החישוב נעשה באזור הזמן של המשתמש,
    והתוצאה נשמרת ב-UTC ב-next_run_at.
    """

    if not cron_expr or not cron_expr.strip():
        return None

    zone = parse_timezone(timezone_name)
    current_utc = now or utc_now()

    if current_utc.tzinfo is None:
        current_utc = current_utc.replace(tzinfo=timezone.utc)

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

        return next_local.astimezone(timezone.utc).isoformat()

    except Exception:
        log.exception(
            "Failed computing next run | cron=%s timezone=%s",
            cron_expr,
            timezone_name,
        )

        return None


# ── Response helpers ──────────────────────────────────────────────────

def response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError:
        return {}

    return value if isinstance(value, dict) else {}


def short_response_body(
    response: httpx.Response,
    limit: int = 500,
) -> str:
    try:
        return response.text[:limit]
    except Exception:
        return "<unavailable>"


# ── Schedule loading ──────────────────────────────────────────────────

def load_due_schedules() -> list[dict[str, Any]]:
    """
    שולף תזמונים פעילים שהגיע זמן ההפעלה שלהם.

    מאחר שקיים Scheduler יחיד בלבד, אין צורך ב-RPC או ב-SKIP LOCKED.
    """

    now = utc_now_iso()

    try:
        result = (
            db.table("schedules")
            .select(
                "id,"
                "user_id,"
                "phone_id,"
                "scenario_id,"
                "schedule_type,"
                "cron_expr,"
                "run_at,"
                "next_run_at,"
                "priority,"
                "status"
            )
            .eq("status", "active")
            .lte("next_run_at", now)
            .order("next_run_at")
            .limit(SCHEDULE_FETCH_LIMIT)
            .execute()
        )

        return result.data or []

    except Exception:
        log.exception("Failed loading due schedules")
        return []


def load_user_timezone(user_id: Optional[str]) -> str:
    """
    מחזיר את אזור הזמן של המשתמש.

    אם user_id חסר, המשתמש לא נמצא או timezone ריק,
    משתמשים ב-Asia/Jerusalem.
    """

    if not user_id:
        return DEFAULT_TIMEZONE

    try:
        result = (
            db.table("users")
            .select("timezone")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )

        if not result.data:
            return DEFAULT_TIMEZONE

        timezone_name = result.data[0].get("timezone")

        return timezone_name or DEFAULT_TIMEZONE

    except Exception:
        log.exception(
            "Failed loading user timezone | user=%s",
            user_id,
        )

        return DEFAULT_TIMEZONE


# ── Claim ─────────────────────────────────────────────────────────────

def claim_schedule(schedule: dict[str, Any]) -> bool:
    """
    מעביר schedule מ-active ל-firing.

    התנאי status=active מספק הגנה בסיסית מפני שינוי מקביל,
    אף שהמערכת מיועדת למופע Scheduler יחיד.
    """

    schedule_id = schedule.get("id")

    if not schedule_id:
        return False

    try:
        result = (
            db.table("schedules")
            .update({
                "status": "firing",
                "updated_at": utc_now_iso(),
            })
            .eq("id", schedule_id)
            .eq("status", "active")
            .execute()
        )

        if not result.data:
            log.info(
                "Schedule was not claimed | schedule=%s",
                schedule_id,
            )

            return False

        return True

    except Exception:
        log.exception(
            "Failed claiming schedule | schedule=%s",
            schedule_id,
        )

        return False


# ── Dispatch ──────────────────────────────────────────────────────────

def fire_schedule(schedule: dict[str, Any]) -> bool:
    """
    מפעיל תרחיש עבור כל אנשי הקשר הפעילים של הטלפון.

    נחשב הצלחה כאשר:

        201 — call התחיל מיד.
        202 — call נכנס לתור.
        409 — Spine דילג בהחלטה עסקית תקינה.

    מחזיר False רק כאשר כל הניסיונות נכשלו טכנית.
    """

    schedule_id = schedule.get("id")
    phone_id = schedule.get("phone_id")
    scenario_id = schedule.get("scenario_id")

    if not schedule_id:
        log.error("Schedule has no id")
        return False

    if not phone_id:
        log.warning(
            "Schedule has no phone_id | schedule=%s",
            schedule_id,
        )
        return False

    if not scenario_id:
        log.warning(
            "Schedule has no scenario_id | schedule=%s",
            schedule_id,
        )
        return False

    try:
        result = (
            db.table("contacts")
            .select("id")
            .eq("phone_id", phone_id)
            .eq("tag", "active")
            .execute()
        )

        contacts = result.data or []

    except Exception:
        log.exception(
            "Failed loading contacts | schedule=%s phone=%s",
            schedule_id,
            phone_id,
        )

        return False

    if not contacts:
        log.info(
            "No active contacts | schedule=%s phone=%s",
            schedule_id,
            phone_id,
        )

        # אין למי לשלוח אינו כשל טכני.
        return True

    accepted_count = 0
    started_count = 0
    queued_count = 0
    skipped_count = 0
    failed_count = 0

    for contact in contacts:
        contact_id = contact.get("id")

        if not contact_id:
            failed_count += 1
            continue

        try:
            response = http.post(
                "/api/calls/ensure",
                json={
                    "phone_id": phone_id,
                    "contact_id": contact_id,
                    "scenario_id": scenario_id,
                    "priority": schedule.get("priority"),
                    "source": "scheduler",
                    "schedule_id": schedule_id,
                },
            )

            body = response_json(response)
            code = str(
                body.get("code")
                or f"HTTP_{response.status_code}"
            )

            if response.status_code == 201:
                accepted_count += 1
                started_count += 1

                log.info(
                    "Call started | code=%s schedule=%s contact=%s",
                    code,
                    schedule_id,
                    contact_id,
                )

            elif response.status_code == 202:
                accepted_count += 1
                queued_count += 1

                log.info(
                    "Call queued | code=%s schedule=%s contact=%s",
                    code,
                    schedule_id,
                    contact_id,
                )

            elif response.status_code == 409:
                accepted_count += 1
                skipped_count += 1

                log.info(
                    "Call skipped | code=%s schedule=%s contact=%s",
                    code,
                    schedule_id,
                    contact_id,
                )

            else:
                failed_count += 1

                log.error(
                    "Dispatch rejected | schedule=%s contact=%s "
                    "http=%s code=%s body=%s",
                    schedule_id,
                    contact_id,
                    response.status_code,
                    code,
                    short_response_body(response),
                )

        except httpx.TimeoutException:
            failed_count += 1

            log.exception(
                "Dispatch timeout | schedule=%s contact=%s",
                schedule_id,
                contact_id,
            )

        except httpx.HTTPError:
            failed_count += 1

            log.exception(
                "Dispatch HTTP error | schedule=%s contact=%s",
                schedule_id,
                contact_id,
            )

        except Exception:
            failed_count += 1

            log.exception(
                "Dispatch crashed | schedule=%s contact=%s",
                schedule_id,
                contact_id,
            )

    log.info(
        "Schedule dispatch finished | schedule=%s contacts=%d "
        "started=%d queued=%d skipped=%d failed=%d",
        schedule_id,
        len(contacts),
        started_count,
        queued_count,
        skipped_count,
        failed_count,
    )

    return accepted_count > 0


# ── Close schedule ────────────────────────────────────────────────────

def close_schedule(
    schedule: dict[str, Any],
    dispatch_ok: bool,
    timezone_name: str,
) -> None:
    """
    סוגר את ניסיון ההפעלה.

    כשל dispatch:
        מחזיר ל-active ומשאיר את next_run_at הנוכחי,
        כדי שה-Scheduler ינסה שוב בבדיקה הבאה.

    once מוצלח:
        completed + next_run_at=null.

    cron מוצלח:
        מחשב next_run_at הבא ומחזיר ל-active.
    """

    schedule_id = schedule.get("id")

    if not schedule_id:
        return

    now = utc_now_iso()

    if not dispatch_ok:
        patch = {
            "status": "active",
            "updated_at": now,
        }

        try:
            result = (
                db.table("schedules")
                .update(patch)
                .eq("id", schedule_id)
                .eq("status", "firing")
                .execute()
            )

            if not result.data:
                log.warning(
                    "Failed restoring schedule after dispatch error | schedule=%s",
                    schedule_id,
                )
            else:
                log.warning(
                    "Schedule restored for retry | schedule=%s",
                    schedule_id,
                )

        except Exception:
            log.exception(
                "Failed restoring schedule | schedule=%s",
                schedule_id,
            )

        return

    schedule_type = str(
        schedule.get("schedule_type") or ""
    ).strip().lower()

    if schedule_type == "once":
        patch = {
            "status": "completed",
            "last_run_at": now,
            "next_run_at": None,
            "updated_at": now,
        }

        result_code = "completed"

    elif schedule_type == "cron":
        next_run_at = compute_next_run(
            cron_expr=schedule.get("cron_expr"),
            timezone_name=timezone_name,
        )

        if next_run_at is None:
            patch = {
                "status": "error",
                "last_run_at": now,
                "next_run_at": None,
                "updated_at": now,
            }

            result_code = "invalid_cron"

        else:
            patch = {
                "status": "active",
                "last_run_at": now,
                "next_run_at": next_run_at,
                "updated_at": now,
            }

            result_code = "rescheduled"

    else:
        patch = {
            "status": "error",
            "last_run_at": now,
            "next_run_at": None,
            "updated_at": now,
        }

        result_code = "unsupported_schedule_type"

    try:
        result = (
            db.table("schedules")
            .update(patch)
            .eq("id", schedule_id)
            .eq("status", "firing")
            .execute()
        )

        if not result.data:
            log.warning(
                "Schedule close updated no rows | schedule=%s result=%s",
                schedule_id,
                result_code,
            )

            return

        log.info(
            "Schedule closed | schedule=%s result=%s next=%s",
            schedule_id,
            result_code,
            patch.get("next_run_at"),
        )

    except Exception:
        log.exception(
            "Failed closing schedule | schedule=%s",
            schedule_id,
        )


# ── Main polling job ──────────────────────────────────────────────────

def check_pending_schedules() -> None:
    schedules = load_due_schedules()

    if not schedules:
        log.debug("No due schedules")
        return

    log.info(
        "Found %d due schedule(s)",
        len(schedules),
    )

    for schedule in schedules:
        schedule_id = schedule.get("id")

        if not schedule_id:
            log.error(
                "Schedule row missing id | row=%s",
                schedule,
            )
            continue

        if not claim_schedule(schedule):
            continue

        timezone_name = load_user_timezone(
            schedule.get("user_id")
        )

        dispatch_ok = False

        try:
            dispatch_ok = fire_schedule(schedule)

        except Exception:
            log.exception(
                "Schedule crashed | schedule=%s",
                schedule_id,
            )

            dispatch_ok = False

        finally:
            close_schedule(
                schedule=schedule,
                dispatch_ok=dispatch_ok,
                timezone_name=timezone_name,
            )


# ── Application entry point ───────────────────────────────────────────

def main() -> None:
    scheduler = BlockingScheduler(
        timezone=timezone.utc,
    )

    scheduler.add_job(
        check_pending_schedules,
        trigger="interval",
        seconds=CHECK_INTERVAL_SECONDS,
        max_instances=1,
        coalesce=True,
        id="check_pending_schedules",
        replace_existing=True,
    )

    log.info(
        "Scheduler started | spine=%s interval=%ds "
        "limit=%d default_timezone=%s",
        SPINE_URL,
        CHECK_INTERVAL_SECONDS,
        SCHEDULE_FETCH_LIMIT,
        DEFAULT_TIMEZONE,
    )

    try:
        scheduler.start()

    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopping")

    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)

        http.close()


if __name__ == "__main__":
    main()
