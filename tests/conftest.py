from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

_ASTROLABE_ENV_VARS = (
    "OPENAI_API_KEY",
    "ASTROLABE_LEDGER_PATH",
    "ASTROLABE_MODEL_MINI",
    "ASTROLABE_MODEL_FLAGSHIP",
    "ASTROLABE_RSS_FEEDS",
    "ASTROLABE_CACHE_DIR",
    "ASTROLABE_MAX_MINI_TOKENS",
    "ASTROLABE_MAX_FLAGSHIP_TOKENS",
    "ASTROLABE_FIXTURES_DIR",
    "ASTROLABE_LEDGER_REPOSITORY",
    "GITHUB_TOKEN",
    "DISCORD_WEBHOOK_URL",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """テストを環境から隔離する。実台帳・実キーには決して触れない。"""
    for name in _ASTROLABE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def ledger(tmp_path):
    """一時DB上の初期化済み台帳接続。"""
    from astrolabe.ledger import db

    conn = db.init_db(tmp_path / "ledger.db")
    yield conn
    conn.close()
