"""最新のHTML朝報告をDiscord webhookへ通知する(M1)。"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

import httpx

DESCRIPTION_LIMIT = 4096
FIELD_NAME_LIMIT = 256
FIELD_VALUE_LIMIT = 1024
FOOTER_LIMIT = 2048
SUMMARY_LIMIT = 100
MAX_FIELDS = 25


def _truncate(text: str, limit: int) -> str:
    """Discordの各文字数上限内へ、末尾の省略記号を含めて収める。"""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _usage_text(report: dict) -> str:
    """embed footer用のトークン使用量を決定的に整形する。"""
    meta = report.get("items", {}).get("meta", {})
    usage = meta.get("usage", {})
    parts = []
    for key in ("mini", "flagship"):
        values = usage.get(key, {})
        if values:
            parts.append(
                f"{key} {int(values.get('used', 0)):,}/{int(values.get('cap', 0)):,} tokens"
            )
    return "使用量: " + (" | ".join(parts) if parts else "記録なし")


def build_discord_embed(report: dict) -> dict:
    """日次報告をDiscord embedの上限内で決定的に整形する。"""
    topics = report.get("items", {}).get("topics", [])
    date = str(report.get("date", ""))
    map_delta = str(report.get("map_delta_text", ""))
    description_parts = [date] if date else []
    if map_delta:
        description_parts.append(f"今日の変化: {map_delta}")
    if not topics:
        description_parts.append("今日の提案: 新規トピックなし")

    fields = []
    for topic in topics[:MAX_FIELDS]:
        name = _truncate(str(topic.get("name", "無題")) or "無題", FIELD_NAME_LIMIT)
        summary = str(topic.get("summary", ""))[:SUMMARY_LIMIT]
        source_urls = topic.get("source_urls", [])
        source_url = str(source_urls[0]) if isinstance(source_urls, list) and source_urls else ""
        value = summary
        if source_url:
            value = f"{value}\n{source_url}" if value else source_url
        fields.append(
            {
                "name": name,
                "value": _truncate(value or "要約なし", FIELD_VALUE_LIMIT),
                "inline": False,
            }
        )

    return {
        "title": "Astrolabe 朝の観測報告",
        "description": _truncate("\n".join(description_parts), DESCRIPTION_LIMIT),
        "fields": fields,
        "footer": {"text": _truncate(_usage_text(report), FOOTER_LIMIT)},
    }


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

    payload = json.dumps({"embeds": [build_discord_embed(report)]}, ensure_ascii=False)
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
