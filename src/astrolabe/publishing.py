"""既存の決定的JSON exportをSupabase公開artifactへ明示publishする。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from astrolabe.exporter import SCHEMA_VERSION
from astrolabe.ledger import store
from astrolabe.ledger.backend import LedgerBackend


class PublishError(ValueError):
    """export契約が不正、または必要ファイルが欠けている。"""


@dataclass(frozen=True)
class PublishResult:
    published: int
    artifact_keys: tuple[str, ...]


def _read_export(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PublishError(f"{label}を読み込めない") from exc
    except json.JSONDecodeError as exc:
        raise PublishError(f"{label}がJSONとして壊れている") from exc
    if not isinstance(value, dict):
        raise PublishError(f"{label}のルートがオブジェクトではない")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise PublishError(
            f"{label}のschema_versionが未対応: {value.get('schema_version')!r}"
        )
    return value


def load_export_artifacts(
    exports_dir: Path,
    *,
    all_reports: bool = False,
    now: datetime | None = None,
) -> list[dict]:
    """map/layout/indexと最新(または全)reportを同じschemaのまま読み込む。"""
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        raise PublishError("nowはtimezone-awareである必要がある")
    updated_at = instant.astimezone(UTC).isoformat()
    map_payload = _read_export(exports_dir / "map.json", "map.json")
    layout_payload = _read_export(exports_dir / "layout.json", "layout.json")
    index_payload = _read_export(exports_dir / "index.json", "index.json")
    artifacts = [
        {
            "artifact_key": kind,
            "kind": kind,
            "report_date": None,
            "schema_version": SCHEMA_VERSION,
            "payload": payload,
            "updated_at": updated_at,
        }
        for kind, payload in (
            ("map", map_payload),
            ("layout", layout_payload),
            ("index", index_payload),
        )
    ]

    raw_dates = index_payload.get("dates")
    if not isinstance(raw_dates, list) or any(not isinstance(value, str) for value in raw_dates):
        raise PublishError("index.jsonのdatesが文字列配列ではない")
    report_dates = raw_dates if all_reports else raw_dates[:1]
    for report_date in report_dates:
        payload = _read_export(
            exports_dir / "reports" / f"{report_date}.json",
            f"reports/{report_date}.json",
        )
        if payload.get("date") != report_date:
            raise PublishError(f"reports/{report_date}.jsonの日付が一致しない")
        artifacts.append(
            {
                "artifact_key": f"report:{report_date}",
                "kind": "report",
                "report_date": report_date,
                "schema_version": SCHEMA_VERSION,
                "payload": payload,
                "updated_at": updated_at,
            }
        )
    return artifacts


def publish_export_directory(
    ledger: LedgerBackend,
    exports_dir: Path,
    *,
    all_reports: bool = False,
    now: datetime | None = None,
) -> PublishResult:
    artifacts = load_export_artifacts(exports_dir, all_reports=all_reports, now=now)
    published = store.publish_artifacts(ledger, artifacts)
    if published != len(artifacts):
        raise PublishError(
            f"公開artifact件数が一致しない: expected={len(artifacts)} actual={published}"
        )
    return PublishResult(
        published=published,
        artifact_keys=tuple(artifact["artifact_key"] for artifact in artifacts),
    )
