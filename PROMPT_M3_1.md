# M3 第1便 キックオフ指示(Codexへの発注プロンプト)

M3は3分割で発注する(①台帳のSupabase移行 ②チューター核 ③Vercelデプロイ+認証)。
本書は**第1便: 台帳のSupabase移行と開発バイパス**。以下をそのままCodexに貼り付けて使う。

---

astrolabe-core の AGENTS.md と docs/design.md を読んでから作業を始めてください。

**M3(常駐チューター)の第1便: 学習台帳のSupabase移行**を実装してください。M3全体は
①台帳移行(本便)→ ②チューター核(ツール+チャットUI)→ ③Vercelデプロイ+Supabase Auth
の3便に分割されており、M2は受け入れ済みです。

## 確定済みの決定(施主承認 2026-07-19)

- **Supabaseを一次データにし、git履歴は捨てない** — 毎朝のジョブがスナップショット
  (SQLiteファイルまたはevents JSONL)を ledger リポジトリに書き出し続ける(バックアップ+監査履歴)
- サーバレスは第2便以降で **Vercel Python Functions が core を直接 import** する前提
  (導出・予算・ツールのロジックをTypeScriptへ二重実装しない=背骨は一本)
- 施主がSupabaseプロジェクト(無料枠)を作成し、`SUPABASE_URL` と
  `SUPABASE_SERVICE_ROLE_KEY` をローカル `.env` と ledger の Actions Secrets に登録する。
  **あなたはこれらの値を要求・入力・表示しない**(必要な登録コマンドの提示のみ可)

## 実装項目

1. **ストレージ抽象化** — ledger層にバックエンドの抽象を導入し、現行SQLite実装と
   Postgres(Supabase)実装の2つを提供する。`derive()` は純関数のまま両者で共有。
   選択は環境変数(例: `ASTROLABE_BACKEND=sqlite|supabase`)。既定はsqlite
2. **Postgresスキーマ** — events / concepts / edges / tasks / profile / daily_reports に加え、
   第2便で使う **llm_usage(日次のトークン実測累積)** を定義。migration SQLをリポジトリ管理
   (`supabase/migrations/`)。eventsは追記のみの原則をDB制約でも表現する(UPDATE/DELETE不可を
   RLSまたは権限で担保できるなら担保する)
3. **移行スクリプト** — `astrolabe migrate-to-supabase`: SQLiteのeventsをPostgresへ移送→
   再導出→**SQLite側のrebuild結果とconcepts/edgesが完全一致することを検証**して結果を報告。
   冪等(再実行しても重複しない)
4. **朝ジョブの切替** — Actions実行時は `ASTROLABE_BACKEND=supabase` で読み書き。
   併せて `astrolabe snapshot` を追加し、SupabaseのeventsからSQLiteスナップショット
   (または `events.jsonl`)を決定的に生成して ledger リポジトリへコミット(既存のexports/
   HTML保存と同じ持続ステップに追加)。coreの参照workflowも同期
5. **開発バイパス** — `astrolabe morning --date YYYY-MM-DD` を追加(報告日・金環判定・
   既出判定が指定日として動く)。**安全ガード**: `--date` は dry-run または
   `ASTROLABE_ALLOW_DATE_OVERRIDE=1` が設定された開発台帳でのみ許可し、Actions
   (本番)では未設定にする。合成データ・シミュレート日付を本番台帳に入れないため。
   併せて `docs/dev-ledger.md` に開発台帳レシピ(SQLiteコピー方式/Supabase開発
   プロジェクト方式)を書く
6. 最初のコミットで AGENTS.md のフェーズを M3 に更新し、design.md の変更履歴に
   「M3を3分割で開始・Supabase一次+スナップショット方式・デプロイと認証はM3③」と追記

## 引き継ぐ制約

- **pytestはSQLiteバックエンドでオフライン・APIキーなし全緑を維持**。Supabase結合テストは
  環境変数が設定された時だけ動くopt-in(CIでは走らせない)
- M0承認条件を維持: 台帳パス必須(sqlite時)/厳密再導出/予算二重確認/リトライ全体1回
- **新規Python依存(supabase-py / psycopg 等)は計画で理由付き提案**してから使う
- 台帳実データ・キー類をpublicリポジトリに入れない

## 受け入れ条件

- `pytest` 全緑(オフライン)。抽象化後もSQLite経路の既存テストが1件も壊れない
- 実台帳で `migrate-to-supabase` が検証合格(events件数一致+derive結果完全一致)
- workflow_dispatch で朝ジョブが Supabase読み書き→スナップショット→exports→HTML→
  Discord通知まで完走し、ledgerにスナップショットがコミットされる
- 開発台帳で `morning --date 2026-07-25` が翌日シミュレートとして動き、本番設定では拒否される
- UI(M2)は無改修のまま動き続ける(exportsを読む契約は不変)

まず実装計画(抽象化の設計・依存の提案・スキーマ・コミット分割)を短く提示して
施主の承認を待つこと。承認後に実装し、受け入れ条件を自分で全部実行して報告する。
