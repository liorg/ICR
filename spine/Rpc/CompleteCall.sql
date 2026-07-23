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
            'code',
            'INVALID_STATUS',

            'message',
            format(
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

    if not found then
        return jsonb_build_object(
            'code',
            'CALL_NOT_FOUND',

            'message',
            'No call with that id'
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

    get diagnostics v_rows = row_count;

    if v_rows = 0 then
        return jsonb_build_object(
            'code',
            'CALL_NOT_RUNNING',

            'message',
            'Call exists but is not running',

            'call_id',
            p_call_id
        );
    end if;

    return jsonb_build_object(
        'code',
        'CALL_CLOSED',

        'message',
        'Call closed successfully',

        'call_id',
        p_call_id,

        'status',
        v_status,

        'phone_id',
        v_phone,

        'contact_id',
        v_contact
    );
end;
$$;
