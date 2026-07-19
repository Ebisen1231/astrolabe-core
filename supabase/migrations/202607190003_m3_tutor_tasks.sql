-- Astrolabe M3第2便: tasks行と対応イベントを同一トランザクションで更新するRPC。
-- SQL Editorで施主レビュー後に適用する。公開ロールには一切公開しない。

create or replace function public.astrolabe_create_task(
  p_task jsonb,
  p_event jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  created public.tasks%rowtype;
begin
  if coalesce(p_task->>'kind', '') not in ('read', 'implement', 'quiz', 'build_app_feature') then
    raise exception 'invalid task kind';
  end if;
  if btrim(coalesce(p_task->>'title', '')) = '' then
    raise exception 'task title is required';
  end if;

  insert into public.tasks(concept_id, title, kind, status, est_minutes, created_at)
  values (
    nullif(p_task->>'concept_id', ''),
    p_task->>'title',
    p_task->>'kind',
    'open',
    nullif(p_task->>'est_minutes', '')::integer,
    coalesce(nullif(p_task->>'created_at', '')::timestamptz, now())
  )
  returning * into created;

  insert into public.events(ts, type, concept_id, payload)
  values (
    coalesce(nullif(p_event->>'ts', '')::timestamptz, created.created_at),
    'task_created',
    created.concept_id,
    coalesce(p_event->'payload', '{}'::jsonb) || jsonb_build_object('task_id', created.id)
  );

  return to_jsonb(created);
end;
$$;

create or replace function public.astrolabe_complete_task(
  p_task_id bigint,
  p_evidence text,
  p_done_at timestamptz,
  p_event_payload jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  target public.tasks%rowtype;
begin
  if btrim(coalesce(p_evidence, '')) = '' then
    raise exception 'task evidence is required';
  end if;

  select * into target from public.tasks where id = p_task_id for update;
  if not found then
    raise exception 'task id=% not found', p_task_id;
  end if;
  if target.status = 'done' then
    raise exception 'task id=% already done', p_task_id;
  end if;

  update public.tasks
  set status = 'done', evidence = p_evidence, done_at = p_done_at
  where id = p_task_id
  returning * into target;

  insert into public.events(ts, type, concept_id, payload)
  values (
    p_done_at,
    'task_done',
    target.concept_id,
    coalesce(p_event_payload, '{}'::jsonb)
      || jsonb_build_object('task_id', target.id, 'evidence', p_evidence)
  );

  return to_jsonb(target);
end;
$$;

revoke all on function public.astrolabe_create_task(jsonb, jsonb)
from public, anon, authenticated;
revoke all on function public.astrolabe_complete_task(bigint, text, timestamptz, jsonb)
from public, anon, authenticated;

grant execute on function public.astrolabe_create_task(jsonb, jsonb) to service_role;
grant execute on function public.astrolabe_complete_task(bigint, text, timestamptz, jsonb)
to service_role;
