import httpx
import pytest

from astrolabe.ledger.backend import LedgerBackendError
from astrolabe.ledger.supabase import (
    SupabaseAuthError,
    SupabaseLedger,
    SupabaseUnavailableError,
)


def _ledger(handler, sleeps: list[float] | None = None) -> SupabaseLedger:
    return SupabaseLedger(
        "https://example.supabase.co",
        "secret-not-for-output",
        transport=httpx.MockTransport(handler),
        sleeper=(sleeps.append if sleeps is not None else lambda _: None),
    )


def test_supabase_retries_one_5xx_then_succeeds():
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"message": "temporary"}, request=request)
        return httpx.Response(200, json=[], request=request)

    ledger = _ledger(handler, sleeps)
    try:
        assert ledger.load_events() == []
    finally:
        ledger.close()
    assert calls == 2
    assert sleeps == [1.0]


def test_supabase_connection_failure_retries_only_once():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline", request=request)

    ledger = _ledger(handler)
    with pytest.raises(SupabaseUnavailableError, match="Supabaseへ接続できない"):
        ledger.load_events()
    ledger.close()
    assert calls == 2


def test_supabase_auth_error_is_immediate_and_does_not_expose_key():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"message": "bad jwt"}, request=request)

    ledger = _ledger(handler)
    with pytest.raises(SupabaseAuthError) as exc:
        ledger.load_events()
    ledger.close()
    message = str(exc.value)
    assert "SUPABASE_URL" in message
    assert "SUPABASE_SERVICE_ROLE_KEY" in message
    assert "secret-not-for-output" not in message
    assert calls == 1


def test_supabase_non_5xx_4xx_is_not_retried():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"message": "rate limited"}, request=request)

    ledger = _ledger(handler)
    with pytest.raises(LedgerBackendError, match="HTTP 429"):
        ledger.load_events()
    ledger.close()
    assert calls == 1


def test_missing_rpc_points_to_migrations():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "function missing"}, request=request)

    ledger = _ledger(handler)
    with pytest.raises(LedgerBackendError, match="supabase/migrations"):
        ledger.replace_derived([], [])
    ledger.close()


def test_preserved_event_import_uses_rpc_and_keeps_ids():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(200, json={"events": 1}, request=request)

    ledger = _ledger(handler)
    rows = [
        {
            "id": 7,
            "ts": "2026-07-19T00:00:00+00:00",
            "type": "chat_note",
            "concept_id": None,
            "payload": {"note": "fixture"},
        }
    ]
    try:
        assert ledger.append_events(rows, preserve_ids=True) == [7]
    finally:
        ledger.close()
    assert seen["path"].endswith("/rpc/astrolabe_import_events")
    assert b"secret-not-for-output" not in seen["body"]


def test_supabase_url_accepts_existing_rest_v1_suffix():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json=[], request=request)

    ledger = SupabaseLedger(
        "https://example.supabase.co/rest/v1",
        "secret-not-for-output",
        transport=httpx.MockTransport(handler),
    )
    try:
        ledger.load_events()
    finally:
        ledger.close()
    assert paths == ["/rest/v1/events"]


def test_tutor_task_operations_use_atomic_rpcs():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("astrolabe_create_task"):
            return httpx.Response(200, json={"id": 1, "status": "open"}, request=request)
        return httpx.Response(200, json={"id": 1, "status": "done"}, request=request)

    ledger = _ledger(handler)
    try:
        created = ledger.create_task(
            {"title": "read", "kind": "read"},
            {"type": "task_created", "payload": {}},
        )
        completed = ledger.complete_task(1, "evidence", "2026-07-19T00:00:00Z", {})
    finally:
        ledger.close()
    assert created["status"] == "open"
    assert completed["status"] == "done"
    assert seen == [
        "/rest/v1/rpc/astrolabe_create_task",
        "/rest/v1/rpc/astrolabe_complete_task",
    ]


def test_supabase_usage_aggregation_can_filter_tutor_prefix():
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(str(request.url.query))
        return httpx.Response(200, json=[{"tokens": 3}, {"tokens": 4}], request=request)

    ledger = _ledger(handler)
    try:
        assert ledger.get_llm_usage_total("2026-07-19", "flagship", "tutor-") == 7
    finally:
        ledger.close()
    assert "run_id=like.tutor-%2A" in queries[0]
