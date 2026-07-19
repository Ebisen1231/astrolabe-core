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


def test_llm_usage_can_separate_tutor_and_total(ledger):
    store.save_llm_usage(ledger, "2026-07-19", "morning-1", {"flagship": {"used": 35_000}})
    store.save_llm_usage(
        ledger, "2026-07-19", "tutor-session-a", {"flagship": {"used": 4_000}}
    )
    store.save_llm_usage(
        ledger, "2026-07-19", "tutor-session-b", {"flagship": {"used": 3_000}}
    )

    assert store.get_llm_usage_total(ledger, "2026-07-19", "flagship") == 42_000
    assert (
        store.get_llm_usage_total(ledger, "2026-07-19", "flagship", "tutor-") == 7_000
    )
    assert (
        store.get_llm_usage_for_run(ledger, "2026-07-19", "tutor-session-a", "flagship")
        == 4_000
    )


def test_task_create_and_complete_append_events_and_rebuild(ledger):
    created = store.create_task(
        ledger,
        {
            "concept_id": "position-encoding",
            "title": "位置エンコーディングを10分で読む",
            "kind": "read",
            "est_minutes": 10,
            "created_at": "2026-07-19T01:00:00+00:00",
        },
        {
            "ts": "2026-07-19T01:00:00+00:00",
            "type": "task_created",
            "concept_id": "position-encoding",
            "payload": {
                "name": "位置エンコーディング",
                "edges": [
                    {
                        "src": "rope",
                        "dst": "position-encoding",
                        "dst_name": "位置エンコーディング",
                        "type": "prerequisite",
                    }
                ],
            },
        },
    )
    assert created["status"] == "open"
    assert store.list_concepts(ledger)[0]["status"] == "learning"
    assert store.list_edges(ledger)[0]["src"] == "rope"

    completed = store.complete_task(
        ledger,
        created["id"],
        "ノートを3行作成",
        "2026-07-19T01:15:00+00:00",
        {"confidence_delta": 0.3},
    )
    assert completed["status"] == "done"
    assert completed["evidence"] == "ノートを3行作成"
    assert {row["type"] for row in as_backend(ledger).load_events()} == {
        "task_created",
        "task_done",
    }
    concept = next(row for row in store.list_concepts(ledger) if row["id"] == "position-encoding")
    assert concept["confidence"] == 0.3
