"""
POST /incoming

Webhook מסוג Trigger מה-HostAgent.

ה-HostAgent כבר שמר את ההודעה בטבלת messages,
לכן Spine לא יוצר אותה שוב.

messageId:
    מזהה פנימי של הרשומה בטבלת messages.

whatsAppMessageId:
    מזהה ההודעה המקורי של WhatsApp.

חלוקת אחריות:
    outgoing:
        Spine משלים את הקישור בין leaf לבין messages.

        הודעת חותמת סיום של הבוט אינה הודעת תרחיש רגילה:
        היא מוחזרת ב-200 ללא קישור Leaf, ללא Trigger וללא Worker.

    incoming:
        Spine מנתב את ההודעה ל-Worker.
        ה-Worker מטפל בהתאמת ה-leaf ובסטטוס העסקי.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from dependencies import get_supabase
from services.calls import (
    ensure_call,
    entry_payload,
    is_call_end_marker,
    send_to_worker,
)

router = APIRouter(prefix="/incoming", tags=["incoming"])
log = logging.getLogger("spine.incoming")



def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IncomingDispatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # מזהה פנימי בטבלת messages.
    message_id: str = Field(alias="messageId")

    # C# WhatsAppMessageId נהפך ב-camelCase ל-whatsAppMessageId.
    whatsapp_message_id: str = Field(alias="whatsAppMessageId")

    phone_id: str = Field(alias="phoneId")
    contact_id: str = Field(alias="contactId")

    # true = incoming, false = outgoing
    direction: bool


@router.post("")
@router.post("/")
async def handle_incoming(body: IncomingDispatch):
    db = get_supabase()

    log.info(
        "[INCOMING] phone=%s contact=%s message=%s "
        "whatsapp=%s direction=%s",
        body.phone_id,
        body.contact_id,
        body.message_id,
        body.whatsapp_message_id,
        "incoming" if body.direction else "outgoing",
    )

    message = _load_message(
        db,
        message_id=body.message_id,
        whatsapp_message_id=body.whatsapp_message_id,
        phone_id=body.phone_id,
        contact_id=body.contact_id,
    )

    if not message:
        log.warning(
            "[INCOMING] message not found | "
            "phone=%s contact=%s message=%s whatsapp=%s",
            body.phone_id,
            body.contact_id,
            body.message_id,
            body.whatsapp_message_id,
        )

        raise HTTPException(
            status_code=404,
            detail=(
                "Message not found for supplied messageId, "
                "whatsAppMessageId, phoneId and contactId"
            ),
        )

    # ── Call end marker ────────────────────────────────────────────────
    #
    # ה-Summary של Spine שלח את החותמת דרך HostAgent.
    # רק לאחר שה-webhook חוזר עם message_id ו-whatsapp_message_id
    # מעדכנים את ה-call — זו הוכחה שההודעה עברה במסלול הרגיל.
    if not body.direction and is_call_end_marker(message):
        marker_call_id = message.get("call_id")
        updated_data: list[dict] = []

        if marker_call_id:
            marker_patch = {
                "last_send_time": message.get("sent_at") or _utc_now(),
                "last_whatsapp_id": body.whatsapp_message_id,
                "last_message_id": body.message_id,
            }

            updated = (
                db.table("calls")
                .update(marker_patch)
                .eq("id", marker_call_id)
                .eq("phone_id", body.phone_id)
                .eq("contact_id", body.contact_id)
                .execute()
            )

            updated_data = updated.data or []

            if not updated_data:
                log.warning(
                    "[CALL-END] call not updated | "
                    "call=%s phone=%s contact=%s message=%s whatsapp=%s",
                    marker_call_id,
                    body.phone_id,
                    body.contact_id,
                    body.message_id,
                    body.whatsapp_message_id,
                )
            else:
                log.info(
                    "[CALL-END] webhook confirmed | "
                    "call=%s message=%s whatsapp=%s",
                    marker_call_id,
                    body.message_id,
                    body.whatsapp_message_id,
                )
        else:
            log.warning(
                "[CALL-END] marker has no call_id | message=%s whatsapp=%s",
                body.message_id,
                body.whatsapp_message_id,
            )

        return {
            "ok": True,
            "ignored": True,
            "reason": "call_end_marker",
            "incoming": False,
            "call_id": marker_call_id,
            "message_id": body.message_id,
            "whatsapp_message_id": body.whatsapp_message_id,
            "call_updated": bool(updated_data),
            "leaf_links_completed": 0,
            "routed_to_active": False,
            "triggered": 0,
        }

    # ── Outgoing ───────────────────────────────────────────────────────
    #
    # הודעה יוצאת אינה reply עסקי לתרחיש.
    # ה-Worker לא צריך לקבל אותה כאירוע entryMessage.
    #
    # בזמן השליחה Spine כבר שמר ב-spine_leaf_messages:
    #
    #   leaf_id
    #   whatsapp_message_id
    #   message_id = NULL
    #
    # לאחר שה-HostAgent שמר את ההודעה בטבלת messages,
    # משלימים כאן את message_id לפי whatsapp_message_id.
    if not body.direction:
        linked_count = await _complete_leaf_message_links(
            whatsapp_message_id=body.whatsapp_message_id,
            internal_message_id=body.message_id,
        )

        return {
            "ok": True,
            "incoming": False,
            "message_id": body.message_id,
            "whatsapp_message_id": body.whatsapp_message_id,
            "leaf_links_completed": linked_count,
            "routed_to_active": False,
            "triggered": 0,
        }

    # ── Incoming ───────────────────────────────────────────────────────
    #
    # הודעה נכנסת היא אירוע עסקי.
    # מעבירים אותה ל-Worker עם שני המזהים.
    msg_type, content, metadata = _normalize_message(message)

    first_message = {
        "message_id": body.message_id,
        "whatsapp_message_id": body.whatsapp_message_id,
        "type": msg_type,
        "data": (
            {"text": content or ""}
            if msg_type == "text"
            else metadata
        ),
    }

    active_call = _get_active_call(
        db,
        phone_id=body.phone_id,
        contact_id=body.contact_id,
    )

    delivered_to_active = False

    if active_call:
        delivered_to_active = await send_to_worker(
            db,
            body.phone_id,
            entry_payload(
                call_id=active_call["id"],
                scenario_id=active_call.get("scenario_id"),
                contact_id=body.contact_id,
                message_id=body.message_id,
                whatsapp_message_id=body.whatsapp_message_id,
                msg_type=msg_type,
                content=content,
                metadata=metadata,
            ),
        )

    scenarios = _load_trigger_scenarios(
        db,
        phone_id=body.phone_id,
    )

    results: list[dict[str, Any]] = []

    for scenario in scenarios:
        result = await ensure_call(
            db,
            phone_id=body.phone_id,
            contact_id=body.contact_id,
            scenario_id=scenario["id"],
            priority=scenario.get("priority"),
            source="trigger",
            first_message=first_message,
        )

        delivered = False

        if result.needs_worker:
            delivered = await send_to_worker(
                db,
                result.phone_id or body.phone_id,
                result.worker_payload,
            )

            result.with_delivery(delivered)

        results.append(
            {
                "scenario_id": scenario["id"],
                "priority": scenario.get("priority"),
                "call_id": result.call_id,
                "code": result.code,
                "status": result.body.get("status"),
                "http_status": result.http_status,
                "delivered": delivered,
            }
        )

    return {
        "ok": True,
        "incoming": True,
        "message_id": body.message_id,
        "whatsapp_message_id": body.whatsapp_message_id,
        "active_call_id": active_call["id"] if active_call else None,
        "routed_to_active": delivered_to_active,
        "triggered": len(results),
        "calls": results,
    }


def _load_message(
    db,
    message_id: str,
    whatsapp_message_id: str,
    phone_id: str,
    contact_id: str,
) -> Optional[dict]:
    """
    טוען את ההודעה שכבר נוצרה על ידי HostAgent.

    מאמת שכל המזהים מתאימים לאותה רשומה:
      - messages.id
      - messages.whatsapp_message_id
      - phone_id
      - contact_id
    """

    result = (
        db.table("messages")
        # messages אין בה payload/event — ה-type יושב בתוך content.
        .select(
            # messages אין בה metadata/message_type — הן מפילות את
            # השאילתה ב-42703 (→ 500 על ה-webhook הנכנס). הן גם לא
            # נצרכות: אף שורה בקובץ לא קוראת אותן. ה-type יושב ב-content.
            "id, phone_id, contact_id, call_id, content, "
            "whatsapp_message_id, direction, status, "
            "sent_at, media_url"
        )
        .eq("id", message_id)
        .eq("whatsapp_message_id", whatsapp_message_id)
        .eq("phone_id", phone_id)
        .eq("contact_id", contact_id)
        .limit(1)
        .execute()
        .data
    )

    return result[0] if result else None


def _get_active_call(
    db,
    phone_id: str,
    contact_id: str,
) -> Optional[dict]:
    result = (
        db.table("calls")
        .select("id, scenario_id, started_at")
        .eq("phone_id", phone_id)
        .eq("contact_id", contact_id)
        .eq("status", "running")
        .order("started_at", desc=True)
        .limit(1)
        .execute()
        .data
    )

    return result[0] if result else None


def _load_trigger_scenarios(
    db,
    phone_id: str,
) -> list[dict]:
    return (
        db.table("scenarios")
        .select("id, priority")
        .eq("phone_id", phone_id)
        .eq("status", "active")
        .eq("event_type", "trigger")
        .order("priority")
        .order("created_at")
        .execute()
        .data
        or []
    )


async def _complete_leaf_message_links(
    whatsapp_message_id: str,
    internal_message_id: str,
) -> int:
    """
    משלים את הקישור הטכני עבור הודעה יוצאת.

        לפני:   leaf-1 | WA-123 | NULL
        אחרי:   leaf-1 | WA-123 | MSG-789

    כל השורות עם אותו whatsapp_message_id מתעדכנות — קשר רבים-לרבים.

    ה-UPDATE נבנה ידנית מול PostgREST ולא דרך postgrest-py:
      • .is_() לא מייצר את הפילטר is.null כמו שצריך
      • בלי Prefer: return=representation התשובה ריקה, אז אי אפשר לספור
    זה בדיוק ה-PATCH שאומת ידנית ב-curl.
    """
    if not whatsapp_message_id or not internal_message_id:
        return 0

    base = os.environ["SUPABASE_URL"].rstrip("/")
    key  = os.environ["SUPABASE_SERVICE_KEY"]

    url = (
        f"{base}/rest/v1/spine_leaf_messages"
        f"?whatsapp_message_id=eq.{whatsapp_message_id}"
        f"&message_id=is.null"
    )

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.patch(
                url,
                json={"message_id": internal_message_id},
                headers={
                    "apikey":        key,
                    "Authorization": f"Bearer {key}",
                    "Content-Type":  "application/json",
                    "Prefer":        "return=representation",
                },
            )
    except Exception:
        log.exception("[LEAF-LINK] request failed | whatsapp=%s", whatsapp_message_id)
        return 0

    rows = []
    if r.status_code < 400 and r.content:
        try:
            parsed = json.loads(r.content.decode("utf-8"))
            rows = parsed if isinstance(parsed, list) else []
        except Exception:
            rows = []

    linked_count = len(rows)

    log.info(
        "[LEAF-LINK] whatsapp=%s message=%s completed=%s status=%s",
        whatsapp_message_id,
        internal_message_id,
        linked_count,
        r.status_code,
    )

    return linked_count


def _normalize_message(
    message: dict,
) -> tuple[str, Optional[str], dict[str, Any]]:
    # content הוא JSON string: {"text": "...", "type": "text"}
    merged: dict[str, Any] = _as_dict(message.get("content"))

    msg_type = str(merged.get("type") or "text")

    normalized_type = {
        "conversation": "text",
        "extendedTextMessage": "text",
        "listMessage": "list_message",
        "buttonsMessage": "buttons",
        "buttonsResponseMessage": "button_response",
        "listResponseMessage": "list_response",
        "imageMessage": "image",
        "videoMessage": "video",
        "audioMessage": "audio",
        "documentMessage": "document",
    }.get(msg_type, msg_type)

    content = (
        _to_optional_string(merged.get("text"))
        or _to_optional_string(merged.get("caption"))
        or _to_optional_string(merged.get("displayText"))
        or _to_optional_string(merged.get("title"))
        or _to_optional_string(merged.get("description"))
    )

    if normalized_type == "text":
        metadata = {
            "text": content or "",
        }
    else:
        metadata = {
            **merged,
            "media_url": message.get("media_url"),
        }

    return normalized_type, content, metadata


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    return {}


def _to_optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None

    text = str(value).strip()
    return text or None
