from astrolabe.collect.dedupe import (
    canonical_url,
    dedupe_items,
    filter_seen,
    item_keys,
    normalize_title,
    seen_keys_from_ledger,
)
from astrolabe.ledger import events as events_mod


def test_normalize_title():
    assert normalize_title("Self-Correcting RAG!") == normalize_title("self correcting rag")
    assert normalize_title("Ａ　Ｂ") == "a b"  # NFKC
    assert normalize_title("  spaced\n\ttitle  ") == "spaced title"


def test_canonical_url():
    assert canonical_url("https://Example.com/x/") == "example.com/x"
    assert canonical_url("http://example.com/x") == "example.com/x"
    assert canonical_url("http://arxiv.org/abs/2507.11001v2") == "arxiv.org/abs/2507.11001"


def test_dedupe_items_first_wins():
    a = {"id": "2507.1", "title": "Same Title", "url": "http://arxiv.org/abs/2507.1v1"}
    b = {"id": "https://blog/x", "title": "same   title!", "url": "https://blog/x"}
    c = {"id": "https://blog/y", "title": "Other", "url": "https://blog/y"}
    assert dedupe_items([a, b, c]) == [a, c]


def test_filter_seen_matches_any_key():
    item = {"id": "2507.1", "title": "T1", "url": "http://arxiv.org/abs/2507.1v1"}
    # 別バージョンURLでも既出として弾ける
    seen = {"u:arxiv.org/abs/2507.1"}
    assert filter_seen([item], seen) == []
    assert filter_seen([item], {"u:unrelated"}) == [item]


def test_seen_keys_from_ledger(ledger):
    events_mod.append_event(
        ledger, "proposed", "rag", {"dedupe_keys": ["t:x", "u:y"]}
    )
    ledger.commit()
    assert seen_keys_from_ledger(ledger) == {"t:x", "u:y"}


def test_item_keys_contains_title_and_urls():
    keys = item_keys({"id": "2507.1", "title": "A B", "url": "https://arxiv.org/abs/2507.1v1"})
    assert "t:a b" in keys
    assert "u:2507.1" in keys
    assert "u:arxiv.org/abs/2507.1" in keys
