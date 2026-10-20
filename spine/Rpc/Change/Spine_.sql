-- ═══════════════════════════════════════════════════════════════════════
-- spine_ensure_call — נקודת הכניסה היחידה ליצירת calls
--
-- שני מצבי הפעלה, אותו תהליך עסקי:
--
--   p_scenario_id = null  →  מצב trigger. ה-RPC בוחר בעצמו את כל
--                            תרחישי ה-trigger הפעילים של הטלפון.
--                            הראשון לפי priority = running, השאר queued.
--
--   p_scenario_id = uuid  →  תרחיש בודד מפורש (scheduler / api / manual).
--
-- הכללים המשותפים — נכתבים פעם אחת ותקפים לשני המצבים:
--
--   1. הקונטקט חייב tag='active'.
--   2. advisory lock על (phone_id, contact_id) — אין מרוץ.
--   3. "call פתוח" = status in ('running','queued').
--
-- מדיניות מול call פתוח:
--
--   trigger              → denied. לא נרשמת שורה בכלל.
--
--   scheduler/api/manual → aborted, ולא נכנס לתור.
--                          בנוסף מרוקן את התור: כל ה-queued הופכים
--                          ל-aborted עם PREEMPTED_BY_<SOURCE>, כי מה
--                          שנקבע לרוץ אחרי ה-running כבר לא רלוונטי.
--                          status_reason = SCHEDULER_INSTANCE_EXISTS אם
--                          ה-running עצמו scheduler, אחרת ACTIVE_CALL_EXISTS.
--
--   ה-running לא מופסק בשום מקרה — עד summary או עד expired.
--
-- SLA: started_at ו-expected_end נקבעים אך ורק ברגע המעבר ל-running.
--      שורת queued נוצרת בלי שניהם; הקידום ב-spine_complete_call קובע.
--
-- priority: משמש לסידור באטץ' של triggers — כשיש כמה תרחישי trigger
--            על אותו טלפון, הוא קובע מי רץ ראשון ומי ממתין בתור.
--            בתרחיש בודד הוא נשמר לתיעוד ולא משפיע.
--
-- תיעוד: first_message / first_message_at / event_id זהים לכל השורות
--        שנוצרו באותה קריאה.
-- ═══════════════════════════════════════════════════════════════════════



-- ── אינדקסים ──────────────────────────────────────────────────────────
create unique index if not exists uniq_running_call_per_contact
    on public.calls (phone_id, contact_id)
    where status = 'running';

create index if not exists idx_calls_queued
    on public.calls (phone_id, contact_id, priority, created_at)
    where status = 'queued';

create index if not exists idx_calls_open_per_contact
    on public.calls (phone_id, contact_id, created_at)
    where status in ('running', 'queued');

create index if not exists idx_calls_event_id
    on public.calls (event_id)
    where event_id is not null;


create or replace function public.spine_lock_slot(
    p_phone_id uuid,
    p_contact_id uuid
)
returns void
language sql
as $$
    select pg_advisory_xact_lock(
        hashtextextended(p_phone_id::text || ':' || p_contact_id::text, 0)
    );
$$;


-- ── חישוב expected_end מתוך snapshot ──────────────────────────────────
create or replace function public.spine_sla_deadline(
    p_snapshot jsonb,
    p_from     timestamptz default now()
)
returns timestamptz
language sql
immutable
as $$
    select p_from + make_interval(secs =>
        coalesce(
            public.safe_int(p_snapshot->'estimated_time'->>'totalSeconds', null),
            public.bot_config_int('sla.default_estimated_seconds', 120)
        )
        + public.bot_config_int('sla.buffer_seconds', 600)
    );
$$;


-- ═══════════════════════════════════════════════════════════════════════

