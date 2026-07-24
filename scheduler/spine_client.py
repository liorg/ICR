# scheduler/spine_client.py

from dataclasses import dataclass
from typing import Any

import requests

from config import (
    REQUEST_TIMEOUT_SECONDS,
    SPINE_ENSURE_PATH,
    SPINE_URL,
)


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


def ensure_call(
    schedule: dict[str, Any],
) -> EnsureResult:
    url = f"{SPINE_URL}{SPINE_ENSURE_PATH}"

    payload = {
        "phone_id": schedule["phone_id"],
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
