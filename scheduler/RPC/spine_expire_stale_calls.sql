create or replace function spine_expire_stale_calls()
returns jsonb
language plpgsql
as $fn$
declare
  v_fallback_min int;
  v_expired_ids  uuid[];
begin
  -- fallback לשיחות בלי expected_end — מ-bot_config, ברירת מחדל 30 דק'
  select coalesce(
    (select value::int from bot_config where key = 'sla.fallback_minutes'),
    30
  ) into v_fallback_min;

  with expired as (
    update calls
    set status   = 'expired',
        ended_at = now()
    where status = 'running'
      and (
        -- המקרה הרגיל: עבר ה-deadline
        (expected_end is not null and expected_end < now())
        -- ה-fallback: אין deadline בכלל (call יתום) — לפי גיל
        or (expected_end is null
            and started_at < now() - make_interval(mins => v_fallback_min))
      )
    returning id
  )
  select coalesce(array_agg(id), '{}') into v_expired_ids from expired;

  -- queued שנתקעו (נדחו שוב ושוב / הטלפון מת) — ניקוי אחרי שעה
  update calls
  set status   = 'expired',
      ended_at = now()
  where status = 'queued'
    and created_at < now() - interval '60 minutes';

  return jsonb_build_object(
    'expired_running', coalesce(array_length(v_expired_ids, 1), 0),
    'ids', to_jsonb(v_expired_ids)
  );
end;
$fn$;

-- מפתח הקונפיג (אופציונלי — יש ברירת מחדל 30)
insert into bot_config (key, value, description)
values ('sla.fallback_minutes', '30',
        'SLA: פקיעת calls במצב running ללא expected_end אחרי X דקות')
on conflict (key) do nothing;
