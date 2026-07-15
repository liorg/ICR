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
        accepted=response.status_code in (200, 201, 202),
        http_status=response.status_code,
        body=body,
    )
