"""CLI・ローカルHTTPが共有するチューター実行境界。"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from astrolabe.config import Config
from astrolabe.ledger import db
from astrolabe.ledger.supabase import SupabaseLedger
from astrolabe.llm.budget import TokenBudget
from astrolabe.llm.client import ResponsesLLM
from astrolabe.tutor.budget import TutorBudgetGuard
from astrolabe.tutor.engine import TutorEngine
from astrolabe.tutor.tools import TutorTools

JST = ZoneInfo("Asia/Tokyo")


def open_configured_ledger(config: Config):
    if config.backend == "supabase":
        assert config.supabase_url is not None
        assert config.supabase_service_role_key is not None
        return SupabaseLedger(config.supabase_url, config.supabase_service_role_key)
    assert config.ledger_path is not None
    return db.open_ledger(config.ledger_path)


class LocalTutorRuntime:
    """リクエストごとに台帳を開閉し、サーバ側状態を持たない。"""

    def __init__(self, config: Config, *, now=None) -> None:
        self.config = config
        self._now = now or (lambda: datetime.now(UTC))

    def _usage_date(self) -> str:
        return self._now().astimezone(JST).date().isoformat()

    def turn(self, history: list[dict], session_id: str) -> dict:
        ledger = open_configured_ledger(self.config)
        try:
            guard = TutorBudgetGuard(
                ledger,
                self._usage_date(),
                session_id,
                tutor_cap=self.config.tutor_max_flagship_tokens,
                total_cap=self.config.max_flagship_tokens,
            )
            budget = TokenBudget(
                {"mini": self.config.max_mini_tokens, "flagship": self.config.max_flagship_tokens}
            )
            llm = ResponsesLLM(
                api_key=self.config.api_key or "",
                models={"flagship": self.config.model_flagship or ""},
                budget=budget,
                usage_observer=guard.record_usage,
                attempt_precheck=guard.attempt_precheck,
            )
            return TutorEngine(
                llm,
                TutorTools(ledger, now=self._now),
                budget_guard=guard,
            ).run_turn(history, session_id)
        finally:
            ledger.close()

    def list_tasks(self) -> list[dict]:
        ledger = open_configured_ledger(self.config)
        try:
            result = TutorTools(ledger, now=self._now).search_ledger("", False, "all")
            return result["tasks"]
        finally:
            ledger.close()

    def complete_task(
        self, task_id: int, evidence: str, confidence_delta: float = 0.2
    ) -> dict:
        ledger = open_configured_ledger(self.config)
        try:
            return TutorTools(ledger, now=self._now).complete_task(
                task_id, evidence, confidence_delta
            )
        finally:
            ledger.close()
