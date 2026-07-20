"""M1の単一HTML報告生成。ネットワーク・APIキー不要。"""

from pathlib import Path

from astrolabe.render_html import (
    feedback_issue_url,
    render_html_report,
    write_html_report,
)

TOPICS = [
    {
        "name": "検証器つきRAG <script>alert(1)</script>",
        "kind": "concept",
        "summary": "生成後に根拠を検証する。",
        "why_now": "エージェントの信頼性に直結する。",
        "learn_content": "- 取得\n- 検証\n- 修正",
        "practice_task": {
            "title": "失敗例を3件集める <script>alert(2)</script>",
            "kind": "implement",
            "est_minutes": 10,
        },
        "est_minutes": 10,
        "source_urls": ["https://example.com/paper?a=1&b=2", "javascript:alert(1)"],
    }
]

CONCEPTS = [
    {
        "id": "検証器つきrag-script-alert-1-script",
        "name": "検証器つきRAG",
        "kind": "concept",
        "status": "unknown",
    },
    {"id": "rag", "name": "RAG", "kind": "concept", "status": "learned"},
    {"id": "retrieval", "name": "検索", "kind": "concept", "status": "unknown"},
]

EDGES = [
    {
        "src": "検証器つきrag-script-alert-1-script",
        "dst": "rag",
        "type": "prerequisite",
        "weight": 1.0,
    },
    {"src": "rag", "dst": "retrieval", "type": "related", "weight": 1.0},
]


def test_html_contains_star_map_feedback_and_escaped_content():
    output = render_html_report(
        "2026-07-18",
        TOPICS,
        "金の環が1つ増えました。",
        CONCEPTS,
        EDGES,
    )

    assert output.startswith("<!doctype html>")
    assert "cytoscape@3.34.0" in output
    assert "#0B1226" in output
    assert "#D8A03D" in output
    assert "#E8B84B" in output
    assert "#7C8FB8" in output
    assert "#5C74AB" in output
    assert "#3A4E7E" in output
    assert "'font-size': 11" in output
    assert "今日の提案(金の環)" in output
    assert "前提(実線)" in output
    assert "関連(破線)" in output

    for label in ("学ぶ", "気になる", "もう知っている", "興味がない"):
        assert f">{label}</a>" in output
    assert output.count("issues/new?") == 4

    assert "<script>alert(1)</script>" not in output
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in output
    assert "javascript:alert(1)" not in output
    assert "失敗例を3件集める" in output
    assert "<script>alert(2)</script>" not in output
    assert "\\u003cscript\\u003e" not in output  # topic labelはHTMLエスケープされる


def test_feedback_url_uses_machine_readable_title():
    url = feedback_issue_url(
        "Ebisen1231/astrolabe-ledger",
        "selected-later",
        "rag",
        "RAG",
        "2026-07-18",
    )
    assert "title=%5Bfb%5D+selected-later+rag" in url
    assert "astrolabe-feedback-v1" in url


def test_write_html_report_is_atomic_and_named_by_date(tmp_path: Path):
    destination = write_html_report(
        tmp_path,
        "2026-07-18",
        TOPICS,
        "今日の変化",
        CONCEPTS,
        EDGES,
    )
    assert destination == tmp_path / "2026-07-18.html"
    assert destination.exists()
    assert not (tmp_path / "2026-07-18.html.tmp").exists()
    assert "今日の変化" in destination.read_text(encoding="utf-8")
