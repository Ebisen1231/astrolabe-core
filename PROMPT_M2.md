# M2 キックオフ指示(Codexへの発注プロンプト)

以下をそのままCodexへの最初の指示として貼り付けて使う。

---

astrolabe-core の AGENTS.md と docs/design.md を読んでから作業を始めてください。

**M2(Next.js UIと学習マップ)** を実装してください。完了条件は「**朝、ブラウザのトップでマップの成長差分が見える**」(設計書§9)。M1は受け入れ済みです。

## 確定済みの決定(施主承認 2026-07-19)

- **ローカル起動で始める。** デプロイ・認証・リモート閲覧はM2の範囲外(スマホ対応はM3のSupabase移行と同時に解く)。fixtureデータの公開デモも今回はやらず、バックログに積む
- UIは新規 **public リポジトリ `Ebisen1231/astrolabe-ui`**(設計§3)。実データは private ledger のエクスポートJSONをローカルで読む
- フィードバックはM1と同じ **prefilled GitHub Issue リンク**を使う(API route→台帳の書き込みはM3でSupabaseと同時に導入)

## 実装項目

### A. core側: エクスポート(データ契約)

1. `astrolabe export [--out DIR]` を追加。台帳から決定的に生成:
   - `exports/map.json` — concepts(id/name/kind/status/confidence/first_seen/last_touched)+ edges + 直近報告日 + map_delta_text
   - `exports/reports/YYYY-MM-DD.json` — その日の topics(learn_content含む)+ meta
   - `exports/index.json` — 存在する報告日の一覧
   - **レイアウト座標もcore側で計算して `exports/layout.json` に永続**することを推奨(前回座標を初期値にした決定的レイアウト。同一グラフ→同一座標、ノード追加時は既存ノードを動かさない。§7「毎朝全体が組み変わると成長が読めなくなる」対策)。別方式を採る場合は計画で代案を示すこと
2. 既定の出力先は台帳と同じディレクトリの `exports/`。非dry-runは `ASTROLABE_LEDGER_PATH` 必須・dry-runは一時ディレクトリ(M0条件1を維持)
3. ledger側 workflow に export ステップを追加し `exports/` をコミット(coreの参照コピー `docs/morning.workflow.yml` も同期)
4. 最初のコミットで AGENTS.md のフェーズを M2 に更新し、docs/design.md は**追記式**で変更履歴に「M2着手・ローカル起動決定・デモ公開/リモート閲覧はバックログ」を記録。docs/backlog.md に「fixtureデモのVercel公開」「実データのリモート閲覧(M3のSupabase移行と同時)」を追記

### B. ui側: Next.jsアプリ(新規リポジトリ)

1. `gh repo create Ebisen1231/astrolabe-ui --public` し、Next.js(App Router / TypeScript)で scaffold。**依存は next / react / react-dom / cytoscape + 開発系(typescript / eslint / vitest 等)まで**。それ以外を入れたい場合は実装前に理由付きで提案(CSSはプレーンCSSかCSS Modules。Tailwind等は入れない)
2. データ読み込み: サーバ側で `ASTROLABE_EXPORTS_DIR` のJSONを読む。未設定時はリポジトリ同梱の **fixtureエクスポート**(coreの fixtures から生成したサンプル)にフォールバック。**実データ・実エクスポートをこのpublicリポジトリにコミットしない**(.gitignoreと計画レビューで担保)
3. ページ構成(設計§7 v1):
   - **トップ = 星図マップ**(全画面)。濃紺の地に概念を星、つながりを星座線で描く。上部に日付とマップ差分一文。**当日の新規ノードは金の環で強調**。ノードクリックで概念名・status・summary・出典を表示
   - **今日の報告** — トピックカード(summary / why_now / learn_content / 出典 / フィードバックリンク4種)
   - **履歴** — index.json の日付一覧から過去の報告へ
   - **タスク** — 空状態のスタブのみ(「常駐チューター(M3)で有効になる」と表示)
4. スタイルは以下の星図トークンで固定(CSS変数化):
   - 地 #0B1226 / 区切り #1D2A4A / 文字(明)#E8E3D8 / 文字(暗)#A8B4CE / 補助 #6B7EA3
   - 今日の提案: 星 #F2E8D5 + 金環 #D8A03D / 習得済み: #E8B84B / 未学習: #7C8FB8
   - prerequisite=実線 #5C74AB / related=破線 #3A4E7E / アクセント #D8A03D
5. UIに独自 AGENTS.md を置く(内容: 設計の正は astrolabe-core の docs/design.md、実データコミット禁止、依存追加は提案制、実行時LLM呼び出しゼロ=描画は決定的コード)

## 引き継ぐ制約

- core側はM0承認条件をすべて維持(pytest オフライン全緑 / 台帳パス必須 / 厳密再導出 / 予算二重確認 / リトライ全体1回 / 新規Python依存なし)
- UIの実行時にLLM・外部APIを呼ばない(設計原則2: 描画はコード、トークンゼロ)
- 秘密情報は一切不要な構成のはず。必要になったらそれは設計逸脱なので実装前に相談

## 受け入れ条件

- core: `pytest` 全緑(export・レイアウト安定のテスト含む)。`astrolabe morning --dry-run` でエクスポートも一時ディレクトリに生成される
- レイアウト安定: 同一グラフ2回で同一座標、ノード追加時に既存ノードの座標が変わらないことをテストで保証
- ui: fixtureモードで `npm run dev` が環境変数なしで起動し星図が表示される。lint・build 緑
- 実データ: `astrolabe export` → `ASTROLABE_EXPORTS_DIR=... npm run dev` で、トップに昨日との差分一文と当日ノードの金環が見える(完了条件)
- ledger の朝ジョブが exports/ を毎朝コミットする(workflow_dispatch で確認)

進め方はこれまでどおり: まず実装計画(リポジトリ構成・データ契約の型・レイアウト方式・コミット分割)を短く提示して施主の承認を待つこと。承認後に実装し、受け入れ条件を自分で全部実行して報告する。
