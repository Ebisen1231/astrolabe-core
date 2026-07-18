"""events から concepts / edges を再導出する。

原則(設計書§4): events が一次データであり、本モジュールの derive() は純関数。
同じイベント集合からは常に同じ concepts/edges が得られる(投入順にも依存しない。
適用順は (ts, id) で全順序化する)。遷移規則の仕様は tests/test_derive.py が兼ねる。

イベント型ごとの規則(§5.1 の二軸分離に従う):
- proposed:      概念を登録する(status には触れない=既習を格下げしない)。summary /
                 source_urls / kind を最新情報で補強する。
- selected:      unknown → queued。learned だった概念の再選択は review。
                 payload.later=true は興味シグナルだけなのでstatusを変えない。
- dismissed:     興味シグナルのみ。知識状態は変えない。
- marked_known:  status=learned。confidence は max(現在値, payload.confidence, 既定0.8)。
- task_created:  unknown/queued → learning。
- task_done:     confidence += payload.confidence_delta(既定0.2、上限1.0)。
- quiz_result:   confidence = score(0..1にクランプ)。score>=0.8 で learned、未満は learning。
- interview / chat_note: concepts/edges には影響しない。

エッジは payload["edges"] から作る。要素は {src?, dst, dst_name?, type, weight?} で、
src 省略時はイベントの concept_id。type "prerequisite" は「dst は src の前提」と読む。
未知の端点は stub 概念(status=unknown)として自動生成する。同一 (src,dst,type) は
weight の最大値を採り、created_at/created_by は初出イベントのものを保持する。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

from astrolabe.ledger.backend import LedgerBackend, as_backend

VALID_EDGE_TYPES = {"prerequisite", "related", "derived_from", "appeared_in"}


def concept_id_from_name(name: str) -> str:
    """表示名から決定的な概念IDを作る(NFKC → casefold → 非単語文字を'-'に)。"""
    s = unicodedata.normalize("NFKC", name or "").casefold().strip()
    s = re.sub(r"[^\w]+", "-", s).strip("-")
    return s or "unnamed"


@dataclass
class _Concept:
    id: str
    name: str
    kind: str = "concept"
    status: str = "unknown"
    confidence: float = 0.0
    summary: str = ""
    source_urls: list[str] = field(default_factory=list)
    first_seen: str = ""
    last_touched: str = ""


def _clamp01(x: float) -> float:
    return min(1.0, max(0.0, x))


def derive(events: list[dict]) -> tuple[list[dict], list[dict]]:
    """イベント列から (concepts, edges) を導出する純関数。"""
    concepts: dict[str, _Concept] = {}
    edges: dict[tuple[str, str, str], dict] = {}

    def ensure(cid: str, ts: str, name: str | None = None, kind: str | None = None) -> _Concept:
        c = concepts.get(cid)
        if c is None:
            c = _Concept(id=cid, name=name or cid, first_seen=ts, last_touched=ts)
            concepts[cid] = c
        if name and c.name == c.id:
            c.name = name  # stub の仮名(=id)より表示名を優先
        if kind:
            c.kind = kind
        c.last_touched = ts
        return c

    for ev in sorted(events, key=lambda e: (e["ts"], e["id"])):
        etype = ev["type"]
        ts = ev["ts"]
        payload = ev.get("payload") or {}
        if isinstance(payload, str):
            payload = json.loads(payload or "{}")
        cid = ev.get("concept_id")

        c = ensure(cid, ts, payload.get("name"), payload.get("kind")) if cid else None

        if c is not None:
            if etype == "proposed":
                if payload.get("summary"):
                    c.summary = payload["summary"]
                for url in payload.get("source_urls", []):
                    if url not in c.source_urls:
                        c.source_urls.append(url)
            elif etype == "selected":
                if payload.get("later"):
                    pass
                elif c.status == "learned":
                    c.status = "review"
                elif c.status == "unknown":
                    c.status = "queued"
            elif etype == "dismissed":
                pass
            elif etype == "marked_known":
                c.status = "learned"
                c.confidence = max(c.confidence, _clamp01(float(payload.get("confidence", 0.8))))
            elif etype == "task_created":
                if c.status in ("unknown", "queued"):
                    c.status = "learning"
            elif etype == "task_done":
                c.confidence = _clamp01(
                    c.confidence + float(payload.get("confidence_delta", 0.2))
                )
            elif etype == "quiz_result":
                score = _clamp01(float(payload.get("score", 0.0)))
                c.confidence = score
                c.status = "learned" if score >= 0.8 else "learning"

        for edge in payload.get("edges", []):
            src = edge.get("src") or cid
            dst = edge.get("dst")
            etype_edge = edge.get("type")
            if not src or not dst or etype_edge not in VALID_EDGE_TYPES or src == dst:
                continue
            ensure(src, ts)
            ensure(dst, ts, edge.get("dst_name"))
            key = (src, dst, etype_edge)
            weight = float(edge.get("weight", 1.0))
            if key in edges:
                edges[key]["weight"] = max(edges[key]["weight"], weight)
            else:
                edges[key] = {
                    "src": src,
                    "dst": dst,
                    "type": etype_edge,
                    "weight": weight,
                    "created_by": f"event:{ev['id']}",
                    "created_at": ts,
                }

    concept_rows = [
        {
            "id": c.id,
            "name": c.name,
            "kind": c.kind,
            "status": c.status,
            "confidence": round(c.confidence, 4),
            "summary": c.summary,
            "source_urls": c.source_urls,
            "first_seen": c.first_seen,
            "last_touched": c.last_touched,
        }
        for c in sorted(concepts.values(), key=lambda c: c.id)
    ]
    edge_rows = [edges[k] for k in sorted(edges)]
    return concept_rows, edge_rows


def rebuild(conn: LedgerBackend) -> tuple[int, int]:
    """全イベントから concepts / edges を作り直してテーブルを置き換える。"""
    backend = as_backend(conn)
    concept_rows, edge_rows = derive(backend.load_events())
    return backend.replace_derived(concept_rows, edge_rows)
