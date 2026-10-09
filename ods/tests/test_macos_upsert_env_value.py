"""Literal key and delimiter-safe updates in macOS upsert_env_value."""

import subprocess
import tempfile
from pathlib import Path


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


def test_upsert_env_value_handles_delimiters_and_literal_keys():
    repo_root = Path(__file__).resolve().parents[1]
    env_gen = repo_root / "installers/macos/lib/env-generator.sh"
    helper_code = extract_helper_func(env_gen)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env_file = root / ".env"
        env_file.write_text("FOO_BAR=existing_value\n")

        runner = root / "run_test.sh"
        runner.write_text(f"""#!/usr/bin/env bash
set -euo pipefail
{helper_code}

# 1. Setting a key with regex dot should not overwrite FOO_BAR
upsert_env_value "{env_file}" "FOO.BAR" "new_dot_val"

grep -q "FOO_BAR=existing_value" "{env_file}" || {{ echo "FOO_BAR was clobbered by FOO.BAR"; exit 2; }}

# 2. Setting a value containing pipe and ampersand
upsert_env_value "{env_file}" "PIPE_VAR" "http://host|auth&token"

# 3. In-place update of existing value with delimiter
upsert_env_value "{env_file}" "FOO_BAR" "updated|pipe&amp"
""")
        runner.chmod(0o755)

        result = subprocess.run(["bash", str(runner)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, f"stdout: {result.stdout}, stderr: {result.stderr}"

        content = env_file.read_text()
        assert "FOO_BAR=updated|pipe&amp" in content
        assert "FOO.BAR=new_dot_val" in content
        assert "PIPE_VAR=http://host|auth&token" in content


if __name__ == "__main__":
    test_upsert_env_value_handles_delimiters_and_literal_keys()
    print("test_macos_upsert_env_value passed.")
