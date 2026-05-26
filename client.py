"""
db/client.py
Async Supabase client עם connection pool
כל פעולות ה-DB עוברות דרך כאן
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional
from uuid import UUID

import asyncpg
from asyncpg import Pool, Connection

logger = logging.getLogger(__name__)


# ================================================================
# Pool Singleton
# ================================================================

_pool: Optional[Pool] = None


async def init_pool(dsn: str, min_size: int = 5, max_size: int = 20) -> None:
    """
    קרא פעם אחת בעת startup של FastAPI
    dsn = postgresql://user:pass@host:5432/postgres
    """
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=min_size,
        max_size=max_size,
        command_timeout=30,
    )
    logger.info("DB pool initialized (min=%d, max=%d)", min_size, max_size)


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        logger.info("DB pool closed")


@asynccontextmanager
async def get_conn() -> AsyncGenerator[Connection, None]:
    """Context manager — מחזיר connection מהpool"""
    if not _pool:
        raise RuntimeError("DB pool not initialized — call init_pool() first")
    async with _pool.acquire() as conn:
        yield conn


# ================================================================
# Schedules — SKIP LOCKED לעבודה עם NLB
# ================================================================

async def fetch_due_schedules(limit: int = 10) -> list[dict[str, Any]]:
    """
    שולף schedules שמגיע זמנם — עם SKIP LOCKED
    מבטיח שאף שתי מכונות לא מריצות את אותו schedule
    """
    async with get_conn() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, phone_id, contact_id, scenario_id,
                       schedule_name, schedule_type, status,
                       run_at, cron_expr, next_run
                FROM   public.schedules
                WHERE  status   = 'ready'
                  AND  next_run <= NOW()
                ORDER  BY next_run ASC
                LIMIT  $1
                FOR UPDATE SKIP LOCKED
                """,
                limit,
            )

            if not rows:
                return []

            ids = [r["id"] for r in rows]
            await conn.execute(
                """
                UPDATE public.schedules
                SET    status   = 'running',
                       last_run = NOW()
                WHERE  id = ANY($1::uuid[])
                """,
                ids,
            )

            return [dict(r) for r in rows]


async def update_schedule_next_run(
    schedule_id: UUID,
    next_run,
    status: str = "ready",
) -> None:
    async with get_conn() as conn:
        await conn.execute(
            """
            UPDATE public.schedules
            SET    next_run = $2,
                   status   = $3
            WHERE  id = $1
            """,
            schedule_id, next_run, status,
        )


# ================================================================
# Scenarios
# ================================================================

async def get_scenario(scenario_id: UUID) -> Optional[dict[str, Any]]:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "SELECT id, phone_id, contact_id, name, status, config "
            "FROM public.scenarios WHERE id = $1",
            scenario_id,
        )
        return dict(row) if row else None


# ================================================================
# Calls
# ================================================================

async def create_call(
    phone_id:    UUID,
    contact_id:  UUID,
    scenario_id: UUID,
) -> dict[str, Any]:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.calls
              (phone_id, contact_id, scenario_id, status, started_at)
            VALUES ($1, $2, $3, 'active', NOW())
            RETURNING id, phone_id, contact_id, scenario_id, status, started_at
            """,
            phone_id, contact_id, scenario_id,
        )
        return dict(row)


async def update_call_status(call_id: UUID, status: str) -> None:
    async with get_conn() as conn:
        await conn.execute(
            """
            UPDATE public.calls
            SET    status   = $2,
                   ended_at = CASE WHEN $2 IN ('completed','failed','stopped')
                                   THEN NOW() ELSE ended_at END
            WHERE  id = $1
            """,
            call_id, status,
        )


async def get_active_call(phone_id: UUID, contact_id: UUID) -> Optional[dict[str, Any]]:
    """מחפש שיחה פעילה לפי טלפון + איש קשר"""
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, phone_id, contact_id, scenario_id, status, started_at
            FROM   public.calls
            WHERE  phone_id   = $1
              AND  contact_id = $2
              AND  status     = 'active'
            ORDER  BY started_at DESC
            LIMIT  1
            """,
            phone_id, contact_id,
        )
        return dict(row) if row else None


# ================================================================
# Scenario Runs
# ================================================================

async def create_scenario_run(
    scenario_id: UUID,
    phone_id:    UUID,
    call_id:     UUID,
    config:      dict[str, Any],
) -> dict[str, Any]:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.scenario_runs
              (scenario_id, phone_id, call_id, status, started_at, config)
            VALUES ($1, $2, $3, 'running', NOW(), $4::jsonb)
            RETURNING id, scenario_id, phone_id, call_id, status
            """,
            scenario_id, phone_id, call_id,
            __import__("json").dumps(config),
        )
        return dict(row)


async def update_scenario_run(
    run_id: UUID,
    status: str,
    config: Optional[dict[str, Any]] = None,
) -> None:
    async with get_conn() as conn:
        if config:
            await conn.execute(
                """
                UPDATE public.scenario_runs
                SET    status   = $2,
                       ended_at = CASE WHEN $2 IN ('completed','failed','stopped')
                                       THEN NOW() ELSE ended_at END,
                       config   = $3::jsonb
                WHERE  id = $1
                """,
                run_id, status,
                __import__("json").dumps(config),
            )
        else:
            await conn.execute(
                """
                UPDATE public.scenario_runs
                SET    status   = $2,
                       ended_at = CASE WHEN $2 IN ('completed','failed','stopped')
                                       THEN NOW() ELSE ended_at END
                WHERE  id = $1
                """,
                run_id, status,
            )


# ================================================================
# Messages — שמירת כל הודעה נכנסת/יוצאת
# ================================================================

async def save_message(
    call_id:             UUID,
    phone_id:            UUID,
    contact_id:          UUID,
    sender:              str,         # "bot" או מספר טלפון
    direction:           bool,        # True=יוצא, False=נכנס
    content:             dict[str, Any],
    leaf_id:             Optional[str] = None,
    whatsapp_message_id: Optional[str] = None,
) -> UUID:
    import json
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.messages
              (call_id, phone_id, contact_id, sender, direction,
               content, leaf_id, whatsapp_message_id)
            VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8)
            RETURNING id
            """,
            call_id, phone_id, contact_id,
            sender, direction,
            json.dumps(content, ensure_ascii=False),
            leaf_id, whatsapp_message_id,
        )
        return row["id"]


# ================================================================
# Webhook Registrations
# ================================================================

async def upsert_webhook_registration(
    phone_id:     UUID,
    callback_url: str,
    secret_token: str,
    contact_id:   Optional[UUID] = None,
) -> dict[str, Any]:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.webhook_registrations
              (phone_id, contact_id, callback_url, secret_token, status)
            VALUES ($1, $2, $3, $4, 'active')
            ON CONFLICT (phone_id)
            DO UPDATE SET
              callback_url = EXCLUDED.callback_url,
              secret_token = EXCLUDED.secret_token,
              status       = 'active',
              updated_at   = NOW()
            RETURNING id, phone_id, callback_url, status
            """,
            phone_id, contact_id, callback_url, secret_token,
        )
        return dict(row)


async def get_webhook_registration(phone_id: UUID) -> Optional[dict[str, Any]]:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, phone_id, contact_id, callback_url, secret_token, status
            FROM   public.webhook_registrations
            WHERE  phone_id = $1 AND status = 'active'
            """,
            phone_id,
        )
        return dict(row) if row else None


# ================================================================
# Webhook Messages — log נכנס
# ================================================================

async def save_webhook_message(
    phone_id:            UUID,
    contact_id:          Optional[UUID],
    message_type:        str,
    content:             dict[str, Any],
    whatsapp_message_id: Optional[str] = None,
    call_id:             Optional[UUID] = None,
) -> UUID:
    import json
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.webhook_messages
              (phone_id, contact_id, call_id, message_type,
               content, whatsapp_message_id, status)
            VALUES ($1,$2,$3,$4,$5::jsonb,$6,'received')
            RETURNING id
            """,
            phone_id, contact_id, call_id,
            message_type,
            json.dumps(content, ensure_ascii=False),
            whatsapp_message_id,
        )
        return row["id"]


async def mark_webhook_message(
    message_id:    UUID,
    status:        str,
    call_id:       Optional[UUID] = None,
    error_message: Optional[str]  = None,
) -> None:
    async with get_conn() as conn:
        await conn.execute(
            """
            UPDATE public.webhook_messages
            SET    status        = $2,
                   call_id       = COALESCE($3, call_id),
                   error_message = $4,
                   processed_at  = CASE WHEN $2 != 'received' THEN NOW() ELSE processed_at END
            WHERE  id = $1
            """,
            message_id, status, call_id, error_message,
        )


# ================================================================
# REGEX Patterns
# ================================================================

async def get_regex_pattern(name: str) -> Optional[str]:
    """
    שולף REGEX מטבלת ה-patterns לפי שם (לדוגמא "OTP")
    הטבלה הזו נפרדת ומנוהלת בנפרד לפי מה שציינת
    """
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "SELECT pattern FROM public.regex_patterns WHERE name = $1 AND active = true",
            name,
        )
        return row["pattern"] if row else None
