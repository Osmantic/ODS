"""Literal key and delimiter-safe updates in macOS upsert_env_value.

Two copies of upsert_env_value ship on macOS — one in the env generator library
and one in the ods-macos CLI — and both must decide "does this key exist?" with
the same literal-prefix test they use to update. A regex probe (grep "^${key}=")
or a sed replacement (s|^${key}=.*|${key}=${value}|) treats dots/brackets in the
key as wildcards (so FOO.BAR clobbers FOO_BAR) and treats | & \\ in the value as
sed syntax (so a URL/token is dropped or corrupted). Exercise both copies.
"""

import subprocess
import tempfile
from pathlib import Path

import pytest


def extract_helper_func(script_path: Path) -> str:
    lines = script_path.read_text().splitlines()
    start = -1
    brace_count = 0
    collected = []
    for i, line in enumerate(lines):
        if line.startswith("upsert_env_value() {"):
            start = i
            brace_count = 0
        if start != -1:
            collected.append(line)
            brace_count += line.count("{") - line.count("}")
            if brace_count == 0:
                break
    return "\n".join(collected)


def _assert_delimiters_and_literal_keys(script_path: Path) -> None:
    helper_code = extract_helper_func(script_path)
    assert helper_code, f"could not extract upsert_env_value from {script_path}"

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env_file = root / ".env"
        env_file.write_text("FOO_BAR=existing_value\n")

        runner = root / "run_test.sh"
        runner.write_text(f"""#!/usr/bin/env bash
set -euo pipefail
{helper_code}

# 1. Setting a key with a regex dot must not overwrite FOO_BAR
upsert_env_value "{env_file}" "FOO.BAR" "new_dot_val"

grep -q "FOO_BAR=existing_value" "{env_file}" || {{ echo "FOO_BAR was clobbered by FOO.BAR"; exit 2; }}

# 2. Setting a value containing pipe and ampersand
upsert_env_value "{env_file}" "PIPE_VAR" "http://host|auth&token"

# 3. In-place update of an existing value with delimiter characters
upsert_env_value "{env_file}" "FOO_BAR" "updated|pipe&amp"
""")
        runner.chmod(0o755)

        result = subprocess.run(["bash", str(runner)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, f"stdout: {result.stdout}, stderr: {result.stderr}"

        content = env_file.read_text()
        assert "FOO_BAR=updated|pipe&amp" in content
        assert "FOO.BAR=new_dot_val" in content
        assert "PIPE_VAR=http://host|auth&token" in content


_REPO_ROOT = Path(__file__).resolve().parents[1]
_UPSERT_SCRIPTS = {
    "env-generator": _REPO_ROOT / "installers/macos/lib/env-generator.sh",
    "ods-macos": _REPO_ROOT / "installers/macos/ods-macos.sh",
}


@pytest.mark.parametrize("script_path", _UPSERT_SCRIPTS.values(), ids=list(_UPSERT_SCRIPTS))
def test_upsert_env_value_handles_delimiters_and_literal_keys(script_path):
    _assert_delimiters_and_literal_keys(script_path)


if __name__ == "__main__":
    for name, path in _UPSERT_SCRIPTS.items():
        _assert_delimiters_and_literal_keys(path)
        print(f"test_macos_upsert_env_value[{name}] passed.")
