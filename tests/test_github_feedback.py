"""GitHub Issueフィードバック同期。すべてMockTransportでオフライン実行する。"""

import json

import httpx

from astrolabe.github_feedback import GitHubFeedbackClient, sync_feedback
from astrolabe.ledger import derive, events

REPOSITORY = "Ebisen1231/astrolabe-ledger"


def _seed_concept(ledger, concept_id="rag", name="RAG"):
    with ledger:
        events.append_event(
            ledger,
            "proposed",
            concept_id,
            {"name": name, "kind": "concept", "summary": "seed"},
        )
    derive.rebuild(ledger)


def _issue(number: int, title: str) -> dict:
    return {"number": number, "title": title, "state": "open"}


def test_four_feedback_actions_map_to_events(ledger):
    _seed_concept(ledger)
    issues = [
        _issue(1, "[fb] selected rag"),
        _issue(2, "[fb] selected-later rag"),
        _issue(3, "[fb] marked_known rag"),
        _issue(4, "[fb] dismissed rag"),
        _issue(5, "普通のIssue"),
    ]
    closed: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, request=request, json=issues)
        closed.append(int(request.url.path.rsplit("/", 1)[-1]))
        return httpx.Response(200, request=request, json={"state": "closed"})

    client = GitHubFeedbackClient(
        "test-token",
        REPOSITORY,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _: None,
    )
    result = sync_feedback(ledger, client)
    client.close()

    assert result.imported == 4
    assert result.closed == 4
    assert closed == [1, 2, 3, 4]
    rows = ledger.execute(
        "SELECT type, payload FROM events WHERE type != 'proposed' ORDER BY id"
    ).fetchall()
    assert [row["type"] for row in rows] == [
        "selected",
        "selected",
        "marked_known",
        "dismissed",
    ]
    assert json.loads(rows[1]["payload"])["later"] is True
    assert ledger.execute("SELECT status FROM concepts WHERE id='rag'").fetchone()[0] == "learned"


def test_close_failure_retried_next_run_without_duplicate_event(ledger):
    _seed_concept(ledger)
    patch_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal patch_calls
        if request.method == "GET":
            return httpx.Response(
                200,
                request=request,
                json=[_issue(41, "[fb] selected rag")],
            )
        patch_calls += 1
        if patch_calls <= 2:  # 初回 + 1retryを両方失敗させる
            return httpx.Response(503, request=request, json={"message": "temporary"})
        return httpx.Response(200, request=request, json={"state": "closed"})

    client = GitHubFeedbackClient(
        "test-token",
        REPOSITORY,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _: None,
    )
    first = sync_feedback(ledger, client)
    assert (first.imported, first.close_failed, first.closed) == (1, 1, 0)
    assert patch_calls == 2

    second = sync_feedback(ledger, client)
    client.close()
    assert (second.imported, second.already_recorded, second.closed) == (0, 1, 1)
    assert patch_calls == 3
    assert (
        ledger.execute("SELECT COUNT(*) FROM events WHERE type='selected'").fetchone()[0]
        == 1
    )


def test_unknown_concept_is_left_open(ledger):
    closed = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal closed
        if request.method == "GET":
            return httpx.Response(
                200,
                request=request,
                json=[_issue(9, "[fb] selected missing-concept")],
            )
        closed = True
        return httpx.Response(200, request=request)

    client = GitHubFeedbackClient(
        "test-token", REPOSITORY, transport=httpx.MockTransport(handler)
    )
    result = sync_feedback(ledger, client)
    client.close()
    assert result.invalid == 1
    assert result.imported == 0
    assert closed is False
    assert ledger.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
