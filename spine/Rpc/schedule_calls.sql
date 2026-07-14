-- ═══════════════════════════════════════════════════════════════════════
-- טאב Calls בתזמון = ההפעלות עצמן. אין ישות "run" נפרדת:
-- כל הפעלה של תזמון היא call אחד, ו-spine_calls כבר מחזיק את ה-summary.
-- דרוש רק הקישור schedule_id.
-- ═══════════════════════════════════════════════════════════════════════

alter table calls add column if not exists schedule_id uuid;

create index if not exists idx_calls_schedule
    on calls (schedule_id, created_at desc)
    where schedule_id is not null;


-- ── Calls של תזמון — ברמת summary ────────────────────────────────────
create or replace function spine_schedule_calls(
    p_schedule_id uuid,
    p_limit       int default 50
)
returns jsonb
language sql stable as $$
    select coalesce(jsonb_agg(to_jsonb(t) order by t.created_at desc), '[]'::jsonb)
    from (
        select
            c.id            as call_id,
            c.status,                       -- running|queued|completed|failed|aborted
            c.created_at,                   -- ← זמן ההפעלה
            c.started_at,
            c.ended_at,
            ct.name         as contact_name,
            ct.phone        as contact_phone,
            s.name          as scenario_name,
            sc.duration_seconds,
            sc.mismatch_count,
            sc.last_step_id,
            -- מצב העלים: כמה נשלחו מתוך כמה
            (select count(*) from spine_leaves l
              where l.call_id = c.id)                          as leaves_total,
            (select count(*) from spine_leaves l
              where l.call_id = c.id and l.status = 'Sent')    as leaves_sent,
            (select count(*) from spine_leaves l
              where l.call_id = c.id and l.status = 'Failed')  as leaves_failed
        from calls c
        left join contacts    ct on ct.id = c.contact_id
        left join scenarios   s  on s.id  = c.scenario_id
        left join spine_calls sc on sc.call_id = c.id
        where c.schedule_id = p_schedule_id
        order by c.created_at desc
        limit p_limit
    ) t;
$$;
