"""一次選別: 要旨+プロファイルを mini モデルに渡してスコアリングする。

チャンク単位で呼び出し、1チャンクの失敗はスキップして続行する。
ただし致命的エラーと予算超過は必ず貫通させる(回路遮断)。
"""

from __future__ import annotations

import json
import logging

from astrolabe.llm.budget import BudgetExceededError
from astrolabe.llm.client import FatalLLMError, raise_if_fatal_text

TRIAGE_SCHEMA_NAME = "triage_scores"
TRIAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "score": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "score", "reason"],
            },
        }
    },
    "required": ["scores"],
}

SYSTEM_PROMPT = (
    "あなたは個人学習者のためのキュレーター。学習者プロファイルに照らして、"
    "各記事の「今日学ぶ価値」を0〜1で採点する。"
    "規準は §5.2 に従う: 新規性 × 興味適合 × 学習価値(前提概念がほぼ埋まっている"
    "未知トピックを優先)− 既知ペナルティ。"
    "全項目に対して id をそのまま返し、reason は日本語で30字以内。"
)


def build_user_prompt(items: list[dict], profile: dict) -> str:
    """user プロンプトは機械可読なJSONにする(FixtureLLMもこれを解釈する)。"""
    return json.dumps(
        {
            "profile": {
                "interests": profile.get("interests", {}),
                "goals": profile.get("goals", ""),
                "background": profile.get("background", ""),
            },
            "items": [
                {
                    "id": i["id"],
                    "title": i["title"],
                    "summary": (i.get("summary") or "")[:600],
                    "source": i.get("source", ""),
                }
                for i in items
            ],
        },
        ensure_ascii=False,
    )


def run_triage(
    llm,
    items: list[dict],
    profile: dict,
    *,
    chunk_size: int = 20,
    logger: logging.Logger | None = None,
) -> dict[str, dict]:
    """{item_id: {"score": float, "reason": str}} を返す。失敗チャンクの項目は含まれない。"""
    logger = logger or logging.getLogger("astrolabe.triage")
    scores: dict[str, dict] = {}
    for start in range(0, len(items), chunk_size):
        chunk = items[start : start + chunk_size]
        try:
            out = llm.structured(
                "mini",
                TRIAGE_SCHEMA_NAME,
                TRIAGE_SCHEMA,
                SYSTEM_PROMPT,
                build_user_prompt(chunk, profile),
                max_output_tokens=80 * len(chunk) + 200,
            )
        except (FatalLLMError, BudgetExceededError):
            raise
        except Exception as e:
            raise_if_fatal_text(str(e))  # broad except に致命的エラーを飲み込ませない
            logger.warning("一次選別チャンク失敗、%d件をスキップ: %s", len(chunk), e)
            continue
        for s in out.get("scores", []):
            item_id = str(s.get("id", ""))
            if item_id:
                scores[item_id] = {
                    "score": min(1.0, max(0.0, float(s.get("score", 0.0)))),
                    "reason": str(s.get("reason", "")),
                }
    return scores
