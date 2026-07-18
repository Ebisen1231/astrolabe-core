"""CLIの受け入れ条件に対応するテスト(typer CliRunner、すべてオフライン)。"""

import contextlib
from pathlib import Path

from typer.testing import CliRunner

from astrolabe.cli import app
from astrolabe.ledger import db, store

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
runner = CliRunner()


def all_output(result) -> str:
    out = result.output
    with contextlib.suppress(ValueError, AttributeError):
        out += result.stderr
    return out


# --- init -----------------------------------------------------------------


def test_init_requires_ledger_path_env():
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 2  # 暗黙のフォールバックで作らない


def test_init_creates_ledger(tmp_path, monkeypatch):
    path = tmp_path / "ledger.db"
    monkeypatch.setenv("ASTROLABE_LEDGER_PATH", str(path))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, all_output(result)
    assert path.exists()
    # 再実行しても壊れない
    assert runner.invoke(app, ["init"]).exit_code == 0


# --- interview ------------------------------------------------------------

INTERVIEW_INPUT = "\n".join(
    [
        "RAGとエージェント設計を体系的に理解する",  # 目標
        "Web開発3年、ML初学者",  # 背景
        "30分",  # 時間
        "rag:0.9, agents, evals:0.7",  # 興味
        "transformer, attention",  # 既知1行目
        "",  # 既知入力の終了
        "y",  # 確認
    ]
) + "\n"


def test_interview_records_profile_and_known_concepts(tmp_path, monkeypatch):
    path = tmp_path / "ledger.db"
    monkeypatch.setenv("ASTROLABE_LEDGER_PATH", str(path))
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["interview"], input=INTERVIEW_INPUT)
    assert result.exit_code == 0, all_output(result)

    conn = db.open_ledger(path)
    profile = store.get_profile(conn)
    assert profile["goals"].startswith("RAG")
    assert profile["interests"] == {"rag": 0.9, "agents": 1.0, "evals": 0.7}

    counts = {
        r["type"]: r["n"]
        for r in conn.execute("SELECT type, COUNT(*) AS n FROM events GROUP BY type")
    }
    assert counts == {"interview": 1, "marked_known": 2}
    learned = conn.execute(
        "SELECT COUNT(*) FROM concepts WHERE status='learned'"
    ).fetchone()[0]
    assert learned == 2
    conn.close()


def test_interview_requires_initialized_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTROLABE_LEDGER_PATH", str(tmp_path / "none.db"))
    result = runner.invoke(app, ["interview"], input=INTERVIEW_INPUT)
    assert result.exit_code == 2  # init 前は失敗(暗黙生成しない)


# --- morning --------------------------------------------------------------


def test_morning_dry_run_never_touches_real_ledger(tmp_path, monkeypatch):
    # 実台帳パスを設定していても、dry-run はそれを開かない・作らない
    real_ledger = tmp_path / "real-ledger.db"
    monkeypatch.setenv("ASTROLABE_LEDGER_PATH", str(real_ledger))
    result = runner.invoke(
        app, ["morning", "--dry-run", "--fixtures-dir", str(FIXTURES_DIR)]
    )
    assert result.exit_code == 0, all_output(result)
    assert "ASTROLABE 朝の観測報告" in result.output
    assert "[dry-run]" in result.output
    assert "検証器つきRAG" in result.output
    assert not real_ledger.exists()


def test_morning_dry_run_needs_no_env():
    result = runner.invoke(
        app, ["morning", "--dry-run", "--fixtures-dir", str(FIXTURES_DIR)]
    )
    assert result.exit_code == 0, all_output(result)


def test_morning_real_requires_env():
    result = runner.invoke(app, ["morning"])
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in all_output(result)


# --- report ---------------------------------------------------------------


def test_report_before_any_morning(tmp_path, monkeypatch):
    path = tmp_path / "ledger.db"
    monkeypatch.setenv("ASTROLABE_LEDGER_PATH", str(path))
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 1  # まだ報告がない


def test_report_shows_latest_and_by_date(tmp_path, monkeypatch):
    path = tmp_path / "ledger.db"
    monkeypatch.setenv("ASTROLABE_LEDGER_PATH", str(path))
    runner.invoke(app, ["init"])
    conn = db.open_ledger(path)
    meta = {"collected": 1, "after_dedupe": 1, "fresh": 1, "dry_run": False}
    store.save_daily_report(
        conn,
        "2026-07-17",
        {"topics": [{"name": "旧トピック", "kind": "concept"}], "meta": meta},
        "昨日の差分",
    )
    store.save_daily_report(
        conn,
        "2026-07-18",
        {"topics": [{"name": "新トピック", "kind": "concept"}], "meta": meta},
        "今日の差分",
    )
    conn.close()

    latest = runner.invoke(app, ["report"])
    assert latest.exit_code == 0, all_output(latest)
    assert "新トピック" in latest.output

    dated = runner.invoke(app, ["report", "--date", "2026-07-17"])
    assert dated.exit_code == 0
    assert "旧トピック" in dated.output
