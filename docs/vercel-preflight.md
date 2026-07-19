# M3第3便 Vercel Python Functions 事前検証

実施日: 2026-07-19 / Project: `astrolabe-core` Preview / Region: `iad1`

## 結果

- Vercel CLI: 56.3.2、remote build CLI: 56.2.0
- Python: 3.12.13 / uv: 0.10.11
- remote build: 9秒
- core module import: 1.06〜1.12秒
- 初回Function応答TTFB: 1.07秒 / warm TTFB: 0.736秒
- bundle: 20.25MB(Python上限500MBの約4%)
- 65秒sleep: HTTP 200、Function内部65.00秒
- 305秒sleep: 約300秒でHTTP 504 `FUNCTION_INVOCATION_TIMEOUT`
- 実flagship＋TutorEngine＋stub tool loop: Function内部7.88秒、外形9.89秒
- tool結果: `ledger_search` → `task_created`

## 判定

Go。通常のチューター1ターンは実効上限300秒に十分収まる。本実装では暴走を抑えるため
Function `maxDuration` を120秒、OpenAI clientと1ターン共有deadlineを100秒に固定する。

現行runtimeではsrc-layoutに複数のPython entrypoint候補がある場合、`pyproject.toml` の
`[tool.vercel].entrypoint` が必須である。デプロイ上は単一の`index` Functionへ束ねる。

検証専用Preview deploymentとprobeコードは測定直後に削除した。
