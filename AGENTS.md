# Astrolabe — エージェント向けプロジェクト指示書

このリポジトリで作業するコーディングエージェント(Codex / Claude Code / その他)への指示。
詳細設計は `docs/design.md`(設計書v0.1)を必ず先に読むこと。本書と設計書が矛盾した場合は設計書を正とし、矛盾を発見したら報告する。

## プロジェクト概要

LLM/AIエージェント領域を学ぶ個人のための学習観測エージェント。単一の学習台帳の上に三つの機能を載せる。

1. 朝の観測報告 — arXiv/RSS/ニュースを毎朝巡回し、知るべきトピックと短い学習コンテンツを提案(定期実行)
2. 常駐チューター — 基礎の穴を埋めるタスク管理と面談(対話型)
3. 学習マップ — 学んだ概念のつながりを毎朝再描画するトップ画面

## 現在のフェーズ: M3 第3便

施主が2026-07-19にM3第2便を受け入れ、Vercel Python Functionsの事前実測を経て
**第3便(公開)**への移行を承認済み。第3便に含むもの:

- 朝ジョブexportのSupabase公開artifactへの明示publish
- coreを直接importする認証付き単一Vercel Python Function
- Supabase Authとowner ID一致による単独利用者ガード
- デプロイUIからの星図・報告・履歴・タスク・チューター利用
- ローカルfixture/exports、CLI、127.0.0.1固定APIの維持

以下はM3第3便の範囲外であり、先回りして作らないこと:

- 週次自己改修ジョブ、精読・下読み工程、Hacker News収集

範囲を広げたくなったら、実装せずに提案として報告すること。

## 確定済みの技術決定

- 言語: Python 3.12+(エージェント核)。UIはNext.js App Router / TypeScript
- パッケージ管理: uv / リント: ruff / テスト: pytest / CLI: typer
- ストレージ: M3第1便からSupabaseを一次データとし、SQLiteはオフライン開発・復元用に維持する。ORMは導入しない
- LLM呼び出し: OpenAI Responses API。**全工程でJSON Schemaによる構造化出力**を用いる
- モデルはコードに直書きせず環境変数で注入する:
  - `ASTROLABE_MODEL_MINI`(大量処理: 選別・下読み用)
  - `ASTROLABE_MODEL_FLAGSHIP`(統合・面談用)
  - 実際のモデルIDは施主がダッシュボードの無料枠対象一覧から選んで設定する
- APIキーは `OPENAI_API_KEY`。キー・台帳実データ・プロファイルは一切コミットしない
- 通知チャネルはDiscord webhook。URLは `DISCORD_WEBHOOK_URL` からのみ読む
- GitHub Actionsの実体はprivateの `astrolabe-ledger` 側に置く。core側には参照コピーだけを置く
- Actionsではledger自身の `GITHUB_TOKEN` に `issues: write` と `contents: write` だけを与える。
  deploy keyやprivate-repo横断用PATは使わない

## 設計原則(要約)

1. 背骨は一本 — すべての機能は学習台帳を読み書きする導出物。機能ごとの独立データを作らない
2. events が一次データ(追記のみ)。concepts/edges はイベントから**常に再導出可能**に保つ。導出関数には必ずテストを書く
3. LLMはデータ(JSONと短文)を作る。描画・整形は決定的なコードが行う。実行時にLLMへHTMLを書かせない
4. 予算分担 — 実行時推論はAPI無料枠(mini系10M/日・上位系1M/日)。開発はCodex/Claude Codeの購読枠。1回の朝ジョブの見積は mini 約0.5M・上位 約0.07M を超えないこと
5. データ共有前提 — 台帳に入るのは公開情報と学習履歴のみ。私信・個人生活情報を扱うコードを書かない

## 外部APIの作法

- arXiv API: リクエスト間隔3秒以上、結果はローカルキャッシュ、カテゴリは cs.CL / cs.AI / cs.LG から開始
- RSS: 標準的なフィード取得。失敗はスキップして続行(1ソースの障害で朝ジョブを落とさない)
- ネットワーク呼び出しはすべてリトライ1回+タイムアウト付き

## リポジトリ規約

- このリポジトリ(astrolabe-core)は**公開前提**。台帳の実データは `ASTROLABE_LEDGER_PATH` が指す外部パス(別のprivateリポジトリ)に置く
- `fixtures/` にオフライン開発用のサンプルデータ(arXiv応答のモック等)を置き、**APIキーなしでもテストと --dry-run が通る**状態を維持する
- `--dry-run` は実台帳・実API・`ledger/reports/` に一切触れず、一時DBと一時HTMLだけを使う
- ledgerのevents JSONL snapshot・HTML・exportsはprivateリポジトリにコミットする。coreには実データやSecretsを置かない
- コミットは小さく、意味単位で。破壊的変更は理由を添える

## M3 第3便の受け入れ条件

- Vercel Python Functionからcore importと実flagship＋tool loopが制限内で完走する
- 未認証の全ページ・全データAPIを拒否し、ownerだけが利用できる
- デプロイUIから未知語の橋渡しtaskをSupabaseへ生成できる
- 朝ジョブ後、再デプロイなしで星図・報告・履歴が更新される
- anonキー単体では台帳・公開artifactのどの表/RPCにも触れない
- private ledgerのsnapshot/exports git履歴とローカルfixture/exports経路を維持する
- core/UIのテスト・lint・buildがオフラインで全緑
