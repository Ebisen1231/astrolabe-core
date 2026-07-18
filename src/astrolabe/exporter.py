"""SQLite台帳からM2 UI向けの静的JSONを決定的に生成する。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from astrolabe import layout
from astrolabe.ledger import store

SCHEMA_VERSION = 1


class ExportError(ValueError):
    """台帳または既存exportが安全に書き出せない。"""


@dataclass(frozen=True)
class ExportResult:
    output_dir: Path
    report_dates: tuple[str, ...]
    concept_count: int
    edge_count: int


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def _load_existing_layout(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"既存layout.jsonを読み込めない: {path}") from exc
    if not isinstance(value, dict):
        raise ExportError("既存layout.jsonのルートがオブジェクトではない")
    return value


def _first_proposed_report_dates(conn: sqlite3.Connection) -> dict[str, str]:
    first_dates: dict[str, str] = {}
    rows = conn.execute(
        "SELECT concept_id, payload FROM events"
        " WHERE type = 'proposed' AND concept_id IS NOT NULL ORDER BY id"
    ).fetchall()
    for row in rows:
        concept_id = str(row["concept_id"])
        if concept_id in first_dates:
            continue
        try:
            payload = json.loads(row["payload"] or "{}")
        except json.JSONDecodeError as exc:
            raise ExportError(f"proposedイベントのpayloadが不正: {concept_id}") from exc
        report_date = str(payload.get("report_date", ""))
        if report_date:
            first_dates[concept_id] = report_date
    return first_dates


def _validate_report_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ExportError(f"日次報告の日付がYYYY-MM-DDではない: {value!r}") from exc
    if parsed.isoformat() != value:
        raise ExportError(f"日次報告の日付が正規形ではない: {value!r}")
    return value


def export_ledger(conn: sqlite3.Connection, output_dir: Path) -> ExportResult:
    """台帳全体をUI契約へ書き出す。生成時刻は含めず、同一台帳なら同一bytesにする。"""
    concepts = store.list_concepts(conn)
    edges = store.list_edges(conn)
    reports = store.list_daily_reports(conn)
    report_dates = tuple(_validate_report_date(str(report["date"])) for report in reports)
    latest = reports[-1] if reports else None
    latest_date = str(latest["date"]) if latest else None
    first_dates = _first_proposed_report_dates(conn)
    today_node_ids = sorted(
        concept["id"]
        for concept in concepts
        if latest_date is not None and first_dates.get(concept["id"]) == latest_date
    )

    layout_path = output_dir / "layout.json"
    previous_layout = _load_existing_layout(layout_path)
    try:
        layout_export = layout.build_layout(
            [str(concept["id"]) for concept in concepts], edges, previous_layout
        )
    except layout.LayoutError as exc:
        raise ExportError(str(exc)) from exc

    map_export = {
        "schema_version": SCHEMA_VERSION,
        "latest_report_date": latest_date,
        "map_delta_text": str(latest["map_delta_text"]) if latest else "",
        "today_node_ids": today_node_ids,
        "concepts": concepts,
        "edges": edges,
    }
    index_export = {
        "schema_version": SCHEMA_VERSION,
        "dates": list(reversed(report_dates)),
    }

    _write_json(output_dir / "map.json", map_export)
    _write_json(layout_path, layout_export)
    _write_json(output_dir / "index.json", index_export)
    for report in reports:
        items = report["items"]
        report_export = {
            "schema_version": SCHEMA_VERSION,
            "date": report["date"],
            "map_delta_text": report["map_delta_text"],
            "topics": items.get("topics", []),
            "meta": items.get("meta", {}),
        }
        _write_json(output_dir / "reports" / f"{report['date']}.json", report_export)

    return ExportResult(
        output_dir=output_dir,
        report_dates=report_dates,
        concept_count=len(concepts),
        edge_count=len(edges),
    )
