"""
POST /send/{phone_id} — שליחה דרך ה-HostAgent, ושמירה ב-messages.

חוזה ה-HostAgent (אומת מול Swagger):
    POST /api/phones/{phoneId}/send/{type}
    SendTextRequest     → { "jid": "...", "text": "..." }
    SendTemplateRequest → { "jid": "...", "name": "...", "lang": "he",
                            "templateId": "uuid|null", "params": { "header": [], "body": [] } }

הסוגים הנתמכים בפועל:
    text · buttons · list · button-response · list-response · ping · status · template
אין image/file/audio — כל שליחת מדיה תחזיר 404.

ה-Spine לא מחולל טקסט. במסלול template הוא מעביר שם + פרמטרים as-is;
ה-HostAgent הוא היחיד שקורא את phone_templates ובונה את ההודעה.

אין spine_webhooks. יש HostAgent אחד שמנתב לפי phoneId → env.
"""
import os, logging
from typing import Optional, Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dependencies import get_supabase

router = APIRouter(prefix="/send", tags=["send"])
log = logging.getLogger("spine.send")

HOST_AGENT_URL = os.getenv("HOST_AGENT_URL", "http://10.186.0.2:5000")
SEND_PATH      = os.getenv("HOST_AGENT_SEND_PATH", "/api/phones/{phone_id}/send/{type}")

# מה שה-HostAgent באמת חושף. כל השאר → 404.
SUPPORTED = {"text", "buttons", "list", "button-response", "list-response",
             "ping", "status", "template"}


class SendReq(BaseModel):
    contact_id:    str
    contact_phone: str                      # ה-jid: lid אם קיים, אחרת number
    message_type:  str = "text"
    content:       Optional[str] = None
    metadata:      Optional[Any] = None
    scenario_id:   Optional[str] = None    # ה-Worker שולח אותו; נדרש למפתח ה-upsert
    leaf_id:       Optional[str] = None
    call_id:       Optional[str] = None

    # { "id": "uuid|null", "name": "...", "lang": "he",
    #   "parameters": { "header": [...], "body": [...] } }
    # הפרמטרים כבר מפוענחים ע"י ה-Worker (InterpolateVars).
    template:      Optional[dict] = None


@router.post("/{phone_id}")
async def send_message(phone_id: str, req: SendReq):
    db = get_supabase()

    if req.message_type not in SUPPORTED:
        raise HTTPException(400, f"unsupported message_type '{req.message_type}' "
                                 f"(HostAgent supports: {', '.join(sorted(SUPPORTED))})")

    # ── jid ────────────────────────────────────────────────────────────
    #
    # contact_phone מגיע מ-spine_ensure_call בלי סיומת, והוא LID אם קיים
    # ואחרת מספר. אם נשלח LID עם @s.whatsapp.net, WhatsApp מקבל את
    # הבקשה אך לא מוצא נמען — ההודעה נעלמת בשקט בלי message_status.
    jid = req.contact_phone
    if "@" not in jid:
        jid = f"{jid}@lid" if len(jid) >= 14 else f"{jid}@s.whatsapp.net"

    # ── payload ────────────────────────────────────────────────────────
    meta = req.metadata or {}

    if req.message_type == "template":
        # SendTemplateRequest של ה-HostAgent. אין text.
        tpl    = req.template or meta.get("template") or {}
        name   = tpl.get("name") or ""
        tpl_id = tpl.get("id")

        if not name and not tpl_id:
            raise HTTPException(400, "template requires name or id")

        payload = {
            "jid":        jid,
            "name":       name,
            "lang":       tpl.get("lang"),
            "templateId": tpl_id,
            "params":     tpl.get("parameters") or {},
        }
    else:
        payload = {"jid": jid, "text": req.content or ""}

        if req.message_type == "buttons":
            payload["buttons"] = meta.get("buttons", [])
        elif req.message_type == "list":
            payload["sections"] = meta.get("sections", [])

    url = HOST_AGENT_URL.rstrip("/") + SEND_PATH.format(
        phone_id=phone_id, type=req.message_type)

    # ── שליחה ──────────────────────────────────────────────────────────
    # resp_text נשמר בנפרד כדי שיהיה זמין ב-raise שאחרי ה-try.
    # בלעדיו, כשל רשת היה מפיל את ה-handler ב-UnboundLocalError
    # במקום להחזיר 502 נקי.
    wa_id, status, resp_text = None, "failed", ""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp      = await c.post(url, json=payload)
            resp_text = resp.text or ""
            result    = resp.json() if resp.content else {}
            wa_id     = result.get("messageId") or (result.get("key") or {}).get("id")
            status    = "sent" if resp.status_code == 200 else "failed"

            if status == "failed":
                log.error("Send rejected | phone=%s jid=%s url=%s status=%s body=%s",
                          phone_id, jid, url, resp.status_code, resp_text[:200])

                if req.message_type == "template":
                    log.error("Template rejected | phone=%s tpl=%s lang=%s params=%s",
                              phone_id, payload.get("name"), payload.get("lang"),
                              {k: len(v) for k, v in (payload.get("params") or {}).items()})
    except Exception as e:
        resp_text = str(e)
        log.error("Send failed | phone=%s url=%s: %s", phone_id, url, e)

    if req.leaf_id and req.call_id and wa_id:
        # אותו מפתח שבו משתמש worker_events — uq_spine_leaf_messages_wa
        # (scenario_id, call_id, leaf_id, whatsapp_message_id).
        # upsert ולא insert, כי PATCH /leaves/status עשוי להקדים אותנו.
        # message_id נשאר NULL ומושלם ע"י ה-webhook או ע"י update_leaf.
        try:
            db.table("spine_leaf_messages").upsert(
                {
                    "scenario_id": req.scenario_id,
                    "call_id": req.call_id,
                    "leaf_id": req.leaf_id,
                    "whatsapp_message_id": wa_id,
                },
                on_conflict="scenario_id,call_id,leaf_id,whatsapp_message_id",
            ).execute()
        except Exception:
            log.exception("Failed linking outgoing leaf | call=%s leaf=%s whatsapp=%s",
                          req.call_id, req.leaf_id, wa_id)

    if status == "failed":
        # ה-HostAgent מחזיר 404/409/501 עם detail מפורש במסלול התבנית
        # (לא נמצאה / לא מאושרת / לא מפורסמת / פרמטרים חסרים).
        raise HTTPException(502, f"HostAgent send failed for phone {phone_id}: {resp_text[:300]}")

    return {"ok": True, "message_id": wa_id, "wa_message_id": wa_id, "status": status}
