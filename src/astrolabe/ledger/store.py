"""profile / daily_reports の読み書き。

profile の更新は対応イベント(interview)と同一トランザクションで行う。
daily_reports は朝の報告のアーカイブであり、同日再実行では置き換える。
"""

from __future__ import annotations

from astrolabe.ledger import derive as derive_mod
from astrolabe.ledger import events as events_mod
from astrolabe.ledger.backend import LedgerBackend, as_backend


def get_profile(conn: LedgerBackend) -> dict:
    return as_backend(conn).get_profile()


def record_interview(
    conn: LedgerBackend, profile: dict, known_concepts: list[str]
) -> dict[str, int]:
    """初回面談の結果を記録する。

    profile 更新 + interview イベント + marked_known イベント一括投入を
    1トランザクションで行い、その後 concepts/edges を再導出する。
    """
    ts = events_mod.utcnow_iso()
    event_rows = [
        {
            "ts": ts,
            "type": "interview",
            "concept_id": None,
            "payload": {"profile": profile, "known_count": len(known_concepts)},
        }
    ]
    event_rows.extend(
        {
            "ts": ts,
            "type": "marked_known",
            "concept_id": derive_mod.concept_id_from_name(name),
            "payload": {"name": name, "confidence": 0.8, "origin": "interview"},
        }
        for name in known_concepts
    )
    as_backend(conn).record_interview(profile, event_rows)
    n_concepts, n_edges = derive_mod.rebuild(conn)
    return {"known": len(known_concepts), "concepts": n_concepts, "edges": n_edges}


def save_daily_report(
    conn: LedgerBackend,
    date: str,
    items: dict,
    map_delta_text: str,
    html_path: str | None = None,
) -> None:
    as_backend(conn).save_daily_report(date, items, map_delta_text, html_path)


def get_daily_report(conn: LedgerBackend, date: str | None = None) -> dict | None:
    return as_backend(conn).get_daily_report(date)


def list_daily_reports(conn: LedgerBackend) -> list[dict]:
    """export向けに全日次報告を古い順で返す。"""
    return as_backend(conn).list_daily_reports()


def list_concepts(conn: LedgerBackend) -> list[dict]:
    """HTML/通知向けに導出済みconceptsを安定順で読み出す。"""
    return as_backend(conn).list_concepts()


def list_edges(conn: LedgerBackend) -> list[dict]:
    """HTML/export向けに導出済みedgesを安定順で読み出す。"""
    return as_backend(conn).list_edges()


def get_learning_context(conn: LedgerBackend, recent_limit: int = 20) -> dict:
    """一次選別へ渡す既知・直近フィードバックを決定的に組み立てる。"""
    backend = as_backend(conn)
    concepts = backend.list_concepts()
    names = {row["id"]: row["name"] for row in concepts}
    learned = [
        row["name"]
        for row in sorted(concepts, key=lambda row: (row["name"], row["id"]))
        if row["status"] == "learned"
    ]
    rows = sorted(
        (
            row
            for row in backend.load_events()
            if row["type"] in ("selected", "dismissed")
        ),
        key=lambda row: (row["ts"], row["id"]),
        reverse=True,
    )
    selected: list[str] = []
    dismissed: list[str] = []
    seen: dict[str, set[str]] = {"selected": set(), "dismissed": set()}
    for row in rows:
        bucket = row["type"]
        target = selected if bucket == "selected" else dismissed
        cid = row["concept_id"] or ""
        if cid in seen[bucket] or len(target) >= recent_limit:
            continue
        payload = row.get("payload") or {}
        name = str(names.get(cid) or payload.get("name") or cid)
        if name:
            target.append(name)
            seen[bucket].add(cid)
    return {
        "learned_concepts": learned,
        "recent_selected": selected,
        "recent_dismissed": dismissed,
    }


def list_tasks(conn: LedgerBackend) -> list[dict]:
    return as_backend(conn).list_tasks()


def save_llm_usage(conn: LedgerBackend, usage_date: str, run_id: str, usage: dict) -> None:
    as_backend(conn).save_llm_usage(usage_date, run_id, usage)
