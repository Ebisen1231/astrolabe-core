"""公開anonキーだけではSupabase台帳を読めないことを実環境で検証する。"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

PROTECTED_TABLES = (
    "events",
    "concepts",
    "edges",
    "tasks",
    "profile",
    "daily_reports",
    "llm_usage",
    "published_artifacts",
)


class AnonGuardError(RuntimeError):
    """anonで読める表、未適用migration、接続不能がある。"""


def verify_anon_denied(
    supabase_url: str,
    anon_key: str,
    *,
    timeout: float = 10.0,
    transport: httpx.BaseTransport | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    api_root = supabase_url.rstrip("/")
    if not api_root.endswith("/rest/v1"):
        api_root += "/rest/v1"
    statuses: dict[str, int] = {}
    with httpx.Client(
        base_url=api_root,
        timeout=timeout,
        transport=transport,
        headers={"apikey": anon_key, "Authorization": f"Bearer {anon_key}"},
    ) as client:
        for table in PROTECTED_TABLES:
            response = None
            for attempt in range(2):
                try:
                    response = client.get(f"/{table}", params={"select": "*", "limit": "1"})
                except httpx.TransportError as exc:
                    if attempt == 0:
                        sleeper(0.5)
                        continue
                    raise AnonGuardError("Supabaseへ接続できない") from exc
                if response.status_code >= 500 and attempt == 0:
                    sleeper(0.5)
                    continue
                break
            assert response is not None
            statuses[table] = response.status_code
            if response.status_code not in (401, 403):
                raise AnonGuardError(
                    f"anon拒否を確認できないtable={table} status={response.status_code}"
                )
    return statuses
