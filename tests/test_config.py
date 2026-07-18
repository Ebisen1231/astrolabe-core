from pathlib import Path

import pytest

from astrolabe.config import (
    DEFAULT_MAX_FLAGSHIP_TOKENS,
    DEFAULT_MAX_MINI_TOKENS,
    DEFAULT_RSS_FEEDS,
    ConfigError,
    load_config,
)


def test_dry_run_requires_nothing():
    config = load_config(env={})
    assert config.ledger_path is None
    assert config.api_key is None
    assert config.max_mini_tokens == DEFAULT_MAX_MINI_TOKENS
    assert config.max_flagship_tokens == DEFAULT_MAX_FLAGSHIP_TOKENS
    assert config.rss_feeds == DEFAULT_RSS_FEEDS


def test_ledger_required_without_fallback():
    """非dry-runでは ASTROLABE_LEDGER_PATH 必須。~/.astrolabe 等へのフォールバックはしない。"""
    with pytest.raises(ConfigError, match="ASTROLABE_LEDGER_PATH"):
        load_config(require_ledger=True, env={})


def test_supabase_backend_requires_both_credentials_without_sqlite_fallback():
    with pytest.raises(ConfigError) as exc:
        load_config(require_ledger=True, env={"ASTROLABE_BACKEND": "supabase"})
    message = str(exc.value)
    assert "SUPABASE_URL" in message
    assert "SUPABASE_SERVICE_ROLE_KEY" in message
    assert "ASTROLABE_LEDGER_PATH" not in message


def test_supabase_backend_configuration():
    config = load_config(
        require_ledger=True,
        env={
            "ASTROLABE_BACKEND": "supabase",
            "SUPABASE_URL": "https://project.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-key",
            "ASTROLABE_ARTIFACT_ROOT": "/tmp/artifacts",
            "GITHUB_RUN_ID": "123",
        },
    )
    assert config.backend == "supabase"
    assert config.ledger_path is None
    assert config.artifact_root == Path("/tmp/artifacts")
    assert config.run_id == "123"


def test_invalid_backend_is_rejected():
    with pytest.raises(ConfigError, match="ASTROLABE_BACKEND"):
        load_config(env={"ASTROLABE_BACKEND": "memory"})


def test_date_override_requires_exact_opt_in():
    assert not load_config(env={"ASTROLABE_ALLOW_DATE_OVERRIDE": "true"}).allow_date_override
    assert load_config(env={"ASTROLABE_ALLOW_DATE_OVERRIDE": "1"}).allow_date_override


def test_api_required_lists_all_missing():
    with pytest.raises(ConfigError) as exc:
        load_config(require_api=True, env={})
    message = str(exc.value)
    for name in ("OPENAI_API_KEY", "ASTROLABE_MODEL_MINI", "ASTROLABE_MODEL_FLAGSHIP"):
        assert name in message


def test_env_values_are_used_exactly():
    """設定優先順位の回帰テスト: 環境変数の値がそのまま使われる。"""
    env = {
        "ASTROLABE_LEDGER_PATH": "/tmp/l.db",
        "OPENAI_API_KEY": "sk-test",
        "ASTROLABE_MODEL_MINI": "mini-model",
        "ASTROLABE_MODEL_FLAGSHIP": "flagship-model",
        "ASTROLABE_RSS_FEEDS": "https://a/feed, https://b/feed",
        "ASTROLABE_CACHE_DIR": "/tmp/cache",
        "ASTROLABE_MAX_MINI_TOKENS": "1000",
        "ASTROLABE_MAX_FLAGSHIP_TOKENS": "200",
    }
    config = load_config(require_ledger=True, require_api=True, env=env)
    assert config.ledger_path == Path("/tmp/l.db")
    assert config.api_key == "sk-test"
    assert config.model_mini == "mini-model"
    assert config.model_flagship == "flagship-model"
    assert config.rss_feeds == ("https://a/feed", "https://b/feed")
    assert config.cache_dir == Path("/tmp/cache")
    assert config.max_mini_tokens == 1000
    assert config.max_flagship_tokens == 200


def test_invalid_token_cap():
    with pytest.raises(ConfigError, match="ASTROLABE_MAX_MINI_TOKENS"):
        load_config(env={"ASTROLABE_MAX_MINI_TOKENS": "abc"})
    with pytest.raises(ConfigError, match="ASTROLABE_MAX_MINI_TOKENS"):
        load_config(env={"ASTROLABE_MAX_MINI_TOKENS": "-5"})
