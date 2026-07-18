"""RSS/Atomフィード取得。1ソースの失敗はスキップして続行する。"""

from __future__ import annotations

import html
import logging
import re
import time
from collections.abc import Callable
from urllib.parse import urlparse

import feedparser
import httpx

from astrolabe.collect import USER_AGENT, CollectError

_norm = re.compile(r"\s+")


def _strip_html(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s or ""))


def _norm_ws(s: str) -> str:
    return _norm.sub(" ", s or "").strip()


def parse_feed_text(text: str, source: str) -> list[dict]:
    parsed = feedparser.parse(text)
    items = []
    for e in parsed.entries:
        link = e.get("link", "")
        items.append(
            {
                "id": e.get("id") or link,
                "title": _norm_ws(e.get("title", "")),
                "summary": _norm_ws(_strip_html(e.get("summary", "")))[:2000],
                "url": link,
                "published": e.get("published", ""),
                "source": source,
                "categories": [],
            }
        )
    return [i for i in items if i["title"]]


def _source_name(url: str) -> str:
    return urlparse(url).netloc or url


def _get_with_retry(
    client: httpx.Client, url: str, sleeper: Callable[[float], None]
) -> str:
    last_error: Exception | None = None
    for attempt in range(2):  # リトライは全体で1回
        try:
            r = client.get(url, follow_redirects=True)
            r.raise_for_status()
            return r.text
        except httpx.HTTPError as e:
            last_error = e
            if attempt == 0:
                sleeper(2.0)
    raise CollectError(f"RSS取得失敗(リトライ1回込み): {url}: {last_error}")


def fetch_feeds(
    urls: tuple[str, ...],
    *,
    timeout: float = 20.0,
    transport: httpx.BaseTransport | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    logger: logging.Logger | None = None,
) -> list[dict]:
    items: list[dict] = []
    with httpx.Client(
        timeout=timeout, transport=transport, headers={"User-Agent": USER_AGENT}
    ) as client:
        for url in urls:
            try:
                text = _get_with_retry(client, url, sleeper)
                items.extend(parse_feed_text(text, _source_name(url)))
            except Exception as e:  # 1ソースの障害で朝ジョブを落とさない
                if logger:
                    logger.warning("RSSソースをスキップ: %s (%s)", url, e)
    return items
