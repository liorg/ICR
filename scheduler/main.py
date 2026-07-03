"""
Scheduler — runs in Swarm, triggers Spine dispatch API.

Polls schedules table, dispatches scenarios to Workers via Spine.
"""
import os, logging, httpx
from datetime import datetime, timezone
from apscheduler.schedulers.blocking import BlockingScheduler
from supabase import create_client

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("scheduler")

SPINE_URL = os.getenv("SPINE_URL", "http://scenario_data-spine:8000")
db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

def check_pending_schedules():
    """Find schedules ready to fire, dispatch via Spine."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        res = db.table("schedules") \
            .select("*, scenarios(id, config)") \
            .eq("status", "active") \
            .lte("next_run_at", now) \
            .execute()

        for sched in (res.data or []):
            scenario = sched.get("scenarios")
            if not scenario:
                continue

            # Get contacts to dispatch to
            contacts = db.table("contacts") \
                .select("id, phone, name") \
                .eq("phone_id", sched["phone_id"]) \
                .eq("tag", "active") \
                .execute().data or []

            for contact in contacts:
                try:
                    resp = httpx.post(f"{SPINE_URL}/api/dispatch", json={
                        "phone_id":      sched["phone_id"],
                        "contact_id":    contact["id"],
                        "scenario_id":   scenario["id"],
                        "scenario_json": str(scenario.get("config", "{}")),
                        "contact_phone": contact.get("phone", ""),
                        "contact_name":  contact.get("name", ""),
                    }, timeout=10)

                    result = resp.json()
                    log.info("Dispatch | schedule=%s contact=%s ok=%s",
                             sched["id"], contact["id"], result.get("ok"))

                except Exception as e:
                    log.error("Dispatch failed | schedule=%s contact=%s: %s",
                              sched["id"], contact["id"], e)

            # Update next_run_at based on interval
            # (simplified — extend with cron/interval logic as needed)
            db.table("schedules").update({
                "last_run_at": now,
                "status": "completed",
            }).eq("id", sched["id"]).execute()

    except Exception as e:
        log.error("Schedule check failed: %s", e)

if __name__ == "__main__":
    scheduler = BlockingScheduler()
    interval = int(os.getenv("CHECK_INTERVAL_SECONDS", "30"))
    scheduler.add_job(check_pending_schedules, "interval", seconds=interval)
    log.info("Scheduler started | spine=%s interval=%ds", SPINE_URL, interval)
    try:
        scheduler.start()
    except KeyboardInterrupt:
        scheduler.shutdown()
