"""チューター帰属枠と全flagship枠を同時に守る回路遮断。"""

from __future__ import annotations

from astrolabe.ledger import store
from astrolabe.ledger.backend import LedgerBackend

TUTOR_RUN_ID_PREFIX = "tutor-"


class TutorBudgetExceeded(RuntimeError):
    """チューターまたは全体のflagship日次枠に達した。"""


class TutorBudgetGuard:
    def __init__(
        self,
        ledger: LedgerBackend,
        usage_date: str,
        run_id: str,
        *,
        tutor_cap: int,
        total_cap: int,
    ) -> None:
        if not run_id.startswith(TUTOR_RUN_ID_PREFIX):
            raise ValueError(f"チューターsession_idは{TUTOR_RUN_ID_PREFIX!r}で始める")
        self.ledger = ledger
        self.usage_date = usage_date
        self.run_id = run_id
        self.tutor_cap = tutor_cap
        self.total_cap = total_cap
        self._run_used = store.get_llm_usage_for_run(
            ledger, usage_date, run_id, "flagship"
        )

    def check(self, estimated_tokens: int = 0) -> None:
        estimate = max(0, estimated_tokens)
        tutor_used = store.get_llm_usage_total(
            self.ledger, self.usage_date, "flagship", TUTOR_RUN_ID_PREFIX
        )
        total_used = store.get_llm_usage_total(
            self.ledger, self.usage_date, "flagship"
        )
        if tutor_used + estimate > self.tutor_cap:
            raise TutorBudgetExceeded(
                "今日はチューター予算切れです"
                f"(tutor {tutor_used:,} + 見積 {estimate:,} > {self.tutor_cap:,})"
            )
        if total_used + estimate > self.total_cap:
            raise TutorBudgetExceeded(
                "今日は全体のflagship予算切れです"
                f"(total {total_used:,} + 見積 {estimate:,} > {self.total_cap:,})"
            )

    def attempt_precheck(self, budget_key: str, estimated_tokens: int) -> None:
        if budget_key == "flagship":
            self.check(estimated_tokens)

    def record_usage(self, budget_key: str, tokens: int) -> None:
        if budget_key != "flagship":
            return
        self._run_used += max(0, tokens)
        store.save_llm_usage(
            self.ledger,
            self.usage_date,
            self.run_id,
            {"flagship": {"used": self._run_used}},
        )
