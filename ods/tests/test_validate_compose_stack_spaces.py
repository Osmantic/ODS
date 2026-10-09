#!/usr/bin/env python3
"""Regression tests for validate-compose-stack.sh path quoting and space handling."""

import os
import subprocess
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = ROOT_DIR / "scripts" / "validate-compose-stack.sh"


def _create_mock_docker(bin_dir: Path, log_file: Path) -> Path:
    docker_script = bin_dir / "docker"
    docker_script.write_text(f"""#!/bin/sh
if [ "$1" = "compose" ] && [ "$2" = "version" ]; then
    echo "Docker Compose version v2.29.2"
    exit 0
fi
for arg in "$@"; do
    echo "$arg" >> "{log_file}"
done
echo "  service_alpha"
exit 0
""", encoding="utf-8")
    docker_script.chmod(0o755)
    return docker_script


def test_validate_compose_stack_spaces_in_env_file():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        mock_bin = tdp / "mock_bin"
        mock_bin.mkdir()
        mock_log = tdp / "mock.log"
        _create_mock_docker(mock_bin, mock_log)

        space_dir = tdp / "path with spaces"
        space_dir.mkdir()
        env_file = space_dir / ".env"
        env_file.write_text("DEVICE_TIER=1\n", encoding="utf-8")
        compose_file = space_dir / "compose.yml"
        compose_file.write_text("services:\n  test:\n    image: test\n", encoding="utf-8")

        env = os.environ.copy()
        env["PATH"] = f"{mock_bin}:{env.get('PATH', '')}"

        cmd = [
            "bash",
            str(SCRIPT),
            "--compose-flags",
            f'-f "{compose_file}"',
            "--env-file",
            str(env_file),
            "--quiet",
        ]
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
        assert res.returncode == 0, f"Script failed: {res.stderr}\nStdout: {res.stdout}"

        args = mock_log.read_text(encoding="utf-8").splitlines()
        # Verify --env-file is immediately followed by the intact path with spaces
        env_idx = args.index("--env-file")
        assert args[env_idx + 1] == str(env_file), f"Expected {env_file}, got {args[env_idx + 1]}"

        # Verify -f is followed by the intact compose file path
        f_idx = args.index("-f")
        assert args[f_idx + 1] == str(compose_file), f"Expected {compose_file}, got {args[f_idx + 1]}"


def test_validate_compose_stack_without_env_file():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        mock_bin = tdp / "mock_bin"
        mock_bin.mkdir()
        mock_log = tdp / "mock.log"
        _create_mock_docker(mock_bin, mock_log)

        compose_file = tdp / "compose.yml"
        compose_file.write_text("services:\n  test:\n    image: test\n", encoding="utf-8")

        env = os.environ.copy()
        env["PATH"] = f"{mock_bin}:{env.get('PATH', '')}"

        cmd = [
            "bash",
            str(SCRIPT),
            "--compose-flags",
            f"-f {compose_file}",
            "--quiet",
        ]
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
        assert res.returncode == 0, f"Script failed: {res.stderr}\nStdout: {res.stdout}"

        args = mock_log.read_text(encoding="utf-8").splitlines()
        assert "--env-file" not in args
        f_idx = args.index("-f")
        assert args[f_idx + 1] == str(compose_file)


if __name__ == "__main__":
    test_validate_compose_stack_spaces_in_env_file()
    test_validate_compose_stack_without_env_file()
    print("[PASS] All validate-compose-stack space handling tests passed.")
