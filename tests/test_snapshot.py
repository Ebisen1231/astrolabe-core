from typer.testing import CliRunner

from astrolabe.cli import app
from astrolabe.ledger import db, derive, events, store
from astrolabe.ledger.snapshot import restore_sqlite, write_events_jsonl

runner = CliRunner()


def _seed(ledger) -> None:
    store.record_interview(
        ledger,
        {
            "interests": {"rag": 0.9},
            "goals": "understand RAG",
            "background": "web",
            "time_budget": "20m",
        },
        ["Transformer"],
    )
    events.append_event(
        ledger,
        "proposed",
        "rag",
        {"name": "RAG", "report_date": "2026-07-19"},
        "2026-07-19T00:00:00+00:00",
    )
    derive.rebuild(ledger)


def test_snapshot_is_byte_deterministic_and_restorable(tmp_path, ledger):
    _seed(ledger)
    snapshot_path = tmp_path / "snapshots" / "events.jsonl"

    first = write_events_jsonl(ledger, snapshot_path)
    first_bytes = snapshot_path.read_bytes()
    second = write_events_jsonl(ledger, snapshot_path)

    assert first == second
    assert snapshot_path.read_bytes() == first_bytes
    assert first_bytes.endswith(b"\n")

    restored_path = tmp_path / "restored.db"
    result = restore_sqlite(snapshot_path, restored_path)
    restored = db.open_ledger(restored_path)
    try:
        assert result.event_count == len(events.load_events(ledger))
        assert events.load_events(restored) == events.load_events(ledger)
        assert store.list_concepts(restored) == store.list_concepts(ledger)
        assert store.list_edges(restored) == store.list_edges(ledger)
        assert store.get_profile(restored) == store.get_profile(ledger)
    finally:
        restored.close()


def test_snapshot_cli_defaults_next_to_sqlite_ledger(tmp_path, monkeypatch):
    path = tmp_path / "ledger.db"
    connection = db.init_db(path)
    _seed(connection)
    connection.close()
    monkeypatch.setenv("ASTROLABE_LEDGER_PATH", str(path))

    result = runner.invoke(app, ["snapshot"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "snapshots" / "events.jsonl").exists()


def test_restore_snapshot_cli(tmp_path, ledger):
    _seed(ledger)
    snapshot_path = tmp_path / "events.jsonl"
    write_events_jsonl(ledger, snapshot_path)
    restored = tmp_path / "from-cli.db"

    result = runner.invoke(
        app,
        ["restore-snapshot", str(snapshot_path), "--out", str(restored)],
    )

    assert result.exit_code == 0, result.output
    assert restored.exists()


def test_restore_never_overwrites_existing_file(tmp_path, ledger):
    snapshot_path = tmp_path / "events.jsonl"
    write_events_jsonl(ledger, snapshot_path)
    existing = tmp_path / "existing.db"
    existing.write_bytes(b"sentinel")

    result = runner.invoke(
        app,
        ["restore-snapshot", str(snapshot_path), "--out", str(existing)],
    )

    assert result.exit_code == 2
    assert existing.read_bytes() == b"sentinel"
