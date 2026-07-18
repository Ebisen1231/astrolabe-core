"""学習履歴が翌朝の一次選別プロンプトへ決定的に入ることを固定する。"""

import json

from astrolabe.ledger import derive, events, store
from astrolabe.llm.triage import build_user_prompt


def test_learning_context_is_stable_and_recency_ordered(ledger):
    with ledger:
        events.append_event(
            ledger,
            "marked_known",
            "transformer",
            {"name": "Transformer"},
            ts="2026-07-18T00:00:00+00:00",
        )
        events.append_event(
            ledger,
            "marked_known",
            "attention",
            {"name": "Attention"},
            ts="2026-07-18T00:00:01+00:00",
        )
        events.append_event(
            ledger,
            "selected",
            "rag",
            {"name": "RAG"},
            ts="2026-07-18T00:00:02+00:00",
        )
        events.append_event(
            ledger,
            "selected",
            "agents",
            {"name": "LLMエージェント"},
            ts="2026-07-18T00:00:03+00:00",
        )
        events.append_event(
            ledger,
            "selected",
            "rag",
            {"name": "RAG"},
            ts="2026-07-18T00:00:04+00:00",
        )
        events.append_event(
            ledger,
            "dismissed",
            "prompt-hack",
            {"name": "プロンプト小技"},
            ts="2026-07-18T00:00:05+00:00",
        )
    derive.rebuild(ledger)

    assert store.get_learning_context(ledger) == {
        "learned_concepts": ["Attention", "Transformer"],
        "recent_selected": ["RAG", "LLMエージェント"],
        "recent_dismissed": ["プロンプト小技"],
    }


def test_triage_prompt_contains_learning_context_deterministically():
    context = {
        "learned_concepts": ["Attention", "Transformer"],
        "recent_selected": ["RAG"],
        "recent_dismissed": ["プロンプト小技"],
    }
    item = {
        "id": "paper-1",
        "title": "A Paper",
        "summary": "summary",
        "source": "arxiv",
    }
    prompt1 = build_user_prompt([item], {"goals": "RAG"}, context)
    prompt2 = build_user_prompt([item], {"goals": "RAG"}, context)
    assert prompt1 == prompt2
    payload = json.loads(prompt1)
    assert payload["learning_context"] == context
    assert payload["profile"]["goals"] == "RAG"
