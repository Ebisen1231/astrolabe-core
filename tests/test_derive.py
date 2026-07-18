"""導出関数 events → concepts/edges の仕様テスト(このファイルが遷移規則の仕様書を兼ねる)。"""

import random

from astrolabe.ledger import events as events_mod
from astrolabe.ledger.derive import concept_id_from_name, derive, rebuild


def ts(n: int) -> str:
    return f"2026-07-01T06:00:{n:02d}+00:00"


def ev(id_: int, n: int, type_: str, concept_id: str | None = None, **payload) -> dict:
    return {"id": id_, "ts": ts(n), "type": type_, "concept_id": concept_id, "payload": payload}


def by_id(concepts: list[dict]) -> dict[str, dict]:
    return {c["id"]: c for c in concepts}


# --- concept_id_from_name -----------------------------------------------


def test_concept_id_is_deterministic_and_normalized():
    assert concept_id_from_name("RAG") == "rag"
    assert concept_id_from_name("Self-Correcting RAG!") == "self-correcting-rag"
    assert concept_id_from_name("ＲＡＧ") == "rag"  # NFKC全角→半角
    assert concept_id_from_name("リランキング") == "リランキング"  # 日本語は保持
    assert concept_id_from_name("  ") == "unnamed"


# --- 基本遷移 -------------------------------------------------------------


def test_empty_events():
    assert derive([]) == ([], [])


def test_proposed_creates_unknown_concept():
    concepts, edges = derive(
        [ev(1, 0, "proposed", "rag", name="RAG", kind="concept", summary="s", source_urls=["u1"])]
    )
    assert edges == []
    c = by_id(concepts)["rag"]
    assert c["name"] == "RAG"
    assert c["status"] == "unknown"
    assert c["confidence"] == 0.0
    assert c["summary"] == "s"
    assert c["source_urls"] == ["u1"]
    assert c["first_seen"] == ts(0)
    assert c["last_touched"] == ts(0)


def test_proposed_merges_urls_and_keeps_first_seen():
    concepts, _ = derive(
        [
            ev(1, 0, "proposed", "rag", name="RAG", source_urls=["u1"]),
            ev(2, 5, "proposed", "rag", name="RAG", summary="s2", source_urls=["u1", "u2"]),
        ]
    )
    c = by_id(concepts)["rag"]
    assert c["source_urls"] == ["u1", "u2"]
    assert c["first_seen"] == ts(0)
    assert c["last_touched"] == ts(5)
    assert c["summary"] == "s2"


def test_selected_transitions():
    # unknown → queued
    concepts, _ = derive(
        [ev(1, 0, "proposed", "rag", name="RAG"), ev(2, 1, "selected", "rag")]
    )
    assert by_id(concepts)["rag"]["status"] == "queued"
    # learned → review(既習の再選択は復習)
    concepts, _ = derive(
        [ev(1, 0, "marked_known", "rag", name="RAG"), ev(2, 1, "selected", "rag")]
    )
    assert by_id(concepts)["rag"]["status"] == "review"


def test_selected_later_keeps_knowledge_state():
    concepts, _ = derive(
        [
            ev(1, 0, "proposed", "rag", name="RAG"),
            ev(2, 1, "selected", "rag", later=True),
        ]
    )
    assert by_id(concepts)["rag"]["status"] == "unknown"


def test_dismissed_keeps_knowledge_state():
    concepts, _ = derive(
        [ev(1, 0, "proposed", "rag", name="RAG"), ev(2, 1, "dismissed", "rag")]
    )
    c = by_id(concepts)["rag"]
    assert c["status"] == "unknown"  # 興味シグナルのみ。知識軸は動かない(§5.1)
    assert c["last_touched"] == ts(1)


def test_marked_known_sets_learned_and_confidence():
    concepts, _ = derive([ev(1, 0, "marked_known", "rag", name="RAG")])
    c = by_id(concepts)["rag"]
    assert (c["status"], c["confidence"]) == ("learned", 0.8)

    concepts, _ = derive(
        [
            ev(1, 0, "marked_known", "rag", name="RAG", confidence=0.9),
            ev(2, 1, "marked_known", "rag", confidence=0.5),
        ]
    )
    assert by_id(concepts)["rag"]["confidence"] == 0.9  # 下げない


def test_proposed_does_not_downgrade_learned():
    concepts, _ = derive(
        [
            ev(1, 0, "marked_known", "rag", name="RAG"),
            ev(2, 1, "proposed", "rag", summary="再登場"),
        ]
    )
    c = by_id(concepts)["rag"]
    assert c["status"] == "learned"
    assert c["summary"] == "再登場"


def test_task_events():
    concepts, _ = derive(
        [ev(1, 0, "proposed", "rag", name="RAG"), ev(2, 1, "task_created", "rag")]
    )
    assert by_id(concepts)["rag"]["status"] == "learning"

    concepts, _ = derive(
        [
            ev(1, 0, "proposed", "rag", name="RAG"),
            ev(2, 1, "task_created", "rag"),
            ev(3, 2, "task_done", "rag"),
            ev(4, 3, "task_done", "rag", confidence_delta=0.9),
        ]
    )
    assert by_id(concepts)["rag"]["confidence"] == 1.0  # 0.2 + 0.9 は 1.0 で頭打ち


def test_quiz_result():
    concepts, _ = derive(
        [ev(1, 0, "proposed", "rag", name="RAG"), ev(2, 1, "quiz_result", "rag", score=0.9)]
    )
    c = by_id(concepts)["rag"]
    assert (c["status"], c["confidence"]) == ("learned", 0.9)

    concepts, _ = derive(
        [ev(1, 0, "proposed", "rag", name="RAG"), ev(2, 1, "quiz_result", "rag", score=0.4)]
    )
    c = by_id(concepts)["rag"]
    assert (c["status"], c["confidence"]) == ("learning", 0.4)


def test_interview_and_chat_note_do_not_touch_concepts():
    concepts, edges = derive([ev(1, 0, "interview", None, profile={"goals": "g"})])
    assert (concepts, edges) == ([], [])


# --- エッジ ---------------------------------------------------------------


def _proposed_with_edges(weight=1.0):
    return ev(
        1,
        0,
        "proposed",
        "reranking",
        name="リランキング",
        edges=[
            {"dst": "rag", "dst_name": "RAG", "type": "prerequisite", "weight": weight},
            {"dst": "rag", "dst_name": "RAG", "type": "bogus-type"},  # 無効typeは捨てる
            {"dst": "reranking", "type": "related"},  # 自己エッジは捨てる
        ],
    )


def test_edges_create_stub_concepts():
    concepts, edges = derive([_proposed_with_edges()])
    assert [e for e in edges] == [
        {
            "src": "reranking",
            "dst": "rag",
            "type": "prerequisite",
            "weight": 1.0,
            "created_by": "event:1",
            "created_at": ts(0),
        }
    ]
    stub = by_id(concepts)["rag"]
    assert stub["name"] == "RAG"  # dst_name から命名
    assert stub["status"] == "unknown"


def test_duplicate_edges_keep_max_weight_and_first_created():
    events = [
        _proposed_with_edges(weight=0.5),
        ev(2, 1, "proposed", "reranking",
           edges=[{"dst": "rag", "type": "prerequisite", "weight": 2.0}]),
    ]
    _, edges = derive(events)
    assert len(edges) == 1
    assert edges[0]["weight"] == 2.0
    assert edges[0]["created_by"] == "event:1"
    assert edges[0]["created_at"] == ts(0)


# --- 決定性・再導出 ---------------------------------------------------------


def _sample_events() -> list[dict]:
    return [
        ev(1, 0, "proposed", "rag", name="RAG", source_urls=["u1"]),
        ev(2, 1, "marked_known", "transformer", name="Transformer", confidence=0.9),
        ev(3, 2, "selected", "rag"),
        ev(4, 3, "proposed", "reranking", name="リランキング",
           edges=[{"dst": "rag", "type": "prerequisite"}]),
        ev(5, 4, "quiz_result", "rag", score=0.85),
    ]


def test_derive_is_order_independent():
    events = _sample_events()
    expected = derive(events)
    shuffled = events[:]
    random.Random(42).shuffle(shuffled)
    assert derive(shuffled) == expected


def test_same_ts_resolved_by_event_id():
    events = [
        {"id": 2, "ts": ts(0), "type": "selected", "concept_id": "rag", "payload": {}},
        {"id": 1, "ts": ts(0), "type": "proposed", "concept_id": "rag",
         "payload": {"name": "RAG"}},
    ]
    concepts, _ = derive(events)
    assert by_id(concepts)["rag"]["status"] == "queued"  # proposed(id=1) が先に適用される


def test_rebuild_is_idempotent(ledger):
    for e in _sample_events():
        events_mod.append_event(ledger, e["type"], e["concept_id"], e["payload"], ts=e["ts"])
    n1 = rebuild(ledger)
    rows1 = ledger.execute("SELECT * FROM concepts ORDER BY id").fetchall()
    edges1 = ledger.execute("SELECT * FROM edges ORDER BY src, dst, type").fetchall()
    n2 = rebuild(ledger)
    rows2 = ledger.execute("SELECT * FROM concepts ORDER BY id").fetchall()
    edges2 = ledger.execute("SELECT * FROM edges ORDER BY src, dst, type").fetchall()
    assert n1 == n2 == (3, 1)
    assert [tuple(r) for r in rows1] == [tuple(r) for r in rows2]
    assert [tuple(r) for r in edges1] == [tuple(r) for r in edges2]
