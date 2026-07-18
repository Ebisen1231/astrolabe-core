from pathlib import Path


def _migration_sql() -> str:
    path = (
        Path(__file__).resolve().parent.parent
        / "supabase"
        / "migrations"
        / "202607190001_m3_ledger.sql"
    )
    return path.read_text(encoding="utf-8").lower()


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
    ):
        assert f"create table if not exists public.{table}" in sql
    for function in (
        "astrolabe_import_events",
        "astrolabe_replace_derived",
        "astrolabe_record_interview",
        "astrolabe_import_state",
    ):
        assert f"function public.{function}" in sql


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
