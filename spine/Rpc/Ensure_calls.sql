-- ═══════════════════════════════════════════════════════════════════════
-- Invariant: תמיד ≤ 1 call פעיל לכל (phone_id, contact_id).
--
-- spine_ensure_call הוא מקור האמת היחיד עבור:
--   • Contact קיים ושייך ל-phone
--   • Contact פעיל
--   • Scenario קיים, פעיל ושייך ל-phone
--   • config + priority
--   • scenario_snapshot
--   • running / queued / blocked
-- ═══════════════════════════════════════════════════════════════════════

create unique index if not exists uniq_running_call_per_contact
    on public.calls (phone_id, contact_id)
    where status = 'running';

create index if not exists idx_calls_queued
    on public.calls (phone_id, contact_id, priority, created_at)
    where status = 'queued';


create or replace function public.spine_lock_slot(
    p_phone_id uuid,
    p_contact_id uuid
)
returns void
language sql
as $$
    select pg_advisory_xact_lock(
        hashtextextended(
            p_phone_id::text || ':' || p_contact_id::text,
            0
        )
    );
$$;


create or replace function public.spine_ensure_call(
    p_phone_id    uuid,
    p_contact_id  uuid,
    p_scenario_id uuid,
    p_priority    integer default null,
    p_source      text    default 'trigger',
    p_schedule_id uuid    default null
)
returns jsonb
language plpgsql
as $$
declare
    v_call_id    uuid := gen_random_uuid();
    v_contact    record;
    v_scenario   record;
    v_priority   integer;
    v_active     record;
    v_constraint text;
begin
    -- ── Contact ─────────────────────────────────────────────────────
    select
        c.id,
        c.phone_id,
        c.number,
        c.lid,
        c.name,
        c.whatsapp_name,
        c.tag
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
            'status',      'blocked',
            'code',        'CONTACT_NOT_ACTIVE',
            'message',     format(
                'Contact is not active; tag=%s',
                coalesce(v_contact.tag, 'null')
            ),
            'contact_tag', v_contact.tag
        );
    end if;

    -- ── Scenario ────────────────────────────────────────────────────
    select
        s.id,
        s.phone_id,
        s.config,
        s.priority,
        s.status
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

    v_priority := coalesce(
        p_priority,
        v_scenario.priority,
        100
    );

    perform public.spine_lock_slot(
        p_phone_id,
        p_contact_id
    );

    begin
        insert into public.calls (
            id,
            scenario_id,
            scenario_snapshot,
            phone_id,
            contact_id,
            status,
            priority,
            source,
            schedule_id,
            started_at,
            created_at
        )
        values (
            v_call_id,
            v_scenario.id,
            coalesce(v_scenario.config, '{}'::jsonb),
            p_phone_id,
            p_contact_id,
            'running',
            v_priority,
            p_source,
            p_schedule_id,
            now(),
            now()
        );

        return jsonb_build_object(
            'call_id',         v_call_id,
            'status',          'running',
            'code',            'CALL_CREATED',
            'message',         'Call created and started',

            'phone_id',        p_phone_id,

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
                               ),

            'scenario_id',     v_scenario.id,
            'scenario_status', v_scenario.status,
            'scenario_json',   coalesce(
                                   v_scenario.config,
                                   '{}'::jsonb
                               ),
            'priority',        v_priority
        );

    exception when unique_violation then
        get stacked diagnostics
            v_constraint = constraint_name;

        if v_constraint is distinct from
           'uniq_running_call_per_contact'
        then
            raise;
        end if;

        select
            c.id,
            c.scenario_id,
            c.started_at
        into v_active
        from public.calls c
        where c.phone_id = p_phone_id
          and c.contact_id = p_contact_id
          and c.status = 'running'
        limit 1;

        if p_source is distinct from 'trigger' then
            return jsonb_build_object(
                'status',          'blocked',
                'code',            'CALL_ALREADY_ACTIVE',
                'message',         format(
                    'Active call exists; source "%s" is not queued',
                    p_source
                ),
                'active_call_id',  v_active.id,
                'active_since',    v_active.started_at
            );
        end if;

        insert into public.calls (
            id,
            scenario_id,
            scenario_snapshot,
            phone_id,
            contact_id,
            status,
            priority,
            source,
            schedule_id,
            created_at
        )
        values (
            v_call_id,
            v_scenario.id,
            coalesce(v_scenario.config, '{}'::jsonb),
            p_phone_id,
            p_contact_id,
            'queued',
            v_priority,
            p_source,
            p_schedule_id,
            now()
        );

        return jsonb_build_object(
            'call_id',         v_call_id,
            'status',          'queued',
            'code',            'CALL_QUEUED',
            'message',         'Active call exists; queued by priority',
            'active_call_id',  v_active.id,

            'phone_id',        p_phone_id,

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
                               ),

            'scenario_id',     v_scenario.id,
            'scenario_status', v_scenario.status,
            'scenario_json',   coalesce(
                                   v_scenario.config,
                                   '{}'::jsonb
                               ),
            'priority',        v_priority
        );
    end;
end;
$$;

