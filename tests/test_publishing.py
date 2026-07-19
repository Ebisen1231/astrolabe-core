import json
from datetime import UTC, datetime

import pytest

from astrolabe import publishing
from astrolabe.ledger import store


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _exports(tmp_path):
    root = tmp_path / "exports"
    _write(root / "map.json", {"schema_version": 1, "latest_report_date": "2026-07-19"})
    _write(root / "layout.json", {"schema_version": 1, "positions": {}})
    _write(root / "index.json", {"schema_version": 1, "dates": ["2026-07-19", "2026-07-18"]})
    for report_date in ("2026-07-19", "2026-07-18"):
        _write(
            root / "reports" / f"{report_date}.json",
            {"schema_version": 1, "date": report_date, "topics": []},
        )
    return root


def test_publish_latest_exports_atomically_to_backend(ledger, tmp_path):
    result = publishing.publish_export_directory(
        ledger,
        _exports(tmp_path),
        now=datetime(2026, 7, 19, tzinfo=UTC),
    )
    assert result.artifact_keys == ("map", "layout", "index", "report:2026-07-19")
    assert store.get_published_artifact(ledger, "map")["payload"]["schema_version"] == 1
    assert store.get_published_artifact(ledger, "report:2026-07-18") is None


def test_first_publish_can_seed_all_report_exports(ledger, tmp_path):
    result = publishing.publish_export_directory(
        ledger,
        _exports(tmp_path),
        all_reports=True,
        now=datetime(2026, 7, 19, tzinfo=UTC),
    )
    assert result.artifact_keys[-2:] == ("report:2026-07-19", "report:2026-07-18")
    assert store.get_published_artifact(ledger, "report:2026-07-18") is not None


def test_publish_rejects_mixed_schema_before_writing(ledger, tmp_path):
    root = _exports(tmp_path)
    _write(root / "layout.json", {"schema_version": 2, "positions": {}})
    with pytest.raises(publishing.PublishError, match="schema_version"):
        publishing.publish_export_directory(ledger, root)
    assert store.get_published_artifact(ledger, "map") is None
