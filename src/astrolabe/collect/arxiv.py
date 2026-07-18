"""arXiv APIクライアント。

作法(AGENTS.md): リクエスト間隔3秒以上、応答はローカルキャッシュ、
リトライは全体で1回+タイムアウト付き。
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from pathlib import Path

import feedparser
import httpx

from astrolabe.collect import USER_AGENT, CollectError

ARXIV_API_URL = "https://export.arxiv.org/api/query"


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def canonical_arxiv_id(raw_id: str) -> str:
    """'http://arxiv.org/abs/2507.11001v2' → '2507.11001'(バージョン番号を落とす)。"""
    m = re.search(r"abs/([^/]+?)(v\d+)?$", raw_id or "")
    return m.group(1) if m else (raw_id or "")


def parse_arxiv_atom(text: str) -> list[dict]:
    parsed = feedparser.parse(text)
    items = []
    for e in parsed.entries:
        link = next(
            (ln.get("href") for ln in e.get("links", []) if ln.get("rel") == "alternate"),
            e.get("link"),
        )
        items.append(
            {
                "id": canonical_arxiv_id(e.get("id", "")),
                "title": _norm_ws(e.get("title", "")),
                "summary": _norm_ws(e.get("summary", ""))[:2000],
                "url": link or e.get("id", ""),
                "published": e.get("published", ""),
                "source": "arxiv",
                "categories": [t.get("term") for t in e.get("tags", []) if t.get("term")],
            }
        )
    return [i for i in items if i["title"]]


class ArxivClient:
    def __init__(
        self,
        cache_dir: Path | str,
        categories: tuple[str, ...],
        *,
        min_interval: float = 3.0,
        timeout: float = 30.0,
        cache_ttl: float = 6 * 3600,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.categories = categories
        self.min_interval = min_interval
        self.cache_ttl = cache_ttl
        self._clock = clock
        self._sleeper = sleeper
        self._now = now
        self._last_request_at: float | None = None
        self._client = httpx.Client(
            timeout=timeout, transport=transport, headers={"User-Agent": USER_AGENT}
        )

    def close(self) -> None:
        self._client.close()

    def fetch(self, max_results: int = 100) -> list[dict]:
        params = {
            "search_query": " OR ".join(f"cat:{c}" for c in self.categories),
            "start": "0",
            "max_results": str(max_results),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        key = hashlib.sha256(repr(sorted(params.items())).encode()).hexdigest()[:24]
        cache_path = self.cache_dir / f"arxiv-{key}.atom.xml"
        text = self._read_cache(cache_path)
        if text is None:
            text = self._get(params)
            cache_path.write_text(text, encoding="utf-8")
        return parse_arxiv_atom(text)

    def _read_cache(self, path: Path) -> str | None:
        if path.exists() and (self._now() - path.stat().st_mtime) < self.cache_ttl:
            return path.read_text(encoding="utf-8")
        return None

    def _wait_interval(self) -> None:
        if self._last_request_at is not None:
            elapsed = self._clock() - self._last_request_at
            if elapsed < self.min_interval:
                self._sleeper(self.min_interval - elapsed)

    def _get(self, params: dict[str, str]) -> str:
        last_error: Exception | None = None
        for _attempt in range(2):  # リトライは全体で1回(AGENTS.md)
            self._wait_interval()
            self._last_request_at = self._clock()
            try:
                r = self._client.get(ARXIV_API_URL, params=params)
                r.raise_for_status()
                return r.text
            except httpx.HTTPError as e:
                last_error = e
        raise CollectError(f"arXiv取得失敗(リトライ1回込み): {last_error}")
