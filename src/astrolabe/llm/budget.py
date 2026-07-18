"""トークン予算の実測管理。

条件: 呼び出し前の残予算確認(precheck)と応答後の usage 累積(add)の両方を行う。
カナリア分も計上する。上限の既定は AGENTS.md の朝ジョブ見積上限
(mini 0.5M / flagship 0.07M)。
"""

from __future__ import annotations

import threading


class BudgetExceededError(RuntimeError):
    """トークン予算超過(見込み含む)。runを安全に中断する。"""


def estimate_tokens(text: str) -> int:
    """呼び出し前の残予算確認に使う概算。

    英文≈4字/トークン、和文≈1字/トークンの中間として len/2 を採り、
    英文中心の入力では安全側(過大)に倒れる。実測は応答後に usage で取る。
    """
    return max(1, len(text) // 2)


class TokenBudget:
    def __init__(self, caps: dict[str, int]) -> None:
        self._caps = dict(caps)
        self._used = {k: 0 for k in caps}
        self._lock = threading.Lock()

    def precheck(self, key: str, estimated_tokens: int) -> None:
        with self._lock:
            used, cap = self._used[key], self._caps[key]
            if used + max(0, estimated_tokens) > cap:
                raise BudgetExceededError(
                    f"トークン予算超過見込みのため中断: {key} 使用済み {used:,}"
                    f" + 見積 {estimated_tokens:,} > 上限 {cap:,}"
                )

    def add(self, key: str, tokens: int) -> tuple[int, int]:
        """実測usageを累積し、(累積, 上限) を返す。"""
        with self._lock:
            self._used[key] += max(0, tokens)
            return self._used[key], self._caps[key]

    def summary(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {k: {"used": self._used[k], "cap": self._caps[k]} for k in self._caps}
