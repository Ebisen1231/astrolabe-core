import json
from pathlib import Path

import pytest

from astrolabe.public_api.runtime import (
    PUBLIC_LLM_TIMEOUT_SECONDS,
    PublicApiConfigError,
    PublicApiSettings,
)

ROOT = Path(__file__).resolve().parent.parent


def test_vercel_duration_leaves_margin_after_llm_deadline():
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert config["functions"]["api/index.py"]["maxDuration"] == 120
    assert PUBLIC_LLM_TIMEOUT_SECONDS == 100


def test_vercel_python_entrypoint_is_explicit():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '[tool.vercel]\nentrypoint = "api.index:handler"' in pyproject


def test_public_origin_requires_exact_https_without_trailing_slash(monkeypatch):
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.setenv("ASTROLABE_OWNER_USER_ID", "owner")
    for invalid in ("*", "http://example.com", "https://example.com/"):
        monkeypatch.setenv("ASTROLABE_ALLOWED_ORIGIN", invalid)
        with pytest.raises(PublicApiConfigError, match="ASTROLABE_ALLOWED_ORIGIN"):
            PublicApiSettings.from_env()
    monkeypatch.setenv("ASTROLABE_ALLOWED_ORIGIN", "https://example.com")
    assert PublicApiSettings.from_env().allowed_origin == "https://example.com"
