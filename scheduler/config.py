# scheduler/config.py

import os


SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

SPINE_URL = os.getenv(
    "SPINE_URL",
    "http://scenario_data-spine:8000",
).rstrip("/")

SPINE_ENSURE_PATH = os.getenv(
    "SPINE_ENSURE_PATH",
    "/api/calls/ensure",
)

DEFAULT_TIMEZONE = os.getenv(
    "DEFAULT_TIMEZONE",
    "Asia/Jerusalem",
)

POLL_SECONDS = max(
    int(os.getenv("POLL_SECONDS", "5")),
    1,
)

REQUEST_TIMEOUT_SECONDS = max(
    int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20")),
    1,
)

MAX_SCHEDULES_PER_POLL = max(
    int(os.getenv("MAX_SCHEDULES_PER_POLL", "50")),
    1,
)

# תדירות בדיקת ה-SLA בשניות (סגירת calls פג-תוקף).
# ברירת מחדל בקוד; הערך התפעולי מנוהל ב-bot_config (sla.check_seconds).
SLA_CHECK_SECONDS = max(
    int(os.getenv("SLA_CHECK_SECONDS", "60")),
    10,
)
