# scheduler/database.py

from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client

from config import (
    MAX_SCHEDULES_PER_POLL,
    SUPABASE_SERVICE_KEY,
    SUPABASE_URL,
)


db: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_due_schedules() -> list[dict[str, Any]]:
    """
    מחזיר תזמונים פעילים שהגיע זמן ההפעלה שלהם.
    """

    # שמות העמודות חייבים להתאים ל-routers/schedules.py (ה-UI):
    #   next_run / last_run — ולא next_run_at / last_run_at.
    # ה-UI כותב ל-next_run; קריאה מ-next_run_at מחזירה תמיד ריק,
    # ולכן שום תזמון לא נורה מעולם.
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
            "next_run,"
            "status"
        )
        .eq("status", "active")
        .not_.is_("next_run", "null")
        .lte("next_run", utc_now_iso())
        .order("next_run")
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
