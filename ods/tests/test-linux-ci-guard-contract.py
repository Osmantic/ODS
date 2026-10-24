from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT.parent / ".github/workflows/test-linux.yml").read_text(encoding="utf-8")


def test_linux_integration_runs_bind_and_bash32_guards():
    assert "- name: Bind Address and Bash 3.2 Guards" in WORKFLOW
    assert "bash tests/test-bind-address-sweep.sh" in WORKFLOW
    assert "bash tests/test-bash32-guards.sh" in WORKFLOW

