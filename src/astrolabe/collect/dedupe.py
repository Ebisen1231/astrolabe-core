"""重複排除: タイトル正規化 + 既出判定(設計書§6.1 手順2の初期実装)。

既出判定は proposed イベントの payload["dedupe_keys"](過去の報告で引用された
アイテムのキー)に対して行う。embeddings は使わない(M0)。
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata


def normalize_title(title: str) -> str:
    s = unicodedata.normalize("NFKC", title or "").casefold()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def canonical_url(url: str) -> str:
    s = (url or "").strip().lower().rstrip("/")
    s = re.sub(r"^https?://", "", s)
    if "arxiv.org" in s:
        s = re.sub(r"v\d+$", "", s)  # abs/2507.11001v2 と v1 を同一視
    return s


def item_keys(item: dict) -> set[str]:
    """1アイテムを識別するキー集合(タイトル正規化キー + URL/IDキー)。"""
    keys: set[str] = set()
    if item.get("title"):
        keys.add("t:" + normalize_title(item["title"]))
    for field in ("id", "url"):
        value = item.get(field)
        if value:
            keys.add("u:" + canonical_url(value))
    return keys


def dedupe_items(items: list[dict]) -> list[dict]:
    """バッチ内重複を除去する。先勝ち(収集順を保持)。"""
    seen: set[str] = set()
    out: list[dict] = []
    for item in items:
        keys = item_keys(item)
        if keys & seen:
            continue
        seen |= keys
        out.append(item)
    return out


def filter_seen(items: list[dict], seen_keys: set[str]) -> list[dict]:
    """過去に報告済みのアイテムを除く。"""
    return [i for i in items if not (item_keys(i) & seen_keys)]


def seen_keys_from_ledger(conn: sqlite3.Connection) -> set[str]:
    import json

    keys: set[str] = set()
    for row in conn.execute("SELECT payload FROM events WHERE type = 'proposed'"):
        payload = json.loads(row["payload"] or "{}")
        keys.update(payload.get("dedupe_keys", []))
    return keys
