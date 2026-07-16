"""
POST /incoming

Webhook מסוג Trigger מה-AgentHost.
ה-AgentHost כבר שמר את ההודעה בטבלת messages, ולכן Spine לא יוצר אותה שוב.
"""

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from dependencies import get_supabase
from services.calls import ensure_call, entry_payload, send_to_worker

router = APIRouter(prefix="/incoming", tags=["incoming"])
log = logging.getLogger("spine.incoming")


class IncomingDispatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message_id: str = Field(alias="messageId")
    phone_id: str = Field(alias="phoneId")
    contact_id: str = Field(alias="contactId")
    direction: bool


@router.post("")
@router.post("/")
async def handle_incoming(body: IncomingDispatch):
    db = get_supabase()

    message = _load_message(
        db,
        message_id=body.message_id,
        phone_id=body.phone_id,
        contact_id=body.contact_id,
    )

    if not message:
        raise HTTPException(
            status_code=404,
            detail="Message not found for supplied messageId, phoneId and contactId",
        )

    whatsapp_message_id = message.get("whatsapp_message_id")

    linked_count = _complete_leaf_message_links(
        db,
        whatsapp_message_id=whatsapp_message_id,
        internal_message_id=message["id"],
    )

    # direction=true הוא incoming לפי AddMessageAsync/DB.
    if not body.direction:
        return {
            "ok": True,
            "incoming": False,
            "message_id": body.message_id,
            "whatsapp_message_id": whatsapp_message_id,
            "leaf_links_completed": linked_count,
            "routed_to_active": False,
            "triggered": 0,
        }

    msg_type, content, metadata = _normalize_message(message)

    first_message = {
        "type": msg_type,
        "data": {"text": content or ""} if msg_type == "text" else metadata,
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
                active_call["id"],
                active_call.get("scenario_id"),
                body.contact_id,
                whatsapp_message_id or body.message_id,
                msg_type,
                content,
                metadata,
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

        results.append({
            "scenario_id": scenario["id"],
            "priority": scenario.get("priority"),
            "call_id": result.call_id,
            "code": result.code,
            "status": result.body.get("status"),
            "http_status": result.http_status,
            "delivered": delivered,
        })

    return {
        "ok": True,
        "incoming": True,
        "message_id": body.message_id,
        "whatsapp_message_id": whatsapp_message_id,
        "leaf_links_completed": linked_count,
        "active_call_id": active_call["id"] if active_call else None,
        "routed_to_active": delivered_to_active,
        "triggered": len(results),
        "calls": results,
    }



def _load_message(db, message_id: str, phone_id: str, contact_id: str) -> Optional[dict]:
    result = (
        db.table("messages")
        .select(
            "id, phone_id, contact_id, call_id, content, payload, "
            "whatsapp_message_id, direction, status, event, sent_at, media_url"
        )
        .eq("id", message_id)
        .eq("phone_id", phone_id)
        .eq("contact_id", contact_id)
        .limit(1)
        .execute()
        .data
    )

    return result[0] if result else None


def _get_active_call(db, phone_id: str, contact_id: str) -> Optional[dict]:
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


def _load_trigger_scenarios(db, phone_id: str) -> list[dict]:
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


def _complete_leaf_message_links(
    db,
    whatsapp_message_id: Optional[str],
    internal_message_id: str,
) -> int:
    if not whatsapp_message_id:
        return 0

    result = (
        db.table("spine_leaf_messages")
        .update({"message_id": internal_message_id})
        .eq("whatsapp_message_id", whatsapp_message_id)
        .execute()
    )

    return len(result.data or [])


def _normalize_message(message: dict) -> tuple[str, Optional[str], dict[str, Any]]:
    content_obj = _as_dict(message.get("content"))
    payload_obj = _as_dict(message.get("payload"))

    merged: dict[str, Any] = {**payload_obj, **content_obj}

    msg_type = str(merged.get("type") or message.get("event") or "text")

    normalized_type = {
        "conversation": "text",
        "listMessage": "list_message",
        "buttonsMessage": "buttons",
        "buttonsResponseMessage": "button_response",
        "listResponseMessage": "list_response",
    }.get(msg_type, msg_type)

    content = (
        _to_optional_string(merged.get("text"))
        or _to_optional_string(merged.get("caption"))
        or _to_optional_string(merged.get("displayText"))
        or _to_optional_string(merged.get("title"))
        or _to_optional_string(merged.get("description"))
    )

    metadata = {"text": content or ""} if normalized_type == "text" else merged
    return normalized_type, content, metadata


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}

    return {}


def _to_optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None

    text = str(value)
    return text if text else None

