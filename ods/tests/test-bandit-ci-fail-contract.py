from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT.parent / ".github/workflows/autonomous-code-scanner.yml").read_text(encoding="utf-8")


def test_bandit_findings_fail_the_security_scan():
    assert "BANDIT_STATUS=$?" in WORKFLOW
    assert 'if [ "$BANDIT_STATUS" -ne 0 ]; then' in WORKFLOW
    assert 'exit "$BANDIT_STATUS"' in WORKFLOW
    assert "|| true  # Don't fail on findings" not in WORKFLOW

