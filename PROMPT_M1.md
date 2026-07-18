# M1 キックオフ指示(Codexへの発注プロンプト)

以下をそのままCodexへの最初の指示として貼り付けて使う。

---

AGENTS.md と docs/design.md を読んでから作業を始めてください。

このリポジトリで、設計書のマイルストーン **M1(自動化とHTML報告)** を実装してください。
完了条件は「**何もしなくても毎朝届き、選択が翌朝の提案に効果を持つ**」(設計書§9)。

注意: AGENTS.md には「現在の実装範囲はM0のみ」とありますが、施主が 2026-07-18 にM1移行を承認済みです。最初のコミットで AGENTS.md のフェーズ表記を M1 に更新し、この矛盾を解消してください(M1でも範囲外のもの: Next.js UI、Supabase、常駐チューター対話、週次自己改修、精読・下読み工程、HN収集)。

## 進め方

1. まず実装計画を短く提示して、施主の承認を待つこと(ファイル構成、Actionsワークフロー設計、コミット分割)
2. 承認後に実装。コミットは小さく意味単位で
3. 受け入れ条件(下記)を自分で実行確認し、結果を報告する

## 確定済みの決定(施主承認 2026-07-18)

- 通知チャネル: **Discord webhook**(設計§12の未決事項は解消済み)
- astrolabe-core は **public リポジトリ化**(`Ebisen1231/astrolabe-core`)
- 台帳は **private リポジトリ** `Ebisen1231/astrolabe-ledger` で運用(実体は `~/astrolabe-ledger`、初期化済みの実データがある)
- 一次選別モデルは現在 `gpt-5.4-nano`(検証段階の施主判断)。モデルIDは今後も環境変数注入で、コードに書かない

## 実装項目(6点)

1. **自己完結HTML報告** — `src/astrolabe/render_html.py` を新設。標準ライブラリのテンプレート(`string.Template` 等)で1ファイルHTMLを生成する。内容: 日付/マップ差分一文/トピック(学習コンテンツ)/学習マップ(concepts・edges を埋め込みJSONにし、CDN読み込みの Cytoscape.js で描画。設計§7 v0.1)/トピックごとのフィードバックリンク4種。`daily_reports.html_path` に保存先を記録。**LLMにHTMLを書かせない**(描画は決定的コード、設計原則3)
2. **フィードバック→events** — フィードバックリンクの実体は ledger リポジトリへの **prefilled GitHub Issue 作成URL**(タイトル規約例: `[fb] selected <concept_id>`。4操作: 学ぶ=selected / 気になる=selected(payload later) / もう知っている=marked_known / 興味がない=dismissed。設計§5.1)。朝ジョブ冒頭で open な `[fb]` issue を GitHub API で取り込み → 対応イベント追記 → issue をクローズ。トークンがないローカル実行では警告してスキップ(ジョブは落とさない)
3. **選択が翌朝に効く** — 一次選別プロンプトに (a) 学習済み概念リスト(既知ペナルティ用)(b) 直近の selected / dismissed 概念名を決定的に注入する。プロンプト構築関数にテストを書く。interests の時間減衰・強化アルゴリズムは実装せず `docs/backlog.md` に追記(M2)
4. **台帳のprivateリポジトリ運用** — `~/astrolabe-ledger` を git 化し private リポジトリへ push(SQLiteのコミット運用は設計§3どおりM2まで)。HTML報告もこのリポジトリにコミットして履歴を残す
5. **GitHub Actions** — astrolabe-core に workflow を追加: `schedule: cron "30 21 * * *"`(=06:30 JST、無料枠リセット前の前日枠を使う設計§6.1)+ `workflow_dispatch`。ジョブ: core と ledger を checkout → `astrolabe morning`(カナリア→収集→選別→統合→HTML)→ ledger のDB・HTMLをコミット&push → Discord webhook へ通知(トピック題目+使用トークン+HTML添付またはリンク)。通知失敗は警告に留めジョブを落とさない。morning の非0終了はワークフローも失敗にして可視化する
6. **リポジトリ公開** — `gh repo create Ebisen1231/astrolabe-core --public` で公開しpush(gh は認証済み)

## 引き継ぐ制約(M0承認条件。全て維持すること)

1. 非dry-runでは `ASTROLABE_LEDGER_PATH` 必須。暗黙のフォールバック禁止。dry-run・テストは一時DBのみで、実台帳と実APIに触れない(pytest はAPIキーなしで全緑を維持)
2. concepts/edges は events からの厳密再導出のみ(`derive.rebuild()` 以外から書かない)。profile 更新は対応イベントと同一トランザクション
3. トークン予算は「呼び出し前 precheck + 応答後 usage 実測累積(カナリア込み)」の両輪を維持
4. ネットワークリトライはSDK込みで全体1回に統一(OpenAIクライアント max_retries=0 を変えない)
5. **新規Python依存は追加しない**(Jinja2禁止。テンプレートは標準ライブラリ、HTTP は既存の httpx)。増やしたい場合は実装前に理由付きで施主に提案
6. 台帳実データ・APIキー・webhook URLをコミットしない。台帳DBの中身をログや core リポジトリに出さない(件数の表示は可)

## 秘密情報の分担(厳守)

- `OPENAI_API_KEY` と `DISCORD_WEBHOOK_URL` の Actions Secrets 登録は**施主が実行**する。あなたはコピペ用の `gh secret set` コマンドを提示するだけで、値を要求・入力・表示しない
- ledger checkout 用のデプロイキーは新規生成なのであなたが作ってよい(`ssh-keygen` → 公開鍵を ledger リポジトリの deploy key に登録(write可)、秘密鍵を core の Actions Secret へ。値を画面に出力しない)
- モデルID(`ASTROLABE_MODEL_MINI=gpt-5.4-nano` / `ASTROLABE_MODEL_FLAGSHIP=gpt-5.6-terra`)は秘密ではないので workflow YAML の env に直接書いてよい(変更しやすいようにコメントを添える)

## M1 の受け入れ条件

- `pytest` 全緑(オフライン・APIキーなしのまま)。HTML生成/フィードバック取り込み/プロンプト注入のテストを含む
- `astrolabe morning --dry-run` が従来どおり動き、HTML も一時ディレクトリに生成される
- ローカルの実行で自己完結HTMLがブラウザで開け、マップとフィードバックリンクが機能する
- ledger リポジトリにテスト用 `[fb]` issue を1件作り、次の morning 実行で events に反映され issue が閉じること
- `workflow_dispatch` での Actions 実行が最後まで通り、ledger にコミットが積まれ、Discord に通知が届くこと
- スケジュール実行が翌朝、人手なしで届くこと(これがM1の完了条件)

まず計画の提示から。
