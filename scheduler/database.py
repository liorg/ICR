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
            "priority,"
            "next_run_at,"
            "status"
        )
        .eq("status", "active")
        .not_.is_("next_run_at", "null")
        .lte("next_run_at", utc_now_iso())
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
