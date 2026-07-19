"""CLI: 台帳・朝ジョブ・export・migration・snapshot・通知。

終了コード: 0=成功 / 1=致命的エラー・データなし / 2=設定不備または要調査 / 3=予算超過
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from datetime import date as date_type
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import typer

from astrolabe import exporter, render
from astrolabe.config import Config, ConfigError, load_config
from astrolabe.github_feedback import (
    GitHubFeedbackClient,
    close_feedback_issues,
    import_feedback_issues,
    sync_feedback,
)
from astrolabe.ledger import db, store
from astrolabe.ledger.backend import LedgerBackendError
from astrolabe.ledger.migration import MigrationVerificationError, migrate_sqlite_to_supabase
from astrolabe.ledger.snapshot import SnapshotError, restore_sqlite, write_events_jsonl
from astrolabe.ledger.supabase import SupabaseLedger
from astrolabe.llm.budget import BudgetExceededError, TokenBudget
from astrolabe.llm.client import FatalLLMError, LLMCallError, ResponsesLLM, classify_error
from astrolabe.llm.fixtures import FixtureLLM
from astrolabe.notify_discord import send_discord_report
from astrolabe.pipeline import morning as morning_mod
from astrolabe.tutor.engine import TutorEngineError
from astrolabe.tutor.runtime import LocalTutorRuntime
from astrolabe.tutor.server import LOOPBACK_HOST, serve_tutor

app = typer.Typer(add_completion=False, help="Astrolabe — 学習観測エージェント(M3)")
log = logging.getLogger("astrolabe")
REPORT_TIME_ZONE = ZoneInfo("Asia/Tokyo")


@app.callback()
def _setup() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    # httpxのINFOログはSupabase URLを含むため、秘密情報の運用方針に従い抑止する。
    logging.getLogger("httpx").setLevel(logging.WARNING)


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
        if config.backend == "supabase":
            assert config.supabase_url is not None
            assert config.supabase_service_role_key is not None
            return SupabaseLedger(config.supabase_url, config.supabase_service_role_key)
        assert config.ledger_path is not None
        return db.open_ledger(config.ledger_path)
    except (db.LedgerError, LedgerBackendError) as e:
        _fail(str(e), 2)


def _artifact_root_or_fail(config: Config) -> Path:
    if config.artifact_root is not None:
        return config.artifact_root
    if config.backend == "sqlite" and config.ledger_path is not None:
        return config.ledger_path.parent
    _fail(
        "SupabaseバックエンドではASTROLABE_ARTIFACT_ROOTを設定する"
        "(HTML・exports・snapshotの保存先)",
        2,
    )
    raise AssertionError  # unreachable


def _resolve_report_date(value: str | None, *, now: datetime | None = None) -> str:
    if value is None:
        instant = now or datetime.now(UTC)
        if instant.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return instant.astimezone(REPORT_TIME_ZONE).date().isoformat()
    try:
        parsed = date_type.fromisoformat(value)
    except ValueError:
        _fail("--date はYYYY-MM-DD形式で指定する", 2)
    if parsed.isoformat() != value:
        _fail("--date はYYYY-MM-DDの正規形で指定する", 2)
    return value


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


def _feedback_client(config: Config) -> GitHubFeedbackClient | None:
    if not config.github_token:
        log.warning("GITHUB_TOKEN未設定。GitHubフィードバック取り込みをスキップ")
        return None
    return GitHubFeedbackClient(config.github_token, config.ledger_repository)


@app.command()
def tutor() -> None:
    """常駐チューターとのローカル対話。会話履歴はこのCLIプロセスだけが保持する。"""
    config = _load_config_or_fail(require_ledger=True, require_flagship=True)
    runtime = LocalTutorRuntime(config)
    session_id = f"tutor-{uuid.uuid4().hex}"
    history: list[dict] = []
    typer.echo("Astrolabe tutor。終了は /quit。会話全文は台帳へ保存しません。")
    while True:
        message = typer.prompt("you", default="", show_default=False).strip()
        if message in {"/quit", "/exit"}:
            return
        if not message:
            continue
        history.append({"role": "user", "content": message})
        try:
            result = runtime.turn(history, session_id)
        except FatalLLMError as exc:
            _fail(f"致命的LLMエラー: {exc}", 1)
        except (LLMCallError, TutorEngineError, LedgerBackendError) as exc:
            _fail(f"チューター処理に失敗: {exc}", 2)
        typer.echo(f"tutor> {result['message']}")
        for card in result.get("cards", []):
            typer.echo("  [tool] " + json.dumps(card, ensure_ascii=False, sort_keys=True))
        history.append(
            {
                "role": "assistant",
                "content": result["message"],
                "cards": result.get("cards", []),
            }
        )
        if result.get("budget_exhausted"):
            return


@app.command("tutor-serve")
def tutor_serve(
    port: Annotated[int, typer.Option(help="ローカルAPIのポート")] = 8787,
) -> None:
    """認証なしローカルAPIを127.0.0.1固定で起動する。"""
    config = _load_config_or_fail(require_ledger=True, require_flagship=True)
    typer.echo(f"Tutor APIを http://{LOOPBACK_HOST}:{port} で起動")
    try:
        serve_tutor(LocalTutorRuntime(config), port)
    except (OSError, ValueError) as exc:
        _fail(f"Tutor APIを起動できない: {exc}", 2)


@app.command()
def init() -> None:
    """台帳(SQLite)を初期化する。ASTROLABE_LEDGER_PATH 必須。"""
    config = _load_config_or_fail(require_sqlite=True)
    assert config.ledger_path is not None
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
    try:
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
    except LedgerBackendError as exc:
        _fail(f"台帳バックエンドエラー: {exc}", 2)
    finally:
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
    report_date: Annotated[
        str | None,
        typer.Option(
            "--date",
            help="開発用の報告日YYYY-MM-DD。dry-run以外は明示的な許可が必要",
        ),
    ] = None,
) -> None:
    """朝の観測ジョブ: 収集 → 選別(mini) → 統合(flagship) → 日次報告。"""
    today = _resolve_report_date(report_date)
    if report_date is not None and not dry_run:
        guard_config = _load_config_or_fail()
        if not guard_config.allow_date_override:
            _fail(
                "本番台帳への合成日付混入を防ぐため--dateを拒否した。"
                "開発台帳だけでASTROLABE_ALLOW_DATE_OVERRIDE=1を設定する",
                2,
            )

    if dry_run:
        fdir = _resolve_fixtures_dir(fixtures_dir)
        config = _load_config_or_fail()  # dry-run は何も必須にしない
        budget = _make_budget(config, max_mini_tokens, max_flagship_tokens)
        typer.secho(f"[dry-run] fixtures={fdir} / 一時DBで実行、台帳・APIに触れない", err=True)
        artifact_root = Path(tempfile.mkdtemp(prefix="astrolabe-dryrun-artifacts-"))
        html_dir = artifact_root / "reports"
        exports_dir = artifact_root / "exports"
        with tempfile.TemporaryDirectory(prefix="astrolabe-dryrun-") as tmp:
            conn = db.init_db(Path(tmp) / "ledger.db")
            llm = FixtureLLM(fdir, budget)
            _run_canaries(llm)
            items = morning_mod.collect_items(config, offline_dir=fdir, logger=log)
            outcome = morning_mod.run_morning(
                conn, llm, items, today=today, budget=budget,
                top_k=top_k, dry_run=True, html_output_dir=html_dir,
                html_path_base=html_dir, feedback_repository=config.ledger_repository,
                logger=log,
            )
            exporter.export_ledger(conn, exports_dir)
            conn.close()
        typer.echo(outcome.report_text)
        typer.echo(f"HTML: {outcome.html_path}")
        typer.echo(f"Exports: {exports_dir}")
        return

    config = _load_config_or_fail(require_ledger=True, require_api=True)
    conn = _open_ledger_or_fail(config)
    try:
        feedback_client = _feedback_client(config)
        if feedback_client is not None:
            try:
                feedback = sync_feedback(conn, feedback_client, logger=log)
                typer.secho(
                    "フィードバック: "
                    f"反映 {feedback.imported} / 既存 {feedback.already_recorded} / "
                    f"close {feedback.closed} / close失敗 {feedback.close_failed}",
                    err=True,
                )
            finally:
                feedback_client.close()
        budget = _make_budget(config, max_mini_tokens, max_flagship_tokens)
        llm = ResponsesLLM(
            api_key=config.api_key,
            models={"mini": config.model_mini, "flagship": config.model_flagship},
            budget=budget,
            logger=log,
        )
        _run_canaries(llm)
        items = morning_mod.collect_items(config, arxiv_max=arxiv_max, logger=log)
        ledger_root = _artifact_root_or_fail(config)
        outcome = morning_mod.run_morning(
            conn, llm, items, today=today, budget=budget, top_k=top_k,
            html_output_dir=ledger_root / "reports", html_path_base=ledger_root,
            feedback_repository=config.ledger_repository, run_id=config.run_id, logger=log,
        )
    except FatalLLMError as e:
        _fail(f"致命的エラーで中断。台帳へは未反映: {e}", 1)
    except BudgetExceededError as e:
        _fail(f"トークン予算により中断。台帳へは未反映: {e}", 3)
    except LLMCallError as e:
        _fail(f"LLM呼び出しに失敗して中断。台帳へは未反映: {e}", 1)
    except LedgerBackendError as e:
        _fail(f"台帳バックエンドエラーで中断(SQLiteへはフォールバックしない): {e}", 2)
    finally:
        conn.close()
    typer.echo(outcome.report_text)
    typer.echo(f"HTML: {outcome.html_path}")


@app.command("export")
def export_data(
    out: Annotated[
        Path | None,
        typer.Option("--out", help="出力先。省略時は台帳と同じディレクトリのexports/"),
    ] = None,
) -> None:
    """台帳からM2 UI向けのバージョン付き静的JSONを生成する。"""
    config = _load_config_or_fail(require_ledger=True)
    output_dir = out or _artifact_root_or_fail(config) / "exports"
    conn = _open_ledger_or_fail(config)
    try:
        result = exporter.export_ledger(conn, output_dir)
    except (exporter.ExportError, LedgerBackendError) as exc:
        _fail(f"exportに失敗: {exc}", 2)
    finally:
        conn.close()
    typer.echo(
        f"Export完了: {result.output_dir} / reports {len(result.report_dates)} / "
        f"concepts {result.concept_count} / edges {result.edge_count}"
    )


@app.command("migrate-to-supabase")
def migrate_to_supabase() -> None:
    """SQLite一次台帳をSupabaseへ冪等移行し、再導出の完全一致を検証する。"""
    config = _load_config_or_fail(require_sqlite=True, require_supabase=True)
    assert config.ledger_path is not None
    assert config.supabase_url is not None
    assert config.supabase_service_role_key is not None
    try:
        source = db.open_ledger(config.ledger_path)
    except db.LedgerError as exc:
        _fail(str(exc), 2)
    target = SupabaseLedger(config.supabase_url, config.supabase_service_role_key)
    try:
        result = migrate_sqlite_to_supabase(source, target)
    except (LedgerBackendError, MigrationVerificationError) as exc:
        _fail(f"Supabase移行検証に失敗: {exc}", 2)
    finally:
        source.close()
        target.close()
    typer.echo(
        "Supabase移行検証: 合格 / "
        f"events {result.event_count} / concepts {result.concept_count} / "
        f"edges {result.edge_count} / tasks {result.task_count} / "
        f"reports {result.report_count}"
    )


@app.command("snapshot")
def snapshot(
    out: Annotated[
        Path | None,
        typer.Option("--out", help="出力先。省略時はartifact root/snapshots/events.jsonl"),
    ] = None,
) -> None:
    """選択中バックエンドのeventsを決定的JSONLへ書き出す。"""
    config = _load_config_or_fail(require_ledger=True)
    output_path = out or _artifact_root_or_fail(config) / "snapshots" / "events.jsonl"
    ledger = _open_ledger_or_fail(config)
    try:
        result = write_events_jsonl(ledger, output_path)
    except (LedgerBackendError, SnapshotError) as exc:
        _fail(f"snapshotに失敗: {exc}", 2)
    finally:
        ledger.close()
    typer.echo(f"Snapshot完了: {result.path} / events {result.event_count}")


@app.command("restore-snapshot")
def restore_snapshot(
    snapshot_file: Annotated[Path, typer.Argument(help="events.jsonlのパス")],
    out: Annotated[Path, typer.Option("--out", help="新規SQLiteファイルの保存先")],
) -> None:
    """events JSONLからローカルSQLite台帳を新規再構築する。"""
    try:
        result = restore_sqlite(snapshot_file, out)
    except (SnapshotError, LedgerBackendError) as exc:
        _fail(f"snapshot復元に失敗: {exc}", 2)
    typer.echo(
        f"SQLite復元完了: {result.path} / events {result.event_count} / "
        f"concepts {result.concept_count} / edges {result.edge_count}"
    )


@app.command("feedback-import", hidden=True)
def feedback_import(
    receipt: Annotated[Path, typer.Option(help="close対象Issue番号のJSON保存先")],
) -> None:
    """Actions用: Issueを台帳へ反映し、close対象のreceiptを保存する。"""
    config = _load_config_or_fail(require_ledger=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    client = _feedback_client(config)
    if client is None:
        receipt.write_text("[]\n", encoding="utf-8")
        return
    conn = _open_ledger_or_fail(config)
    try:
        result = import_feedback_issues(conn, client, logger=log)
    except LedgerBackendError as exc:
        _fail(f"フィードバックの台帳反映に失敗: {exc}", 2)
    finally:
        conn.close()
        client.close()
    receipt.write_text(json.dumps(result.issues_to_close) + "\n", encoding="utf-8")
    typer.echo(
        f"フィードバック反映: 新規 {result.imported} / 既存 {result.already_recorded} / "
        f"無効 {result.invalid}"
    )


@app.command("feedback-close", hidden=True)
def feedback_close(
    receipt: Annotated[Path, typer.Option(help="feedback-importが生成したJSON")],
) -> None:
    """Actions用: ledger push後にreceiptのIssueをcloseする。"""
    config = _load_config_or_fail()
    client = _feedback_client(config)
    if client is None:
        return
    try:
        numbers = [int(n) for n in json.loads(receipt.read_text(encoding="utf-8"))]
        result = close_feedback_issues(client, numbers, logger=log)
    finally:
        client.close()
    typer.echo(f"フィードバックclose: 成功 {result.closed} / 失敗 {result.close_failed}")


@app.command("notify-discord", hidden=True)
def notify_discord() -> None:
    """Actions用: 最新HTML報告をDiscordへ通知する。失敗はwarningで終了する。"""
    config = _load_config_or_fail(require_ledger=True)
    conn = _open_ledger_or_fail(config)
    try:
        report_row = store.get_daily_report(conn)
    except LedgerBackendError as exc:
        _fail(f"Discord通知用の台帳読み出しに失敗: {exc}", 2)
    finally:
        conn.close()
    if report_row is None:
        log.warning("日次報告がないためDiscord通知をスキップ")
        return
    stored_path = report_row.get("html_path")
    if not stored_path:
        log.warning("日次報告にhtml_pathがないためDiscord通知をスキップ")
        return
    ledger_root = _artifact_root_or_fail(config).resolve()
    html_path = (ledger_root / stored_path).resolve()
    if not html_path.is_relative_to(ledger_root):
        log.warning("html_pathがledger外を指すためDiscord通知をスキップ")
        return
    if send_discord_report(
        config.discord_webhook_url or "",
        report_row,
        html_path,
        logger=log,
    ):
        typer.echo(f"Discord通知完了: {html_path.name}")


@app.command()
def report(
    date: Annotated[
        str | None, typer.Option("--date", help="YYYY-MM-DD。省略時は直近の報告")
    ] = None,
) -> None:
    """直近(または指定日)の日次報告を再表示する。"""
    config = _load_config_or_fail(require_ledger=True)
    conn = _open_ledger_or_fail(config)
    try:
        row = store.get_daily_report(conn, date)
    except LedgerBackendError as exc:
        _fail(f"報告の台帳読み出しに失敗: {exc}", 2)
    finally:
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
