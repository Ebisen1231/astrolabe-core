from pathlib import Path


def _migration_sql() -> str:
    migration_dir = (
        Path(__file__).resolve().parent.parent
        / "supabase"
        / "migrations"
    )
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(migration_dir.glob("*.sql"))
    ).lower()


def test_migration_defines_all_m3_tables_and_rpc():
    sql = _migration_sql()
    for table in (
        "events",
        "concepts",
        "edges",
        "tasks",
        "profile",
        "daily_reports",
        "llm_usage",
        "published_artifacts",
    ):
        assert f"create table if not exists public.{table}" in sql
    for function in (
        "astrolabe_import_events",
        "astrolabe_replace_derived",
        "astrolabe_record_interview",
        "astrolabe_import_state",
        "astrolabe_create_task",
        "astrolabe_complete_task",
        "astrolabe_publish_artifacts",
    ):
        assert f"function public.{function}" in sql


def test_tutor_task_rpcs_are_service_role_only_and_fix_search_path():
    latest = (
        Path(__file__).resolve().parent.parent
        / "supabase"
        / "migrations"
        / "202607190003_m3_tutor_tasks.sql"
    ).read_text(encoding="utf-8").lower()
    for function in ("astrolabe_create_task", "astrolabe_complete_task"):
        assert f"create or replace function public.{function}" in latest
        assert f"grant execute on function public.{function}" in latest
    assert latest.count("set search_path = public, pg_temp") == 2
    assert latest.count("from public, anon, authenticated") == 2
    assert latest.count("to service_role") == 2


def test_events_are_append_only_even_for_service_role():
    sql = _migration_sql()
    assert "before update or delete on public.events" in sql
    assert "events is append-only" in sql
    assert (
        "revoke update, delete, truncate on public.events "
        "from anon, authenticated, service_role"
    ) in sql
    assert "grant select, insert on public.events to service_role" in sql


def test_import_rpc_adjusts_identity_sequence():
    sql = _migration_sql()
    assert "pg_get_serial_sequence('public.events', 'id')" in sql
    assert "setval" in sql


def test_derived_replacement_uses_safe_update_compatible_delete():
    latest = (
        Path(__file__).resolve().parent.parent
        / "supabase"
        / "migrations"
        / "202607190002_fix_derived_replace.sql"
    ).read_text(encoding="utf-8").lower()
    assert "delete from public.edges where src is not null" in latest
    assert "delete from public.concepts where id is not null" in latest
    assert "delete from public.edges;" not in latest
    assert "delete from public.concepts;" not in latest


def test_published_artifact_rpc_is_service_role_only_with_rls_and_fixed_search_path():
    latest = (
        Path(__file__).resolve().parent.parent
        / "supabase"
        / "migrations"
        / "202607190004_m3_published_artifacts.sql"
    ).read_text(encoding="utf-8").lower()
    assert "alter table public.published_artifacts enable row level security" in latest
    assert "set search_path = public, pg_temp" in latest
    assert "from public, anon, authenticated" in latest
    assert (
        "grant execute on function public.astrolabe_publish_artifacts(jsonb) to service_role"
        in latest
    )
    assert (
        "grant select, insert, update, delete on public.published_artifacts to service_role"
        in latest
    )
