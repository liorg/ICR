-- ═══════════════════════════════════════════════════════════════════════
-- Schedules — מותאם לסכמה האמיתית:
--   status:        ready | running | disabled
--   next_run / last_run  (לא next_run_at / last_run_at)
--   cron_expr:     JSON string → {hour, intervalHours, days, dayOfMonth}
--   schedule_type: hourly | daily | weekly | monthly | once
--   contact_id:    התזמון יודע למי הוא מיועד
-- ═══════════════════════════════════════════════════════════════════════

alter table calls add column if not exists schedule_id uuid;
create index if not exists idx_calls_schedule
    on calls (schedule_id, created_at desc) where schedule_id is not null;

create index if not exists idx_schedules_due
    on schedules (next_run) where status = 'running';


-- ── חישוב next_run מ-cron_expr. הלב שחסר לגמרי. ──────────────────────
create or replace function spine_compute_next_run(
    p_type     text,
    p_cron     text,            -- JSON string כמו שה-frontend שולח
    p_from     timestamptz default now()
)
returns timestamptz
language plpgsql immutable as $$
declare
    c        jsonb;
    v_hour   int;
    v_min    int;
    v_every  int;
    v_dom    int;
    v_days   jsonb;
    v_next   timestamptz;
    v_time   text;
    -- מיפוי ימי השבוע העבריים ל-DOW של פוסטגרס (0=ראשון)
    v_dow    int;
    i        int;
begin
    begin
        c := coalesce(p_cron, '{}')::jsonb;
    exception when others then
        c := '{}'::jsonb;
    end;

    v_time := coalesce(c->>'hour', '09:00');
    v_hour := split_part(v_time, ':', 1)::int;
    v_min  := coalesce(nullif(split_part(v_time, ':', 2), ''), '0')::int;

    if p_type = 'hourly' then
        v_every := greatest(coalesce((c->>'intervalHours')::int, 1), 1);
        -- מתקדם מ-p_from בקפיצות של intervalHours עד לזמן עתידי
        v_next := date_trunc('day', p_from)
                  + make_interval(hours => v_hour, mins => v_min);
        while v_next <= p_from loop
            v_next := v_next + make_interval(hours => v_every);
        end loop;
        return v_next;
    end if;

    if p_type = 'daily' then
        v_next := date_trunc('day', p_from) + make_interval(hours => v_hour, mins => v_min);
        if v_next <= p_from then
            v_next := v_next + interval '1 day';
        end if;
        return v_next;
    end if;

    if p_type = 'weekly' then
        v_days := coalesce(c->'days', '[]'::jsonb);
        if jsonb_array_length(v_days) = 0 then
            return null;                       -- לא נבחרו ימים → לא רץ
        end if;
        -- סורק 7 ימים קדימה ומחזיר את הראשון שמופיע ברשימה
        for i in 0..7 loop
            v_next := date_trunc('day', p_from) + make_interval(days => i,
                                                                hours => v_hour, mins => v_min);
            if v_next <= p_from then
                continue;
            end if;
            v_dow := extract(dow from v_next)::int;
            if v_days ? (array['ראשון','שני','שלישי','רביעי','חמישי','שישי','שבת'])[v_dow + 1]
               or v_days ? v_dow::text then
                return v_next;
            end if;
        end loop;
        return null;
    end if;

    if p_type = 'monthly' then
        v_dom  := least(greatest(coalesce((c->>'dayOfMonth')::int, 1), 1), 28);
        v_next := date_trunc('month', p_from)
                  + make_interval(days => v_dom - 1, hours => v_hour, mins => v_min);
        if v_next <= p_from then
            v_next := date_trunc('month', p_from) + interval '1 month'
                      + make_interval(days => v_dom - 1, hours => v_hour, mins => v_min);
        end if;
        return v_next;
    end if;

    return null;   -- once → נקבע ידנית ב-run_at, לא חוזר
end;
$$;


-- ── claim אטומי. status='running' = תזמון מופעל (לא "רץ עכשיו"). ──────
create or replace function spine_claim_due_schedules(p_limit int default 50)
returns setof jsonb
language plpgsql as $$
begin
    return query
    with due as (
        select id from schedules
         where status = 'running'
           and next_run is not null
           and next_run <= now()
         order by next_run
         limit p_limit
           for update skip locked
    )
    update schedules s
       set last_run = now()
      from due
     where s.id = due.id
    returning to_jsonb(s);
end;
$$;


-- ── סגירה: מחשב next_run אמיתי. p_ok=false → מנסה שוב בסבב הבא. ──────
create or replace function spine_close_schedule(
    p_schedule_id uuid,
    p_ok          boolean default true
)
returns jsonb
language plpgsql as $$
declare
    s      record;
    v_next timestamptz;
begin
    select * into s from schedules where id = p_schedule_id;
    if not found then
        return jsonb_build_object('code', 'SCHEDULE_NOT_FOUND');
    end if;

    -- once → ירייה אחת בלבד.
    if s.schedule_type = 'once' then
        update schedules set status = 'ready', next_run = null where id = p_schedule_id;
        return jsonb_build_object('code', 'SCHEDULE_ONCE_DONE');
    end if;

    v_next := spine_compute_next_run(s.schedule_type, s.cron_expr, now());

    update schedules set next_run = v_next where id = p_schedule_id;

    return jsonb_build_object(
        'code',     case when p_ok then 'SCHEDULE_RESCHEDULED' else 'SCHEDULE_RETRY' end,
        'next_run', v_next
    );
end;
$$;


-- ── Calls של תזמון = ההפעלות עצמן. אין ישות run נפרדת. ───────────────
-- spine_calls נמחקה — שדות ה-summary יושבים ישירות על calls.
create or replace function spine_schedule_calls(
    p_schedule_id uuid,
    p_limit       int default 50
)
returns jsonb
language sql stable as $$
    select coalesce(jsonb_agg(to_jsonb(t) order by t.created_at desc), '[]'::jsonb)
    from (
        select c.id as call_id, c.status, c.source,
               c.created_at, c.started_at, c.ended_at,
               c.duration_seconds, c.mismatch_count, c.last_step_id,
               ct.name  as contact_name,
               ct.number as contact_phone,
               s.name   as scenario_name,
               (select count(*) from spine_leaves l where l.call_id = c.id)                         as leaves_total,
               (select count(*) from spine_leaves l where l.call_id = c.id and l.status = 'Sent')   as leaves_sent,
               (select count(*) from spine_leaves l where l.call_id = c.id and l.status = 'Failed') as leaves_failed
        from calls c
        left join contacts  ct on ct.id = c.contact_id
        left join scenarios s  on s.id  = c.scenario_id
        where c.schedule_id = p_schedule_id
        order by c.created_at desc
        limit p_limit
    ) t;
$$;
