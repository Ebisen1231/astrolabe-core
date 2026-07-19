import json

from astrolabe.ledger import store
from astrolabe.tutor.budget import TutorBudgetGuard
from astrolabe.tutor.engine import TutorEngine
from astrolabe.tutor.tools import TutorTools


class StubTutorLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def tool_turn(self, input_items, tools, *, max_output_tokens=2_000):
        self.calls.append({"input": input_items, "tools": tools})
        return self.responses.pop(0)


def _tool_call(call_id, name, arguments):
    return {
        "text": "",
        "tool_calls": [
            {"call_id": call_id, "name": name, "arguments": json.dumps(arguments)}
        ],
        "output_items": [
            {
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(arguments),
            }
        ],
    }


def test_unknown_word_creates_bridge_task_and_prerequisite_edge(ledger):
    edge = {
        "src": "rope",
        "src_name": "RoPE",
        "dst": "position-encoding",
        "dst_name": "位置エンコーディング",
        "type": "prerequisite",
        "weight": 1.0,
    }
    llm = StubTutorLLM(
        [
            _tool_call(
                "c1",
                "search_ledger",
                {"query": "RoPE", "include_today": True, "task_status": "open"},
            ),
            _tool_call(
                "c2",
                "create_task",
                {
                    "concept_id": "position-encoding",
                    "concept_name": "位置エンコーディング",
                    "title": "位置エンコーディングを10分で読む",
                    "kind": "read",
                    "est_minutes": 10,
                    "edges": [edge],
                },
            ),
            {
                "text": "RoPEは位置情報を回転として埋め込む方法です。"
                "橋渡しタスクを作りました。",
                "tool_calls": [],
                "output_items": [],
            },
        ]
    )
    engine = TutorEngine(llm, TutorTools(ledger))
    result = engine.run_turn(
        [{"role": "user", "content": "RoPEって何?"}], "tutor-test-session"
    )
    assert "橋渡しタスク" in result["message"]
    assert [card["type"] for card in result["cards"]] == [
        "ledger_search",
        "task_created",
    ]
    assert store.list_tasks(ledger)[0]["title"] == "位置エンコーディングを10分で読む"
    assert store.list_edges(ledger)[0]["src"] == "rope"


def test_morning_usage_does_not_block_available_tutor_budget(ledger):
    store.save_llm_usage(ledger, "2026-07-19", "morning-1", {"flagship": {"used": 35_000}})
    guard = TutorBudgetGuard(
        ledger,
        "2026-07-19",
        "tutor-test",
        tutor_cap=30_000,
        total_cap=70_000,
    )
    llm = StubTutorLLM(
        [{"text": "応答できます。", "tool_calls": [], "output_items": []}]
    )
    result = TutorEngine(llm, TutorTools(ledger), budget_guard=guard).run_turn(
        [{"role": "user", "content": "質問"}], "tutor-test"
    )
    assert result["message"] == "応答できます。"
    assert len(llm.calls) == 1


def test_tutor_cap_blocks_without_calling_llm(ledger):
    store.save_llm_usage(
        ledger, "2026-07-19", "tutor-old", {"flagship": {"used": 30_000}}
    )
    guard = TutorBudgetGuard(
        ledger,
        "2026-07-19",
        "tutor-test",
        tutor_cap=30_000,
        total_cap=70_000,
    )
    llm = StubTutorLLM([])
    result = TutorEngine(llm, TutorTools(ledger), budget_guard=guard).run_turn(
        [{"role": "user", "content": "質問"}], "tutor-test"
    )
    assert result["budget_exhausted"] is True
    assert "チューター予算切れ" in result["message"]
    assert llm.calls == []


def test_total_cap_blocks_without_calling_llm(ledger):
    store.save_llm_usage(ledger, "2026-07-19", "morning-1", {"flagship": {"used": 70_000}})
    guard = TutorBudgetGuard(
        ledger,
        "2026-07-19",
        "tutor-test",
        tutor_cap=30_000,
        total_cap=70_000,
    )
    llm = StubTutorLLM([])
    result = TutorEngine(llm, TutorTools(ledger), budget_guard=guard).run_turn(
        [{"role": "user", "content": "質問"}], "tutor-test"
    )
    assert result["budget_exhausted"] is True
    assert "全体のflagship予算切れ" in result["message"]
    assert llm.calls == []


def test_usage_is_accumulated_for_only_the_current_tutor_session(ledger):
    store.save_llm_usage(ledger, "2026-07-19", "morning-1", {"flagship": {"used": 35_000}})
    guard = TutorBudgetGuard(
        ledger,
        "2026-07-19",
        "tutor-session-a",
        tutor_cap=30_000,
        total_cap=70_000,
    )
    guard.record_usage("flagship", 120)
    guard.record_usage("flagship", 30)
    assert (
        store.get_llm_usage_for_run(
            ledger, "2026-07-19", "tutor-session-a", "flagship"
        )
        == 150
    )
    assert store.get_llm_usage_total(ledger, "2026-07-19", "flagship") == 35_150
