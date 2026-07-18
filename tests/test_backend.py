from astrolabe.ledger import derive, events, store
from astrolabe.ledger.backend import SQLiteBackend, as_backend


def test_sqlite_connection_is_adapted_without_breaking_existing_api(ledger):
    backend = as_backend(ledger)

    assert isinstance(backend, SQLiteBackend)
    events.append_event(
        ledger,
        "proposed",
        "rag",
        {"name": "RAG", "kind": "concept", "report_date": "2026-07-19"},
        "2026-07-19T00:00:00+00:00",
    )
    assert derive.rebuild(ledger) == (1, 0)
    assert store.list_concepts(ledger)[0]["name"] == "RAG"
    assert ledger.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_llm_usage_is_idempotent_per_run(ledger):
    usage = {"mini": {"used": 10}, "flagship": {"used": 4}}
    store.save_llm_usage(ledger, "2026-07-19", "run-1", usage)
    store.save_llm_usage(ledger, "2026-07-19", "run-1", usage)

    rows = ledger.execute(
        "SELECT model_role, tokens FROM llm_usage ORDER BY model_role"
    ).fetchall()
    assert [tuple(row) for row in rows] == [("flagship", 4), ("mini", 10)]
