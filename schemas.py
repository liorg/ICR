"""
models/schemas.py
כל ה-Pydantic models של המערכת
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Literal, Optional, Union
from uuid import UUID
from pydantic import BaseModel, Field


# ================================================================
# Canvas Node Types — מבנה ה-JSON מה-Designer
# ================================================================

class OnFailure(BaseModel):
    action:     Literal["retry", "stop"]
    retryCount: int = 3


# --- SEND nodes ---

class InputNode(BaseModel):
    id:    str
    type:  Literal["input"]
    value: str
    side:  Literal["send"]


class ButtonSelectNode(BaseModel):
    id:         str
    type:       Literal["button_select"]
    buttonId:   str
    buttonText: str
    side:       Literal["send"]


# --- EXPECT nodes ---

class TextExpectNode(BaseModel):
    id:        str
    type:      Literal["text"]
    value:     str
    side:      Literal["expect"]
    matchMode: Literal["any", "contain", "pattern"] = "any"
    onFailure: Optional[OnFailure] = None


class MenuExpectNode(BaseModel):
    id:        str
    type:      Literal["menu"]
    value:     str
    items:     list[dict[str, str]]
    side:      Literal["expect"]
    onFailure: Optional[OnFailure] = None


class ButtonsExpectNode(BaseModel):
    id:        str
    type:      Literal["buttons"]
    header:    str
    buttons:   list[dict[str, str]]
    side:      Literal["expect"]
    onFailure: Optional[OnFailure] = None


CanvasNode = Union[
    InputNode,
    ButtonSelectNode,
    TextExpectNode,
    MenuExpectNode,
    ButtonsExpectNode,
]


# --- Arrow Rules ---

class WaitUntilArrow(BaseModel):
    type:     Literal["waitUntil"]
    maxSkips: int = 1


class TimeArrow(BaseModel):
    type: Literal["time"]
    mins: int = 0
    secs: int = 0

    @property
    def total_seconds(self) -> int:
        return self.mins * 60 + self.secs


class StopArrow(BaseModel):
    type: Literal["stop"]


ArrowRule = Union[WaitUntilArrow, TimeArrow, StopArrow]


# --- Scenario Config (scenarios.config jsonb) ---

class ScenarioConfig(BaseModel):
    fileName:      str = ""
    description:   str = ""
    botContact:    Optional[str] = None
    phone:         str = ""
    interval:      dict[str, int] = Field(default_factory=lambda: {"mins": 0, "secs": 1})
    estimatedTime: Optional[int] = None
    useAutoCalc:   bool = True
    arrowData:     dict[str, Any] = Field(default_factory=dict)  # key=str(index)
    canvas:        list[dict[str, Any]] = Field(default_factory=list)


# ================================================================
# Session State — מצב שיחה פעילה בזיכרון
# ================================================================

class SessionState(BaseModel):
    call_id:       UUID
    scenario_id:   UUID
    phone_id:      UUID
    contact_id:    UUID
    canvas:        list[dict[str, Any]]
    arrow_data:    dict[str, Any]
    current_index: int      = 0
    retry_count:   int      = 0
    skip_count:    int      = 0
    waiting_since: Optional[datetime] = None
    status:        Literal["running", "waiting", "completed", "failed"] = "running"


# ================================================================
# Webhook Payloads — מה DOTNET שולח ל-FastAPI
# ================================================================

class IncomingMessagePayload(BaseModel):
    phone_id:            UUID
    contact_id:          UUID
    message_type:        Literal["text", "image", "audio", "buttons", "list", "document"]
    content:             dict[str, Any]
    whatsapp_message_id: Optional[str] = None
    received_at:         Optional[datetime] = None


class RegisterWebhookPayload(BaseModel):
    phone_id:     UUID
    contact_id:   Optional[UUID] = None
    callback_url: str
    secret_token: str


# ================================================================
# Outgoing Message — מה FastAPI שולח ל-DOTNET Agent
# ================================================================

class OutgoingTextMessage(BaseModel):
    phone_id:   UUID
    contact_id: UUID
    type:       Literal["text"] = "text"
    body:       str


class OutgoingButtonSelectMessage(BaseModel):
    phone_id:            UUID
    contact_id:          UUID
    type:                Literal["button_select"] = "button_select"
    button_id:           str
    button_text:         str
    context_message_id:  Optional[str] = None


OutgoingMessage = Union[OutgoingTextMessage, OutgoingButtonSelectMessage]


# ================================================================
# DB Row models (קריאה מ-Supabase)
# ================================================================

class ScenarioRow(BaseModel):
    id:         UUID
    phone_id:   UUID
    contact_id: Optional[UUID]
    name:       str
    status:     str
    config:     dict[str, Any]


class CallRow(BaseModel):
    id:          UUID
    phone_id:    UUID
    contact_id:  UUID
    scenario_id: UUID
    status:      str
    started_at:  Optional[datetime]
    ended_at:    Optional[datetime]


class ScheduleRow(BaseModel):
    id:           UUID
    phone_id:     UUID
    contact_id:   UUID
    scenario_id:  UUID
    schedule_name: Optional[str]
    schedule_type: str
    status:        str
    run_at:        Optional[datetime]
    cron_expr:     Optional[str]
    next_run:      Optional[datetime]
