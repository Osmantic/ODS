"""The installer preserves owner-controlled dotenv modes without evaluating them."""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("line,expected", [
    ('ODS_MODE=cloud\n', "cloud"),
    ('ODS_MODE=cloud\r\n', "cloud"),
    ('ODS_MODE="cloud"\n', "cloud"),
    ("ODS_MODE='hybrid'\n", "hybrid"),
    ('  ODS_MODE = lemonade  # existing remote server\r\n', "lemonade"),
    ('ODS_MODE="cloud" # operator comment', "cloud"),
    ("ODS_MODE='hybrid'\t# comment\n", "hybrid"),
    ('ODS_MODE=cloud#not-a-comment\n', "local"),
    ('ODS_MODE="cloud\n', "local"),
    ("ODS_MODE=" + chr(34) + "cloud" + chr(39) + "\n", "local"),
    ('ODS_MODE=cloud\n ODS_MODE = hybrid\n', "local"),
    ('ODS_MODE="$(touch marker)"\n', "local"),
])
def test_implicit_rerun_matches_literal_dotenv_mode(tmp_path, line, expected):
    env_file = tmp_path / ".env"
    env_file.write_bytes(line.encode())
    env_file.chmod(0o600)
    result = subprocess.run([
        "bash", "-c",
        'set -euo pipefail; source "$1"; ods_preserve_existing_install_mode local false "$2"',
        "mode-fixture", str(ROOT / "installers/lib/install-mode.sh"), str(env_file),
    ], cwd=tmp_path, text=True, capture_output=True, timeout=5, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
    assert not (tmp_path / "marker").exists()
    if expected != "local":
        loaded = subprocess.run([
            "bash", "-c",
            'set -euo pipefail; source "$1"; load_env_file "$2"; printf "%s" "$ODS_MODE"',
            "env-fixture", str(ROOT / "lib/safe-env.sh"), str(env_file),
        ], cwd=tmp_path, text=True, capture_output=True, check=True, timeout=5)
        assert loaded.stdout == expected
