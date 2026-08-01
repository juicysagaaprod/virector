alter table public.render_jobs
    drop constraint if exists render_jobs_worker_mode_check;

alter table public.render_jobs
    add constraint render_jobs_worker_mode_check
    check (worker_mode in ('mock', 'ltx', 'vace', 'performance', 'cloud'));
