# M3 第3便 キックオフ指示(Codexへの発注プロンプト)

M3最終便: **Vercelデプロイ+Supabase Auth(スマホ対応)**。以下をそのままCodexに貼り付けて使う。

---

astrolabe-core の AGENTS.md と docs/design.md を読んでから作業を始めてください。

**M3第3便: 公開**を実装してください。第1便(Supabase一次台帳)・第2便(チューター核)は
受け入れ済み。本便で「外出先のスマホから、朝の報告・星図・チューターに触れる」を実現し、
M3を完了させます。完了条件は「**未知語に出会ったとき、その場で橋渡しタスクが生まれる**」が
**デプロイされたUIから**できること。

## 最初にやること: 事前検証(設計書§11方式)

実装前に **Vercel Python Functions の実測検証**を行い、結果を報告してから本実装に入ること:

1. core を import する最小の Python Function をデプロイし、コールドスタート時間・
   バンドルサイズ制限・**関数の最大実行時間**(無料プランの現行値)を実測する
2. チューター1ターン(flagship呼び出し+ツール実行で5〜30秒)が実行時間制限に
   収まるかを確認する。収まらない場合は実装に入らず、対策(maxDuration設定・
   ストリーミング・分割応答など)を提案して施主の判断を仰ぐ
3. フォールバック(検証が不成立の場合): 読み取り系(星図・報告・タスク)+認証のみを
   デプロイし、チューターはローカル限定のまま残す縮退案を提示する

## アーキテクチャ

- **データ経路の変更**: デプロイされたUIはローカルのexportsを読めないため、
  **本番のデータ源はSupabaseに一本化**する。朝ジョブに「map / index / layout /
  当日reportのJSONをSupabaseへupsert」するステップを追加(ledgerリポジトリへの
  exports/snapshotコミットは従来どおり維持=git履歴は捨てない)。
  スキーマ追加は `supabase/migrations/` に置き、既存の権限方針
  (RLS有効・anon/authenticated revoke・service_roleのみ)を踏襲する。
  **ローカル開発のfixture/exportsフォールバックは壊さない**
- **API**: Vercel Python Functions が core を直接 import(TS二重実装禁止)。
  エンドポイントは第2便のローカルAPIと同じ契約(/v1/tutor/turn, /v1/tasks, …)+
  読み取り系(map / reports / index)。エンジンはステートレスのまま
- **認証**: Supabase Auth。利用者は施主ひとり:
  - UI側: supabase-js(anonキー使用)でログイン。未認証は全ページでログイン画面へ
  - API側: Authorization ヘッダのJWTを Supabase の `/auth/v1/user` 呼び出しで検証する
    (**PyJWT等の新規依存を増やさないため**。個人規模なら1リクエスト1検証で十分)。
    さらに検証済みユーザーのIDが環境変数 `ASTROLABE_OWNER_USER_ID` と一致することを
    確認する(単独利用者ガード)
  - anonキーはWebに公開される前提。第1便で入れた**RLS全拒否がここで効く**
    (anonキーでは台帳に一切触れない)ことをテストで確認する
- **Secrets**: `OPENAI_API_KEY` / `SUPABASE_SERVICE_ROLE_KEY` 等は Vercel の環境変数
  (server-side)のみ。`NEXT_PUBLIC_` に載せてよいのは SUPABASE_URL と anonキーだけ。
  値の設定は施主が行う(コマンド・画面手順をあなたが提示。値は要求・表示しない)

## 施主の分担(手順書をあなたが用意)

1. Vercelアカウント作成と `vercel` CLIログイン、ui/coreプロジェクトのリンク
2. Vercel環境変数の設定(提示されたコマンドで)
3. Supabase Authでの自分のユーザー作成、**public sign-upの無効化**
   (Dashboard → Authentication)、`ASTROLABE_OWNER_USER_ID` の設定
4. migration の SQL Editor 適用(従来どおり施主レビュー後)

## 引き継ぐ制約

- 新規Python依存なし(JWT検証は上記方式)。UI側の依存追加は supabase-js のみ許可、
  それ以外は提案制
- M0承認条件・予算二重ゲート・127.0.0.1ローカルサーバ等、既存の安全機構を壊さない
- 朝ジョブ(Actions)は引き続き唯一の定期書き込み者。Vercel側から朝ジョブ相当の
  処理を動かさない
- 実データ・Secretsをリポジトリにコミットしない。pytestオフライン全緑を維持

## 受け入れ条件

- 事前検証レポート(コールドスタート・制限値・チューター1ターンの実測)が提示されている
- デプロイURLで: 未認証アクセスは全ページ・全APIで拒否/ログイン後に星図・報告・
  履歴・タスク・チューターが動作/**スマホ実機で同じことができる**(施主確認)
- デプロイUIから橋渡しシナリオ(未知語→タスク生成→台帳反映)が実行できる(M3完了条件)
- anonキー単体では台帳のどのテーブルにもアクセスできないことをテストで確認
- 翌朝の朝ジョブ後、**再デプロイなしで**UIに新しい報告と星が反映される
- ローカル開発経路(fixtureモードUI・tutor-serve・CLI)に回帰がない
- core / ui の全テスト・lint・build 緑

まず事前検証の結果と実装計画(データupsertの形・関数構成・認証フロー・コミット分割)を
提示して施主の承認を待つこと。承認後に実装し、受け入れ条件を自分で全部実行して報告する。
