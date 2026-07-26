create or replace function public.bot_config_int(
    p_key     text,
    p_default integer
)
returns integer
language sql
stable
as $$
    select coalesce(
        (select value::integer from public.bot_config where key = p_key),
        p_default
    );
$$;
