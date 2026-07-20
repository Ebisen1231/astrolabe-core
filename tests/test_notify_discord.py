"""Discord通知。MockTransportだけを使い、実webhookには触れない。"""

import json
from copy import deepcopy

import httpx

from astrolabe.notify_discord import build_discord_embed, send_discord_report


def load_report(fixtures_dir):
    return json.loads((fixtures_dir / "discord_report.json").read_text(encoding="utf-8"))


def test_build_discord_embed_uses_topic_fields_and_usage_footer(fixtures_dir):
    report = load_report(fixtures_dir)
    embed = build_discord_embed(report)

    assert embed["title"] == "Astrolabe 朝の観測報告"
    assert embed["description"].startswith("2026-07-18\n今日の変化: ")
    assert [field["name"] for field in embed["fields"]] == [
        "検証器つきRAG(Self-Correcting RAG)",
        "リランキング(Reranking)",
    ]
    first_topic = report["items"]["topics"][0]
    assert embed["fields"][0] == {
        "name": first_topic["name"],
        "value": first_topic["summary"][:100] + "\n" + first_topic["source_urls"][0],
        "inline": False,
    }
    assert report["items"]["topics"][1]["source_urls"][1] not in embed["fields"][1]["value"]
    assert embed["footer"]["text"] == (
        "使用量: mini 1,200/500,000 tokens | flagship 340/70,000 tokens"
    )


def test_build_discord_embed_truncates_description_and_field(fixtures_dir):
    report = deepcopy(load_report(fixtures_dir))
    report["map_delta_text"] = "変" * 5000
    topic = report["items"]["topics"][0]
    topic["name"] = "題" * 300
    topic["summary"] = "要" * 200
    topic["source_urls"] = ["https://example.com/" + "u" * 1200]

    embed = build_discord_embed(report)

    assert len(embed["description"]) == 4096
    assert embed["description"].endswith("…")
    assert len(embed["fields"][0]["name"]) == 256
    assert len(embed["fields"][0]["value"]) == 1024
    assert embed["fields"][0]["value"].startswith("要" * 100 + "\nhttps://example.com/")
    assert embed["fields"][0]["value"].endswith("…")


def test_build_discord_embed_appends_due_reviews(fixtures_dir):
    report = deepcopy(load_report(fixtures_dir))
    report["items"]["reviews"] = [
        {"concept_id": "rag", "concept_name": "RAG", "due_date": "2026-07-20"}
    ]
    embed = build_discord_embed(report)
    assert embed["fields"][-1] == {
        "name": "今日の復習",
        "value": "• RAG (期日 2026-07-20)",
        "inline": False,
    }


def test_send_discord_attaches_html_with_embed(fixtures_dir, tmp_path):
    report = load_report(fixtures_dir)
    html_path = tmp_path / "2026-07-18.html"
    html_path.write_text("<html>report</html>", encoding="utf-8")
    requests: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.read())
        return httpx.Response(204, request=request)

    ok = send_discord_report(
        "https://discord.com/api/webhooks/test/token",
        report,
        html_path,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _: None,
    )
    assert ok is True
    assert len(requests) == 1
    body = requests[0]
    assert b"2026-07-18.html" in body
    assert b"<html>report</html>" in body
    payload = json.dumps({"embeds": [build_discord_embed(report)]}, ensure_ascii=False).encode()
    assert payload in body


def test_send_discord_failure_retries_once_and_returns_false(fixtures_dir, tmp_path):
    report = load_report(fixtures_dir)
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
            report,
            html_path,
            transport=httpx.MockTransport(handler),
            sleeper=lambda _: None,
        )
        is False
    )
    assert calls == 2


def test_missing_webhook_or_html_is_nonfatal(fixtures_dir, tmp_path):
    report = load_report(fixtures_dir)
    assert send_discord_report("", report, tmp_path / "missing.html") is False
