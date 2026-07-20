from datetime import date

import pytest

from astrolabe.ledger.review import due_reviews, grade_from_score, review_schedule


def event(event_id, day, event_type, concept_id="rag", **payload):
    return {
        "id": event_id,
        "ts": f"2026-07-{day:02d}T00:00:00+09:00",
        "type": event_type,
        "concept_id": concept_id,
        "payload": payload,
    }


@pytest.mark.parametrize(
    ("score", "grade"),
    [
        (-1, 1),
        (0, 1),
        (0.4999, 1),
        (0.5, 2),
        (0.7999, 2),
        (0.8, 3),
        (0.9499, 3),
        (0.95, 4),
        (1, 4),
        (2, 4),
    ],
)
def test_legacy_score_to_grade_mapping_is_frozen_at_boundaries(score, grade):
    assert grade_from_score(score) == grade


@pytest.mark.parametrize(
    ("grade", "interval", "repetitions", "ease"),
    [
        (1, 1, 0, 2.3),
        (2, 2, 1, 2.35),
        (3, 3, 2, 2.5),
        (4, 7, 2, 2.65),
    ],
)
def test_grade_transitions_from_one_day_state(grade, interval, repetitions, ease):
    events = [
        event(1, 1, "quiz_result", score=0.8, grade=3, name="RAG"),
        event(2, 2, "quiz_result", score=0.8, grade=grade, name="RAG"),
    ]
    row = review_schedule(events, "2026-07-20")[0]
    assert row["interval_days"] == interval
    assert row["repetitions"] == repetitions
    assert row["ease"] == ease
    assert row["due_date"] == f"2026-07-{2 + interval:02d}"


def test_same_events_produce_same_dates_independent_of_input_order():
    events = [
        event(3, 8, "quiz_result", score=1.0, grade=4, name="RAG"),
        event(1, 1, "marked_known", name="RAG"),
        event(2, 4, "selected", name="RAG"),
    ]
    expected = review_schedule(events, date(2026, 7, 20))
    assert review_schedule(list(reversed(events)), date(2026, 7, 20)) == expected
    assert expected[0]["due_date"] == "2026-07-15"
    assert expected[0]["is_due"] is True


def test_due_reviews_are_due_then_concept_sorted_and_limited():
    events = [
        event(1, 1, "marked_known", "z", name="Z"),
        event(2, 1, "marked_known", "a", name="A"),
        event(3, 3, "marked_known", "later", name="Later"),
    ]
    assert [row["concept_id"] for row in due_reviews(events, "2026-07-08", limit=2)] == [
        "a",
        "z",
    ]


def test_selected_later_and_unlearned_selected_do_not_create_schedule():
    events = [
        event(1, 1, "selected", "new", name="New"),
        event(2, 2, "marked_known", "rag", name="RAG"),
        event(3, 3, "selected", "rag", name="RAG", later=True),
    ]
    rows = review_schedule(events, "2026-07-20")
    assert [row["concept_id"] for row in rows] == ["rag"]
    assert rows[0]["due_date"] == "2026-07-09"


def test_ease_and_interval_are_clamped():
    events = [event(1, 1, "quiz_result", grade=1, score=0.0, name="RAG")]
    for index in range(2, 12):
        events.append(event(index, index, "quiz_result", grade=1, score=0.0, name="RAG"))
    row = review_schedule(events, "2026-07-20")[0]
    assert row["ease"] == 1.3
    assert row["interval_days"] == 1

    easy_events = [event(1, 1, "marked_known", name="RAG")]
    for index in range(2, 10):
        easy_events.append(
            {
                **event(index, 2, "quiz_result", grade=4, score=1.0, name="RAG"),
                "ts": f"2026-07-02T00:00:{index:02d}+09:00",
            }
        )
    easy = review_schedule(easy_events, "2026-07-20")[0]
    assert easy["ease"] == 3.0
    assert easy["interval_days"] == 365
