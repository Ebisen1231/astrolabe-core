from astrolabe.ledger import db, derive, events, store
from astrolabe.ledger.backend import SQLiteBackend
from astrolabe.ledger.migration import migrate_sqlite_to_supabase


def test_migration_is_idempotent_and_derived_rows_match(tmp_path):
    source = db.init_db(tmp_path / "source.db")
    target_connection = db.init_db(tmp_path / "target.db")
    target = SQLiteBackend(target_connection)
    try:
        store.record_interview(
            source,
            {
                "interests": {"agents": 1.0},
                "goals": "learn",
                "background": "web",
                "time_budget": "30m",
            },
            ["Transformer"],
        )
        events.append_event(
            source,
            "proposed",
            "rag",
            {
                "name": "RAG",
                "summary": "retrieval augmented generation",
                "source_urls": ["https://example.com/rag"],
                "report_date": "2026-07-19",
                "edges": [
                    {
                        "dst": "transformer",
                        "dst_name": "Transformer",
                        "type": "related",
                    }
                ],
            },
            "2026-07-19T00:00:00+00:00",
        )
        derive.rebuild(source)
        store.save_daily_report(
            source,
            "2026-07-19",
            {"topics": [{"name": "RAG"}], "meta": {}},
            "RAGを追加",
        )

        first = migrate_sqlite_to_supabase(source, target)
        second = migrate_sqlite_to_supabase(source, target)

        assert first == second
        assert first.event_count == 3
        assert target_connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
        assert store.list_concepts(source) == store.list_concepts(target)
        assert store.list_edges(source) == store.list_edges(target)
        assert store.get_profile(target) == store.get_profile(source)
        assert store.list_daily_reports(target) == store.list_daily_reports(source)
    finally:
        source.close()
        target.close()
