"""Responses APIへ渡すstrict function tool schema。"""

from __future__ import annotations

from typing import Any


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


EDGE_SCHEMA = _object(
    {
        "src": {"type": "string"},
        "src_name": {"type": "string"},
        "dst": {"type": "string"},
        "dst_name": {"type": "string"},
        "type": {
            "type": "string",
            "enum": ["prerequisite", "related", "derived_from", "appeared_in"],
        },
        "weight": {"type": "number", "minimum": 0},
    },
    ["src", "src_name", "dst", "dst_name", "type", "weight"],
)


def _tool(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": parameters,
        "strict": True,
    }


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    _tool(
        "search_ledger",
        "概念、関連エッジ、今日の報告、タスク、プロファイルを確認する。未知語では最初に使う。",
        _object(
            {
                "query": {"type": "string"},
                "include_today": {"type": "boolean"},
                "task_status": {"type": "string", "enum": ["open", "done", "all"]},
            },
            ["query", "include_today", "task_status"],
        ),
    ),
    _tool(
        "record_feedback",
        "利用者が明示した選択・既知・却下・短い学習メモだけをイベントへ記録する。",
        _object(
            {
                "event_type": {
                    "type": "string",
                    "enum": ["selected", "marked_known", "dismissed", "chat_note"],
                },
                "concept_id": {"type": ["string", "null"]},
                "concept_name": {"type": ["string", "null"]},
                "note": {"type": ["string", "null"]},
                "later": {"type": "boolean"},
                "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            },
            ["event_type", "concept_id", "concept_name", "note", "later", "confidence"],
        ),
    ),
    _tool(
        "create_task",
        "未知概念を前提へ橋渡しする学習タスクを作り、必要な概念エッジもイベントへ載せる。",
        _object(
            {
                "concept_id": {"type": "string"},
                "concept_name": {"type": "string"},
                "title": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["read", "implement", "quiz", "build_app_feature"],
                },
                "est_minutes": {"type": "integer", "minimum": 1, "maximum": 480},
                "edges": {"type": "array", "items": EDGE_SCHEMA, "maxItems": 12},
            },
            ["concept_id", "concept_name", "title", "kind", "est_minutes", "edges"],
        ),
    ),
    _tool(
        "complete_task",
        "evidenceを必須としてタスクを完了し、理解度差分を記録する。",
        _object(
            {
                "task_id": {"type": "integer", "minimum": 1},
                "evidence": {"type": "string"},
                "confidence_delta": {"type": "number", "minimum": 0, "maximum": 1},
            },
            ["task_id", "evidence", "confidence_delta"],
        ),
    ),
    _tool(
        "quiz",
        "選択肢クイズを構造化して出題するか、利用者の回答を採点してquiz_resultを記録する。",
        _object(
            {
                "action": {"type": "string", "enum": ["ask", "grade"]},
                "concept_id": {"type": "string"},
                "concept_name": {"type": "string"},
                "question": {"type": "string"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 4,
                },
                "user_answer": {"type": "string"},
                "score": {"type": "number", "minimum": 0, "maximum": 1},
                "feedback": {"type": "string"},
            },
            [
                "action",
                "concept_id",
                "concept_name",
                "question",
                "options",
                "user_answer",
                "score",
                "feedback",
            ],
        ),
    ),
    _tool(
        "update_profile",
        "面談で合意した学習プロファイルを更新し、interviewイベントと同時に記録する。",
        _object(
            {
                "interests": {
                    "type": "array",
                    "items": _object(
                        {
                            "tag": {"type": "string"},
                            "weight": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        ["tag", "weight"],
                    ),
                },
                "goals": {"type": "string"},
                "background": {"type": "string"},
                "time_budget": {"type": "string"},
                "known_concepts": {"type": "array", "items": {"type": "string"}},
            },
            ["interests", "goals", "background", "time_budget", "known_concepts"],
        ),
    ),
]
