-- Supabaseのsafe-update設定下でも導出テーブルを原子的に再構築できるようにする。
-- 主キー列はNOT NULLなので、条件は全行に一致しつつ無条件DELETEにはならない。

create or replace function public.astrolabe_replace_derived(
  p_concepts jsonb,
  p_edges jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if jsonb_typeof(p_concepts) <> 'array' or jsonb_typeof(p_edges) <> 'array' then
    raise exception 'derived payloads must be JSON arrays';
  end if;

  delete from public.edges where src is not null;
  delete from public.concepts where id is not null;

  insert into public.concepts(
    id, name, kind, status, confidence, summary, source_urls, first_seen, last_touched
  )
  select
    row.id,
    row.name,
    coalesce(row.kind, 'concept'),
    coalesce(row.status, 'unknown'),
    coalesce(row.confidence, 0.0),
    coalesce(row.summary, ''),
    coalesce(row.source_urls, '[]'::jsonb),
    row.first_seen,
    row.last_touched
  from jsonb_to_recordset(p_concepts) as row(
    id text,
    name text,
    kind text,
    status text,
    confidence double precision,
    summary text,
    source_urls jsonb,
    first_seen timestamptz,
    last_touched timestamptz
  );

  insert into public.edges(src, dst, type, weight, created_by, created_at)
  select
    row.src,
    row.dst,
    row.type,
    coalesce(row.weight, 1.0),
    row.created_by,
    row.created_at
  from jsonb_to_recordset(p_edges) as row(
    src text,
    dst text,
    type text,
    weight double precision,
    created_by text,
    created_at timestamptz
  );

  return jsonb_build_object(
    'concepts', (select count(*) from public.concepts),
    'edges', (select count(*) from public.edges)
  );
end;
$$;

revoke all on function public.astrolabe_replace_derived(jsonb, jsonb)
from public, anon, authenticated;
grant execute on function public.astrolabe_replace_derived(jsonb, jsonb) to service_role;
