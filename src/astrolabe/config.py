"""環境変数からの設定読み込み。

方針:
- 非dry-run実行では ASTROLABE_LEDGER_PATH を必須とする。暗黙のフォールバックパスは持たない。
- .env ローダは使わず、プロセス環境変数だけを読む(モック向け設定が実キーに
  上書きされる事故の温床を断つため)。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_MINI_TOKENS = 500_000
DEFAULT_MAX_FLAGSHIP_TOKENS = 70_000
DEFAULT_ARXIV_CATEGORIES: tuple[str, ...] = ("cs.CL", "cs.AI", "cs.LG")
DEFAULT_RSS_FEEDS: tuple[str, ...] = (
    "https://simonwillison.net/atom/everything/",
    "https://huggingface.co/blog/feed.xml",
    "https://bair.berkeley.edu/blog/feed.xml",
)
DEFAULT_LEDGER_REPOSITORY = "Ebisen1231/astrolabe-ledger"


class ConfigError(Exception):
    """設定不備。メッセージに不足している環境変数名を含める。"""


@dataclass(frozen=True)
class Config:
    ledger_path: Path | None
    api_key: str | None
    model_mini: str | None
    model_flagship: str | None
    rss_feeds: tuple[str, ...]
    arxiv_categories: tuple[str, ...]
    cache_dir: Path
    max_mini_tokens: int
    max_flagship_tokens: int
    ledger_repository: str
    github_token: str | None


def _int_env(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as e:
        raise ConfigError(f"{name} は整数で指定する: {raw!r}") from e
    if value <= 0:
        raise ConfigError(f"{name} は正の整数で指定する: {raw!r}")
    return value


def load_config(
    *,
    require_ledger: bool = False,
    require_api: bool = False,
    env: Mapping[str, str] | None = None,
) -> Config:
    """環境変数を検証して Config を返す。

    require_ledger: 台帳を触るコマンド(init/interview/morning/report)で True。
    require_api: 実APIを呼ぶコマンド(morning 非dry-run / canary)で True。
    不足があれば ConfigError に不足変数名を列挙する。フォールバックはしない。
    """
    env = os.environ if env is None else env
    missing: list[str] = []

    ledger_raw = env.get("ASTROLABE_LEDGER_PATH", "").strip()
    if require_ledger and not ledger_raw:
        missing.append("ASTROLABE_LEDGER_PATH")

    api_key = env.get("OPENAI_API_KEY", "").strip() or None
    model_mini = env.get("ASTROLABE_MODEL_MINI", "").strip() or None
    model_flagship = env.get("ASTROLABE_MODEL_FLAGSHIP", "").strip() or None
    if require_api:
        if not api_key:
            missing.append("OPENAI_API_KEY")
        if not model_mini:
            missing.append("ASTROLABE_MODEL_MINI")
        if not model_flagship:
            missing.append("ASTROLABE_MODEL_FLAGSHIP")

    if missing:
        raise ConfigError(
            "環境変数が未設定: " + ", ".join(missing) + "(暗黙のフォールバックは行わない)"
        )

    feeds_raw = env.get("ASTROLABE_RSS_FEEDS", "").strip()
    rss_feeds = (
        tuple(u.strip() for u in feeds_raw.split(",") if u.strip())
        if feeds_raw
        else DEFAULT_RSS_FEEDS
    )
    cache_raw = env.get("ASTROLABE_CACHE_DIR", "").strip()
    cache_dir = Path(cache_raw) if cache_raw else Path.home() / ".astrolabe" / "cache"

    return Config(
        ledger_path=Path(ledger_raw) if ledger_raw else None,
        api_key=api_key,
        model_mini=model_mini,
        model_flagship=model_flagship,
        rss_feeds=rss_feeds,
        arxiv_categories=DEFAULT_ARXIV_CATEGORIES,
        cache_dir=cache_dir,
        max_mini_tokens=_int_env(env, "ASTROLABE_MAX_MINI_TOKENS", DEFAULT_MAX_MINI_TOKENS),
        max_flagship_tokens=_int_env(
            env, "ASTROLABE_MAX_FLAGSHIP_TOKENS", DEFAULT_MAX_FLAGSHIP_TOKENS
        ),
        ledger_repository=(
            env.get("ASTROLABE_LEDGER_REPOSITORY", "").strip()
            or DEFAULT_LEDGER_REPOSITORY
        ),
        github_token=env.get("GITHUB_TOKEN", "").strip() or None,
    )
