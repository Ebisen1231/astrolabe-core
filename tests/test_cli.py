"""CLIの受け入れ条件に対応するテスト(typer CliRunner、すべてオフライン)。"""

import contextlib
import json
import re
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
    ledger_root = tmp_path / "ledger"
    real_ledger = ledger_root / "real-ledger.db"
    real_reports = ledger_root / "reports"
    real_exports = ledger_root / "exports"
    real_reports.mkdir(parents=True)
    real_exports.mkdir(parents=True)
    sentinel = real_reports / "do-not-touch.txt"
    export_sentinel = real_exports / "do-not-touch.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    export_sentinel.write_text("unchanged", encoding="utf-8")
    monkeypatch.setenv("ASTROLABE_LEDGER_PATH", str(real_ledger))
    result = runner.invoke(
        app, ["morning", "--dry-run", "--fixtures-dir", str(FIXTURES_DIR)]
    )
    assert result.exit_code == 0, all_output(result)
    assert "ASTROLABE 朝の観測報告" in result.output
    assert "[dry-run]" in result.output
    assert "検証器つきRAG" in result.output
    assert not real_ledger.exists()
    assert list(real_reports.iterdir()) == [sentinel]
    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert list(real_exports.iterdir()) == [export_sentinel]
    assert export_sentinel.read_text(encoding="utf-8") == "unchanged"

    match = re.search(r"^HTML: (.+)$", result.output, flags=re.MULTILINE)
    assert match is not None
    html_path = Path(match.group(1).strip())
    assert html_path.exists()
    assert real_reports not in html_path.parents

    exports_match = re.search(r"^Exports: (.+)$", result.output, flags=re.MULTILINE)
    assert exports_match is not None
    exports_path = Path(exports_match.group(1).strip())
    assert (exports_path / "map.json").exists()
    assert (exports_path / "layout.json").exists()
    assert (exports_path / "index.json").exists()
    assert real_exports != exports_path


def test_morning_dry_run_needs_no_env():
    result = runner.invoke(
        app, ["morning", "--dry-run", "--fixtures-dir", str(FIXTURES_DIR)]
    )
    assert result.exit_code == 0, all_output(result)


def test_morning_dry_run_date_override_drives_report_and_gold_ring():
    result = runner.invoke(
        app,
        [
            "morning",
            "--dry-run",
            "--date",
            "2026-07-25",
            "--fixtures-dir",
            str(FIXTURES_DIR),
        ],
    )
    assert result.exit_code == 0, all_output(result)
    assert "ASTROLABE 朝の観測報告  2026-07-25" in result.output
    exports_match = re.search(r"^Exports: (.+)$", result.output, flags=re.MULTILINE)
    assert exports_match is not None
    map_export = json.loads(
        (Path(exports_match.group(1).strip()) / "map.json").read_text(encoding="utf-8")
    )
    assert map_export["latest_report_date"] == "2026-07-25"
    assert map_export["today_node_ids"]


def test_morning_real_date_override_is_rejected_before_ledger_or_api(tmp_path, monkeypatch):
    path = tmp_path / "must-not-be-created.db"
    monkeypatch.setenv("ASTROLABE_LEDGER_PATH", str(path))

    result = runner.invoke(app, ["morning", "--date", "2026-07-25"])

    assert result.exit_code == 2
    assert "ASTROLABE_ALLOW_DATE_OVERRIDE=1" in all_output(result)
    assert not path.exists()


def test_morning_invalid_date_is_rejected():
    result = runner.invoke(app, ["morning", "--dry-run", "--date", "2026-7-25"])
    assert result.exit_code == 2
    assert "YYYY-MM-DD" in all_output(result)


def test_morning_real_requires_env():
    result = runner.invoke(app, ["morning"])
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in all_output(result)


# --- export ---------------------------------------------------------------


def test_export_requires_ledger_path_env():
    result = runner.invoke(app, ["export"])
    assert result.exit_code == 2
    assert "ASTROLABE_LEDGER_PATH" in all_output(result)


def test_export_defaults_next_to_ledger(tmp_path, monkeypatch):
    path = tmp_path / "ledger.db"
    monkeypatch.setenv("ASTROLABE_LEDGER_PATH", str(path))
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["export"])

    assert result.exit_code == 0, all_output(result)
    assert (tmp_path / "exports" / "map.json").exists()
    assert (tmp_path / "exports" / "layout.json").exists()


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
