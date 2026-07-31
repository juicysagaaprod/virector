begin;

create table public.projects (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    name text not null default 'Untitled project'
        check (char_length(name) between 1 and 160),
    description text not null default ''
        check (char_length(description) <= 2000),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (id, owner_id)
);

create table public.render_jobs (
    id text primary key
        check (id ~ '^[0-9a-f]{32}$'),
    project_id uuid not null,
    owner_id uuid not null,
    title text not null default 'Untitled shot'
        check (char_length(title) between 1 and 200),
    direction_prompt text not null
        check (char_length(direction_prompt) between 1 and 4000),
    shot_spec jsonb not null,
    status text not null default 'queued'
        check (
            status in (
                'queued',
                'validating',
                'composed',
                'rendering',
                'completed',
                'failed',
                'cancelled'
            )
        ),
    progress smallint not null default 0
        check (progress between 0 and 100),
    worker_mode text not null default 'mock'
        check (worker_mode in ('mock', 'ltx', 'vace', 'cloud')),
    attempt_count smallint not null default 0
        check (attempt_count >= 0),
    output_object_key text,
    error_code text,
    error_message text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    started_at timestamptz,
    completed_at timestamptz,
    constraint render_jobs_project_owner_fkey
        foreign key (project_id, owner_id)
        references public.projects(id, owner_id)
        on delete cascade,
    constraint render_jobs_shot_spec_object
        check (jsonb_typeof(shot_spec) = 'object')
);

create table public.render_assets (
    id uuid primary key default gen_random_uuid(),
    render_job_id text not null
        references public.render_jobs(id) on delete cascade,
    kind text not null
        check (
            kind in (
                'reference',
                'manifest',
                'conditioning',
                'preview',
                'final'
            )
        ),
    image_tag text
        check (image_tag is null or image_tag ~ '^@image[1-9]$'),
    ordinal smallint
        check (ordinal is null or ordinal between 1 and 9),
    object_key text not null,
    content_type text,
    size_bytes bigint check (size_bytes is null or size_bytes >= 0),
    metadata jsonb not null default '{}'::jsonb
        check (jsonb_typeof(metadata) = 'object'),
    created_at timestamptz not null default now(),
    unique (render_job_id, object_key),
    constraint render_assets_reference_identity
        check (
            (kind = 'reference' and image_tag is not null and ordinal is not null)
            or
            (kind <> 'reference' and image_tag is null and ordinal is null)
        )
);

create table public.render_events (
    id bigint primary key generated always as identity,
    render_job_id text not null
        references public.render_jobs(id) on delete cascade,
    event_type text not null
        check (
            event_type in (
                'accepted',
                'progress',
                'retry',
                'completed',
                'failed',
                'cancelled'
            )
        ),
    stage text,
    progress smallint check (progress is null or progress between 0 and 100),
    message text not null default '',
    details jsonb not null default '{}'::jsonb
        check (jsonb_typeof(details) = 'object'),
    created_at timestamptz not null default now()
);

create index projects_owner_created_idx
    on public.projects(owner_id, created_at desc);
create index render_jobs_owner_created_idx
    on public.render_jobs(owner_id, created_at desc);
create index render_jobs_project_created_idx
    on public.render_jobs(project_id, created_at desc);
create index render_jobs_status_created_idx
    on public.render_jobs(status, created_at);
create index render_assets_job_kind_idx
    on public.render_assets(render_job_id, kind);
create index render_events_job_created_idx
    on public.render_events(render_job_id, created_at);

create function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create trigger projects_set_updated_at
before update on public.projects
for each row execute function public.set_updated_at();

create trigger render_jobs_set_updated_at
before update on public.render_jobs
for each row execute function public.set_updated_at();

alter table public.projects enable row level security;
alter table public.render_jobs enable row level security;
alter table public.render_assets enable row level security;
alter table public.render_events enable row level security;

create policy projects_select_own
on public.projects
for select
to authenticated
using (owner_id = (select auth.uid()));

create policy projects_insert_own
on public.projects
for insert
to authenticated
with check (owner_id = (select auth.uid()));

create policy projects_update_own
on public.projects
for update
to authenticated
using (owner_id = (select auth.uid()))
with check (owner_id = (select auth.uid()));

create policy projects_delete_own
on public.projects
for delete
to authenticated
using (owner_id = (select auth.uid()));

create policy render_jobs_select_own
on public.render_jobs
for select
to authenticated
using (owner_id = (select auth.uid()));

create policy render_assets_select_own
on public.render_assets
for select
to authenticated
using (
    exists (
        select 1
        from public.render_jobs
        where render_jobs.id = render_assets.render_job_id
          and render_jobs.owner_id = (select auth.uid())
    )
);

create policy render_events_select_own
on public.render_events
for select
to authenticated
using (
    exists (
        select 1
        from public.render_jobs
        where render_jobs.id = render_events.render_job_id
          and render_jobs.owner_id = (select auth.uid())
    )
);

revoke all on table public.projects from anon, authenticated;
revoke all on table public.render_jobs from anon, authenticated;
revoke all on table public.render_assets from anon, authenticated;
revoke all on table public.render_events from anon, authenticated;
revoke all on sequence public.render_events_id_seq from anon, authenticated;

grant select, insert, update, delete on table public.projects to authenticated;
grant select on table public.render_jobs to authenticated;
grant select on table public.render_assets to authenticated;
grant select on table public.render_events to authenticated;

grant all on table public.projects to service_role;
grant all on table public.render_jobs to service_role;
grant all on table public.render_assets to service_role;
grant all on table public.render_events to service_role;
grant usage, select on sequence public.render_events_id_seq to service_role;

revoke execute on function public.set_updated_at() from public, anon, authenticated;
grant execute on function public.set_updated_at() to service_role;

commit;
