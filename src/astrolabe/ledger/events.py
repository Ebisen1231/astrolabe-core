"""events テーブルへの追記と読み出し。events は追記のみ(UPDATE/DELETE禁止)。"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

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
    conn: sqlite3.Connection,
    type_: str,
    concept_id: str | None = None,
    payload: dict | None = None,
    ts: str | None = None,
) -> int:
    if type_ not in EVENT_TYPES:
        raise ValueError(f"未知のイベント型: {type_}")
    cur = conn.execute(
        "INSERT INTO events(ts, type, concept_id, payload) VALUES(?, ?, ?, ?)",
        (ts or utcnow_iso(), type_, concept_id, json.dumps(payload or {}, ensure_ascii=False)),
    )
    return int(cur.lastrowid or 0)


def load_events(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT id, ts, type, concept_id, payload FROM events").fetchall()
    return [
        {
            "id": r["id"],
            "ts": r["ts"],
            "type": r["type"],
            "concept_id": r["concept_id"],
            "payload": json.loads(r["payload"] or "{}"),
        }
        for r in rows
    ]
