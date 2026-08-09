"""
Data Spine — reverse proxy דו-כיווני בין Workers לבין ה-HostAgent.

רישום ה-webhook:
  ה-HostAgent שולף רישומים לפי TYPE בלבד (GetActiveWebhooksByTypeAsync),
  בלי סינון phone_id, ושולח את PhoneId בתוך ה-payload.
  לכן הרישום הוא callback גלובלי אחד — לא שורה לכל טלפון.
  רישום פר-טלפון היה גורם ל-fan-out של כל הודעה לכל ה-callbacks.
"""
import os, logging, asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dependencies import get_supabase
from routers import calls, send, incoming, worker_events, dispatch, active_chats

log = logging.getLogger("spine")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s — %(message)s", datefmt="%H:%M:%S")

# תג ה-build. נקבע ב-Dockerfile (ARG/ENV) ומוחזר ב-/version.
# בלי זה אין דרך אמינה לדעת איזה קוד רץ: latest גורם ל-Swarm להריץ
# image ישן, ו-docker cp מעדכן קובץ בלי לטעון אותו מחדש.
BUILD_TAG  = os.getenv("BUILD_TAG", "unknown")
STARTED_AT = datetime.now(timezone.utc).isoformat()

# פנימי ל-overlay — משמש את ה-Workers בלבד.
SPINE_SELF_URL = os.getenv("SPINE_SELF_URL", "http://scenario_data-spine:8000")

# ← הכתובת שה-HostAgent קורא אליה. הוא systemd על ה-host, לא ב-overlay,
#   ולכן הוא לא יכול לפתור "scenario_data-spine". חייב host:published-port.
SPINE_CALLBACK_URL = os.getenv("SPINE_CALLBACK_URL", "http://10.186.0.3:8001")

WEBHOOK_TYPE     = os.getenv("WEBHOOK_TYPE", "trigger")
REGISTER_EVERY_S = int(os.getenv("REGISTER_INTERVAL_SECONDS", "60"))


def _now():
    return datetime.now(timezone.utc)


def ensure_registration(db, callback_url: str, call_type: str):
    """
    Upsert webhook registration — עמיד בפני race conditions.
    on_conflict של Supabase במקום SELECT+INSERT ידני.
    """
    try:
        db.table("webhook_registrations").upsert(
            {
                "callback_url": callback_url,
                "type":         call_type,
                "status":       "active",
                "is_active":    True,
                "created_at":   _now().isoformat(),
            },
            on_conflict="callback_url,type",   # ← unique constraint
        ).execute()
        log.info("[WEBHOOK] Upserted. url=%s type=%s", callback_url, call_type)
    except Exception as e:
        log.error("[WEBHOOK] Upsert failed. url=%s type=%s error=%s", callback_url, call_type, e)


async def registration_loop():
    """
    Reconciliation — לא ריצה חד-פעמית.
    ה-HostAgent הוא production ויכול לעשות restart; הרישום חייב להתאושש לבדו.
    Supabase client סינכרוני — to_thread כדי לא לחסום את ה-event loop.
    """
    callback = f"{SPINE_CALLBACK_URL}/incoming"   # ← גלובלי. phone_id מגיע ב-body.
    db = get_supabase()

    while True:
        try:
            await asyncio.to_thread(ensure_registration, db, callback, WEBHOOK_TYPE)
        except Exception as e:
            log.error("[WEBHOOK] Registration loop error: %s", e)
        await asyncio.sleep(REGISTER_EVERY_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(registration_loop())
    log.info("Spine started | build=%s self=%s callback=%s type=%s",
             BUILD_TAG, SPINE_SELF_URL, SPINE_CALLBACK_URL, WEBHOOK_TYPE)
    yield
    task.cancel()
    log.info("Spine shutting down")


app = FastAPI(title="Data Spine", version="3.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── סדר הרישום קובע ─────────────────────────────────────────────────────
#
# FastAPI מתאים את הנתיב הראשון שתופס. calls.router רשום ראשון ומגדיר
# prefix="/calls", ולכן כל התנגשות איתו מנצחת את מי שנרשם אחריו.
#
# בעבר הוא הגדיר גם POST /calls/{id}/summary, וחטף את כל הסיכומים
# מה-Worker במקום worker_events — ששם נמצאת הסגירה שמקדמת את התור.
# ה-summary הוסר מ-calls.py; לא להחזיר אותו לשם.

app.include_router(calls.router)                     # POST /calls  ·  GET /calls/{id}
app.include_router(send.router)                      # /send/{phone_id}          ← Worker
app.include_router(incoming.router)                  # /incoming                 ← HostAgent
app.include_router(worker_events.router)             # /events /leaves
                                                     # /workers/heartbeat        ← Worker
                                                     # /calls/{id}/summary       ← Worker (סוגר call + מקדם תור)
app.include_router(active_chats.router)              # /active-chats/{phone_id}/...
app.include_router(dispatch.router, prefix="/api")   # /api/calls/ensure         ← Scheduler + incoming
                                                     # /api/calls/sweep          ← Scheduler (SLA)

# promote.router הוסר: הקידום עבר ל-spine_complete_call, שרץ תחת אותו
# advisory lock שסוגר את ה-call. endpoint נפרד לקידום היה מסלול מקביל
# שיכול להתנגש איתו.


@app.get("/")
def root():
    return {"service": "data-spine", "status": "online", "build": BUILD_TAG}


@app.get("/version")
def version():
    """
    אימות שהקוד שרץ הוא הקוד שנפרס.

    grep על קבצים בקונטיינר לא אמין: docker cp מעדכן את הקובץ אבל
    Python כבר טען את המודול לזיכרון. started_at מגלה אם היה restart
    בפועל, ו-ensure_call_unified בודק את החתימה שה-Spine באמת מייבא.
    """
    from inspect import signature
    from services.calls import ensure_call

    params = signature(ensure_call).parameters

    return {
        "build":      BUILD_TAG,
        "started_at": STARTED_AT,
        "unified_ensure_call": "scenario_id" in params,
        "routes": sorted(
            {r.path for r in app.routes if hasattr(r, "path")}
        ),
    }
