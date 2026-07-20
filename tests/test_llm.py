"""予算保護(TokenBudget)と ResponsesLLM の回路遮断・リトライ・usage計上のテスト。"""

import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from astrolabe.llm.budget import BudgetExceededError, TokenBudget, estimate_tokens
from astrolabe.llm.client import (
    FatalLLMError,
    LLMCallError,
    LLMTimeoutError,
    ResponsesLLM,
    classify_error,
    raise_if_fatal_text,
)
from astrolabe.llm.fixtures import FixtureLLM

# --- TokenBudget ----------------------------------------------------------


def test_budget_precheck_and_add():
    budget = TokenBudget({"mini": 100})
    budget.precheck("mini", 100)  # ちょうど上限まではOK
    assert budget.add("mini", 60) == (60, 100)
    with pytest.raises(BudgetExceededError):
        budget.precheck("mini", 41)
    budget.precheck("mini", 40)
    assert budget.summary() == {"mini": {"used": 60, "cap": 100}}


def test_estimate_tokens_positive():
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 100) == 50


# --- エラー分類 -------------------------------------------------------------


def _http_response(status: int) -> httpx.Response:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return httpx.Response(status, request=request, json={"error": {"message": "x"}})


def test_classify_fatal_errors():
    auth = openai.AuthenticationError("bad key", response=_http_response(401), body=None)
    assert classify_error(auth) == "fatal"
    quota = openai.RateLimitError(
        "You exceeded your current quota", response=_http_response(429), body=None
    )
    assert classify_error(quota) == "fatal"  # 文字列パターンが優先
    assert classify_error(ValueError("insufficient_quota during batch")) == "fatal"


def test_classify_retryable_and_other():
    rate = openai.RateLimitError("slow down", response=_http_response(429), body=None)
    assert classify_error(rate) == "retryable"
    server = openai.InternalServerError("oops", response=_http_response(500), body=None)
    assert classify_error(server) == "retryable"
    conn = openai.APIConnectionError(request=httpx.Request("POST", "https://x"))
    assert classify_error(conn) == "retryable"
    assert classify_error(ValueError("nope")) == "other"


def test_raise_if_fatal_text():
    raise_if_fatal_text("ふつうのエラー")
    with pytest.raises(FatalLLMError):
        raise_if_fatal_text("Error code 429: insufficient_quota")


# --- ResponsesLLM(内部クライアント差し替え)------------------------------


class StubResponses:
    def __init__(self, results):
        self.results = list(results)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def ok_resp(payload='{"ok": 1}', input_tokens=100, output_tokens=20, status="completed"):
    return SimpleNamespace(
        status=status,
        output_text=payload,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class StubOutputItem:
    def __init__(self, **values):
        self.__dict__.update(values)

    def model_dump(self, exclude_none=True):
        return dict(self.__dict__)


def tool_resp(items, text="", input_tokens=10, output_tokens=5):
    return SimpleNamespace(
        status="completed",
        output=items,
        output_text=text,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def make_llm(results, caps=None):
    budget = TokenBudget(caps or {"mini": 10_000, "flagship": 10_000})
    llm = ResponsesLLM(
        api_key="sk-test-dummy",
        models={"mini": "m-mini", "flagship": "m-flag"},
        budget=budget,
        sleeper=lambda s: None,
    )
    stub = StubResponses(results)
    llm._client = SimpleNamespace(responses=stub)
    return llm, budget, stub


SCHEMA = {"type": "object", "additionalProperties": False, "properties": {}, "required": []}


def _call(llm):
    return llm.structured("mini", "test_schema", SCHEMA, "sys", "user", max_output_tokens=100)


def test_sdk_retries_disabled():
    """リトライは全体で1回に統一する: SDK内蔵リトライは無効でなければならない。"""
    budget = TokenBudget({"mini": 100})
    llm = ResponsesLLM(api_key="sk-test-dummy", models={"mini": "m"}, budget=budget)
    assert llm._client.max_retries == 0


def test_structured_success_records_usage():
    llm, budget, stub = make_llm([ok_resp()])
    assert _call(llm) == {"ok": 1}
    assert budget.summary()["mini"]["used"] == 120
    body = stub.calls[0]
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True


def test_retryable_error_retried_once():
    rate = openai.RateLimitError("slow down", response=_http_response(429), body=None)
    llm, budget, stub = make_llm([rate, ok_resp()])
    assert _call(llm) == {"ok": 1}
    assert len(stub.calls) == 2


def test_serverless_timeout_is_returned_without_second_attempt():
    timeout = openai.APITimeoutError(request=httpx.Request("POST", "https://x"))
    budget = TokenBudget({"mini": 10_000})
    llm = ResponsesLLM(
        api_key="sk-test-dummy",
        models={"mini": "m-mini"},
        budget=budget,
        retry_timeouts=False,
    )
    stub = StubResponses([timeout, ok_resp()])
    llm._client = SimpleNamespace(responses=stub)
    with pytest.raises(LLMTimeoutError):
        _call(llm)
    assert len(stub.calls) == 1


def test_serverless_deadline_is_shared_across_tool_rounds():
    now = [10.0]
    budget = TokenBudget({"flagship": 10_000})
    llm = ResponsesLLM(
        api_key="sk-test-dummy",
        models={"flagship": "m-flag"},
        budget=budget,
        timeout=100,
        deadline_seconds=100,
        clock=lambda: now[0],
    )
    stub = StubResponses([tool_resp([], text="ok"), tool_resp([], text="ok")])
    llm._client = SimpleNamespace(responses=stub)
    llm.tool_turn([{"role": "user", "content": "one"}], [])
    now[0] = 105.0
    llm.tool_turn([{"role": "user", "content": "two"}], [])
    assert stub.calls[0]["timeout"] == 100
    assert stub.calls[1]["timeout"] == 5
    now[0] = 111.0
    with pytest.raises(LLMTimeoutError):
        llm.tool_turn([{"role": "user", "content": "three"}], [])
    assert len(stub.calls) == 2


def test_retry_is_exactly_one():
    rate = openai.RateLimitError("slow down", response=_http_response(429), body=None)
    llm, _, stub = make_llm([rate, rate, ok_resp()])
    with pytest.raises(LLMCallError):
        _call(llm)
    assert len(stub.calls) == 2  # 初回 + 1回だけ


def test_fatal_error_aborts_immediately_without_retry():
    auth = openai.AuthenticationError("bad key", response=_http_response(401), body=None)
    llm, budget, stub = make_llm([auth, ok_resp()])
    with pytest.raises(FatalLLMError):
        _call(llm)
    assert len(stub.calls) == 1
    assert budget.summary()["mini"]["used"] == 0


def test_other_error_not_retried():
    bad_request = openai.BadRequestError("bad", response=_http_response(400), body=None)
    llm, _, stub = make_llm([bad_request])
    with pytest.raises(LLMCallError):
        _call(llm)
    assert len(stub.calls) == 1


def test_parse_failure_retried_then_raises_and_usage_counted():
    llm, budget, stub = make_llm([ok_resp("not-json"), ok_resp("still not json")])
    with pytest.raises(LLMCallError):
        _call(llm)
    assert len(stub.calls) == 2
    assert budget.summary()["mini"]["used"] == 240  # 消費したusageは失敗でも計上


def test_incomplete_response_retried():
    llm, _, stub = make_llm([ok_resp(status="incomplete"), ok_resp()])
    assert _call(llm) == {"ok": 1}
    assert len(stub.calls) == 2


def test_precheck_blocks_before_any_call():
    llm, _, stub = make_llm([ok_resp()], caps={"mini": 10, "flagship": 10})
    with pytest.raises(BudgetExceededError):
        _call(llm)
    assert stub.calls == []  # 1コールも発射していない


def test_canary_counts_into_budget():
    llm, budget, stub = make_llm([ok_resp("OK", input_tokens=10, output_tokens=2)])
    info = llm.canary("mini")
    assert info == {"model": "m-mini", "output": "OK"}
    assert budget.summary()["mini"]["used"] == 12  # カナリア分も計上


def test_tool_turn_returns_function_calls_and_uses_stateless_input():
    item = StubOutputItem(
        type="function_call", call_id="call-1", name="search_ledger", arguments='{"query":"RoPE"}'
    )
    llm, budget, stub = make_llm([tool_resp([item])])
    result = llm.tool_turn(
        [{"role": "user", "content": "RoPEって何?"}],
        [{"type": "function", "name": "search_ledger", "parameters": {}}],
    )
    assert result["tool_calls"][0]["name"] == "search_ledger"
    assert result["output_items"][0]["call_id"] == "call-1"
    assert stub.calls[0]["store"] is False
    assert "previous_response_id" not in stub.calls[0]
    assert budget.summary()["flagship"]["used"] == 15


def test_usage_and_attempt_observers_cover_each_response():
    observed_usage: list[tuple[str, int]] = []
    observed_checks: list[tuple[str, int]] = []
    budget = TokenBudget({"mini": 10_000, "flagship": 10_000})
    llm = ResponsesLLM(
        api_key="sk-test-dummy",
        models={"mini": "m-mini", "flagship": "m-flag"},
        budget=budget,
        usage_observer=lambda key, tokens: observed_usage.append((key, tokens)),
        attempt_precheck=lambda key, estimate: observed_checks.append((key, estimate)),
        sleeper=lambda _: None,
    )
    stub = StubResponses([ok_resp(status="incomplete"), ok_resp()])
    llm._client = SimpleNamespace(responses=stub)
    assert _call(llm) == {"ok": 1}
    assert observed_usage == [("mini", 120), ("mini", 120)]
    assert len(observed_checks) == 2


# --- FixtureLLM -----------------------------------------------------------


def test_fixture_llm_triage_and_canary(fixtures_dir):
    budget = TokenBudget({"mini": 10_000, "flagship": 10_000})
    llm = FixtureLLM(fixtures_dir, budget)
    user = json.dumps(
        {"profile": {}, "items": [{"id": "2507.11001"}, {"id": "unknown-item"}]}
    )
    out = llm.structured("mini", "triage_scores", SCHEMA, "sys", user, 100)
    scores = {s["id"]: s["score"] for s in out["scores"]}
    assert scores["2507.11001"] == 0.93
    assert scores["unknown-item"] == 0.12
    assert budget.summary()["mini"]["used"] > 0  # dry-runでも予算経路を通す

    llm.canary("flagship")
    assert budget.summary()["flagship"]["used"] == 20

    synthesis = llm.structured("flagship", "daily_synthesis", SCHEMA, "sys", "{}", 100)
    assert len(synthesis["topics"]) == 3
    assert synthesis["topics"][0]["practice_task"] == {
        "title": "手元のRAGに合否判定プロンプトを1本足し、失敗例を3つ収集する",
        "kind": "implement",
        "est_minutes": 10,
    }
