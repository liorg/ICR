-- ═══════════════════════════════════════════════════════════════════════
-- Invariant: תמיד ≤ 1 call פעיל לכל (phone_id, contact_id).
--
-- שתי שכבות הגנה:
--   1. partial unique index  — האמת הסופית, גם מול כתיבה ידנית ל-DB.
--   2. advisory lock משותף   — מסרלל את ensure ↔ complete.
--
-- ה-index לבדו לא מספיק: בין ה-UPDATE שסוגר את ה-running לבין קידום
-- ה-queued, ה-slot פנוי — ו-ensure מקביל היה תופס אותו, עוקף את התור,
-- ומפיל את complete_call ב-unique_violation.
-- ═══════════════════════════════════════════════════════════════════════

create unique index if not exists uniq_running_call_per_contact
    on calls (phone_id, contact_id)
    where status = 'running';

create index if not exists idx_calls_queued
    on calls (phone_id, contact_id, priority, created_at)
    where status = 'queued';


-- ── ה-lock המשותף. נשמר עד סוף הטרנזקציה, משוחרר אוטומטית. ────────────
create or replace function spine_lock_slot(p_phone_id uuid, p_contact_id uuid)
returns void
language sql
as $$
    select pg_advisory_xact_lock(
        hashtextextended(p_phone_id::text || ':' || p_contact_id::text, 0)
    );
$$;


-- ═══════════════════════════════════════════════════════════════════════
-- ensure_call — נקודת היצירה היחידה.
--   trigger            → תור אם תפוס
--   scheduler / api    → נחסם אם תפוס
-- ═══════════════════════════════════════════════════════════════════════
create or replace function spine_ensure_call(
    p_phone_id    uuid,
    p_contact_id  uuid,
    p_scenario_id uuid,
    p_snapshot    jsonb,
    p_priority    int     default 100,
    p_source      text    default 'trigger',
    p_schedule_id uuid    default null
)
returns jsonb
language plpgsql
as $$
declare
    v_call_id    uuid := gen_random_uuid();   -- calls.id הוא uuid
    v_active     record;
    v_constraint text;
begin
    perform spine_lock_slot(p_phone_id, p_contact_id);

    begin
        insert into calls (id, scenario_id, scenario_snapshot, phone_id, contact_id,
                           status, priority, source, schedule_id, started_at, created_at)
        values (v_call_id, p_scenario_id, p_snapshot, p_phone_id, p_contact_id,
                'running', p_priority, p_source, p_schedule_id, now(), now());

        return jsonb_build_object(
            'call_id', v_call_id,
            'status',  'running',
            'code',    'CALL_CREATED',
            'message', 'Call created and started'
        );

    exception when unique_violation then
        -- לוודא שזו באמת ההתנגשות שלנו. כל unique אחר (PK כפול, אינדקס
        -- עסקי אחר) חייב להתפוצץ החוצה ולא להיבלע כ"תפוס".
        get stacked diagnostics v_constraint = constraint_name;
        if v_constraint is distinct from 'uniq_running_call_per_contact' then
            raise;
        end if;

        select id, scenario_id, started_at
          into v_active
          from calls
         where phone_id = p_phone_id
           and contact_id = p_contact_id
           and status = 'running'
         limit 1;

        -- רק trigger ממתין. תזמון/API שמפספסים את החלון — נחסמים.
        -- אחרת תרחיש ארוך היה צובר עשרות calls מתוזמנים שיירו במפולת בסיום.
        if p_source is distinct from 'trigger' then
            return jsonb_build_object(
                'status',         'blocked',
                'code',           'CALL_ALREADY_ACTIVE',
                'message',        format('Active call exists — source "%s" is not queued', p_source),
                'active_call_id', v_active.id,
                'active_since',   v_active.started_at
            );
        end if;

        insert into calls (id, scenario_id, scenario_snapshot, phone_id, contact_id,
                           status, priority, source, schedule_id, created_at)
        values (v_call_id, p_scenario_id, p_snapshot, p_phone_id, p_contact_id,
                'queued', p_priority, p_source, p_schedule_id, now());

        return jsonb_build_object(
            'call_id',        v_call_id,
            'status',         'queued',
            'code',           'CALL_QUEUED',
            'message',        'Active call exists — queued by priority',
            'active_call_id', v_active.id
        );
    end;
end;
$$;


-- ═══════════════════════════════════════════════════════════════════════
-- complete_call — סוגר ומקדם את הבא, תחת אותו lock.
-- ה-slot לא מתפנה לרגע: ensure מקביל ימתין עד שהקידום הושלם.
-- ═══════════════════════════════════════════════════════════════════════
create or replace function spine_complete_call(
    p_call_id uuid,
    p_status  text default 'completed'
)
returns jsonb
language plpgsql
as $$
declare
    v_phone   uuid;
    v_contact uuid;
    v_next    record;
    v_status  text;
begin
    -- רק סטטוסים סופיים. 'running'/'queued' כאן היו משאירים את ה-call
    -- תקוע ב-slot לנצח, וחוסמים כל call עתידי לאותו contact.
    v_status := lower(coalesce(p_status, 'completed'));
    if v_status not in ('completed', 'failed', 'aborted', 'expired', 'timeout') then
        return jsonb_build_object(
            'code',    'INVALID_STATUS',
            'message', format('"%s" is not a terminal status', p_status)
        );
    end if;

    select phone_id, contact_id into v_phone, v_contact
      from calls where id = p_call_id;

    if not found then
        return jsonb_build_object('code', 'CALL_NOT_FOUND',
                                  'message', 'No call with that id');
    end if;

    perform spine_lock_slot(v_phone, v_contact);

    update calls
       set status = v_status, ended_at = now()
     where id = p_call_id
       and status = 'running';

    if not found then
        return jsonb_build_object('code', 'CALL_NOT_RUNNING',
                                  'message', 'Call exists but is not running');
    end if;

    -- FOR UPDATE בלבד — בלי SKIP LOCKED. ה-advisory lock כבר מסרלל,
    -- ו-SKIP LOCKED היה מדלג על ה-queued ומשאיר את התור תקוע.
    select id, scenario_id into v_next
      from calls
     where phone_id   = v_phone
       and contact_id = v_contact
       and status     = 'queued'
     order by priority asc, created_at asc
     limit 1
       for update;

    if not found then
        return jsonb_build_object('code', 'CALL_CLOSED',
                                  'message', 'Closed. Queue empty.',
                                  'next_call_id', null);
    end if;

    update calls
       set status = 'running', started_at = now()
     where id = v_next.id;

    return jsonb_build_object(
        'code',             'CALL_CLOSED_NEXT_PROMOTED',
        'message',          'Closed. Next queued call promoted.',
        'next_call_id',     v_next.id,
        'next_scenario_id', v_next.scenario_id
    );
end;
$$;
