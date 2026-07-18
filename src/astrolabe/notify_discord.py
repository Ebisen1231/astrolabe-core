"""最新のHTML朝報告をDiscord webhookへ通知する(M1)。"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

import httpx


def build_discord_content(report: dict) -> str:
    """Discordのcontent上限内で題目とトークン使用量を整形する。"""
    topics = report.get("items", {}).get("topics", [])
    meta = report.get("items", {}).get("meta", {})
    usage = meta.get("usage", {})
    lines = [f"Astrolabe 朝の観測報告 — {report.get('date', '')}"]
    if topics:
        lines.append("今日の提案: " + " / ".join(str(t.get("name", "")) for t in topics[:5]))
    else:
        lines.append("今日の提案: 新規トピックなし")
    usage_parts = []
    for key in ("mini", "flagship"):
        values = usage.get(key, {})
        if values:
            usage_parts.append(
                f"{key} {int(values.get('used', 0)):,}/{int(values.get('cap', 0)):,} tokens"
            )
    if usage_parts:
        lines.append("使用量: " + " | ".join(usage_parts))
    return "\n".join(lines)[:1900]


def send_discord_report(
    webhook_url: str,
    report: dict,
    html_path: Path,
    *,
    timeout: float = 20.0,
    transport: httpx.BaseTransport | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    logger: logging.Logger | None = None,
) -> bool:
    """HTML添付で通知する。失敗は警告してFalseを返し、朝ジョブを落とさない。"""
    logger = logger or logging.getLogger("astrolabe.discord")
    if not webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL未設定。Discord通知をスキップ")
        return False
    if not html_path.is_file():
        logger.warning("Discord添付HTMLが見つからないため通知をスキップ: %s", html_path.name)
        return False

    payload = json.dumps({"content": build_discord_content(report)}, ensure_ascii=False)
    last_error: Exception | None = None
    with httpx.Client(timeout=timeout, transport=transport) as client:
        for attempt in range(2):
            try:
                with html_path.open("rb") as html_file:
                    response = client.post(
                        webhook_url,
                        data={"payload_json": payload},
                        files={"files[0]": (html_path.name, html_file, "text/html")},
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"一時的なDiscordエラー: {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return True
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_error = exc
                retryable = isinstance(exc, httpx.TransportError)
                if isinstance(exc, httpx.HTTPStatusError):
                    retryable = exc.response.status_code == 429 or exc.response.status_code >= 500
                if attempt == 0 and retryable:
                    sleeper(2.0)
                    continue
                break
    logger.warning("Discord通知に失敗。朝ジョブは継続: %s", last_error)
    return False
