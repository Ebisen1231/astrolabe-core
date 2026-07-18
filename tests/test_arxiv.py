import httpx
import pytest

from astrolabe.collect import CollectError
from astrolabe.collect.arxiv import ArxivClient, canonical_arxiv_id, parse_arxiv_atom

CATS = ("cs.CL", "cs.AI", "cs.LG")


def test_canonical_arxiv_id():
    assert canonical_arxiv_id("http://arxiv.org/abs/2507.11001v2") == "2507.11001"
    assert canonical_arxiv_id("http://arxiv.org/abs/2507.11001") == "2507.11001"


def test_parse_arxiv_atom(fixtures_dir):
    items = parse_arxiv_atom((fixtures_dir / "arxiv_sample.xml").read_text())
    assert len(items) == 6
    first = items[0]
    assert first["id"] == "2507.11001"
    # 改行を含むタイトルが1行に正規化される
    assert first["title"] == (
        "Self-Correcting Retrieval-Augmented Generation with Verifier Feedback"
    )
    assert first["url"] == "http://arxiv.org/abs/2507.11001v1"
    assert first["source"] == "arxiv"
    assert "cs.CL" in first["categories"]
    assert "verifier" in first["summary"]


class FakeClock:
    """クロックとスリープを兼ねるテスト用時計。sleep で時間が進む。"""

    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def _make_client(tmp_path, handler, *, cache_ttl=3600.0):
    clock = FakeClock()
    calls = {"n": 0}

    def counting_handler(request):
        calls["n"] += 1
        return handler(request)

    client = ArxivClient(
        tmp_path / "cache",
        CATS,
        cache_ttl=cache_ttl,
        transport=httpx.MockTransport(counting_handler),
        clock=clock.clock,
        sleeper=clock.sleep,
    )
    return client, clock, calls


def test_cache_avoids_second_request(tmp_path, fixtures_dir):
    text = (fixtures_dir / "arxiv_sample.xml").read_text()
    client, _, calls = _make_client(tmp_path, lambda req: httpx.Response(200, text=text))
    assert len(client.fetch()) == 6
    assert len(client.fetch()) == 6
    assert calls["n"] == 1  # 2回目はキャッシュ
    client.close()


def test_rate_limit_enforces_3s_between_requests(tmp_path, fixtures_dir):
    text = (fixtures_dir / "arxiv_sample.xml").read_text()
    # cache_ttl=0 でキャッシュ読み出しを無効化し、連続リクエストさせる
    client, clock, calls = _make_client(
        tmp_path, lambda req: httpx.Response(200, text=text), cache_ttl=0.0
    )
    client.fetch()
    client.fetch()
    assert calls["n"] == 2
    assert clock.sleeps == [3.0]  # 1回目は待たず、2回目の前に3秒空ける
    client.close()


def test_retry_once_then_succeed(tmp_path, fixtures_dir):
    text = (fixtures_dir / "arxiv_sample.xml").read_text()
    state = {"n": 0}

    def flaky(request):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, text=text)

    client, _, calls = _make_client(tmp_path, flaky)
    assert len(client.fetch()) == 6
    assert calls["n"] == 2
    client.close()


def test_retry_is_exactly_one(tmp_path):
    client, _, calls = _make_client(tmp_path, lambda req: httpx.Response(500, text="err"))
    with pytest.raises(CollectError):
        client.fetch()
    assert calls["n"] == 2  # 初回 + リトライ1回のみ
    client.close()
