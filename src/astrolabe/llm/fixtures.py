"""--dry-run 用のLLMスタブ。APIを一切呼ばない。

fixtures/llm_triage.json と fixtures/llm_synthesis.json の内容を返し、
usage は決定的なダミー値を予算に計上する(予算経路をdry-runでも通すため)。
"""

from __future__ import annotations

import json
from pathlib import Path

from astrolabe.llm.budget import TokenBudget, estimate_tokens
from astrolabe.llm.client import LLMCallError


class FixtureLLM:
    def __init__(self, fixtures_dir: Path, budget: TokenBudget) -> None:
        self._triage = json.loads(
            (fixtures_dir / "llm_triage.json").read_text(encoding="utf-8")
        )
        self._synthesis = json.loads(
            (fixtures_dir / "llm_synthesis.json").read_text(encoding="utf-8")
        )
        self._budget = budget

    def structured(
        self,
        budget_key: str,
        schema_name: str,
        schema: dict,
        system: str,
        user: str,
        max_output_tokens: int,
    ) -> dict:
        self._budget.precheck(budget_key, estimate_tokens(system + user) + max_output_tokens)
        if schema_name == "triage_scores":
            request = json.loads(user)  # triage の user プロンプトはJSON(仕様)
            table = self._triage.get("scores", {})
            default = float(self._triage.get("default_score", 0.1))
            out = {
                "scores": [
                    {
                        "id": item["id"],
                        "score": float(table.get(item["id"], {}).get("score", default)),
                        "reason": table.get(item["id"], {}).get("reason", "fixtures既定値"),
                    }
                    for item in request["items"]
                ]
            }
        elif schema_name == "daily_synthesis":
            out = self._synthesis
        else:
            raise LLMCallError(f"fixtures未対応のスキーマ: {schema_name}")
        self._budget.add(budget_key, estimate_tokens(user) // 4 + 200)  # 決定的なダミーusage
        return out

    def canary(self, budget_key: str) -> dict:
        self._budget.precheck(budget_key, 64)
        self._budget.add(budget_key, 20)
        return {"model": f"fixtures:{budget_key}", "output": "OK"}
