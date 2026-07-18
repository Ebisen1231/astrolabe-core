"""events一次データの決定的JSONL snapshotとSQLite復元。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from astrolabe.ledger import db, derive
from astrolabe.ledger.backend import as_backend


class SnapshotError(RuntimeError):
    """snapshotが不正、または安全に復元できない。"""


@dataclass(frozen=True)
class SnapshotResult:
    path: Path
    event_count: int


@dataclass(frozen=True)
class RestoreResult:
    path: Path
    event_count: int
    concept_count: int
    edge_count: int


def _line(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_events_jsonl(ledger, path: Path) -> SnapshotResult:
    events = sorted(as_backend(ledger).load_events(), key=lambda row: int(row["id"]))
    text = "".join(_line(row) + "\n" for row in events)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    return SnapshotResult(path=path, event_count=len(events))


def load_events_jsonl(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SnapshotError(f"snapshotを読み込めない: {path}") from exc
    rows: list[dict] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SnapshotError(f"snapshot {line_number}行目が不正なJSON") from exc
        if not isinstance(row, dict) or not all(
            key in row for key in ("id", "ts", "type", "concept_id", "payload")
        ):
            raise SnapshotError(f"snapshot {line_number}行目のevent契約が不正")
        rows.append(row)
    ids = [int(row["id"]) for row in rows]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise SnapshotError("snapshotのevent idが昇順一意ではない")
    return rows


def restore_sqlite(snapshot_path: Path, output_path: Path) -> RestoreResult:
    if output_path.exists():
        raise SnapshotError(f"復元先が既に存在するため上書きしない: {output_path}")
    rows = load_events_jsonl(snapshot_path)
    connection = db.init_db(output_path)
    backend = as_backend(connection)
    try:
        backend.append_events(rows, preserve_ids=True)
        profile: dict = {}
        for row in rows:
            if row["type"] == "interview":
                candidate = (row.get("payload") or {}).get("profile")
                if isinstance(candidate, dict):
                    profile = candidate
        if profile:
            backend.import_state(profile, [], [])
        concept_count, edge_count = derive.rebuild(backend)
    except Exception:
        connection.close()
        output_path.unlink(missing_ok=True)
        raise
    connection.close()
    return RestoreResult(
        path=output_path,
        event_count=len(rows),
        concept_count=concept_count,
        edge_count=edge_count,
    )
