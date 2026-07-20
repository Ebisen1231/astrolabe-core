"""イベント列から復習間隔を再導出する決定的スケジューラ。

現在はSM-2系の小さな純関数を使う。一次データにはFSRSの4段階ratingと同じ
``grade`` (1=again, 2=hard, 3=good, 4=easy) を残すため、将来はこの関数だけを
差し替えて全履歴を再計算できる。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

INITIAL_EASE = 2.5
MIN_EASE = 1.3
MAX_EASE = 3.0
MAX_INTERVAL_DAYS = 365
MARKED_KNOWN_INTERVAL_DAYS = 7


def grade_from_score(score: float) -> int:
    """gradeのない歴史quiz_resultをFSRS互換4段階へ決定的に写像する。"""
    value = min(1.0, max(0.0, float(score)))
    if value < 0.5:
        return 1
    if value < 0.8:
        return 2
    if value < 0.95:
        return 3
    return 4


@dataclass
class _ReviewState:
    concept_id: str
    concept_name: str
    interval_days: int = 0
    repetitions: int = 0
    ease: float = INITIAL_EASE
    due_date: date | None = None
    reviewed_at: str | None = None
    last_grade: int | None = None


def _payload(event: dict) -> dict:
    value = event.get("payload") or {}
    if isinstance(value, str):
        value = json.loads(value or "{}")
    return value if isinstance(value, dict) else {}


def _event_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(JST)


def _today(value: date | str) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _round_days(value: float) -> int:
    return int(math.floor(value + 0.5))


def _clamp_interval(value: int) -> int:
    return min(MAX_INTERVAL_DAYS, max(1, value))


def _apply_grade(state: _ReviewState, grade: int, reviewed: datetime) -> None:
    if grade not in (1, 2, 3, 4):
        raise ValueError("gradeは1..4")
    previous = max(1, state.interval_days)
    if grade == 1:
        interval = 1
        repetitions = 0
        ease = state.ease - 0.20
    elif grade == 2:
        interval = math.ceil(previous * 1.2)
        repetitions = max(1, state.repetitions)
        ease = state.ease - 0.15
    elif grade == 3:
        if state.repetitions == 0:
            interval = 1
        elif state.repetitions == 1:
            interval = 3
        else:
            interval = _round_days(previous * state.ease)
        repetitions = state.repetitions + 1
        ease = state.ease
    else:
        if state.repetitions == 0:
            interval = 3
        elif state.repetitions == 1:
            interval = 7
        else:
            interval = _round_days(previous * state.ease * 1.3)
        repetitions = state.repetitions + 1
        ease = state.ease + 0.15
    state.interval_days = _clamp_interval(interval)
    state.repetitions = repetitions
    state.ease = min(MAX_EASE, max(MIN_EASE, ease))
    state.due_date = reviewed.date() + timedelta(days=state.interval_days)
    state.reviewed_at = reviewed.isoformat()
    state.last_grade = grade


def review_schedule(events: list[dict], today: date | str) -> list[dict]:
    """同じイベント列とtodayから常に同じ復習状態を返す純関数。"""
    target_date = _today(today)
    states: dict[str, _ReviewState] = {}
    for event in sorted(events, key=lambda row: (str(row["ts"]), int(row["id"]))):
        concept_id = str(event.get("concept_id") or "").strip()
        if not concept_id:
            continue
        event_type = str(event.get("type") or "")
        payload = _payload(event)
        reviewed = _event_datetime(str(event["ts"]))
        state = states.get(concept_id)
        name = str(payload.get("name") or concept_id)

        if event_type == "selected":
            if state is not None and not payload.get("later"):
                state.due_date = reviewed.date()
            continue
        if event_type not in {"marked_known", "task_done", "quiz_result"}:
            continue
        if state is None:
            state = _ReviewState(concept_id=concept_id, concept_name=name)
            states[concept_id] = state
        elif name != concept_id:
            state.concept_name = name

        if event_type == "marked_known" and state.interval_days == 0:
            state.interval_days = MARKED_KNOWN_INTERVAL_DAYS
            state.repetitions = 1
            state.due_date = reviewed.date() + timedelta(days=MARKED_KNOWN_INTERVAL_DAYS)
            state.reviewed_at = reviewed.isoformat()
            state.last_grade = 4
        elif event_type == "marked_known":
            _apply_grade(state, 4, reviewed)
        elif event_type == "task_done":
            _apply_grade(state, 3, reviewed)
        else:
            raw_grade = payload.get("grade")
            grade = (
                int(raw_grade)
                if isinstance(raw_grade, int) and not isinstance(raw_grade, bool)
                else grade_from_score(float(payload.get("score", 0.0)))
            )
            _apply_grade(state, grade, reviewed)

    result = []
    for concept_id in sorted(states):
        state = states[concept_id]
        if state.due_date is None:
            continue
        result.append(
            {
                "concept_id": state.concept_id,
                "concept_name": state.concept_name,
                "grade": state.last_grade,
                "reviewed_at": state.reviewed_at,
                "interval_days": state.interval_days,
                "repetitions": state.repetitions,
                "ease": round(state.ease, 4),
                "due_date": state.due_date.isoformat(),
                "is_due": state.due_date <= target_date,
            }
        )
    return result


def due_reviews(events: list[dict], today: date | str, *, limit: int = 5) -> list[dict]:
    """期日到来分だけを期日・concept ID順で返す。"""
    due = [row for row in review_schedule(events, today) if row["is_due"]]
    return sorted(due, key=lambda row: (row["due_date"], row["concept_id"]))[:limit]
