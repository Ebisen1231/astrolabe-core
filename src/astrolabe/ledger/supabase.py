"""Supabase Data API(PostgREST)を使う学習台帳バックエンド。

新規SDK依存は持たず、既存httpxでtimeoutと全体1回のリトライを明示管理する。
認証系4xxは即時失敗し、5xx・接続断・timeoutだけを一度再試行する。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from astrolabe.ledger.backend import LedgerBackendError


class SupabaseAuthError(LedgerBackendError):
    """SupabaseのURLまたはservice role keyが無効。"""


class SupabaseUnavailableError(LedgerBackendError):
    """再試行後もSupabaseへ到達できない。"""


def _normalize_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat(timespec="seconds")


def _error_detail(response: httpx.Response) -> str:
    try:
        value = response.json()
    except ValueError:
        return response.text[:300].strip()
    if isinstance(value, dict):
        for key in ("message", "hint", "details", "code"):
            if value.get(key):
                return str(value[key])[:300]
    return str(value)[:300]


class SupabaseLedger:
    kind = "supabase"

    def __init__(
        self,
        url: str,
        service_role_key: str,
        *,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sleeper = sleeper
        self._client = httpx.Client(
            base_url=url.rstrip("/") + "/rest/v1",
            timeout=timeout,
            transport=transport,
            headers={
                "Accept": "application/json",
                "apikey": service_role_key,
                "Authorization": f"Bearer {service_role_key}",
                "User-Agent": "astrolabe-core/0.1",
            },
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                last_error = exc
                if attempt == 0:
                    self._sleeper(1.0)
                    continue
                raise SupabaseUnavailableError(
                    "Supabaseへ接続できない。SUPABASE_URLとネットワークを確認する"
                ) from exc

            if response.status_code in (401, 403):
                raise SupabaseAuthError(
                    "Supabase認証に失敗した。SUPABASE_URLと"
                    "SUPABASE_SERVICE_ROLE_KEYの設定を確認する"
                )
            if response.status_code >= 500:
                last_error = httpx.HTTPStatusError(
                    f"Supabase一時障害: HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )
                if attempt == 0:
                    self._sleeper(1.0)
                    continue
                raise SupabaseUnavailableError(
                    f"Supabaseが一時障害から復旧しない: HTTP {response.status_code}"
                ) from last_error
            if response.status_code >= 400:
                detail = _error_detail(response)
                migration_hint = (
                    "。supabase/migrationsの適用状態を確認する"
                    if response.status_code in (404, 409)
                    else ""
                )
                raise LedgerBackendError(
                    f"Supabase APIがHTTP {response.status_code}を返した: {detail}"
                    f"{migration_hint}"
                )
            return response
        raise SupabaseUnavailableError(f"Supabase呼び出し失敗: {last_error}")

    def _get_all(
        self,
        table: str,
        *,
        select: str = "*",
        order: str,
        extra_params: dict[str, str] | None = None,
    ) -> list[dict]:
        rows: list[dict] = []
        page_size = 1000
        offset = 0
        while True:
            params = {"select": select, "order": order, **(extra_params or {})}
            response = self._request(
                "GET",
                f"/{table}",
                params=params,
                headers={"Range": f"{offset}-{offset + page_size - 1}"},
            )
            page = response.json()
            if not isinstance(page, list):
                raise LedgerBackendError(f"Supabase {table}応答が配列ではない")
            rows.extend(page)
            if len(page) < page_size:
                return rows
            offset += page_size

    def append_events(self, rows: list[dict], *, preserve_ids: bool = False) -> list[int]:
        if not rows:
            return []
        if preserve_ids:
            self._request("POST", "/rpc/astrolabe_import_events", json={"p_events": rows})
            return [int(row["id"]) for row in rows]
        response = self._request(
            "POST",
            "/events",
            params={"select": "id"},
            headers={"Prefer": "return=representation"},
            json=rows,
        )
        value = response.json()
        if not isinstance(value, list) or len(value) != len(rows):
            raise LedgerBackendError("Supabase events追記の応答件数が一致しない")
        return [int(row["id"]) for row in value]

    def load_events(self) -> list[dict]:
        rows = self._get_all(
            "events",
            select="id,ts,type,concept_id,payload",
            order="id.asc",
        )
        return [
            {
                "id": int(row["id"]),
                "ts": _normalize_timestamp(row["ts"]),
                "type": row["type"],
                "concept_id": row.get("concept_id"),
                "payload": row.get("payload") or {},
            }
            for row in rows
        ]

    def replace_derived(self, concepts: list[dict], edges: list[dict]) -> tuple[int, int]:
        response = self._request(
            "POST",
            "/rpc/astrolabe_replace_derived",
            json={"p_concepts": concepts, "p_edges": edges},
        )
        result = response.json()
        return int(result["concepts"]), int(result["edges"])

    def get_profile(self) -> dict:
        response = self._request(
            "GET",
            "/profile",
            params={
                "select": "interests,goals,background,time_budget",
                "id": "eq.1",
                "limit": "1",
            },
        )
        rows = response.json()
        if not rows:
            return {"interests": {}, "goals": "", "background": "", "time_budget": ""}
        row = rows[0]
        return {
            "interests": row.get("interests") or {},
            "goals": row.get("goals") or "",
            "background": row.get("background") or "",
            "time_budget": row.get("time_budget") or "",
        }

    def record_interview(self, profile: dict, event_rows: list[dict]) -> list[int]:
        response = self._request(
            "POST",
            "/rpc/astrolabe_record_interview",
            json={"p_profile": profile, "p_events": event_rows},
        )
        value = response.json()
        if not isinstance(value, list):
            raise LedgerBackendError("Supabase面談記録の応答が配列ではない")
        return [int(event_id) for event_id in value]

    def save_daily_report(
        self,
        date: str,
        items: dict,
        map_delta_text: str,
        html_path: str | None,
    ) -> None:
        self._request(
            "POST",
            "/daily_reports",
            params={"on_conflict": "date"},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json={
                "date": date,
                "items": items,
                "map_delta_text": map_delta_text,
                "html_path": html_path,
            },
        )

    @staticmethod
    def _report(row: dict) -> dict:
        return {
            "date": row["date"],
            "items": row["items"],
            "map_delta_text": row.get("map_delta_text") or "",
            "html_path": row.get("html_path"),
        }

    def get_daily_report(self, date: str | None = None) -> dict | None:
        params = {"select": "date,items,map_delta_text,html_path", "limit": "1"}
        if date:
            params["date"] = f"eq.{date}"
        else:
            params["order"] = "date.desc"
        response = self._request("GET", "/daily_reports", params=params)
        rows = response.json()
        return None if not rows else self._report(rows[0])

    def list_daily_reports(self) -> list[dict]:
        rows = self._get_all(
            "daily_reports",
            select="date,items,map_delta_text,html_path",
            order="date.asc",
        )
        return [self._report(row) for row in rows]

    def list_concepts(self) -> list[dict]:
        rows = self._get_all("concepts", order="id.asc")
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "kind": row["kind"],
                "status": row["status"],
                "confidence": float(row["confidence"]),
                "summary": row.get("summary") or "",
                "source_urls": row.get("source_urls") or [],
                "first_seen": _normalize_timestamp(row.get("first_seen")),
                "last_touched": _normalize_timestamp(row.get("last_touched")),
            }
            for row in rows
        ]

    def list_edges(self) -> list[dict]:
        rows = self._get_all("edges", order="src.asc,dst.asc,type.asc")
        return [
            {
                "src": row["src"],
                "dst": row["dst"],
                "type": row["type"],
                "weight": float(row["weight"]),
                "created_by": row.get("created_by"),
                "created_at": _normalize_timestamp(row.get("created_at")),
            }
            for row in rows
        ]

    def list_tasks(self) -> list[dict]:
        return self._get_all("tasks", order="id.asc")

    def import_state(self, profile: dict, tasks: list[dict], reports: list[dict]) -> None:
        self._request(
            "POST",
            "/rpc/astrolabe_import_state",
            json={"p_profile": profile, "p_tasks": tasks, "p_reports": reports},
        )

    def save_llm_usage(self, usage_date: str, run_id: str, usage: dict) -> None:
        rows = [
            {
                "usage_date": usage_date,
                "run_id": run_id,
                "model_role": model_role,
                "tokens": int(values.get("used", 0)),
            }
            for model_role, values in sorted(usage.items())
        ]
        if rows:
            self._request(
                "POST",
                "/llm_usage",
                params={"on_conflict": "usage_date,run_id,model_role"},
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                json=rows,
            )
