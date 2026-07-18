"""M2 UI向けexportの契約・report_date判定・bytes決定性。"""

import json

from astrolabe import exporter
from astrolabe.ledger import derive, events, store


def _seed_ledger(conn):
    events.append_event(
        conn,
        "proposed",
        concept_id="rag",
        payload={
            "name": "RAG",
            "kind": "concept",
            "summary": "検索結果を根拠に回答する。",
            "source_urls": ["https://example.com/rag"],
            "report_date": "2026-07-18",
            "edges": [
                {
                    "dst": "retrieval",
                    "dst_name": "検索",
                    "type": "prerequisite",
                    "weight": 1.0,
                }
            ],
        },
        ts="2026-07-18T00:00:00+00:00",
    )
    events.append_event(
        conn,
        "proposed",
        concept_id="agents",
        payload={
            "name": "LLMエージェント",
            "kind": "concept",
            "summary": "ツールを使って目標を進める。",
            "source_urls": ["https://example.com/agents"],
            "report_date": "2026-07-19",
        },
        ts="2026-07-19T00:00:00+00:00",
    )
    derive.rebuild(conn)
    store.save_daily_report(
        conn,
        "2026-07-18",
        {
            "topics": [
                {
                    "name": "RAG",
                    "summary": "検索結果を根拠に回答する。",
                    "why_now": "基礎だから。",
                    "learn_content": "検索と生成を分けて考える。",
                    "source_urls": ["https://example.com/rag"],
                }
            ],
            "meta": {"usage": {"mini": {"used": 10, "cap": 100}}},
        },
        "RAGが加わった。",
    )
    store.save_daily_report(
        conn,
        "2026-07-19",
        {
            "topics": [
                {
                    "name": "LLMエージェント",
                    "summary": "ツールを使って目標を進める。",
                    "why_now": "実装段階だから。",
                    "learn_content": "観測・判断・行動に分解する。",
                    "source_urls": ["https://example.com/agents"],
                }
            ],
            "meta": {"usage": {"flagship": {"used": 20, "cap": 200}}},
        },
        "エージェントのノードが加わった。",
    )


def _snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_export_contract_and_report_date_today_nodes(ledger, tmp_path):
    _seed_ledger(ledger)
    out = tmp_path / "exports"

    result = exporter.export_ledger(ledger, out)

    assert result.report_dates == ("2026-07-18", "2026-07-19")
    map_data = json.loads((out / "map.json").read_text(encoding="utf-8"))
    assert map_data["schema_version"] == 1
    assert map_data["latest_report_date"] == "2026-07-19"
    assert map_data["map_delta_text"] == "エージェントのノードが加わった。"
    assert map_data["today_node_ids"] == ["agents"]
    assert map_data["concepts"][0].keys() >= {"summary", "source_urls", "first_seen"}

    index_data = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert index_data == {
        "schema_version": 1,
        "dates": ["2026-07-19", "2026-07-18"],
    }
    report = json.loads(
        (out / "reports" / "2026-07-19.json").read_text(encoding="utf-8")
    )
    assert report["schema_version"] == 1
    assert report["topics"][0]["learn_content"].startswith("観測")
    assert json.loads((out / "layout.json").read_text())["schema_version"] == 1


def test_two_consecutive_exports_are_byte_identical_for_every_file(ledger, tmp_path):
    _seed_ledger(ledger)
    out = tmp_path / "exports"

    exporter.export_ledger(ledger, out)
    first = _snapshot(out)
    exporter.export_ledger(ledger, out)
    second = _snapshot(out)

    assert first == second
    assert set(first) == {
        "index.json",
        "layout.json",
        "map.json",
        "reports/2026-07-18.json",
        "reports/2026-07-19.json",
    }
