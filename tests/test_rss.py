import httpx

from astrolabe.collect.rss import fetch_feeds, parse_feed_text


def test_parse_feed_text(fixtures_dir):
    items = parse_feed_text((fixtures_dir / "rss_sample.xml").read_text(), "example.com")
    assert len(items) == 4
    first = items[0]
    assert first["id"] == "https://example.com/blog/reranking"
    assert first["title"] == "Contrastive Reranking for Dense Retrieval"
    assert "<" not in first["summary"]  # HTMLタグは除去される
    assert first["source"] == "example.com"


def test_fetch_feeds_skips_failed_source(fixtures_dir):
    text = (fixtures_dir / "rss_sample.xml").read_text()

    def handler(request: httpx.Request) -> httpx.Response:
        if "good" in str(request.url):
            return httpx.Response(200, text=text)
        return httpx.Response(500, text="down")

    items = fetch_feeds(
        ("https://good.example/feed.xml", "https://bad.example/feed.xml"),
        transport=httpx.MockTransport(handler),
        sleeper=lambda s: None,
    )
    # 失敗ソースはスキップされ、例外にならない
    assert len(items) == 4
    assert {i["source"] for i in items} == {"good.example"}
