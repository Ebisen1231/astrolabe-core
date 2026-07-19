"""Supabase Authのuser endpointによるBearer検証。JWT依存は追加しない。"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx


class AuthenticationError(RuntimeError):
    """外部には理由を区別せず401へ畳む認証失敗。"""


class AuthUnavailableError(RuntimeError):
    """認証基盤への接続失敗。外部には定型503だけを返す。"""


class SupabaseAuthVerifier:
    def __init__(
        self,
        supabase_url: str,
        anon_key: str,
        owner_user_id: str,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._owner_user_id = owner_user_id
        self._sleeper = sleeper
        base_url = supabase_url.rstrip("/")
        if base_url.endswith("/rest/v1"):
            base_url = base_url[: -len("/rest/v1")]
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={"apikey": anon_key, "User-Agent": "astrolabe-core/0.1"},
        )

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _bearer(authorization: str | None) -> str:
        parts = (authorization or "").split()
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
            raise AuthenticationError("unauthorized")
        return parts[1]

    def verify(self, authorization: str | None) -> str:
        token = self._bearer(authorization)
        for attempt in range(2):
            try:
                response = self._client.get(
                    "/auth/v1/user", headers={"Authorization": f"Bearer {token}"}
                )
            except httpx.TransportError as exc:
                if attempt == 0:
                    self._sleeper(0.5)
                    continue
                raise AuthUnavailableError("auth unavailable") from exc
            if response.status_code in (401, 403):
                raise AuthenticationError("unauthorized")
            if response.status_code >= 500:
                if attempt == 0:
                    self._sleeper(0.5)
                    continue
                raise AuthUnavailableError("auth unavailable")
            if response.status_code != 200:
                raise AuthUnavailableError("auth unavailable")
            try:
                user = response.json()
            except ValueError as exc:
                raise AuthUnavailableError("auth unavailable") from exc
            user_id = user.get("id") if isinstance(user, dict) else None
            if not isinstance(user_id, str) or user_id != self._owner_user_id:
                raise AuthenticationError("unauthorized")
            return user_id
        raise AuthUnavailableError("auth unavailable")
