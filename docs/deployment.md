# M3第3便 公開手順(施主作業)

秘密値をチャット・Issue・commitへ貼らない。以下の対話コマンドの`Value?`へ直接入力する。
`NEXT_PUBLIC_`を付けてよいのはSupabase URL、anon key、公開API URLだけである。

## 1. migrationのレビューと適用

対象: `supabase/migrations/202607190004_m3_published_artifacts.sql`

1. SQLをレビューする。
2. Supabase Dashboard → SQL Editorで全文を実行する。
3. `Success. No rows returned`を確認する。
4. 適用完了をCodexへ伝える。適用前に`publish-exports`を実行しない。

## 2. Supabase Auth

1. Dashboard → Authentication → Usersで施主ユーザーを作成する。
2. ユーザーUUIDを控える。パスワードは共有しない。
3. Authentication → Providers → Emailでpublic sign-upを無効化する。
4. 匿名ブラウザでsign-upできないことを確認する。

## 3. Vercel project link

coreは`astrolabe-core`へリンク済み。UIをリンクする。

```bash
cd /Users/kawahataseiya/my_project/astrolabe-ui
npx --yes vercel@latest login
npx --yes vercel@latest link
```

Vercel Dashboardの各project → Domainsでcore/UIのProduction URLを確定する。

## 4. coreのProduction環境変数

```bash
cd /Users/kawahataseiya/my_project/astrolabe-handoff
npx --yes vercel@latest env add ASTROLABE_BACKEND production
npx --yes vercel@latest env add SUPABASE_URL production
npx --yes vercel@latest env add SUPABASE_ANON_KEY production
npx --yes vercel@latest env add SUPABASE_SERVICE_ROLE_KEY production
npx --yes vercel@latest env add OPENAI_API_KEY production
npx --yes vercel@latest env add ASTROLABE_MODEL_FLAGSHIP production
npx --yes vercel@latest env add ASTROLABE_OWNER_USER_ID production
npx --yes vercel@latest env add ASTROLABE_ALLOWED_ORIGIN production
```

- `ASTROLABE_BACKEND`: `supabase`
- `ASTROLABE_OWNER_USER_ID`: 手順2のUUID
- `ASTROLABE_ALLOWED_ORIGIN`: UIのProduction origin。`https://...`、末尾slashなし
- Sensitive指定: service role key、OpenAI key。その他もSensitive指定で問題ない

Previewでも受け入れを行う場合は同じ変数を`preview`へ設定する。既にPreviewへ設定済みの
OpenAI key/model以外を追加する。

## 5. UIのProduction環境変数

```bash
cd /Users/kawahataseiya/my_project/astrolabe-ui
npx --yes vercel@latest env add NEXT_PUBLIC_ASTROLABE_API_URL production
npx --yes vercel@latest env add NEXT_PUBLIC_SUPABASE_URL production
npx --yes vercel@latest env add NEXT_PUBLIC_SUPABASE_ANON_KEY production
```

- API URL: coreのProduction origin、末尾slashなし
- anon keyはWeb公開前提。service role keyをUI projectへ設定してはいけない

## 6. 初回artifact投入とanon拒否確認

migration適用後、ローカルcoreの環境へ`SUPABASE_ANON_KEY`も設定する。

```bash
ASTROLABE_BACKEND=supabase astrolabe export
ASTROLABE_BACKEND=supabase astrolabe publish-exports --all-reports
astrolabe verify-anon-denied
```

`verify-anon-denied`はeventsからpublished_artifactsまで全8 tableがHTTP 401/403である場合だけ
成功する。200(空配列を含む)・404・5xxは失敗扱いにする。

private `astrolabe-ledger` workflowには通常日の`publish-exports`を追加済み。publish失敗時は
jobを失敗させ、古い公開データを成功扱いにしない。snapshot/HTML/exportsのgit保存は維持する。

## 7. Production deploy

```bash
cd /Users/kawahataseiya/my_project/astrolabe-handoff
npx --yes vercel@latest deploy --prod

cd /Users/kawahataseiya/my_project/astrolabe-ui
npx --yes vercel@latest deploy --prod
```

環境変数やProduction domainを変更した場合は対象projectを再deployする。

## 8. 受け入れ確認

1. 未ログインのPC/スマホでUIを開き、ログイン画面以外が見えない。
2. Bearerなしでcoreの`/v1/map`を開き、401 JSONになる。
3. ログイン後、星図・今日の報告・履歴・タスクを確認する。
4. チューターへ未登録語を質問し、説明と橋渡しtask cardを確認する。
5. task画面で同じtaskを確認し、evidence付きで完了する。
6. Supabaseのtasks/events/edgesで`task_created`、prerequisite edge、`task_done`を確認する。
7. スマホ実機で1〜5を繰り返す。
8. 翌朝のActions成功後、再deployせず最新report日と新規星が変わることを確認する。
