"""SQLite一次台帳をSupabaseへ冪等移行し、導出結果を完全照合する。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from astrolabe.ledger import derive
from astrolabe.ledger.backend import LedgerBackend, as_backend


class MigrationVerificationError(RuntimeError):
    """移行後のeventsまたは導出結果がSQLiteと一致しない。"""


@dataclass(frozen=True)
class MigrationResult:
    event_count: int
    concept_count: int
    edge_count: int
    task_count: int
    report_count: int


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _assert_equal(label: str, expected: object, actual: object) -> None:
    if _canonical(expected) != _canonical(actual):
        raise MigrationVerificationError(f"{label}がSQLiteとSupabaseで一致しない")


def migrate_sqlite_to_supabase(source, target: LedgerBackend) -> MigrationResult:
    """eventsをID保持で移し、両バックエンドの再導出結果を完全照合する。"""
    source_backend = as_backend(source)
    source_events = source_backend.load_events()

    # 比較元もeventsから作り直し、古い導出テーブルを正として扱わない。
    derive.rebuild(source_backend)
    expected_concepts = source_backend.list_concepts()
    expected_edges = source_backend.list_edges()

    target.append_events(source_events, preserve_ids=True)
    target_events = target.load_events()
    if len(target_events) != len(source_events):
        raise MigrationVerificationError(
            f"events件数不一致: SQLite {len(source_events)} / Supabase {len(target_events)}"
        )
    _assert_equal("events", source_events, target_events)

    derive.rebuild(target)
    actual_concepts = target.list_concepts()
    actual_edges = target.list_edges()
    _assert_equal("concepts", expected_concepts, actual_concepts)
    _assert_equal("edges", expected_edges, actual_edges)

    profile = source_backend.get_profile()
    tasks = source_backend.list_tasks()
    reports = source_backend.list_daily_reports()
    target.import_state(profile, tasks, reports)

    return MigrationResult(
        event_count=len(source_events),
        concept_count=len(expected_concepts),
        edge_count=len(expected_edges),
        task_count=len(tasks),
        report_count=len(reports),
    )
