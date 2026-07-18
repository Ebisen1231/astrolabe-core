"""Discord通知。MockTransportだけを使い、実webhookには触れない。"""

import json

import httpx

from astrolabe.notify_discord import build_discord_content, send_discord_report

REPORT = {
    "date": "2026-07-18",
    "items": {
        "topics": [{"name": "RAG"}, {"name": "エージェント評価"}],
        "meta": {
            "usage": {
                "mini": {"used": 1200, "cap": 500000},
                "flagship": {"used": 340, "cap": 70000},
            }
        },
    },
}


def test_build_discord_content_contains_titles_and_usage():
    content = build_discord_content(REPORT)
    assert "RAG / エージェント評価" in content
    assert "mini 1,200/500,000 tokens" in content
    assert "flagship 340/70,000 tokens" in content


def test_send_discord_attaches_html(tmp_path):
    html_path = tmp_path / "2026-07-18.html"
    html_path.write_text("<html>report</html>", encoding="utf-8")
    requests: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.read())
        return httpx.Response(204, request=request)

    ok = send_discord_report(
        "https://discord.com/api/webhooks/test/token",
        REPORT,
        html_path,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _: None,
    )
    assert ok is True
    assert len(requests) == 1
    body = requests[0]
    assert b"2026-07-18.html" in body
    assert b"<html>report</html>" in body
    payload = json.dumps(
        {"content": build_discord_content(REPORT)}, ensure_ascii=False
    ).encode()
    assert payload in body


def test_send_discord_failure_retries_once_and_returns_false(tmp_path):
    html_path = tmp_path / "report.html"
    html_path.write_text("report", encoding="utf-8")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, request=request)

    assert (
        send_discord_report(
            "https://discord.com/api/webhooks/test/token",
            REPORT,
            html_path,
            transport=httpx.MockTransport(handler),
            sleeper=lambda _: None,
        )
        is False
    )
    assert calls == 2


def test_missing_webhook_or_html_is_nonfatal(tmp_path):
    assert send_discord_report("", REPORT, tmp_path / "missing.html") is False
