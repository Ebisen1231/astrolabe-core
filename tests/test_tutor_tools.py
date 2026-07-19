from datetime import UTC, datetime

import pytest

from astrolabe.ledger import events, store
from astrolabe.tutor.schemas import TOOL_DEFINITIONS
from astrolabe.tutor.tools import TutorToolError, TutorTools

NOW = datetime(2026, 7, 19, 3, 0, tzinfo=UTC)


def _tools(ledger):
    return TutorTools(ledger, now=lambda: NOW)


def _assert_strict_objects(schema):
    if not isinstance(schema, dict):
        return
    if schema.get("type") == "object":
        assert schema.get("additionalProperties") is False
        assert set(schema.get("required", [])) == set(schema.get("properties", {}))
    for value in schema.values():
        if isinstance(value, dict):
            _assert_strict_objects(value)
        elif isinstance(value, list):
            for item in value:
                _assert_strict_objects(item)


def test_all_function_schemas_are_strict_and_closed():
    assert {tool["name"] for tool in TOOL_DEFINITIONS} == {
        "search_ledger",
        "record_feedback",
        "create_task",
        "complete_task",
        "quiz",
        "update_profile",
    }
    for tool in TOOL_DEFINITIONS:
        assert tool["strict"] is True
        _assert_strict_objects(tool["parameters"])


def test_search_ledger_returns_concept_edges_today_and_tasks(ledger):
    task = _tools(ledger).create_task(
        "position-encoding",
        "位置エンコーディング",
        "10分で読む",
        "read",
        10,
        [
            {
                "src": "rope",
                "src_name": "RoPE",
                "dst": "position-encoding",
                "dst_name": "位置エンコーディング",
                "type": "prerequisite",
                "weight": 1.0,
            }
        ],
    )
    store.save_daily_report(ledger, "2026-07-19", {"topics": []}, "delta")
    result = _tools(ledger).search_ledger("RoPE", True, "open")
    assert result["concepts"][0]["name"] == "RoPE"
    assert result["edges"][0]["type"] == "prerequisite"
    assert result["today_report"]["date"] == "2026-07-19"
    assert result["tasks"][0]["id"] == task["task"]["id"]


def test_quiz_ask_does_not_write_but_grade_updates_confidence(ledger):
    tools = _tools(ledger)
    asked = tools.quiz(
        "ask", "rope", "RoPE", "何を回転する?", ["座標", "語彙"], "", 0, ""
    )
    assert asked["type"] == "quiz"
    assert events.load_events(ledger) == []

    graded = tools.quiz(
        "grade",
        "rope",
        "RoPE",
        "何を回転する?",
        ["座標", "語彙"],
        "座標",
        0.9,
        "正解",
    )
    assert graded["type"] == "quiz_result"
    concept = store.list_concepts(ledger)[0]
    assert (concept["status"], concept["confidence"]) == ("learned", 0.9)


def test_update_profile_is_atomic_with_interview_event(ledger):
    result = _tools(ledger).update_profile(
        [{"tag": "agents", "weight": 0.9}],
        "エージェントを作る",
        "Python経験あり",
        "30分",
        ["Python"],
    )
    assert result["type"] == "profile_updated"
    assert store.get_profile(ledger)["interests"] == {"agents": 0.9}
    assert [row["type"] for row in events.load_events(ledger)] == [
        "interview",
        "marked_known",
    ]


def test_conversation_is_not_implicitly_recorded(ledger):
    _tools(ledger).search_ledger("私信を含む会話", False, "all")
    assert events.load_events(ledger) == []


def test_complete_task_requires_evidence(ledger):
    with pytest.raises(TutorToolError, match="evidence"):
        _tools(ledger).complete_task(1, "", 0.2)
