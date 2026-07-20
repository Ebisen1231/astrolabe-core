"""morning パイプラインのE2E(fixturesのみ・ネットワークとAPIキー不要)。"""

import pytest

from astrolabe.config import load_config
from astrolabe.ledger import events, store
from astrolabe.ledger.derive import concept_id_from_name
from astrolabe.llm.budget import BudgetExceededError, TokenBudget
from astrolabe.llm.fixtures import FixtureLLM
from astrolabe.pipeline.morning import collect_items, run_morning
from astrolabe.render import render_report

TODAY = "2026-07-18"


def _run(ledger, fixtures_dir, caps=None, html_output_dir=None):
    config = load_config(env={})
    budget = TokenBudget(caps or {"mini": 500_000, "flagship": 70_000})
    llm = FixtureLLM(fixtures_dir, budget)
    items = collect_items(config, offline_dir=fixtures_dir)
    outcome = run_morning(
        ledger,
        llm,
        items,
        today=TODAY,
        budget=budget,
        top_k=8,
        dry_run=True,
        html_output_dir=html_output_dir,
        html_path_base=html_output_dir,
    )
    return outcome


def test_offline_collect(fixtures_dir):
    config = load_config(env={})
    items = collect_items(config, offline_dir=fixtures_dir)
    assert len(items) == 10  # arXiv 6 + RSS 4
    assert {i["source"] for i in items} == {"arxiv", "fixtures-rss"}


def test_morning_end_to_end(ledger, fixtures_dir):
    outcome = _run(ledger, fixtures_dir)
    meta = outcome.meta
    assert (meta["collected"], meta["after_dedupe"], meta["fresh"]) == (10, 9, 9)
    assert meta["top_k"] == 8

    # 報告テキスト(決定的コードによる整形)
    text = outcome.report_text
    assert "ASTROLABE 朝の観測報告" in text
    assert "[dry-run]" in text
    assert "検証器つきRAG" in text
    assert "-- 実践課題 --" in text
    assert "マップ差分" in text
    assert "トークン使用" in text

    # 台帳: proposed イベントが一次データとして残る
    n_proposed = ledger.execute(
        "SELECT COUNT(*) FROM events WHERE type='proposed'"
    ).fetchone()[0]
    assert n_proposed == 3

    # concepts はイベントから導出される(トピック3 + related の stub 3)
    ids = {r["id"] for r in ledger.execute("SELECT id FROM concepts")}
    for name in ("検証器つきRAG(Self-Correcting RAG)", "リランキング(Reranking)", "RAG"):
        assert concept_id_from_name(name) in ids
    assert len(ids) == 6
    assert ledger.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 6

    # 報告アーカイブ
    row = ledger.execute("SELECT date FROM daily_reports").fetchone()
    assert row["date"] == TODAY
    stored = ledger.execute("SELECT items FROM daily_reports").fetchone()["items"]
    assert '"practice_task"' in stored


def test_morning_writes_html_and_records_relative_path(ledger, fixtures_dir, tmp_path):
    html_dir = tmp_path / "reports"
    outcome = _run(ledger, fixtures_dir, html_output_dir=html_dir)
    assert outcome.html_path == html_dir / f"{TODAY}.html"
    assert outcome.html_path.exists()
    row = ledger.execute(
        "SELECT html_path FROM daily_reports WHERE date = ?", (TODAY,)
    ).fetchone()
    assert row["html_path"] == f"{TODAY}.html"


def test_due_review_is_added_to_text_html_and_daily_report(ledger, fixtures_dir, tmp_path):
    events.append_event(
        ledger,
        "marked_known",
        "rag",
        {"name": "RAG"},
        ts="2026-07-01T00:00:00+09:00",
    )
    outcome = _run(ledger, fixtures_dir, html_output_dir=tmp_path)
    assert outcome.reviews[0]["concept_id"] == "rag"
    assert "今日の復習" in outcome.report_text
    assert "今日の復習" in outcome.html_path.read_text(encoding="utf-8")
    assert store.get_daily_report(ledger, TODAY)["items"]["reviews"] == outcome.reviews


def test_no_due_review_omits_rendered_sections(ledger, fixtures_dir, tmp_path):
    outcome = _run(ledger, fixtures_dir, html_output_dir=tmp_path)
    assert outcome.reviews == []
    assert "今日の復習" not in outcome.report_text
    assert "今日の復習" not in outcome.html_path.read_text(encoding="utf-8")


def test_second_run_dedupes_reported_items(ledger, fixtures_dir):
    _run(ledger, fixtures_dir)
    outcome2 = _run(ledger, fixtures_dir)
    # 前回トピックが引用した4アイテム(タイトル重複のRSS分も同キー)は既出扱い
    assert outcome2.meta["fresh"] == 5
    # 概念は増えない(同名トピックはマージされる)
    assert ledger.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 6
    # events は追記のみで増える
    assert (
        ledger.execute("SELECT COUNT(*) FROM events WHERE type='proposed'").fetchone()[0] == 6
    )
    # 同日の日次報告は置き換え
    assert ledger.execute("SELECT COUNT(*) FROM daily_reports").fetchone()[0] == 1


def test_budget_abort_leaves_ledger_untouched(ledger, fixtures_dir):
    with pytest.raises(BudgetExceededError):
        _run(ledger, fixtures_dir, caps={"mini": 10, "flagship": 10})
    assert ledger.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert ledger.execute("SELECT COUNT(*) FROM daily_reports").fetchone()[0] == 0


def test_render_empty_report():
    text = render_report("2026-07-18", [], "", {"collected": 0, "after_dedupe": 0, "fresh": 0})
    assert "本日の新規トピックはない" in text
