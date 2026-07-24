-- ═══════════════════════════════════════════════════════════════════════
-- spine_promote_call — קידום call יחיד מהתור
--
-- ה-Scheduler סורק את התור ומחליט *מה* לקדם; ה-Spine מבצע ומאמת.
-- האימות חייב לקרות כאן ולא בצד הסורק, כי רק בתוך הטרנזקציה אפשר
-- לנעול את ה-slot ולוודא שאין running — אחרת יש חלון שבו
-- spine_ensure_call יוצר call מתחרה בין הבדיקה לקידום.
--
-- קודים מוחזרים:
--   PROMOTED          קודם בהצלחה; ה-Spine ישלח init
--   CALL_NOT_FOUND    אין call כזה
--   NOT_QUEUED        כבר לא בתור (מישהו הקדים, או שכבר רץ)
--   CONTACT_BUSY      יש running לאותו (phone, contact) — לנסות שוב
--
-- started_at נקבע כאן: זו נקודת ההתחלה של השיחה בפועל, והרגע שממנו
-- נמדד ה-TTL. ההפרש מ-created_at הוא זמן ההמתנה בתור.
-- ═══════════════════════════════════════════════════════════════════════

-- created_at ברירת מחדל, כדי שזמן ההמתנה בתור יהיה ניתן למדידה:
--   started_at - created_at = כמה זמן חלף מרגע ההודעה ועד שהבוט התחיל.
alter table public.calls
    alter column created_at set default now();


drop function if exists public.spine_promote_call(uuid);

create or replace function public.spine_promote_call(
    p_call_id uuid
)
returns jsonb
language plpgsql
as $$
declare
    v_call    record;
    v_contact record;
begin
    select
        c.id,
        c.phone_id,
        c.contact_id,
        c.scenario_id,
        c.scenario_snapshot,
        c.status,
        c.priority,
        c.source,
        c.created_at
    into v_call
    from public.calls c
    where c.id = p_call_id;

    if not found then
        return jsonb_build_object(
            'code',    'CALL_NOT_FOUND',
            'message', 'No call with that id'
        );
    end if;

    if v_call.status is distinct from 'queued' then
        return jsonb_build_object(
            'code',    'NOT_QUEUED',
            'message', format(
                'Call is not queued; current status: %s',
                v_call.status
            ),
            'call_id', p_call_id,
            'status',  v_call.status
        );
    end if;

    -- אותו slot שבו משתמש spine_ensure_call — קידום ויצירה לא
    -- יכולים לרוץ במקביל על אותו איש קשר.
    perform public.spine_lock_slot(
        v_call.phone_id,
        v_call.contact_id
    );

    -- אחרי הנעילה בודקים שוב: ייתכן ש-ensure_call יצר running
    -- בין ה-SELECT לנעילה.
    if exists (
        select 1
        from public.calls r
        where r.phone_id   = v_call.phone_id
          and r.contact_id = v_call.contact_id
          and r.status     = 'running'
    ) then
        return jsonb_build_object(
            'code',       'CONTACT_BUSY',
            'message',    'Active call exists for this contact',
            'call_id',    p_call_id,
            'phone_id',   v_call.phone_id,
            'contact_id', v_call.contact_id
        );
    end if;

    update public.calls
    set
        status     = 'running',
        started_at = now()
    where id = p_call_id
      and status = 'queued';

    -- פרטי איש הקשר, כדי שה-Spine יבנה init_payload בלי שאילתה נוספת.
    select
        ct.id,
        ct.number,
        ct.lid,
        ct.name,
        ct.whatsapp_name
    into v_contact
    from public.contacts ct
    where ct.id = v_call.contact_id;

    return jsonb_build_object(
        'code',            'PROMOTED',
        'message',         'Call promoted from queue',

        'call_id',         v_call.id,
        'phone_id',        v_call.phone_id,
        'scenario_id',     v_call.scenario_id,
        'scenario_json',   coalesce(
                               v_call.scenario_snapshot,
                               '{}'::jsonb
                           ),
        'priority',        v_call.priority,
        'source',          v_call.source,

        -- כמה זמן המתין בתור, מרגע ההודעה ועד שהבוט התחיל.
        'queued_seconds',  extract(
                               epoch from (now() - v_call.created_at)
                           )::integer,

        'contact_id',      v_contact.id,
        'contact_phone',   coalesce(
                               nullif(v_contact.lid, ''),
                               v_contact.number,
                               ''
                           ),
        'contact_number',  coalesce(v_contact.number, ''),
        'contact_lid',     v_contact.lid,
        'contact_name',    coalesce(
                               nullif(v_contact.name, ''),
                               nullif(v_contact.whatsapp_name, ''),
                               ''
                           )
    );
end;
$$;


-- ── אימות ───────────────────────────────────────────────────────────
-- select spine_promote_call('<CALL_ID>');
--
-- select p.oid::regprocedure from pg_proc p
-- join pg_namespace n on n.oid = p.pronamespace
-- where n.nspname = 'public' and p.proname like 'spine_%';
