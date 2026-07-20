"""coreに置くledger workflow参照コピーの重要な安全設定を固定する。"""

from pathlib import Path


def test_ledger_workflow_reference_has_m3_supabase_snapshot_and_minimal_permissions():
    path = Path(__file__).resolve().parent.parent / "docs" / "morning.workflow.yml"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# REFERENCE COPY ONLY")
    assert 'cron: "30 21 * * *"' in text
    assert "timeout-minutes: 10" in text
    assert "contents: write" in text
    assert "issues: write" in text
    assert "repository: Ebisen1231/astrolabe-core" in text
    assert "feedback-import" in text
    assert "feedback-close" in text
    assert "ASTROLABE_BACKEND: supabase" in text
    assert "SUPABASE_URL: ${{ secrets.SUPABASE_URL }}" in text
    assert "SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}" in text
    assert "ASTROLABE_ALLOW_DATE_OVERRIDE" not in text
    assert "astrolabe export" in text
    assert "astrolabe publish-exports" in text
    assert "Publish UI data to Supabase" in text
    assert "astrolabe snapshot" in text
    assert "snapshots/ reports/ exports/" in text
    assert "git add -- astrolabe.db" not in text
    assert "notify-discord" in text
    assert "LEDGER_ISSUES_TOKEN" not in text
    assert "DEPLOY_KEY" not in text
    assert "group: astrolabe-ledger-write" in text


def test_weekly_review_reference_is_draft_only_private_and_serialized():
    path = Path(__file__).resolve().parent.parent / "docs" / "weekly-review.workflow.yml"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# REFERENCE COPY ONLY")
    assert 'cron: "30 1 * * 1"' in text
    assert "workflow_dispatch" in text
    assert "group: astrolabe-ledger-write" in text
    assert "cancel-in-progress: false" in text
    assert "issues: write" in text
    assert "contents: write" in text
    assert "astrolabe weekly-review" in text
    assert "astrolabe snapshot" in text
    assert "DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}" in text
    assert "codex exec" not in text
    assert "CODEX" not in text
    assert "pull_request" not in text
    assert "ASTROLABE_ALLOW_DATE_OVERRIDE" not in text
