import json
from datetime import UTC, datetime

import httpx
import pytest

from astrolabe.ledger import derive, events, store
from astrolabe.llm.budget import BudgetExceededError
from astrolabe.notify_discord import send_weekly_review
from astrolabe.weekly_review import (
    WEEKLY_REVIEW_SCHEMA,
    WEEKLY_REVIEW_SYSTEM_PROMPT,
    GitHubImprovementClient,
    ImprovementIssue,
    PrivacyViolation,
    WeeklyBudgetGuard,
    collect_weekly_signals,
    proposal_key,
    run_weekly_review,
)


class FixtureWeeklyLLM:
    def __init__(self, path):
        self.value = json.loads(path.read_text(encoding="utf-8"))

    def structured(self, *args, **kwargs):
        return self.value


class SpyIssueClient:
    def __init__(self):
        self.calls = []
        self.by_key = {}

    def create_or_get_issue(self, key, title, body):
        self.calls.append((key, title, body))
        created = key not in self.by_key
        self.by_key.setdefault(key, len(self.by_key) + 10)
        number = self.by_key[key]
        return ImprovementIssue(
            number,
            f"https://github.com/private/ledger/issues/{number}",
            created,
        )


class SpyNotifier:
    def __init__(self):
        self.calls = []

    def __call__(self, summary, issues):
        self.calls.append((summary, issues))
        return True


def _seed_private_signals(ledger):
    events.append_event(
        ledger,
        "proposed",
        "rag",
        {
            "name": "RAG",
            "source_urls": ["https://secret.example/note"],
        },
        ts="2026-07-15T00:00:00+09:00",
    )
    events.append_event(
        ledger,
        "chat_note",
        None,
        {
            "note": (
                "RAGについて https://secret.example/note を見た。"
                "検索画面が使いにくいので改善してほしいという原文メモを引用する。"
            )
        },
        ts="2026-07-18T00:00:00+09:00",
    )
    derive.rebuild(ledger)


def test_weekly_schema_is_strict_and_prompt_freezes_privacy_language():
    assert WEEKLY_REVIEW_SCHEMA["additionalProperties"] is False
    assert WEEKLY_REVIEW_SCHEMA["properties"]["proposals"]["minItems"] == 1
    assert WEEKLY_REVIEW_SCHEMA["properties"]["proposals"]["maxItems"] == 3
    assert "既知概念名を列挙または引用してはいけません" in WEEKLY_REVIEW_SYSTEM_PROMPT
    assert "chat_note原文またはその断片" in WEEKLY_REVIEW_SYSTEM_PROMPT
    assert "面談内容を引用してはいけません" in WEEKLY_REVIEW_SYSTEM_PROMPT


def test_weekly_signals_redact_concepts_and_urls(ledger):
    _seed_private_signals(ledger)
    signals = collect_weekly_signals(ledger, "2026-07-20")
    serialized = json.dumps(signals.prompt_data, ensure_ascii=False)
    assert "RAG" not in serialized
    assert "https://secret.example" not in serialized
    assert "<concept>" in serialized


def test_leaking_fixture_fails_before_issue_task_or_discord_side_effects(
    ledger, fixtures_dir
):
    _seed_private_signals(ledger)
    before_events = events.load_events(ledger)
    before_tasks = store.list_tasks(ledger)
    issues = SpyIssueClient()
    notifier = SpyNotifier()

    with pytest.raises(PrivacyViolation):
        run_weekly_review(
            ledger,
            FixtureWeeklyLLM(fixtures_dir / "llm_weekly_review_leak.json"),
            issues,
            notifier,
            today="2026-07-20",
        )

    assert issues.calls == []
    assert notifier.calls == []
    assert store.list_tasks(ledger) == before_tasks == []
    assert events.load_events(ledger) == before_events


def test_safe_weekly_review_creates_issue_task_event_and_is_idempotent(
    ledger, fixtures_dir
):
    _seed_private_signals(ledger)
    issues = SpyIssueClient()
    notifier = SpyNotifier()
    llm = FixtureWeeklyLLM(fixtures_dir / "llm_weekly_review.json")
    def now():
        return datetime(2026, 7, 20, 3, 0, tzinfo=UTC)

    first = run_weekly_review(
        ledger, llm, issues, notifier, today="2026-07-20", now=now
    )
    second = run_weekly_review(
        ledger, llm, issues, notifier, today="2026-07-20", now=now
    )

    assert first.created_issues == 1
    assert first.created_tasks == 1
    assert second.created_issues == 0
    assert second.created_tasks == 0
    assert len(store.list_tasks(ledger)) == 1
    assert store.list_tasks(ledger)[0]["kind"] == "build_app_feature"
    task_events = [row for row in events.load_events(ledger) if row["type"] == "task_created"]
    assert len(task_events) == 1
    assert task_events[0]["payload"]["metadata"]["proposal_key"] == first.proposal_keys[0]
    assert "## 背景" in issues.calls[0][2]
    assert "## 要求" in issues.calls[0][2]
    assert "## 受け入れ条件" in issues.calls[0][2]
    assert len(notifier.calls) == 2


def test_proposal_key_is_normalized_and_deterministic():
    assert proposal_key("2026-07-14", "  操作  導線 ") == proposal_key(
        "2026-07-14", "操作 導線"
    )
    assert proposal_key("2026-07-14", "操作導線") != proposal_key(
        "2026-07-15", "操作導線"
    )


def test_github_client_reuses_marker_without_creating_issue():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json=[
                {
                    "number": 42,
                    "html_url": "https://github.com/private/ledger/issues/42",
                    "body": "<!-- astrolabe-weekly-review:v1 key=abc -->",
                }
            ],
            request=request,
        )

    client = GitHubImprovementClient(
        "token", "private/ledger", transport=httpx.MockTransport(handler)
    )
    try:
        issue = client.create_or_get_issue("abc", "title", "body")
    finally:
        client.close()
    assert issue == ImprovementIssue(42, "https://github.com/private/ledger/issues/42", False)
    assert calls == [("GET", "/repos/private/ledger/issues")]


def test_weekly_discord_retries_once():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        status = 503 if calls == 1 else 204
        return httpx.Response(status, request=request)

    assert send_weekly_review(
        "https://discord.com/api/webhooks/test/token",
        "要約",
        [{"title": "改善", "url": "https://github.com/private/ledger/issues/1"}],
        transport=httpx.MockTransport(handler),
        sleeper=lambda _: None,
    )
    assert calls == 2


def test_weekly_budget_checks_persisted_total_and_records_real_usage(ledger):
    store.save_llm_usage(
        ledger,
        "2026-07-20",
        "morning-1",
        {"flagship": {"used": 69_000}},
    )
    guard = WeeklyBudgetGuard(
        ledger,
        "2026-07-20",
        "weekly-42",
        total_cap=70_000,
    )
    with pytest.raises(BudgetExceededError):
        guard.attempt_precheck("flagship", 1_001)
    guard.attempt_precheck("flagship", 1_000)
    guard.record_usage("flagship", 500)
    assert store.get_llm_usage_for_run(
        ledger, "2026-07-20", "weekly-42", "flagship"
    ) == 500
