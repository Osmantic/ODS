from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT.parent / ".github/workflows/test-linux.yml").read_text(encoding="utf-8")


def test_linux_ci_runs_secret_security_audit():
    assert "- name: Secret Security Audit" in WORKFLOW
    assert "run: bash tests/test-secret-security.sh" in WORKFLOW

