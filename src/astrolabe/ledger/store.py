"""profile / daily_reports の読み書き。

profile の更新は対応イベント(interview)と同一トランザクションで行う。
daily_reports は朝の報告のアーカイブであり、同日再実行では置き換える。
"""

from __future__ import annotations

import json
import sqlite3

from astrolabe.ledger import derive as derive_mod
from astrolabe.ledger import events as events_mod


def get_profile(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT interests, goals, background, time_budget FROM profile WHERE id = 1"
    ).fetchone()
    if row is None:
        return {"interests": {}, "goals": "", "background": "", "time_budget": ""}
    return {
        "interests": json.loads(row["interests"] or "{}"),
        "goals": row["goals"],
        "background": row["background"],
        "time_budget": row["time_budget"],
    }


def record_interview(
    conn: sqlite3.Connection, profile: dict, known_concepts: list[str]
) -> dict[str, int]:
    """初回面談の結果を記録する。

    profile 更新 + interview イベント + marked_known イベント一括投入を
    1トランザクションで行い、その後 concepts/edges を再導出する。
    """
    ts = events_mod.utcnow_iso()
    with conn:
        conn.execute(
            "INSERT INTO profile(id, interests, goals, background, time_budget)"
            " VALUES(1, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET interests=excluded.interests,"
            " goals=excluded.goals, background=excluded.background,"
            " time_budget=excluded.time_budget",
            (
                json.dumps(profile.get("interests", {}), ensure_ascii=False),
                profile.get("goals", ""),
                profile.get("background", ""),
                profile.get("time_budget", ""),
            ),
        )
        events_mod.append_event(
            conn,
            "interview",
            payload={"profile": profile, "known_count": len(known_concepts)},
            ts=ts,
        )
        for name in known_concepts:
            events_mod.append_event(
                conn,
                "marked_known",
                concept_id=derive_mod.concept_id_from_name(name),
                payload={"name": name, "confidence": 0.8, "origin": "interview"},
                ts=ts,
            )
    n_concepts, n_edges = derive_mod.rebuild(conn)
    return {"known": len(known_concepts), "concepts": n_concepts, "edges": n_edges}


def save_daily_report(
    conn: sqlite3.Connection,
    date: str,
    items: dict,
    map_delta_text: str,
    html_path: str | None = None,
) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO daily_reports(date, items, map_delta_text, html_path)"
            " VALUES(?, ?, ?, ?)",
            (date, json.dumps(items, ensure_ascii=False), map_delta_text, html_path),
        )


def get_daily_report(conn: sqlite3.Connection, date: str | None = None) -> dict | None:
    if date:
        row = conn.execute("SELECT * FROM daily_reports WHERE date = ?", (date,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM daily_reports ORDER BY date DESC LIMIT 1").fetchone()
    if row is None:
        return None
    return {
        "date": row["date"],
        "items": json.loads(row["items"]),
        "map_delta_text": row["map_delta_text"],
        "html_path": row["html_path"],
    }


def list_concepts(conn: sqlite3.Connection) -> list[dict]:
    """HTML/通知向けに導出済みconceptsを安定順で読み出す。"""
    rows = conn.execute(
        "SELECT id, name, kind, status, confidence, summary, source_urls,"
        " first_seen, last_touched FROM concepts ORDER BY id"
    ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "kind": row["kind"],
            "status": row["status"],
            "confidence": row["confidence"],
            "summary": row["summary"],
            "source_urls": json.loads(row["source_urls"] or "[]"),
            "first_seen": row["first_seen"],
            "last_touched": row["last_touched"],
        }
        for row in rows
    ]


def list_edges(conn: sqlite3.Connection) -> list[dict]:
    """HTML向けに導出済みedgesを安定順で読み出す。"""
    rows = conn.execute(
        "SELECT src, dst, type, weight, created_by, created_at FROM edges"
        " ORDER BY src, dst, type"
    ).fetchall()
    return [dict(row) for row in rows]


def get_learning_context(conn: sqlite3.Connection, recent_limit: int = 20) -> dict:
    """一次選別へ渡す既知・直近フィードバックを決定的に組み立てる。"""
    learned = [
        row["name"]
        for row in conn.execute(
            "SELECT name FROM concepts WHERE status = 'learned' ORDER BY name, id"
        )
    ]
    rows = conn.execute(
        "SELECT e.id, e.type, e.concept_id, e.payload, c.name"
        " FROM events e LEFT JOIN concepts c ON c.id = e.concept_id"
        " WHERE e.type IN ('selected', 'dismissed')"
        " ORDER BY e.ts DESC, e.id DESC"
    ).fetchall()
    selected: list[str] = []
    dismissed: list[str] = []
    seen: dict[str, set[str]] = {"selected": set(), "dismissed": set()}
    for row in rows:
        bucket = row["type"]
        target = selected if bucket == "selected" else dismissed
        cid = row["concept_id"] or ""
        if cid in seen[bucket] or len(target) >= recent_limit:
            continue
        payload = json.loads(row["payload"] or "{}")
        name = str(row["name"] or payload.get("name") or cid)
        if name:
            target.append(name)
            seen[bucket].add(cid)
    return {
        "learned_concepts": learned,
        "recent_selected": selected,
        "recent_dismissed": dismissed,
    }
