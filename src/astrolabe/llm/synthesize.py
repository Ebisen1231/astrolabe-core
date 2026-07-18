"""統合報告: 選別上位候補を flagship モデルでトピック3〜5件に統合する。"""

from __future__ import annotations

import json

SYNTHESIS_SCHEMA_NAME = "daily_synthesis"
SYNTHESIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": ["concept", "paper", "tool", "event"]},
                    "summary": {"type": "string"},
                    "why_now": {"type": "string"},
                    "learn_content": {"type": "string"},
                    "est_minutes": {"type": "integer"},
                    "source_urls": {"type": "array", "items": {"type": "string"}},
                    "related": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string"},
                                "type": {
                                    "type": "string",
                                    "enum": ["related", "prerequisite"],
                                },
                            },
                            "required": ["name", "type"],
                        },
                    },
                },
                "required": [
                    "name",
                    "kind",
                    "summary",
                    "why_now",
                    "learn_content",
                    "est_minutes",
                    "source_urls",
                    "related",
                ],
            },
        },
        "map_delta_text": {"type": "string"},
    },
    "required": ["topics", "map_delta_text"],
}

SYSTEM_PROMPT = (
    "あなたは朝の学習報告の編集者。候補記事を今日知るべき3〜5件のトピックに統合し、"
    "各トピックに約10分で読める短い学習コンテンツを日本語で書く。"
    "learn_content は箇条書き中心で400字以内、最後に10分でできる実践課題を1つ入れる。"
    "related には前提概念(prerequisite)と関連概念(related)を挙げる。"
    "概念名は一般的な表記で安定させる(同じ概念に別名を作らない)。"
    "source_urls は与えられた候補の url からのみ選ぶ。"
    "map_delta_text は学習マップの今日の変化を一文で書く。"
)


def build_user_prompt(items: list[dict], profile: dict) -> str:
    return json.dumps(
        {
            "profile": profile,
            "candidates": [
                {
                    "title": i["title"],
                    "summary": (i.get("summary") or "")[:1500],
                    "url": i.get("url", ""),
                    "source": i.get("source", ""),
                    "score": i.get("score", 0.0),
                    "reason": i.get("reason", ""),
                }
                for i in items
            ],
        },
        ensure_ascii=False,
    )


def run_synthesis(llm, items: list[dict], profile: dict) -> dict:
    """{"topics": [...], "map_delta_text": str} を返す。出力は決定的コード側で検証・整形する。"""
    out = llm.structured(
        "flagship",
        SYNTHESIS_SCHEMA_NAME,
        SYNTHESIS_SCHEMA,
        SYSTEM_PROMPT,
        build_user_prompt(items, profile),
        max_output_tokens=4000,
    )
    topics = []
    for t in list(out.get("topics", []))[:5]:  # スキーマで件数は縛れないためコードで制限
        if not t.get("name"):
            continue
        related = [
            r
            for r in t.get("related", [])
            if r.get("name") and r.get("type") in ("related", "prerequisite")
        ]
        est = t.get("est_minutes")
        kind = t.get("kind")
        topics.append(
            {
                "name": str(t["name"]),
                "kind": kind if kind in ("concept", "paper", "tool", "event") else "concept",
                "summary": str(t.get("summary", "")),
                "why_now": str(t.get("why_now", "")),
                "learn_content": str(t.get("learn_content", "")),
                "est_minutes": min(60, max(1, int(est))) if isinstance(est, int) else 10,
                "source_urls": [str(u) for u in t.get("source_urls", [])],
                "related": related,
            }
        )
    return {"topics": topics, "map_delta_text": str(out.get("map_delta_text", ""))}
