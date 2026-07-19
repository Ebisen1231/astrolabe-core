"""履歴を毎ターン受け取るステートレスなfunction-callingループ。"""

from __future__ import annotations

import json
from typing import Any, Protocol

from astrolabe.ledger.backend import LedgerBackendError
from astrolabe.tutor.budget import TutorBudgetExceeded, TutorBudgetGuard
from astrolabe.tutor.schemas import TOOL_DEFINITIONS
from astrolabe.tutor.tools import TutorToolError, TutorTools

SYSTEM_PROMPT = """あなたはAstrolabeの常駐チューターです。
会話全文を台帳へ保存してはいけません。台帳の読み書きは必ず提供されたツールだけを使います。
未知語を聞かれたら、最初にsearch_ledgerで既知状態と前提を確認してください。未知または前提不足なら、
短い説明を準備し、create_taskで10〜30分程度の橋渡しタスクを1つ作ってください。元の未知概念を
src、学ぶ前提概念をdstとするprerequisite edgeを含めます。ツール成功後に説明とタスクを伝えます。
利用者が明示していない会話や私生活情報をrecord_feedbackへ記録してはいけません。
クイズ出題はquiz(action=ask)を使い、回答時は履歴の質問と選択肢を見て採点し、
quiz(action=grade)でscoreを記録します。面談内容に合意したときだけupdate_profileを使います。
gradeのuser_answerには正解ではなく、利用者が実際に入力した回答をそのまま渡します。
最終応答は簡潔な日本語のテキストにします。"""


class TutorLLM(Protocol):
    def tool_turn(
        self,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        max_output_tokens: int = 2_000,
    ) -> dict: ...


class TutorEngineError(RuntimeError):
    """ループ上限、履歴不正、ツール実行不能。"""


class TutorEngine:
    def __init__(
        self,
        llm: TutorLLM,
        tools: TutorTools,
        *,
        budget_guard: TutorBudgetGuard | None = None,
        max_rounds: int = 8,
        max_tool_calls: int = 16,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.budget_guard = budget_guard
        self.max_rounds = max_rounds
        self.max_tool_calls = max_tool_calls

    def run_turn(self, history: list[dict], session_id: str) -> dict[str, Any]:
        if not session_id.startswith("tutor-"):
            raise TutorEngineError("session_idは'tutor-'で始める")
        if not history or history[-1].get("role") != "user":
            raise TutorEngineError("history末尾にはuserメッセージが必要")
        if self.budget_guard is not None:
            try:
                # 既に上限ちょうどなら、1トークン以上を使う応答は必ず超過する。
                self.budget_guard.check(1)
            except TutorBudgetExceeded as exc:
                return {
                    "session_id": session_id,
                    "message": str(exc),
                    "cards": [],
                    "budget_exhausted": True,
                }

        input_items: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        for message in history:
            role = message.get("role")
            content = str(message.get("content") or "")
            if role not in {"user", "assistant"}:
                raise TutorEngineError(f"不正なhistory role: {role}")
            cards = message.get("cards") or []
            if cards:
                content += "\n[前ターンの構造化カード] " + json.dumps(
                    cards, ensure_ascii=False, sort_keys=True
                )
            input_items.append({"role": role, "content": content})

        cards: list[dict[str, Any]] = []
        call_count = 0
        for _round in range(self.max_rounds):
            try:
                result = self.llm.tool_turn(input_items, TOOL_DEFINITIONS)
            except TutorBudgetExceeded as exc:
                return {
                    "session_id": session_id,
                    "message": str(exc),
                    "cards": cards,
                    "budget_exhausted": True,
                }
            calls = result.get("tool_calls") or []
            if not calls:
                return {
                    "session_id": session_id,
                    "message": result.get("text") or "応答を生成できませんでした。",
                    "cards": cards,
                    "budget_exhausted": False,
                }
            input_items.extend(result.get("output_items") or [])
            for call in calls:
                call_count += 1
                if call_count > self.max_tool_calls:
                    raise TutorEngineError("1ターンのツール呼び出し上限を超えた")
                try:
                    arguments = json.loads(call["arguments"])
                    card = self.tools.execute(call["name"], arguments)
                    cards.append(card)
                    output = {"ok": True, "result": card}
                except (
                    json.JSONDecodeError,
                    KeyError,
                    TypeError,
                    TutorToolError,
                    LedgerBackendError,
                ) as exc:
                    output = {"ok": False, "error": str(exc)}
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": json.dumps(output, ensure_ascii=False, sort_keys=True),
                    }
                )
        raise TutorEngineError("チューターのfunction-callingループ上限を超えた")
