"""学習台帳バックエンドの共通契約とSQLite実装。

SQL方言をpipelineへ漏らさず、eventsを一次データとするドメイン操作だけを公開する。
既存テストとの互換性のため、sqlite3.Connectionはas_backend()で透過的に包む。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Protocol


class LedgerBackendError(RuntimeError):
    """台帳バックエンドの永続化・接続に失敗した。"""


class LedgerBackend(Protocol):
    kind: str

    def close(self) -> None: ...

    def append_events(self, rows: list[dict], *, preserve_ids: bool = False) -> list[int]: ...

    def load_events(self) -> list[dict]: ...

    def replace_derived(self, concepts: list[dict], edges: list[dict]) -> tuple[int, int]: ...

    def get_profile(self) -> dict: ...

    def record_interview(self, profile: dict, event_rows: list[dict]) -> list[int]: ...

    def save_daily_report(
        self,
        date: str,
        items: dict,
        map_delta_text: str,
        html_path: str | None,
    ) -> None: ...

    def get_daily_report(self, date: str | None = None) -> dict | None: ...

    def list_daily_reports(self) -> list[dict]: ...

    def list_concepts(self) -> list[dict]: ...

    def list_edges(self) -> list[dict]: ...

    def list_tasks(self) -> list[dict]: ...

    def publish_artifacts(self, artifacts: list[dict]) -> int: ...

    def get_published_artifact(self, artifact_key: str) -> dict | None: ...

    def create_task(self, task: dict, event_row: dict) -> dict: ...

    def complete_task(
        self, task_id: int, evidence: str, done_at: str, event_payload: dict
    ) -> dict: ...

    def import_state(self, profile: dict, tasks: list[dict], reports: list[dict]) -> None: ...

    def save_llm_usage(self, usage_date: str, run_id: str, usage: dict) -> None: ...

    def get_llm_usage_total(
        self, usage_date: str, model_role: str, run_id_prefix: str | None = None
    ) -> int: ...

    def get_llm_usage_for_run(self, usage_date: str, run_id: str, model_role: str) -> int: ...


def _event_values(row: dict) -> tuple:
    return (
        row.get("ts"),
        row.get("type"),
        row.get("concept_id"),
        json.dumps(row.get("payload") or {}, ensure_ascii=False),
    )


class SQLiteBackend:
    """既存sqlite3.Connectionをドメイン契約へ適合させる。"""

    kind = "sqlite"

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def close(self) -> None:
        self.connection.close()

    def append_events(self, rows: list[dict], *, preserve_ids: bool = False) -> list[int]:
        if not rows:
            return []
        ids: list[int] = []
        with self.connection:
            for row in rows:
                if preserve_ids:
                    event_id = int(row["id"])
                    existing = self.connection.execute(
                        "SELECT ts, type, concept_id, payload FROM events WHERE id = ?",
                        (event_id,),
                    ).fetchone()
                    values = _event_values(row)
                    if existing is not None:
                        existing_values = (
                            existing["ts"],
                            existing["type"],
                            existing["concept_id"],
                            existing["payload"],
                        )
                        if existing_values != values:
                            raise LedgerBackendError(
                                f"event id={event_id} は既存内容と一致しない"
                            )
                    else:
                        self.connection.execute(
                            "INSERT INTO events(id, ts, type, concept_id, payload)"
                            " VALUES(?, ?, ?, ?, ?)",
                            (event_id, *values),
                        )
                    ids.append(event_id)
                else:
                    cur = self.connection.execute(
                        "INSERT INTO events(ts, type, concept_id, payload) VALUES(?, ?, ?, ?)",
                        _event_values(row),
                    )
                    ids.append(int(cur.lastrowid or 0))
        return ids

    def load_events(self) -> list[dict]:
        rows = self.connection.execute(
            "SELECT id, ts, type, concept_id, payload FROM events ORDER BY id"
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "ts": row["ts"],
                "type": row["type"],
                "concept_id": row["concept_id"],
                "payload": json.loads(row["payload"] or "{}"),
            }
            for row in rows
        ]

    def replace_derived(self, concepts: list[dict], edges: list[dict]) -> tuple[int, int]:
        with self.connection:
            self.connection.execute("DELETE FROM concepts")
            self.connection.execute("DELETE FROM edges")
            self.connection.executemany(
                "INSERT INTO concepts(id, name, kind, status, confidence, summary, source_urls,"
                " first_seen, last_touched) VALUES(?,?,?,?,?,?,?,?,?)",
                [
                    (
                        row["id"],
                        row["name"],
                        row["kind"],
                        row["status"],
                        row["confidence"],
                        row["summary"],
                        json.dumps(row["source_urls"], ensure_ascii=False),
                        row["first_seen"],
                        row["last_touched"],
                    )
                    for row in concepts
                ],
            )
            self.connection.executemany(
                "INSERT INTO edges(src, dst, type, weight, created_by, created_at)"
                " VALUES(?,?,?,?,?,?)",
                [
                    (
                        row["src"],
                        row["dst"],
                        row["type"],
                        row["weight"],
                        row["created_by"],
                        row["created_at"],
                    )
                    for row in edges
                ],
            )
        return len(concepts), len(edges)

    def get_profile(self) -> dict:
        row = self.connection.execute(
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

    def record_interview(self, profile: dict, event_rows: list[dict]) -> list[int]:
        ids: list[int] = []
        with self.connection:
            self.connection.execute(
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
            for row in event_rows:
                cur = self.connection.execute(
                    "INSERT INTO events(ts, type, concept_id, payload) VALUES(?, ?, ?, ?)",
                    _event_values(row),
                )
                ids.append(int(cur.lastrowid or 0))
        return ids

    def save_daily_report(
        self,
        date: str,
        items: dict,
        map_delta_text: str,
        html_path: str | None,
    ) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO daily_reports(date, items, map_delta_text, html_path)"
                " VALUES(?, ?, ?, ?)",
                (date, json.dumps(items, ensure_ascii=False), map_delta_text, html_path),
            )

    @staticmethod
    def _report(row) -> dict:
        return {
            "date": row["date"],
            "items": json.loads(row["items"]),
            "map_delta_text": row["map_delta_text"],
            "html_path": row["html_path"],
        }

    def get_daily_report(self, date: str | None = None) -> dict | None:
        if date:
            row = self.connection.execute(
                "SELECT * FROM daily_reports WHERE date = ?", (date,)
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT * FROM daily_reports ORDER BY date DESC LIMIT 1"
            ).fetchone()
        return None if row is None else self._report(row)

    def list_daily_reports(self) -> list[dict]:
        rows = self.connection.execute("SELECT * FROM daily_reports ORDER BY date").fetchall()
        return [self._report(row) for row in rows]

    def list_concepts(self) -> list[dict]:
        rows = self.connection.execute(
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

    def list_edges(self) -> list[dict]:
        rows = self.connection.execute(
            "SELECT src, dst, type, weight, created_by, created_at FROM edges"
            " ORDER BY src, dst, type"
        ).fetchall()
        return [dict(row) for row in rows]

    def list_tasks(self) -> list[dict]:
        rows = self.connection.execute("SELECT * FROM tasks ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def publish_artifacts(self, artifacts: list[dict]) -> int:
        with self.connection:
            for artifact in artifacts:
                self.connection.execute(
                    "INSERT INTO published_artifacts(artifact_key, kind, report_date,"
                    " schema_version, payload, updated_at) VALUES(?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(artifact_key) DO UPDATE SET kind=excluded.kind,"
                    " report_date=excluded.report_date, schema_version=excluded.schema_version,"
                    " payload=excluded.payload, updated_at=excluded.updated_at",
                    (
                        artifact["artifact_key"],
                        artifact["kind"],
                        artifact.get("report_date"),
                        artifact["schema_version"],
                        json.dumps(artifact["payload"], ensure_ascii=False, sort_keys=True),
                        artifact["updated_at"],
                    ),
                )
        return len(artifacts)

    def get_published_artifact(self, artifact_key: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM published_artifacts WHERE artifact_key = ?", (artifact_key,)
        ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["payload"] = json.loads(value["payload"])
        return value

    def create_task(self, task: dict, event_row: dict) -> dict:
        with self.connection:
            cur = self.connection.execute(
                "INSERT INTO tasks(concept_id, title, kind, status, est_minutes, evidence,"
                " created_at, done_at) VALUES(?, ?, ?, 'open', ?, NULL, ?, NULL)",
                (
                    task.get("concept_id"),
                    task["title"],
                    task["kind"],
                    task.get("est_minutes"),
                    task["created_at"],
                ),
            )
            task_id = int(cur.lastrowid or 0)
            payload = dict(event_row.get("payload") or {})
            payload["task_id"] = task_id
            self.connection.execute(
                "INSERT INTO events(ts, type, concept_id, payload) VALUES(?, ?, ?, ?)",
                (
                    event_row["ts"],
                    event_row["type"],
                    event_row.get("concept_id"),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            row = self.connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        assert row is not None
        return dict(row)

    def complete_task(
        self, task_id: int, evidence: str, done_at: str, event_payload: dict
    ) -> dict:
        with self.connection:
            row = self.connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise LedgerBackendError(f"task id={task_id} が見つからない")
            if row["status"] == "done":
                raise LedgerBackendError(f"task id={task_id} は完了済み")
            self.connection.execute(
                "UPDATE tasks SET status='done', evidence=?, done_at=? WHERE id=?",
                (evidence, done_at, task_id),
            )
            payload = dict(event_payload)
            payload.update({"task_id": task_id, "evidence": evidence})
            self.connection.execute(
                "INSERT INTO events(ts, type, concept_id, payload) VALUES(?, 'task_done', ?, ?)",
                (done_at, row["concept_id"], json.dumps(payload, ensure_ascii=False)),
            )
            completed = self.connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        assert completed is not None
        return dict(completed)

    def import_state(self, profile: dict, tasks: list[dict], reports: list[dict]) -> None:
        with self.connection:
            if any(profile.values()):
                self.connection.execute(
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
            for row in tasks:
                columns = (
                    "id", "concept_id", "title", "kind", "status", "est_minutes",
                    "evidence", "created_at", "done_at",
                )
                self.connection.execute(
                    "INSERT OR REPLACE INTO tasks(" + ",".join(columns) + ")"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    tuple(row.get(column) for column in columns),
                )
            for row in reports:
                self.save_daily_report(
                    row["date"], row["items"], row["map_delta_text"], row.get("html_path")
                )

    def save_llm_usage(self, usage_date: str, run_id: str, usage: dict) -> None:
        with self.connection:
            for model_role, values in sorted(usage.items()):
                self.connection.execute(
                    "INSERT INTO llm_usage(usage_date, run_id, model_role, tokens)"
                    " VALUES(?, ?, ?, ?)"
                    " ON CONFLICT(usage_date, run_id, model_role)"
                    " DO UPDATE SET tokens=excluded.tokens",
                    (usage_date, run_id, model_role, int(values.get("used", 0))),
                )

    def get_llm_usage_total(
        self, usage_date: str, model_role: str, run_id_prefix: str | None = None
    ) -> int:
        sql = "SELECT COALESCE(SUM(tokens), 0) FROM llm_usage WHERE usage_date=? AND model_role=?"
        params: list[object] = [usage_date, model_role]
        if run_id_prefix is not None:
            sql += " AND substr(run_id, 1, ?) = ?"
            params.extend([len(run_id_prefix), run_id_prefix])
        row = self.connection.execute(sql, params).fetchone()
        return int(row[0])

    def get_llm_usage_for_run(self, usage_date: str, run_id: str, model_role: str) -> int:
        row = self.connection.execute(
            "SELECT tokens FROM llm_usage WHERE usage_date=? AND run_id=? AND model_role=?",
            (usage_date, run_id, model_role),
        ).fetchone()
        return 0 if row is None else int(row[0])


def as_backend(ledger: sqlite3.Connection | LedgerBackend) -> LedgerBackend:
    if isinstance(ledger, sqlite3.Connection):
        return SQLiteBackend(ledger)
    return ledger


def canonical_rows(rows: Iterable[dict], keys: tuple[str, ...]) -> list[dict]:
    """比較用に指定キーだけを取り、安定順へ正規化する。"""
    projected = [{key: row.get(key) for key in keys} for row in rows]
    return sorted(projected, key=lambda row: tuple(str(row.get(key, "")) for key in keys))
