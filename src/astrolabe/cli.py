"""CLI: astrolabe init / interview / morning [--dry-run] / report / canary

終了コード: 0=成功 / 1=致命的エラー・データなし / 2=設定不備または要調査 / 3=予算超過
"""

from __future__ import annotations

import logging
import sys
import tempfile
from datetime import date as date_type
from pathlib import Path
from typing import Annotated

import typer

from astrolabe import render
from astrolabe.config import Config, ConfigError, load_config
from astrolabe.ledger import db, store
from astrolabe.llm.budget import BudgetExceededError, TokenBudget
from astrolabe.llm.client import FatalLLMError, LLMCallError, ResponsesLLM, classify_error
from astrolabe.llm.fixtures import FixtureLLM
from astrolabe.pipeline import morning as morning_mod

app = typer.Typer(add_completion=False, help="Astrolabe — 学習観測エージェント(M0)")
log = logging.getLogger("astrolabe")


@app.callback()
def _setup() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)


def _fail(message: str, code: int) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def _load_config_or_fail(**kwargs) -> Config:
    try:
        return load_config(**kwargs)
    except ConfigError as e:
        _fail(str(e), 2)
        raise AssertionError from e  # unreachable


def _open_ledger_or_fail(config: Config):
    try:
        return db.open_ledger(config.ledger_path)
    except db.LedgerError as e:
        _fail(str(e), 2)


def _make_budget(config: Config, max_mini: int | None, max_flagship: int | None) -> TokenBudget:
    return TokenBudget(
        {
            "mini": max_mini or config.max_mini_tokens,
            "flagship": max_flagship or config.max_flagship_tokens,
        }
    )


def _resolve_fixtures_dir(explicit: Path | None) -> Path:
    import os

    candidates = []
    if explicit:
        candidates.append(explicit)
    env_dir = os.environ.get("ASTROLABE_FIXTURES_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(Path.cwd() / "fixtures")
    candidates.append(Path(__file__).resolve().parents[2] / "fixtures")
    for c in candidates:
        if (c / "arxiv_sample.xml").exists():
            return c
    _fail(
        "fixtures ディレクトリが見つからない。--fixtures-dir か ASTROLABE_FIXTURES_DIR で指定する",
        2,
    )
    raise AssertionError  # unreachable


def _run_canaries(llm) -> None:
    """本番バッチ前の疎通確認。致命的なら本番を開始しない(発射管制)。"""
    for key in ("mini", "flagship"):
        try:
            info = llm.canary(key)
            typer.secho(f"カナリア {key}: OK ({info['model']})", err=True)
        except FatalLLMError as e:
            _fail(f"カナリア {key} で致命的エラー。本番を開始しない: {e}", 1)
        except BudgetExceededError as e:
            _fail(f"カナリア {key}: {e}", 3)
        except Exception as e:
            if classify_error(e) == "fatal":
                _fail(f"カナリア {key} で致命的エラー。本番を開始しない: {e}", 1)
            _fail(f"カナリア {key} が失敗(要調査)。本番を開始しない: {e}", 2)


@app.command()
def init() -> None:
    """台帳(SQLite)を初期化する。ASTROLABE_LEDGER_PATH 必須。"""
    config = _load_config_or_fail(require_ledger=True)
    conn = db.init_db(config.ledger_path)
    conn.close()
    typer.echo(f"台帳を初期化した: {config.ledger_path}")


@app.command()
def interview() -> None:
    """初回面談: プロファイルと既知概念を登録する(marked_known 一括投入)。"""
    config = _load_config_or_fail(require_ledger=True)
    conn = _open_ledger_or_fail(config)
    typer.echo("初回面談を始める。プロファイルと既知概念を台帳に登録する。")
    goals = typer.prompt("学習の目標(1行で)")
    background = typer.prompt("現在地・背景(例: Web開発3年、ML初学者)")
    time_budget = typer.prompt("1日の学習時間の目安(例: 30分)")
    interests_raw = typer.prompt("興味タグ(カンマ区切り。`タグ:重み` で0〜1の重み指定可)")
    typer.echo("既知の概念を入力(カンマ区切り可)。空行で入力終了。")
    known: list[str] = []
    while True:
        line = typer.prompt("既知", default="", show_default=False)
        if not line.strip():
            break
        known += [s.strip() for s in line.split(",") if s.strip()]

    interests = _parse_interests(interests_raw)
    typer.echo(f"登録内容: 興味 {len(interests)}件 / 既知概念 {len(known)}件")
    if not typer.confirm("この内容で台帳に記録する?", default=True):
        _fail("中止した(台帳は変更していない)", 1)
    result = store.record_interview(
        conn,
        {
            "interests": interests,
            "goals": goals,
            "background": background,
            "time_budget": time_budget,
        },
        known,
    )
    conn.close()
    typer.echo(
        f"記録完了: marked_known {result['known']}件 / "
        f"concepts {result['concepts']}件 / edges {result['edges']}件"
    )


def _parse_interests(raw: str) -> dict[str, float]:
    interests: dict[str, float] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            tag, weight_raw = part.rsplit(":", 1)
            try:
                weight = min(1.0, max(0.0, float(weight_raw)))
            except ValueError:
                tag, weight = part, 1.0
            interests[tag.strip()] = weight
        else:
            interests[part] = 1.0
    return interests


@app.command()
def morning(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="fixturesのみで実行。実API・実台帳に一切触れない"),
    ] = False,
    top_k: Annotated[int, typer.Option(help="統合報告に渡す上位候補数")] = 8,
    arxiv_max: Annotated[int, typer.Option(help="arXivの取得件数")] = 100,
    fixtures_dir: Annotated[
        Path | None, typer.Option(help="dry-run用fixturesディレクトリ")
    ] = None,
    max_mini_tokens: Annotated[
        int | None, typer.Option(help="mini系トークン上限(既定: 環境変数または500,000)")
    ] = None,
    max_flagship_tokens: Annotated[
        int | None, typer.Option(help="flagship系トークン上限(既定: 環境変数または70,000)")
    ] = None,
) -> None:
    """朝の観測ジョブ: 収集 → 選別(mini) → 統合(flagship) → 日次報告。"""
    today = date_type.today().isoformat()

    if dry_run:
        fdir = _resolve_fixtures_dir(fixtures_dir)
        config = _load_config_or_fail()  # dry-run は何も必須にしない
        budget = _make_budget(config, max_mini_tokens, max_flagship_tokens)
        typer.secho(f"[dry-run] fixtures={fdir} / 一時DBで実行、台帳・APIに触れない", err=True)
        with tempfile.TemporaryDirectory(prefix="astrolabe-dryrun-") as tmp:
            conn = db.init_db(Path(tmp) / "ledger.db")
            llm = FixtureLLM(fdir, budget)
            _run_canaries(llm)
            items = morning_mod.collect_items(config, offline_dir=fdir, logger=log)
            outcome = morning_mod.run_morning(
                conn, llm, items, today=today, budget=budget,
                top_k=top_k, dry_run=True, logger=log,
            )
            conn.close()
        typer.echo(outcome.report_text)
        return

    config = _load_config_or_fail(require_ledger=True, require_api=True)
    conn = _open_ledger_or_fail(config)
    budget = _make_budget(config, max_mini_tokens, max_flagship_tokens)
    llm = ResponsesLLM(
        api_key=config.api_key,
        models={"mini": config.model_mini, "flagship": config.model_flagship},
        budget=budget,
        logger=log,
    )
    _run_canaries(llm)
    try:
        items = morning_mod.collect_items(config, arxiv_max=arxiv_max, logger=log)
        outcome = morning_mod.run_morning(
            conn, llm, items, today=today, budget=budget, top_k=top_k, logger=log
        )
    except FatalLLMError as e:
        _fail(f"致命的エラーで中断。台帳へは未反映: {e}", 1)
    except BudgetExceededError as e:
        _fail(f"トークン予算により中断。台帳へは未反映: {e}", 3)
    except LLMCallError as e:
        _fail(f"LLM呼び出しに失敗して中断。台帳へは未反映: {e}", 1)
    finally:
        conn.close()
    typer.echo(outcome.report_text)


@app.command()
def report(
    date: Annotated[
        str | None, typer.Option("--date", help="YYYY-MM-DD。省略時は直近の報告")
    ] = None,
) -> None:
    """直近(または指定日)の日次報告を再表示する。"""
    config = _load_config_or_fail(require_ledger=True)
    conn = _open_ledger_or_fail(config)
    row = store.get_daily_report(conn, date)
    conn.close()
    if row is None:
        _fail("報告がまだない。先に `astrolabe morning` を実行する", 1)
    items = row["items"]
    typer.echo(
        render.render_report(
            row["date"], items.get("topics", []), row["map_delta_text"], items.get("meta", {})
        )
    )


@app.command()
def canary() -> None:
    """実設定での極小疎通確認(morning本番前の残高・認証チェック、1円未満)。"""
    config = _load_config_or_fail(require_api=True)
    budget = _make_budget(config, None, None)
    llm = ResponsesLLM(
        api_key=config.api_key,
        models={"mini": config.model_mini, "flagship": config.model_flagship},
        budget=budget,
        logger=log,
    )
    _run_canaries(llm)
    typer.echo("カナリア全通過。本番実行してよい。")


if __name__ == "__main__":
    app()
