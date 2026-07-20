"""チューターが使用できる唯一の台帳読み書き境界。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from astrolabe.ledger import derive, events, review, store
from astrolabe.ledger.backend import LedgerBackend, as_backend

JST = ZoneInfo("Asia/Tokyo")
TASK_KINDS = {"read", "implement", "quiz", "build_app_feature"}
FEEDBACK_EVENTS = {"selected", "marked_known", "dismissed", "chat_note"}


class TutorToolError(ValueError):
    """ツール引数または台帳状態が不正。"""


class TutorTools:
    def __init__(self, ledger: LedgerBackend, *, now=None) -> None:
        self.ledger = as_backend(ledger)
        self._now = now or (lambda: datetime.now(UTC))

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, name, None)
        if name.startswith("_") or handler is None:
            raise TutorToolError(f"未定義のツール: {name}")
        return handler(**arguments)

    def _timestamp(self) -> str:
        instant = self._now()
        if instant.tzinfo is None:
            raise TutorToolError("nowはtimezone-awareである必要がある")
        return instant.astimezone(UTC).isoformat()

    def search_ledger(
        self, query: str, include_today: bool, task_status: str
    ) -> dict[str, Any]:
        needle = query.strip().casefold()
        concepts = self.ledger.list_concepts()
        matches = [
            row
            for row in concepts
            if not needle
            or needle in row["id"].casefold()
            or needle in row["name"].casefold()
        ]
        matched_ids = {row["id"] for row in matches}
        edges = [
            row
            for row in self.ledger.list_edges()
            if row["src"] in matched_ids or row["dst"] in matched_ids
        ]
        tasks = self.ledger.list_tasks()
        if task_status != "all":
            tasks = [row for row in tasks if row["status"] == task_status]
        report = None
        if include_today:
            today = self._now().astimezone(JST).date().isoformat()
            report = self.ledger.get_daily_report(today)
        return {
            "type": "ledger_search",
            "query": query,
            "concepts": matches[:50],
            "edges": edges[:100],
            "today_report": report,
            "tasks": tasks,
            "profile": self.ledger.get_profile(),
        }

    def record_feedback(
        self,
        event_type: str,
        concept_id: str | None,
        concept_name: str | None,
        note: str | None,
        later: bool,
        confidence: float | None,
    ) -> dict[str, Any]:
        if event_type not in FEEDBACK_EVENTS:
            raise TutorToolError("記録できないfeedback event")
        if event_type != "chat_note" and not concept_id:
            raise TutorToolError("concept_idが必要")
        payload: dict[str, Any] = {}
        if concept_name:
            payload["name"] = concept_name
        if note:
            payload["note"] = note[:2_000]
        if event_type == "selected":
            payload["later"] = later
        if event_type == "marked_known" and confidence is not None:
            payload["confidence"] = confidence
        event_id = events.append_event(
            self.ledger, event_type, concept_id, payload, ts=self._timestamp()
        )
        derive.rebuild(self.ledger)
        return {"type": "feedback_recorded", "event_id": event_id, "event_type": event_type}

    def create_task(
        self,
        concept_id: str,
        concept_name: str,
        title: str,
        kind: str,
        est_minutes: int,
        edges: list[dict],
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        if kind not in TASK_KINDS:
            raise TutorToolError("不正なtask kind")
        if not title.strip() or not concept_id.strip() or not concept_name.strip():
            raise TutorToolError("taskの概念・タイトルは必須")
        if not 1 <= est_minutes <= 480:
            raise TutorToolError("est_minutesは1..480")
        ts = self._timestamp()
        event_payload = {"name": concept_name, "edges": edges}
        if metadata:
            event_payload["metadata"] = metadata
        task = store.create_task(
            self.ledger,
            {
                "concept_id": concept_id,
                "title": title.strip(),
                "kind": kind,
                "est_minutes": est_minutes,
                "created_at": ts,
            },
            {
                "ts": ts,
                "type": "task_created",
                "concept_id": concept_id,
                "payload": event_payload,
            },
        )
        return {"type": "task_created", "task": task, "edges": edges}

    def complete_task(
        self, task_id: int, evidence: str, confidence_delta: float
    ) -> dict[str, Any]:
        if not evidence.strip():
            raise TutorToolError("完了evidenceは必須")
        if not 0 <= confidence_delta <= 1:
            raise TutorToolError("confidence_deltaは0..1")
        task = store.complete_task(
            self.ledger,
            task_id,
            evidence.strip(),
            self._timestamp(),
            {"confidence_delta": confidence_delta},
        )
        return {"type": "task_completed", "task": task}

    def quiz(
        self,
        action: str,
        concept_id: str,
        concept_name: str,
        question: str,
        options: list[str],
        user_answer: str,
        score: float,
        feedback: str,
        grade: int | None = None,
    ) -> dict[str, Any]:
        if action == "ask":
            if not question.strip() or not 2 <= len(options) <= 4:
                raise TutorToolError("クイズには質問と2..4個の選択肢が必要")
            return {
                "type": "quiz",
                "concept_id": concept_id,
                "concept_name": concept_name,
                "question": question,
                "options": options,
            }
        if action != "grade":
            raise TutorToolError("quiz actionはaskまたはgrade")
        if not user_answer.strip() or not 0 <= score <= 1:
            raise TutorToolError("採点には回答と0..1のscoreが必要")
        if isinstance(grade, bool) or grade not in (1, 2, 3, 4):
            raise TutorToolError("採点にはgrade 1..4が必要")
        payload = {
            "name": concept_name,
            "question": question,
            "user_answer": user_answer,
            "score": score,
            "grade": grade,
            "feedback": feedback,
        }
        event_id = events.append_event(
            self.ledger, "quiz_result", concept_id, payload, ts=self._timestamp()
        )
        derive.rebuild(self.ledger)
        return {
            "type": "quiz_result",
            "event_id": event_id,
            "concept_id": concept_id,
            "score": score,
            "grade": grade,
            "feedback": feedback,
        }

    def get_due_reviews(self, limit: int) -> dict[str, Any]:
        if not 1 <= limit <= 5:
            raise TutorToolError("復習件数は1..5")
        today = self._now().astimezone(JST).date().isoformat()
        rows = review.due_reviews(events.load_events(self.ledger), today, limit=limit)
        names = {row["id"]: row["name"] for row in self.ledger.list_concepts()}
        for row in rows:
            row["concept_name"] = names.get(row["concept_id"], row["concept_name"])
        return {"type": "due_reviews", "date": today, "reviews": rows}

    def update_profile(
        self,
        interests: list[dict],
        goals: str,
        background: str,
        time_budget: str,
        known_concepts: list[str],
    ) -> dict[str, Any]:
        profile = {
            "interests": {row["tag"]: float(row["weight"]) for row in interests},
            "goals": goals,
            "background": background,
            "time_budget": time_budget,
        }
        result = store.record_interview(self.ledger, profile, known_concepts)
        return {"type": "profile_updated", "profile": profile, **result}
