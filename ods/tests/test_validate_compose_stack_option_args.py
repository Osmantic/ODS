#!/usr/bin/env python3
"""Regression tests for validate-compose-stack.sh CLI argument validation."""
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
VALIDATE_SH = ROOT_DIR / "scripts" / "validate-compose-stack.sh"


class ValidateComposeStackOptionArgsTests(unittest.TestCase):
    def test_missing_option_arguments_rejected(self):
        """Options requiring an argument must fail with exit code 1 and a diagnostic when missing."""
        flags_to_test = ["--compose-flags", "--env-file"]
        for flag in flags_to_test:
            result = subprocess.run(
                ["bash", str(VALIDATE_SH), flag],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode,
                1,
                f"Expected exit code 1 when missing argument for {flag}, got {result.returncode}",
            )
            self.assertIn(
                f"ERROR: {flag} requires an argument",
                result.stderr,
                f"Expected error message on stderr for {flag}, got: {result.stderr!r}",
            )

    def test_empty_option_arguments_rejected(self):
        """Passing an empty string for required options must fail with exit code 1."""
        flags_to_test = ["--compose-flags", "--env-file"]
        for flag in flags_to_test:
            result = subprocess.run(
                ["bash", str(VALIDATE_SH), flag, ""],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn(f"ERROR: {flag} requires an argument", result.stderr)

    def test_option_supplied_where_argument_required_rejected(self):
        """Supplying another option flag where an argument is required must fail with a diagnostic."""
        cases = [
            (["--compose-flags", "--quiet"], "ERROR: --compose-flags requires an argument"),
            (["--compose-flags", "--env-file"], "ERROR: --compose-flags requires an argument"),
            (["--env-file", "--quiet"], "ERROR: --env-file requires an argument"),
            (["--env-file", "--compose-flags"], "ERROR: --env-file requires an argument"),
        ]
        for args, expected_error in cases:
            result = subprocess.run(
                ["bash", str(VALIDATE_SH)] + args,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode,
                1,
                f"Expected exit code 1 for args {args}, got {result.returncode}",
            )
            self.assertIn(expected_error, result.stderr)

    def test_missing_compose_flags_when_omitted(self):
        """When --compose-flags is omitted, script reports required flag."""
        result = subprocess.run(
            ["bash", str(VALIDATE_SH)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR: --compose-flags required", result.stderr)

        result_quiet = subprocess.run(
            ["bash", str(VALIDATE_SH), "--quiet"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result_quiet.returncode, 1)
        self.assertIn("ERROR: --compose-flags required", result_quiet.stderr)

    def test_unknown_argument_rejected(self):
        """Unknown arguments must fail with exit code 1 and diagnostic."""
        result = subprocess.run(
            ["bash", str(VALIDATE_SH), "--unknown-flag"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unknown argument: --unknown-flag", result.stderr)

    def test_valid_options_dispatch_with_mock_docker(self):
        """Valid arguments correctly configure flags and pass to docker compose."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            mock_docker = tmp_path / "docker"
            log_file = tmp_path / "docker_call.log"
            mock_docker.write_text(
                f"""#!/bin/sh
if [ "$1" = "compose" ] && [ "$2" = "version" ]; then
    echo "Docker Compose version v2.20.0"
    exit 0
fi
echo "$@" >> "{log_file}"
exit 0
"""
            )
            mock_docker.chmod(mock_docker.stat().st_mode | stat.S_IXUSR)

            env_file = tmp_path / ".env"
            env_file.write_text("FOO=BAR\n")

            env = os.environ.copy()
            env["PATH"] = f"{tmpdir}:{env['PATH']}"

            # Test --compose-flags with --quiet
            result = subprocess.run(
                ["bash", str(VALIDATE_SH), "--compose-flags", "-f docker-compose.yml", "--quiet"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("compose -f docker-compose.yml config", log_file.read_text())

            # Test with --env-file
            log_file.unlink()
            result2 = subprocess.run(
                [
                    "bash",
                    str(VALIDATE_SH),
                    "--env-file",
                    str(env_file),
                    "--compose-flags",
                    "-f docker-compose.yml",
                    "--quiet",
                ],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(result2.returncode, 0, result2.stderr)
            self.assertIn(f"compose --env-file {env_file} -f docker-compose.yml config", log_file.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
