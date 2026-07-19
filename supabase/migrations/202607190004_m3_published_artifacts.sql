-- Astrolabe M3第3便: デプロイUI向けの決定的exportをSupabaseへ公開する。
-- SQL Editorで施主レビュー後に適用し、適用前はpublishを実行しない。

create table if not exists public.published_artifacts (
  artifact_key text primary key,
  kind text not null check (kind in ('map', 'layout', 'index', 'report')),
  report_date date,
  schema_version integer not null check (schema_version > 0),
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  updated_at timestamptz not null default now(),
  check (
    (kind = 'report' and report_date is not null
      and artifact_key = 'report:' || report_date::text)
    or
    (kind <> 'report' and report_date is null and artifact_key = kind)
  )
);

create or replace function public.astrolabe_publish_artifacts(p_artifacts jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  published_count integer;
begin
  if jsonb_typeof(p_artifacts) <> 'array' then
    raise exception 'p_artifacts must be a JSON array';
  end if;

  insert into public.published_artifacts(
    artifact_key, kind, report_date, schema_version, payload, updated_at
  )
  select
    row.artifact_key,
    row.kind,
    row.report_date,
    row.schema_version,
    row.payload,
    coalesce(row.updated_at, now())
  from jsonb_to_recordset(p_artifacts) as row(
    artifact_key text,
    kind text,
    report_date date,
    schema_version integer,
    payload jsonb,
    updated_at timestamptz
  )
  on conflict (artifact_key) do update set
    kind = excluded.kind,
    report_date = excluded.report_date,
    schema_version = excluded.schema_version,
    payload = excluded.payload,
    updated_at = excluded.updated_at;

  get diagnostics published_count = row_count;
  return jsonb_build_object('published', published_count);
end;
$$;

alter table public.published_artifacts enable row level security;

revoke all on public.published_artifacts from public, anon, authenticated;
grant select, insert, update, delete on public.published_artifacts to service_role;

revoke all on function public.astrolabe_publish_artifacts(jsonb)
from public, anon, authenticated;
grant execute on function public.astrolabe_publish_artifacts(jsonb) to service_role;
