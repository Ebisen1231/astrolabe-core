"""OpenAI Responses API ラッパ。全呼び出しで JSON Schema 構造化出力を用いる。

予算保護:
- 呼び出し前に TokenBudget.precheck、応答後に usage 実測を累積(カナリア含む)。
- 致命的エラー(残高・認証・アカウント)は FatalLLMError として即時に run 全体を
  中断させる。リトライしない。
- リトライはSDK内蔵を無効化(max_retries=0)し、本モジュールの1回に統一する
  (レート制限・5xx・接続断のみ対象)。構造化出力の解釈失敗も同じ1回の枠で再試行する。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import openai

from astrolabe.llm.budget import TokenBudget, estimate_tokens

FATAL_PATTERNS = (
    "insufficient_quota",
    "exceeded your current quota",
    "invalid_api_key",
    "incorrect api key",
    "account_deactivated",
    "billing_not_active",
    "access_terminated",
)


class FatalLLMError(RuntimeError):
    """リトライで直らないエラー(残高・認証・アカウント)。run全体を即中断する。"""


class LLMCallError(RuntimeError):
    """リトライ1回を使い切った、または構造化出力を解釈できなかった。"""


def raise_if_fatal_text(text: str) -> None:
    """エラー文字列の致命パターン再スキャン(broad except への貫通対策)。"""
    low = (text or "").lower()
    for pattern in FATAL_PATTERNS:
        if pattern in low:
            raise FatalLLMError(f"致命的エラーパターンを検出: {pattern}")


def classify_error(exc: Exception) -> str:
    """'fatal' | 'retryable' | 'other'"""
    if any(p in str(exc).lower() for p in FATAL_PATTERNS):
        return "fatal"
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return "fatal"
    if isinstance(
        exc, openai.APIConnectionError | openai.APITimeoutError | openai.RateLimitError
    ):
        return "retryable"
    if isinstance(exc, openai.APIStatusError) and exc.status_code >= 500:
        return "retryable"
    return "other"


class ResponsesLLM:
    """budget_key("mini" / "flagship")でモデルと予算枠を選ぶ。"""

    def __init__(
        self,
        *,
        api_key: str,
        models: dict[str, str],
        budget: TokenBudget,
        timeout: float = 120.0,
        retry_wait: float = 3.0,
        sleeper: Callable[[float], None] = time.sleep,
        logger: logging.Logger | None = None,
        usage_observer: Callable[[str, int], None] | None = None,
        attempt_precheck: Callable[[str, int], None] | None = None,
    ) -> None:
        self._client = openai.OpenAI(api_key=api_key, timeout=timeout, max_retries=0)
        self._models = dict(models)
        self._budget = budget
        self._retry_wait = retry_wait
        self._sleeper = sleeper
        self._logger = logger or logging.getLogger("astrolabe.llm")
        self._usage_observer = usage_observer
        self._attempt_precheck = attempt_precheck

    def structured(
        self,
        budget_key: str,
        schema_name: str,
        schema: dict,
        system: str,
        user: str,
        max_output_tokens: int,
    ) -> dict:
        model = self._models[budget_key]
        kwargs = {
            "model": model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                }
            },
            "max_output_tokens": max_output_tokens,
        }

        def validate(resp) -> dict:
            if getattr(resp, "status", None) == "incomplete":
                raise LLMCallError(
                    f"出力が途中で切れた({schema_name}, max_output_tokens={max_output_tokens})"
                )
            try:
                return json.loads(resp.output_text)
            except (json.JSONDecodeError, TypeError) as e:
                raise LLMCallError(f"構造化出力のJSON解釈に失敗({schema_name}): {e}") from e

        estimated = estimate_tokens(system + user) + max_output_tokens
        return self._call_with_single_retry(budget_key, schema_name, kwargs, estimated, validate)

    def canary(self, budget_key: str) -> dict:
        """本番前の極小疎通確認(~50トークン)。usage は予算に計上する。"""
        model = self._models[budget_key]
        kwargs = {
            "model": model,
            "input": "疎通確認。「OK」とだけ返す。",
            "max_output_tokens": 16,
        }

        def validate(resp):
            return {"model": model, "output": (resp.output_text or "").strip()[:40]}

        return self._call_with_single_retry(budget_key, "canary", kwargs, 64, validate)

    def tool_turn(
        self,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        max_output_tokens: int = 2_000,
    ) -> dict:
        """strict function callingを1応答ぶん実行する。

        呼び出し側がoutput_itemsとfunction_call_outputを次のinputへ足すため、
        OpenAI側のprevious_response_idやサーバ側セッションへ依存しない。
        """
        model = self._models["flagship"]
        kwargs = {
            "model": model,
            "input": input_items,
            "tools": tools,
            "tool_choice": "auto",
            "max_output_tokens": max_output_tokens,
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }

        def validate(resp) -> dict:
            if getattr(resp, "status", None) == "incomplete":
                raise LLMCallError(
                    f"出力が途中で切れた(tutor, max_output_tokens={max_output_tokens})"
                )
            output_items = [item.model_dump(exclude_none=True) for item in resp.output]
            calls = [
                {
                    "call_id": item.call_id,
                    "name": item.name,
                    "arguments": item.arguments,
                }
                for item in resp.output
                if getattr(item, "type", None) == "function_call"
            ]
            text = (getattr(resp, "output_text", "") or "").strip()
            if not calls and not text:
                raise LLMCallError("チューター応答にテキストもツール呼び出しもない")
            return {"text": text, "tool_calls": calls, "output_items": output_items}

        serialized = json.dumps(input_items, ensure_ascii=False, sort_keys=True)
        serialized_tools = json.dumps(tools, ensure_ascii=False, sort_keys=True)
        estimated = estimate_tokens(serialized + serialized_tools) + max_output_tokens
        return self._call_with_single_retry("flagship", "tutor", kwargs, estimated, validate)

    def _call_with_single_retry(
        self, budget_key: str, label: str, kwargs: dict, estimated: int, validate
    ):
        last_error: Exception | None = None
        for attempt in range(2):  # 初回 + リトライ1回(全体で1回に統一)
            if self._attempt_precheck is not None:
                self._attempt_precheck(budget_key, estimated)
            self._budget.precheck(budget_key, estimated)
            try:
                resp = self._client.responses.create(**kwargs)
            except Exception as e:
                if classify_error(e) == "fatal":
                    raise FatalLLMError(f"致命的エラー({label})。runを中断する: {e}") from e
                last_error = e
                if classify_error(e) == "retryable" and attempt == 0:
                    self._logger.warning(
                        "リトライ可能エラー(%s)。%.0f秒待って1回だけ再試行: %s",
                        label,
                        self._retry_wait,
                        e,
                    )
                    self._sleeper(self._retry_wait)
                    continue
                raise LLMCallError(f"LLM呼び出し失敗({label}): {e}") from e
            self._record_usage(budget_key, kwargs["model"], label, resp)
            try:
                return validate(resp)
            except LLMCallError as e:
                last_error = e
                if attempt == 0:
                    self._logger.warning("%s。1回だけ再試行する", e)
                    continue
                raise
        raise LLMCallError(f"LLM呼び出し失敗({label}): {last_error}")

    def _record_usage(self, budget_key: str, model: str, label: str, resp) -> None:
        usage = getattr(resp, "usage", None)
        tokens = (getattr(usage, "input_tokens", 0) or 0) + (
            getattr(usage, "output_tokens", 0) or 0
        )
        used, cap = self._budget.add(budget_key, tokens)
        if self._usage_observer is not None:
            self._usage_observer(budget_key, tokens)
        self._logger.info(
            "llm %-14s model=%s tokens=%d 累積 %s %d/%d (%.1f%%)",
            label,
            model,
            tokens,
            budget_key,
            used,
            cap,
            100.0 * used / cap if cap else 0.0,
        )
