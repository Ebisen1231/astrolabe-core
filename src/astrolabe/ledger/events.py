"""events テーブルへの追記と読み出し。events は追記のみ(UPDATE/DELETE禁止)。"""

from __future__ import annotations

from datetime import UTC, datetime

from astrolabe.ledger.backend import LedgerBackend, as_backend

EVENT_TYPES = {
    "proposed",
    "selected",
    "dismissed",
    "marked_known",
    "task_created",
    "task_done",
    "quiz_result",
    "interview",
    "chat_note",
}


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def append_event(
    conn: LedgerBackend,
    type_: str,
    concept_id: str | None = None,
    payload: dict | None = None,
    ts: str | None = None,
) -> int:
    if type_ not in EVENT_TYPES:
        raise ValueError(f"未知のイベント型: {type_}")
    ids = as_backend(conn).append_events(
        [
            {
                "ts": ts or utcnow_iso(),
                "type": type_,
                "concept_id": concept_id,
                "payload": payload or {},
            }
        ]
    )
    return ids[0]


def append_events(conn: LedgerBackend, rows: list[dict]) -> list[int]:
    for row in rows:
        if row.get("type") not in EVENT_TYPES:
            raise ValueError(f"未知のイベント型: {row.get('type')}")
    return as_backend(conn).append_events(rows)


def load_events(conn: LedgerBackend) -> list[dict]:
    return as_backend(conn).load_events()
