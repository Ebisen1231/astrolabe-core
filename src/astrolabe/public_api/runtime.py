"""公開Functionが共有する設定・認証・台帳runtime。会話状態は保持しない。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from astrolabe.config import ConfigError, load_config
from astrolabe.ledger import store
from astrolabe.public_api.auth import SupabaseAuthVerifier
from astrolabe.tutor.runtime import LocalTutorRuntime, open_configured_ledger

PUBLIC_LLM_TIMEOUT_SECONDS = 100.0


class PublicApiConfigError(RuntimeError):
    """値を含めず、不足した環境変数名だけを示す。"""


@dataclass(frozen=True)
class PublicApiSettings:
    allowed_origin: str
    supabase_anon_key: str
    owner_user_id: str

    @classmethod
    def from_env(cls) -> PublicApiSettings:
        required = {
            "ASTROLABE_ALLOWED_ORIGIN": os.environ.get("ASTROLABE_ALLOWED_ORIGIN", "").strip(),
            "SUPABASE_ANON_KEY": os.environ.get("SUPABASE_ANON_KEY", "").strip(),
            "ASTROLABE_OWNER_USER_ID": os.environ.get("ASTROLABE_OWNER_USER_ID", "").strip(),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise PublicApiConfigError("環境変数が未設定: " + ", ".join(missing))
        origin = required["ASTROLABE_ALLOWED_ORIGIN"]
        if not origin.startswith("https://") or origin.endswith("/"):
            raise PublicApiConfigError(
                "ASTROLABE_ALLOWED_ORIGINは末尾slashなしのhttps originを指定する"
            )
        return cls(
            allowed_origin=origin,
            supabase_anon_key=required["SUPABASE_ANON_KEY"],
            owner_user_id=required["ASTROLABE_OWNER_USER_ID"],
        )


class PublicApiRuntime:
    def __init__(self) -> None:
        try:
            self.config = load_config(require_ledger=True, require_flagship=True)
        except ConfigError as exc:
            raise PublicApiConfigError(str(exc)) from exc
        if self.config.backend != "supabase":
            raise PublicApiConfigError("ASTROLABE_BACKEND=supabaseが必要")
        self.tutor = LocalTutorRuntime(
            self.config,
            llm_timeout=PUBLIC_LLM_TIMEOUT_SECONDS,
            retry_timeouts=False,
            llm_deadline_seconds=PUBLIC_LLM_TIMEOUT_SECONDS,
        )

    def turn(self, history: list[dict], session_id: str) -> dict:
        return self.tutor.turn(history, session_id)

    def list_tasks(self) -> list[dict]:
        return self.tutor.list_tasks()

    def complete_task(self, task_id: int, evidence: str, confidence_delta: float) -> dict:
        return self.tutor.complete_task(task_id, evidence, confidence_delta)

    def get_artifact(self, artifact_key: str) -> dict | None:
        ledger = open_configured_ledger(self.config)
        try:
            row = store.get_published_artifact(ledger, artifact_key)
            return None if row is None else row["payload"]
        finally:
            ledger.close()


@dataclass(frozen=True)
class PublicApiServices:
    settings: PublicApiSettings
    auth: SupabaseAuthVerifier
    runtime: PublicApiRuntime


_SERVICES: PublicApiServices | None = None


def get_services() -> PublicApiServices:
    global _SERVICES
    if _SERVICES is None:
        settings = PublicApiSettings.from_env()
        runtime = PublicApiRuntime()
        assert runtime.config.supabase_url is not None
        _SERVICES = PublicApiServices(
            settings=settings,
            auth=SupabaseAuthVerifier(
                runtime.config.supabase_url,
                settings.supabase_anon_key,
                settings.owner_user_id,
            ),
            runtime=runtime,
        )
    return _SERVICES
