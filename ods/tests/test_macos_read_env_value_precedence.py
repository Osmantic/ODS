"""macOS read_env_value must honor dotenv last-wins precedence.

Both macOS copies of read_env_value (env-generator.sh and ods-macos.sh)
returned the FIRST matching line, while the Linux CLI reader (_env_get_raw in
ods-cli) and every dotenv consumer resolve duplicates last-wins. With a .env
that contains "KEY=" followed by "KEY=real", the macOS readers saw the empty
first line, so every "regenerate when empty" backfill (SHIELD_API_KEY,
HERMES_DASHBOARD_SESSION_TOKEN, ODS_SESSION_SECRET, ...) fired and
upsert_env_value rewrote ALL duplicate lines — clobbering the real key.
"""

import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = [
    REPO_ROOT / "installers/macos/lib/env-generator.sh",
    REPO_ROOT / "installers/macos/ods-macos.sh",
]


def extract_helper_func(script_path: Path) -> str:
    lines = script_path.read_text().splitlines()
    start = -1
    brace_count = 0
    collected = []
    for i, line in enumerate(lines):
        if line.startswith("read_env_value() {"):
            start = i
            brace_count = 0
        if start != -1:
            collected.append(line)
            brace_count += line.count("{") - line.count("}")
            if brace_count == 0:
                break
    return "\n".join(collected)


@pytest.mark.parametrize("script_path", SCRIPTS, ids=lambda p: p.name)
def test_read_env_value_uses_last_duplicate(script_path: Path):
    helper_code = extract_helper_func(script_path)
    assert helper_code, f"could not extract read_env_value from {script_path}"

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env_file = root / ".env"
        env_file.write_text(
            "SHIELD_API_KEY=\n"
            "OTHER=untouched\n"
            "SHIELD_API_KEY=real-key-123\n"
            "EMPTY_FIRST=\n"
            "EMPTY_FIRST=last-wins\n"
        )

        runner = root / "run_test.sh"
        runner.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"{helper_code}\n"
            f'got="$(read_env_value "{env_file}" "SHIELD_API_KEY")"\n'
            '[[ "$got" == "real-key-123" ]] || { echo "expected last duplicate, got: $got"; exit 2; }\n'
            f'got="$(read_env_value "{env_file}" "EMPTY_FIRST")"\n'
            '[[ "$got" == "last-wins" ]] || { echo "expected last-wins, got: $got"; exit 3; }\n'
            f'got="$(read_env_value "{env_file}" "MISSING_KEY")"\n'
            '[[ -z "$got" ]] || { echo "missing key should read empty, got: $got"; exit 4; }\n'
            f'got="$(read_env_value "{env_file}" "OTHER")"\n'
            '[[ "$got" == "untouched" ]] || { echo "single key broke, got: $got"; exit 5; }\n'
        )
        runner.chmod(0o755)

        result = subprocess.run(
            ["bash", str(runner)], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize("script_path", SCRIPTS, ids=lambda p: p.name)
def test_backfill_guard_does_not_clobber_real_key(script_path: Path):
    """Simulate the SHIELD_API_KEY backfill check on a duplicated .env."""
    helper_code = extract_helper_func(script_path)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env_file = root / ".env"
        env_file.write_text("SHIELD_API_KEY=\nSHIELD_API_KEY=real-key-123\n")

        runner = root / "run_test.sh"
        runner.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"{helper_code}\n"
            # Mirrors the backfill guard at ods-macos.sh:1066 and
            # env-generator.sh:317/322 — regenerate only when the resolved
            # value is empty.
            f'if [[ -z "$(read_env_value "{env_file}" "SHIELD_API_KEY")" ]]; then\n'
            '    echo "guard fired on a live key"; exit 2;\n'
            "fi\n"
        )
        runner.chmod(0o755)

        result = subprocess.run(
            ["bash", str(runner)], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stderr + result.stdout
