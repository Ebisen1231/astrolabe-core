# M3 第2便 キックオフ指示(Codexへの発注プロンプト)

M3の3分割の**第2便: 常駐チューター核(ツール+チャット)**。以下をそのままCodexに貼り付けて使う。

---

astrolabe-core の AGENTS.md と docs/design.md を読んでから作業を始めてください。

**M3第2便: 常駐チューター核**を実装してください(設計書§6.4)。第1便(Supabase一次台帳)は
受け入れ済み。本便は**ローカルで完結**し、第3便(Vercelデプロイ+Supabase Auth)が
薄いアダプタで公開できる形に作ります。M3全体の完了条件は
「**未知語に出会ったとき、その場で橋渡しタスクが生まれる**」— 本便でそのローカル版を達成します。

## アーキテクチャの確定事項

- **チューターエンジンは core 内の Python**(第3便で Vercel Python Functions が直接 import
  する前提。ロジックのTypeScript二重実装は禁止)
- **エンジンはステートレス** — 会話履歴はクライアント(UI/CLI)が保持して毎ターン渡す。
  サーバ側セッション保存は作らない(サーバレス化の前提)
- モデルは FLAGSHIP(環境変数注入)。ツール呼び出しは Responses API の function calling を
  使い、**ツール引数のスキーマは JSON Schema strict** で縛る
- ローカル実行の入口は2つ: `astrolabe tutor`(CLI対話、デバッグ用)と
  `astrolabe tutor-serve --port 8787`(**標準ライブラリ http.server ベース**の小さなAPIサーバ。
  localhost:3000 からのCORSを許可)。**新規Python依存は追加しない**(FastAPI等は不可。
  必要と考えるなら実装前に提案)
- 会話の全文は台帳に保存しない。台帳に入るのはチューターがツールで明示的に記録する
  イベントのみ(§2原則5: 私信を台帳に入れない)

## チューターのツール(台帳の読み書きは全てここを通す)

1. `search_ledger` — 概念の検索(id/name/status/confidence/summary/関連エッジ)と
   今日の報告・タスク一覧の参照
2. `record_feedback` — selected / marked_known / dismissed / chat_note イベントの追記
3. `create_task` — **橋渡しタスクの生成**: tasks 行の作成 + task_created イベント。
   kind は read / implement / quiz / build_app_feature(§4)。M0以来スキーマのみだった
   tasks テーブルはこの便で解禁される
4. `complete_task` — evidence付きで完了: tasks更新 + task_done イベント
5. `quiz` — 理解度クイズの出題(構造化出力で選択肢生成)と、回答評価後の
   quiz_result イベント記録(score 0..1)
6. `update_profile` — 面談: profile更新 + interview イベント(第1便のRPCを使用、同一トランザクション)

イベント追記後の concepts/edges 更新は必ず `derive.rebuild()` 経由(直接書かない)。

## 中核シナリオ(受け入れの本丸)

利用者が「RoPEって何?」のような未知語を出したとき:
1. `search_ledger` で台帳を確認 → 未知(または前提概念が欠けている)と判定
2. 短い説明を返しつつ、前提概念への**橋渡しタスク**を `create_task` で生成
   (例: 「位置エンコーディングを10分で読む」→ 概念エッジ prerequisite も payload edges で追加)
3. 翌朝の選別・マップに反映される(既存の learning_context 注入と derive がそのまま効く)

## 予算保護(常駐版の回路遮断)

- 呼び出し前に llm_usage の当日 flagship 合計を確認し、上限(既定 70,000 のうち
  チューター想定 30,000。環境変数で調整可)超過なら **LLMを呼ばずに**「今日は予算切れ」と
  応答する
- 応答ごとに usage 実測を llm_usage へ記録(run_id はセッションID)。
- 致命的エラー分類(FatalLLMError)・リトライ全体1回・タイムアウトは既存 client.py を流用

## UI(astrolabe-ui に /tutor ページ)

- チャット画面(星図トークンでスタイル)。履歴はクライアント保持で、エンドポイントは
  環境変数(既定 http://localhost:8787)
- ツール実行の結果(タスク生成・クイズ・記録)は吹き出し内にカードで可視化
- タスク画面のスタブを実タスク一覧(tasks読み出し+完了操作)に昇格
- 実データ・秘密の混入禁止は従来どおり

## 引き継ぐ制約

- pytest はオフライン・APIキーなしで全緑(エンジンはLLMスタブで、ツール実行・予算・
  橋渡しシナリオをテスト)。SQLiteバックエンドの既存テストを壊さない
- M0承認条件・第1便の安全ガード(--date等)を維持
- LLMにHTML/UIを書かせない(応答はテキストとツール呼び出しのみ)

## 受け入れ条件

- pytest 全緑(オフライン)。ツール単体・エンジンループ(スタブ)・予算遮断・橋渡しシナリオを含む
- 実台帳(Supabase)+実APIで: 未知語を聞く → 説明 + 橋渡しタスクが tasks と events に
  生まれ、`derive` 後のマップに前提エッジが現れる
- クイズ: 出題→回答→quiz_result が events に入り、confidence が更新される
- チャットからの面談で profile が更新される(interviewイベント同時)
- 予算: 上限超過時にLLM無呼び出しで拒否応答。llm_usage に実測が積まれる
- UI /tutor でチャット・タスク生成・タスク一覧・完了操作が動く
- 朝ジョブ・既存CLI・M2画面に回帰がない

まず実装計画(エンジンのループ設計・ツールスキーマ・APIサーバの形・コミット分割)を
短く提示して施主の承認を待つこと。承認後に実装し、受け入れ条件を自分で全部実行して
報告する。
