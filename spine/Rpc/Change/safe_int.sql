create or replace function public.safe_int(
    p_txt     text,
    p_default integer
)
returns integer
language plpgsql
immutable
as $$
begin
    return p_txt::integer;
exception when others then
    return p_default;
end;
$$;

notify pgrst, 'reload schema';
