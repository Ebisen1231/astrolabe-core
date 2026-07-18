"""coreに置くledger workflow参照コピーの重要な安全設定を固定する。"""

from pathlib import Path


def test_ledger_workflow_reference_has_m1_schedule_and_minimal_permissions():
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
    assert "notify-discord" in text
    assert "LEDGER_ISSUES_TOKEN" not in text
    assert "DEPLOY_KEY" not in text
