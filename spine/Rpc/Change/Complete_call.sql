-- ═══════════════════════════════════════════════════════════════════════
-- spine_complete_call — סוגר call ומקדם את הבא בתור
--
-- שני תיקונים מול הגרסה הקודמת:
--
-- 1. GET DIAGNOSTICS במקום `if not found` אחרי ה-UPDATE.
--    הדגל FOUND ב-plpgsql נקבע גם ע"י SELECT INTO ו-PERFORM שרצו
--    לפניו, ולכן הבדיקה הישנה החזירה CALL_NOT_RUNNING על calls
--    שכן היו running — וההפך.
--
-- 2. קידום התור הוחזר. spine_ensure_call יוצר שורות queued עבור
--    source='trigger', ובלי הקידום הן נשארות מתות לנצח.
--
-- 3. הקידום קובע גם expected_end. בלעדיו ה-call המקודם נשאר עם
--    expected_end = null, ה-sweeper לא רואה אותו לעולם, ואם ה-Worker
--    ייתקע הקונטקט חסום לנצח.
--
-- סדר הקידום: priority asc (מספר נמוך קודם), ואז created_at asc.
--
-- drop לפני create: שינוי חתימה ב-create or replace יוצר overload
-- נוסף במקום להחליף, ואז PostgREST מחזיר PGRST203.
-- ═══════════════════════════════════════════════════════════════════════

drop function if exists public.spine_complete_call(uuid, text);

create or replace function public.spine_complete_call(
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
    v_rows    integer;
begin
    v_status := lower(
        coalesce(
            p_status,
            'completed'
        )
    );

    if v_status not in (
        'completed',
        'failed',
        'aborted',
        'expired',
        'timeout'
    ) then
        return jsonb_build_object(
            'code',    'INVALID_STATUS',
            'message', format(
                '"%s" is not a terminal status',
                p_status
            )
        );
    end if;

    select
        c.phone_id,
        c.contact_id
    into
        v_phone,
        v_contact
    from public.calls c
    where c.id = p_call_id;

    -- SELECT INTO — כאן `not found` תקין.
    if not found then
        return jsonb_build_object(
            'code',    'CALL_NOT_FOUND',
            'message', 'No call with that id'
        );
    end if;

    perform public.spine_lock_slot(
        v_phone,
        v_contact
    );

    update public.calls
    set
        status   = v_status,
        ended_at = now()
    where id = p_call_id
      and status = 'running';

    -- מודד את ה-UPDATE בלבד, ולא את ה-PERFORM שקדם לו.
    get diagnostics v_rows = row_count;

    if v_rows = 0 then
        return jsonb_build_object(
            'code',    'CALL_NOT_RUNNING',
            'message', 'Call exists but is not running',
            'call_id', p_call_id
        );
    end if;

    -- ── הבא בתור ────────────────────────────────────────────────────
    select
        c.id,
        c.scenario_id,
        c.scenario_snapshot
    into v_next
    from public.calls c
    where c.phone_id = v_phone
      and c.contact_id = v_contact
      and c.status = 'queued'
    order by
        c.priority asc,
        c.created_at asc
    limit 1
    for update;

    -- SELECT INTO — `not found` תקין.
    if not found then
        return jsonb_build_object(
            'code',         'CALL_CLOSED',
            'message',      'Closed. Queue empty.',
            'call_id',      p_call_id,
            'status',       v_status,
            'phone_id',     v_phone,
            'contact_id',   v_contact,
            'next_call_id', null
        );
    end if;

    -- expected_end מחושב כאן ולא ביצירה: ה-SLA מתחיל לרוץ מרגע
    -- שהתרחיש באמת מתחיל, לא מרגע שנכנס לתור.
    update public.calls
    set
        status       = 'running',
        started_at   = now(),
        expected_end = public.spine_sla_deadline(v_next.scenario_snapshot, now())
    where id = v_next.id;

    return jsonb_build_object(
        'code',             'CALL_CLOSED_NEXT_PROMOTED',
        'message',          'Closed. Next queued call promoted.',
        'call_id',          p_call_id,
        'status',           v_status,
        'phone_id',         v_phone,
        'contact_id',       v_contact,
        'next_call_id',     v_next.id,
        'next_scenario_id', v_next.scenario_id
    );
end;
$$;


-- ── אימות: צריך להחזיר בדיוק שתי חתימות ─────────────────────────────
-- select p.oid::regprocedure from pg_proc p
-- join pg_namespace n on n.oid = p.pronamespace
-- where n.nspname = 'public' and p.proname like 'spine_%';
