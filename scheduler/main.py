"""
Scheduler — טריגר מתוזמן. קורא ל-Spine /api/calls/ensure.

שינויים מהותיים מול הגרסה הקודמת:
  1. /api/dispatch → /api/calls/ensure   (הנתיב הקודם לא היה רשום כלל = 404)
  2. claim אטומי (spine_claim_due_schedules) — שני scheduler-ים לא יורים פעמיים
  3. next_run_at מחושב אמיתי — schedule חוזר לא מת אחרי ירייה אחת
  4. כשל dispatch → ה-schedule חוזר ל-active ולא נשרף
  5. 202 (CALL_QUEUED) הוא הצלחה, לא כישלון
"""
import os, logging, httpx
from apscheduler.schedulers.blocking import BlockingScheduler
from supabase import create_client

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("scheduler")

SPINE_URL = os.getenv("SPINE_URL", "http://scenario_data-spine:8000")
db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def fire_schedule(sched) -> bool:
    """מחזיר True אם לפחות contact אחד נותב בהצלחה."""
    scenario_id = sched.get("scenario_id")
    if not scenario_id:
        log.warning("Schedule %s has no scenario_id", sched["id"])
        return False

    contacts = db.table("contacts") \
        .select("id") \
        .eq("phone_id", sched["phone_id"]) \
        .eq("tag", "active") \
        .execute().data or []

    if not contacts:
        log.info("Schedule %s — no active contacts", sched["id"])
        return True          # אין למי לשלוח; לא כישלון.

    ok = 0
    for contact in contacts:
        try:
            # ensure_call שולף בעצמו את ה-config ואוכף call פעיל יחיד.
            resp = httpx.post(f"{SPINE_URL}/api/calls/ensure", json={
                "phone_id":    sched["phone_id"],
                "contact_id":  contact["id"],
                "scenario_id": scenario_id,
                "priority":    sched.get("priority"),
                "source":      "scheduler",
                "schedule_id": sched["id"],   # ← כדי שההפעלה תופיע בטאב Calls
            }, timeout=15)

            # 201 = נוצר ורץ. תזמון לא נכנס לתור — רק טריגר.
            if resp.status_code == 201:
                ok += 1
                log.info("Dispatch %s | schedule=%s contact=%s",
                         resp.json().get("code"), sched["id"], contact["id"])

            elif resp.status_code == 409:
                # CALL_ALREADY_ACTIVE — שיחה פעילה, התזמון נחסם בכוונה.
                # CONTACT_NOT_ACTIVE  — draft contact.
                # שניהם החלטה תקינה של המערכת, לא תקלה → אין retry.
                code = resp.json().get("code")
                log.info("Skipped %s | schedule=%s contact=%s", code, sched["id"], contact["id"])
                ok += 1

            else:
                log.error("Dispatch rejected | schedule=%s contact=%s status=%s body=%s",
                          sched["id"], contact["id"], resp.status_code, resp.text[:200])

        except Exception as e:
            log.error("Dispatch failed | schedule=%s contact=%s: %s",
                      sched["id"], contact["id"], e)

    return ok > 0


def check_pending_schedules():
    try:
        # claim אטומי — ה-status עובר ל-'firing' באותה טרנזקציה של השליפה.
        claimed = db.rpc("spine_claim_due_schedules", {"p_limit": 50}).execute().data or []
    except Exception as e:
        log.error("Claim failed: %s", e)
        return

    for sched in claimed:
        ok = False
        try:
            ok = fire_schedule(sched)
        except Exception as e:
            log.error("Schedule %s crashed: %s", sched.get("id"), e)
        finally:
            # תמיד סוגרים — אחרת ה-schedule נתקע ב-'firing' לנצח.
            try:
                res = db.rpc("spine_close_schedule", {
                    "p_schedule_id": sched["id"],
                    "p_ok":          ok,
                }).execute().data
                log.info("Schedule %s → %s", sched["id"], res.get("code"))
            except Exception as e:
                log.error("Close failed | schedule=%s: %s", sched["id"], e)


if __name__ == "__main__":
    scheduler = BlockingScheduler()
    interval = int(os.getenv("CHECK_INTERVAL_SECONDS", "30"))
    scheduler.add_job(check_pending_schedules, "interval",
                      seconds=interval, max_instances=1, coalesce=True)
    log.info("Scheduler started | spine=%s interval=%ds", SPINE_URL, interval)
    try:
        scheduler.start()
    except KeyboardInterrupt:
        scheduler.shutdown()
