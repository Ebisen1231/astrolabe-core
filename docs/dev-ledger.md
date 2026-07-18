# M3 開発台帳と復旧手順

## 台帳の「正」

M3第1便の切替後、オンラインでの一次データは **Supabaseのeventsテーブル**だけである。
privateの `astrolabe-ledger` にある **`snapshots/events.jsonl` が唯一のgitバックアップ**で、
`astrolabe.db` は追跡しない。concepts/edgesはeventsから再導出し、SQLiteは開発・復元先に限る。

朝ジョブはID昇順、JSONキー順固定、空白なし、各event末尾改行のJSONLを生成する。同一eventsなら
ファイルのバイト列も同一になるため、無変更日はsnapshotのコミット差分が出ない。

## JSONLからローカルSQLiteを再構築する

復元先は新規ファイルでなければならない。既存ファイルは上書きしない。

```bash
uv run astrolabe restore-snapshot \
  ~/astrolabe-ledger/snapshots/events.jsonl \
  --out /tmp/astrolabe-dev/ledger.db
```

このコマンドはeventsのIDを保持して投入し、profileを最新のinterviewイベントから復元した後、
concepts/edgesを純関数 `derive()` から再構築する。過去のHTMLと報告JSONはledgerの
`reports/` と `exports/` に別途残る。

## 開発方式A: SQLiteコピー

本番Supabaseへ接続しない最も安全な方式。まず上記コマンドでsnapshotから基準DBを復元し、
シミュレーションごとにコピーを作る。

```bash
cp /tmp/astrolabe-dev/ledger.db /tmp/astrolabe-dev/ledger-2026-07-25.db
export ASTROLABE_BACKEND=sqlite
export ASTROLABE_LEDGER_PATH=/tmp/astrolabe-dev/ledger-2026-07-25.db
export ASTROLABE_ALLOW_DATE_OVERRIDE=1
uv run astrolabe morning --date 2026-07-25
```

dry-runなら許可変数は不要で、実台帳・実APIへ触れない。

```bash
uv run astrolabe morning --dry-run --date 2026-07-25
```

## 開発方式B: Supabase開発プロジェクト

本番とは別のSupabaseプロジェクトを作り、SQL EditorまたはSupabase CLIで
`supabase/migrations/202607190001_m3_ledger.sql` を適用する。ローカル `.env` はgitignore対象とし、
値をログ・shell履歴・publicリポジトリへ出さない。

```bash
set -a
source .env
set +a
export ASTROLABE_BACKEND=supabase
export ASTROLABE_ARTIFACT_ROOT=/tmp/astrolabe-dev/artifacts
export ASTROLABE_ALLOW_DATE_OVERRIDE=1
uv run astrolabe migrate-to-supabase
uv run astrolabe morning --date 2026-07-25
```

`.env` には `ASTROLABE_LEDGER_PATH`、開発プロジェクト用 `SUPABASE_URL`、
`SUPABASE_SERVICE_ROLE_KEY` とモデル/API設定を置く。`migrate-to-supabase` はSQLite eventsを
ID保持で冪等移送し、両側のconcepts/edges完全一致を確認する。

## 本番Actions Secrets

値は施主がprivate ledgerリポジトリへ登録する。コマンドは値を引数にせず、対話入力を使う。

```bash
gh secret set SUPABASE_URL --repo Ebisen1231/astrolabe-ledger
gh secret set SUPABASE_SERVICE_ROLE_KEY --repo Ebisen1231/astrolabe-ledger
```

本番workflowには `ASTROLABE_ALLOW_DATE_OVERRIDE` を設定しない。Supabaseに到達できない場合も
SQLiteへフォールバックせず、ジョブを失敗させる。
