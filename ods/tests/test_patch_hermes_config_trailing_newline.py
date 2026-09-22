"""Regression test: verify patch-hermes-config.py enforces trailing newlines on written YAML."""

import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "patch-hermes-config.py"


def test_patch_hermes_enforces_trailing_newline():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = Path(tmpdir) / "config.yaml"
        # Input without trailing newline
        cfg.write_text("model:\n  default: old-model", encoding="utf-8")

        res = subprocess.run(
            ["python3", str(SCRIPT), str(cfg), "--model", "new-model"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"patch-hermes-config failed: {res.stderr}"
        assert res.stdout.strip() == "changed"

        patched = cfg.read_text(encoding="utf-8")
        assert patched.endswith("\n"), "Patched config missing trailing newline"
        assert 'default: "new-model"' in patched

        # Idempotency check: running again on identical config returns unchanged
        res2 = subprocess.run(
            ["python3", str(SCRIPT), str(cfg), "--model", "new-model"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res2.returncode == 0
        assert res2.stdout.strip() == "unchanged"


if __name__ == "__main__":
    test_patch_hermes_enforces_trailing_newline()
    print("test_patch_hermes_config_trailing_newline: OK")
