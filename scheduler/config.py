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


# ── sweep ─────────────────────────────────────────────────────────────
#
# ה-Scheduler פונה ל-Spine ולא ל-DB ישירות: הסגירה חייבת לעבור דרך
# spine_complete_call, שמקדם את ה-queued הבא ושולח לו init. UPDATE
# ישיר היה משאיר שורות queued יתומות והקונטקט נשאר חסום.

SPINE_SWEEP_PATH = os.getenv(
    "SPINE_SWEEP_PATH",
    "/api/calls/sweep",
)

# חייב להיות ערך שקיים ב-FINAL_CALL_STATUSES בצד ה-Spine.
SWEEP_STATUS = os.getenv(
    "SWEEP_STATUS",
    "expired",
)

# תקרת calls לסבב אחד. ה-Spine חוסם ב-200 בכל מקרה.
SWEEP_LIMIT = max(
    int(os.getenv("SWEEP_LIMIT", "50")),
    1,
)

# כל call שנסגר גורר קריאת RPC ואולי init ל-Worker,
# ולכן ה-timeout נדיב יחסית.
SWEEP_TIMEOUT_SECONDS = max(
    float(os.getenv("SWEEP_TIMEOUT_SECONDS", "60")),
    5.0,
)
