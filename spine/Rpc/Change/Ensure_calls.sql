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
drop function if exists public.spine_ensure_call(uuid, uuid, uuid, integer, text, uuid);
drop function if exists public.spine_ensure_call(uuid, uuid, uuid, integer, text, uuid, jsonb);
drop function if exists public.spine_ensure_trigger_calls(uuid, uuid, jsonb);

create or replace function public.spine_ensure_call(
    p_phone_id      uuid,
    p_contact_id    uuid,
    p_scenario_id   uuid    default null,
    p_priority      integer default null,
    p_source        text    default 'trigger',
    p_schedule_id   uuid    default null,
    p_first_message jsonb   default null
)
returns jsonb
language plpgsql
as $$
declare
    v_contact    record;
    v_scenario   record;
    v_active     record;
    v_call_id    uuid;
    v_priority   integer;
    v_reason     text;
    v_cancelled  integer := 0;
    v_running    jsonb   := null;
    v_now        timestamptz := now();
    v_event_id   uuid    := gen_random_uuid();
    v_is_trigger boolean := (p_scenario_id is null);
begin
    -- מצב trigger מזוהה לפי היעדר scenario_id, וה-source נגזר ממנו.
    if v_is_trigger then
        p_source := 'trigger';
    end if;

    -- ── 1. Contact ──────────────────────────────────────────────────
    select c.id, c.phone_id, c.number, c.lid, c.name, c.whatsapp_name, c.tag
      into v_contact
      from public.contacts c
     where c.id = p_contact_id
       and c.phone_id = p_phone_id
     limit 1;

    if not found then
        return jsonb_build_object(
            'status',  'error',
            'code',    'CONTACT_NOT_FOUND',
            'message', 'Contact not found for supplied phone'
        );
    end if;

    if v_contact.tag is distinct from 'active' then
        return jsonb_build_object(
            'status',      'denied',
            'code',        'CONTACT_NOT_ACTIVE',
            'message',     format('Contact is not active; tag=%s',
                                  coalesce(v_contact.tag, 'null')),
            'contact_tag', v_contact.tag
        );
    end if;

    -- ── 2. אימות תרחיש בודד ─────────────────────────────────────────
    if not v_is_trigger then
        select s.id, s.config, s.priority, s.status
          into v_scenario
          from public.scenarios s
         where s.id = p_scenario_id
           and s.phone_id = p_phone_id
           and s.status = 'active'
         limit 1;

        if not found then
            return jsonb_build_object(
                'status',  'error',
                'code',    'SCENARIO_NOT_FOUND_OR_INACTIVE',
                'message', 'Active scenario not found for supplied phone'
            );
        end if;
    end if;

    -- ── 3. Slot ─────────────────────────────────────────────────────
    perform public.spine_lock_slot(p_phone_id, p_contact_id);

    select c.id, c.status, c.source, c.started_at, c.created_at
      into v_active
      from public.calls c
     where c.phone_id   = p_phone_id
       and c.contact_id = p_contact_id
       and c.status in ('running', 'queued')
     order by (c.status = 'running') desc, c.created_at
     limit 1;

    -- ── 4. call פתוח ────────────────────────────────────────────────
    if found then
        -- סדר הבדיקה לפי חשיבות ה-source. scheduler ראשון: הוא
        -- ה-source החשוב ביותר, ורק הוא מבדיל בין שתי סיבות פסילה.
        if p_source = 'scheduler' then
            v_reason := case
                when v_active.status = 'running' and v_active.source = 'scheduler'
                then 'SCHEDULER_INSTANCE_EXISTS'
                else 'ACTIVE_CALL_EXISTS'
            end;

        -- trigger: יוצא בלי להשאיר עקבות ובלי לגעת בתור.
        elsif v_is_trigger then
            return jsonb_build_object(
                'status',         'denied',
                'code',           'TRIGGER_DENIED_ACTIVE_CALL',
                'message',        format('Active call exists (%s); trigger denied',
                                         v_active.status),
                'active_call_id', v_active.id,
                'active_status',  v_active.status,
                'active_since',   coalesce(v_active.started_at, v_active.created_at)
            );

        -- api / manual / כל השאר
        else
            v_reason := 'ACTIVE_CALL_EXISTS';
        end if;

        -- יזום: מרוקן את התור ונרשם aborted. ה-running לא נוגעים בו.
        update public.calls
           set status        = 'aborted',
               status_reason = format('PREEMPTED_BY_%s', upper(p_source)),
               ended_at      = v_now
         where phone_id   = p_phone_id
           and contact_id = p_contact_id
           and status     = 'queued';

        get diagnostics v_cancelled = row_count;

        v_call_id := gen_random_uuid();

        insert into public.calls (
            id, scenario_id, scenario_snapshot,
            phone_id, contact_id,
            status, status_reason, priority, source, schedule_id,
            first_message, first_message_at, event_id,
            created_at, ended_at
        )
        values (
            v_call_id,
            v_scenario.id,
            coalesce(v_scenario.config, '{}'::jsonb),
            p_phone_id,
            p_contact_id,
            'aborted',
            v_reason,
            coalesce(p_priority, v_scenario.priority, 100),
            p_source,
            p_schedule_id,
            p_first_message,
            case when p_first_message is null then null else v_now end,
            v_event_id,
            v_now,
            v_now
        );

        return jsonb_build_object(
            'call_id',          v_call_id,
            'event_id',         v_event_id,
            'status',           'aborted',
            'status_reason',    v_reason,
            'code',             v_reason,
            'message',          format(
                'Active call exists (%s); source "%s" aborted, %s queued call(s) cancelled',
                v_active.status, p_source, v_cancelled
            ),
            'cancelled_queued', v_cancelled,
            'active_call_id',   v_active.id,
            'active_status',    v_active.status,
            'active_since',     coalesce(v_active.started_at, v_active.created_at),
            'phone_id',         p_phone_id,
            'contact_id',       v_contact.id,
            'scenario_id',      v_scenario.id,
            'source',           p_source
        );
    end if;

    -- ── 5. יצירה ────────────────────────────────────────────────────
    --
    -- אותה לולאה לשני המצבים: במצב תרחיש בודד היא מסתובבת פעם אחת.
    -- הראשון running, כל השאר queued.
    begin
        for v_scenario in
            select s.id, s.config, s.priority, s.status
              from public.scenarios s
             where s.phone_id = p_phone_id
               and s.status   = 'active'
               and (
                    (v_is_trigger and s.event_type = 'trigger')
                    or (not v_is_trigger and s.id = p_scenario_id)
               )
             order by coalesce(s.priority, 100), s.created_at
        loop
            v_call_id  := gen_random_uuid();
            -- priority קובע את סדר הריצה בתוך באטץ' של triggers.
            -- בתרחיש בודד הוא נשמר לתיעוד בלבד ולא משפיע על כלום.
            v_priority := coalesce(p_priority, v_scenario.priority, 100);

            if v_running is null then
                insert into public.calls (
                    id, scenario_id, scenario_snapshot,
                    phone_id, contact_id,
                    status, priority, source, schedule_id,
                    first_message, first_message_at, event_id,
                    started_at, expected_end, created_at
                )
                values (
                    v_call_id, v_scenario.id,
                    coalesce(v_scenario.config, '{}'::jsonb),
                    p_phone_id, p_contact_id,
                    'running', v_priority, p_source, p_schedule_id,
                    p_first_message,
                    case when p_first_message is null then null else v_now end,
                    v_event_id,
                    v_now,
                    public.spine_sla_deadline(v_scenario.config, v_now),
                    v_now
                );

                v_running := jsonb_build_object(
                    'call_id',         v_call_id,
                    'scenario_id',     v_scenario.id,
                    'scenario_status', v_scenario.status,
                    'scenario_json',   coalesce(v_scenario.config, '{}'::jsonb),
                    'priority',        v_priority
                );
            else
                -- בלי started_at ובלי expected_end: השעון מתחיל בקידום.
                insert into public.calls (
                    id, scenario_id, scenario_snapshot,
                    phone_id, contact_id,
                    status, priority, source, schedule_id,
                    first_message, first_message_at, event_id,
                    created_at
                )
                values (
                    v_call_id, v_scenario.id,
                    coalesce(v_scenario.config, '{}'::jsonb),
                    p_phone_id, p_contact_id,
                    'queued', v_priority, p_source, p_schedule_id,
                    p_first_message,
                    case when p_first_message is null then null else v_now end,
                    v_event_id,
                    v_now
                );

            end if;
        end loop;

    exception when unique_violation then
        select c.id, c.status into v_active
          from public.calls c
         where c.phone_id = p_phone_id
           and c.contact_id = p_contact_id
           and c.status = 'running'
         limit 1;

        return jsonb_build_object(
            'status',         'denied',
            'code',           'CALL_ALREADY_ACTIVE',
            'message',        'Active call exists; nothing created',
            'active_call_id', v_active.id,
            'active_status',  v_active.status
        );
    end;

    if v_running is null then
        return jsonb_build_object(
            'status',  'empty',
            'code',    'NO_SCENARIOS',
            'message', 'No matching active scenarios'
        );
    end if;

    -- ── 6. תשובה ────────────────────────────────────────────────────
    return jsonb_build_object(
        'status',          'running',
        'code',            'CALL_CREATED',
        'message',         'Call created and started',

        'call_id',         v_running->>'call_id',
        'event_id',        v_event_id,
        'phone_id',        p_phone_id,

        'contact_id',      v_contact.id,
        'contact_phone',   coalesce(nullif(v_contact.lid, ''), v_contact.number, ''),
        'contact_number',  coalesce(v_contact.number, ''),
        'contact_lid',     v_contact.lid,
        'contact_name',    coalesce(nullif(v_contact.name, ''),
                                    nullif(v_contact.whatsapp_name, ''), ''),

        'scenario_id',     v_running->>'scenario_id',
        'scenario_status', v_running->>'scenario_status',
        'scenario_json',   v_running->'scenario_json',
        'priority',        (v_running->>'priority')::int,
        'source',          p_source
    );
end;
$$;


-- ── Backfill: running ישנים בלי expected_end ──────────────────────────
update public.calls c
   set expected_end = public.spine_sla_deadline(
           c.scenario_snapshot,
           coalesce(c.started_at, c.created_at)
       )
 where c.status = 'running'
   and c.expected_end is null;


-- ── בדיקת שפיות: queued בלי running (יתומים) ─────────────────────────
-- select phone_id, contact_id, count(*) as queued
--   from public.calls
--  group by phone_id, contact_id
-- having count(*) filter (where status = 'running') = 0
--    and count(*) filter (where status = 'queued')  > 0;
