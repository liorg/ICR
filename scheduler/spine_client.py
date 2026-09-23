# scheduler/spine_client.py

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from config import (
    REQUEST_TIMEOUT_SECONDS,
    SPINE_ENSURE_PATH,
    SPINE_URL,
)


log = logging.getLogger("scheduler.spine_client")

# Worker מדווח heartbeat כל 30 שניות → מרווח לפספוס של שניים.
WORKER_STALE_SECONDS = int(os.getenv("WORKER_STALE_SECONDS", "75"))


@dataclass
class EnsureResult:
    accepted: bool
    http_status: int
    body: dict[str, Any]

    @property
    def blocked(self) -> bool:
        """
        409 = יש כבר call פעיל לאיש הקשר.

        זה לא כשל: source='scheduler' נחסם בכוונה ולא נכנס לתור.
        התזמון פשוט מדלג על הירייה הזו וממשיך לזמן הבא — אחרת
        next_run לא מתעדכן והוא ינסה שוב כל POLL_SECONDS לנצח.
        """
        return self.http_status == 409

    @property
    def worker_not_active(self) -> bool:
        """
        ה-Worker של הטלפון לא פעיל (אין heartbeat טרי ב-phone_workers).
        לא נוצר call. accepted=False → next_run לא מתקדם, ונסה שוב
        בסבב הבא עד שה-Worker חוזר.
        """
        return self.body.get("code") == "WORKER_NOT_ACTIVE"


# ── Worker health (DB) ────────────────────────────────────────────────
def worker_is_active(phone_id: str) -> bool:
    """
    בודק ב-phone_workers שה-Worker running וה-heartbeat טרי.

    ה-Worker מדווח ל-Spine, וה-Spine כותב ל-phone_workers
    (online → running, offline → offline). כאן רק קוראים — ה-Scheduler
    לא מדבר עם ה-Worker ישירות.

    בלי SUPABASE_URL / SUPABASE_SERVICE_KEY, או בשגיאת רשת, מחזיר True
    (fail-open): הבדיקה היא שכבת הגנה, ו-dispatch ב-Spine עדיין מנסה
    שוב ומשחרר slot אם ה-init לא נמסר.
    """
    base = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_KEY") or ""

    if not base or not key:
        log.warning("[WORKER-CHECK] SUPABASE_URL/SUPABASE_SERVICE_KEY missing — skipping check")
        return True

    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=WORKER_STALE_SECONDS)
    ).isoformat()

    try:
        r = requests.get(
            f"{base}/rest/v1/phone_workers",
            params={
                "select": "service_name",
                "phone_id": f"eq.{phone_id}",
                "status": "eq.running",
                "updated_at": f"gt.{cutoff}",
                "limit": "1",
            },
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        log.warning("[WORKER-CHECK] request failed — skipping check | phone=%s error=%s", phone_id, exc)
        return True

    if r.status_code >= 400:
        log.warning(
            "[WORKER-CHECK] query failed — skipping check | phone=%s status=%s body=%s",
            phone_id, r.status_code, r.text[:200],
        )
        return True

    try:
        rows = r.json()
    except ValueError:
        return True

    return bool(rows)


def ensure_call(
    schedule: dict[str, Any],
) -> EnsureResult:
    phone_id = schedule["phone_id"]

    if not worker_is_active(phone_id):
        log.warning(
            "[SCHEDULER] worker not active, skip | phone=%s schedule=%s",
            phone_id, schedule.get("id"),
        )
        return EnsureResult(
            accepted=False,
            http_status=0,
            body={
                "status": "skipped",
                "code": "WORKER_NOT_ACTIVE",
                "phone_id": phone_id,
                "schedule_id": schedule.get("id"),
            },
        )

    url = f"{SPINE_URL}{SPINE_ENSURE_PATH}"

    payload = {
        "phone_id": phone_id,
        "contact_id": schedule["contact_id"],
        "scenario_id": schedule["scenario_id"],
        "priority": schedule.get("priority"),
        "source": "scheduler",
        "first_message": None,
        "schedule_id": schedule["id"],
    }

    response = requests.post(
        url,
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    try:
        body = response.json()
    except ValueError:
        body = {
            "raw": response.text,
        }

    return EnsureResult(
        # 409 נחשב מקובל: ה-call נחסם בכוונה, לא נכשל.
        accepted=response.status_code in (200, 201, 202, 409),
        http_status=response.status_code,
        body=body,
    )


@dataclass
class PromoteResult:
    accepted: bool
    http_status: int
    body: dict[str, Any]

    @property
    def promoted(self) -> bool:
        return self.body.get("code") == "PROMOTED"

    @property
    def busy(self) -> bool:
        """
        409 = או שיש running לאותו איש קשר, או שה-call כבר לא בתור.
        בשני המקרים פשוט מדלגים ומנסים בסבב הבא.
        """
        return self.http_status == 409


def promote_call(call_id: str) -> PromoteResult:
    """
    מבקש מה-Spine לקדם call מהתור.

    ה-Scheduler מחליט *מה* לקדם; ה-Spine מאמת שאין running,
    מקדם ושולח init ל-Worker. האימות חייב להיות שם — כאן אין
    נעילה, ולכן בדיקה מקומית הייתה משאירה חלון למרוץ.
    """
    url = f"{SPINE_URL}/api/calls/{call_id}/promote"

    response = requests.post(
        url,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text}

    return PromoteResult(
        # 409 מקובל: התנגשות צפויה, לא כשל.
        accepted=response.status_code in (200, 409),
        http_status=response.status_code,
        body=body,
    )
