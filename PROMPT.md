# M0 キックオフ指示(コーディングエージェントへの最初のメッセージ)

以下をそのまま最初の指示として貼り付けて使う。

---

AGENTS.md と docs/design.md を読んでから作業を始めてください。

このリポジトリで、設計書のマイルストーンM0(台帳と収集、ローカル動作)を実装してください。進め方は次の順で。

1. まず実装計画を短く提示して、私の承認を待つこと(ファイル構成、モジュール分割、依存パッケージの一覧)
2. 承認後、次の順で実装する
   - 学習台帳: SQLiteスキーマ(events / concepts / edges / tasks / profile / daily_reports)と、events から concepts/edges を再導出する関数。導出関数には pytest を書く
   - 収集: arXiv APIクライアント(3秒間隔・キャッシュ付き)とRSS取得。fixtures/ にモック応答を保存し、オフラインで動く経路を作る
   - 一次選別: 要旨+プロファイルを ASTROLABE_MODEL_MINI に渡してスコアリング(JSON Schema構造化出力)
   - 統合報告: 上位候補を ASTROLABE_MODEL_FLAGSHIP でトピック3〜5件+短い学習コンテンツに統合し、ターミナル向けテキストとして整形
   - CLI: astrolabe init / interview / morning [--dry-run] / report(typer)
3. 受け入れ条件(AGENTS.md記載)を自分で全部実行して確認し、結果を報告する

制約の再確認:
- M0の範囲外(UI、HTML、Actions、チューター対話、マップ描画)には手を付けない
- モデルIDのハードコード禁止。環境変数から読む
- 依存パッケージを一覧の外に増やしたいときは、実装前に理由付きで提案する
- 台帳の実データとAPIキーをコミットしない

まず計画の提示から。

---

## 使い方メモ(エージェントには渡さない)

- 新しい空フォルダにこの一式(AGENTS.md, CLAUDE.md, PROMPT.md, docs/)を置き、`git init` してから Claude Code または `codex` を起動する
- Codex は AGENTS.md を、Claude Code は CLAUDE.md(経由でAGENTS.md)を自動で読む。上のキックオフ指示を最初のメッセージとして貼る
- 環境変数の準備: `OPENAI_API_KEY`、`ASTROLABE_MODEL_MINI`、`ASTROLABE_MODEL_FLAGSHIP`、`ASTROLABE_LEDGER_PATH`
- モデルIDは、OpenAIダッシュボードでデータ共有(complimentary tokens)を有効化した上で、無料枠対象一覧から選んで設定する(設計書 §11-1)
- PROMPT.md 自体はリポジトリに残してもよいが、コミットしたくなければ削除して構わない
