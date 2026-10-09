#!/usr/bin/env python3
"""Exercise actual validator argv boundaries without executing Compose services."""

import json
import importlib.util
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ODS = Path(__file__).resolve().parents[1]
SCRIPT = ODS / "scripts" / "validate-compose-stack.sh"
PHASE02_CALL = r'''
set -euo pipefail
SCRIPT_DIR="$1"
INSTALL_DIR="$1"
LOG_FILE="$1/resolver.log"
TIER=1 GPU_BACKEND=cpu GPU_COUNT=1 ODS_MODE=local
CAP_COMPOSE_OVERLAYS="$3"
log() { :; }
warn() { printf '%s\n' "$*" >&2; }
source "$SCRIPT_DIR/installers/lib/compose-select.sh"
cd "$SCRIPT_DIR"
resolve_compose_config
printf '%s' "$COMPOSE_FLAGS" > resolved-flags
bash "$2" --compose-flags "$COMPOSE_FLAGS" --env-file "$INSTALL_DIR/.env" --quiet
'''


def phase02_fixture(root):
    """Copy actual producer code into a disposable, space-bearing install tree."""
    installed = root / "installed path with spaces'quote"
    installed.mkdir()
    for name in (
        "installers/lib/compose-select.sh", "lib/safe-env.sh", "lib/python-cmd.sh",
        "scripts/resolve-compose-stack.sh", "extensions/services/dashboard-api/env_values.py",
    ):
        target = installed / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ODS / name, target)
        target.chmod(0o755 if name.endswith(".sh") else 0o644)
    (installed / ".env").write_text("TEST_VALUE=actual-config-proof\n", encoding="utf-8")
    (installed / "docker-compose.base.yml").write_text(
        "name: ods-review-7554\nservices:\n  proof:\n    image: scratch\n"
        "    environment:\n      VALUE: ${TEST_VALUE:?env file must resolve}\n", encoding="utf-8",
    )
    (installed / "docker-compose.cpu.yml").write_text("services: {}\n", encoding="utf-8")
    return installed


MOCK_DOCKER = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

with open(os.environ["MOCK_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps([Path(sys.argv[0]).name, *sys.argv[1:]]) + "\n")
if sys.argv[1:] == ["compose", "version"]:
    sys.exit(int(os.environ.get("MOCK_VERSION_EXIT", "0")))
print("  service_alpha")
sys.exit(int(os.environ.get("MOCK_CONFIG_EXIT", "0")))
'''


class ValidateComposeStackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        self.log = self.root / "argv.jsonl"
        for name in ("docker", "docker-compose"):
            executable = self.bin / name
            executable.write_text(MOCK_DOCKER, encoding="utf-8")
            executable.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "TMPDIR": str(self.tmp),
            "MOCK_LOG": str(self.log),
        }

    def run_validator(self, flags, env_file=None, quiet=True):
        command = ["bash", str(SCRIPT), "--compose-flags", flags]
        if env_file is not None:
            command += ["--env-file", str(env_file)]
        if quiet:
            command += ["--quiet"]
        result = subprocess.run(
            command, env=self.env, cwd=self.root, capture_output=True,
            text=True, check=False, timeout=10,
        )
        self.assertEqual(list(self.tmp.iterdir()), [], "temporary files leaked")
        return result

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def test_spaces_in_env_file_and_compose_file(self):
        folder = self.root / "path with spaces"
        folder.mkdir()
        env_file = folder / ".env"
        env_file.write_text("DEVICE_TIER=1\n", encoding="utf-8")
        compose_file = folder / "compose.yml"
        compose_file.write_text("services: {}\n", encoding="utf-8")
        result = self.run_validator(f'-f "{compose_file}"', env_file)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1], [
            "docker", "compose", "--env-file", str(env_file), "-f", str(compose_file), "config",
        ])

    def test_without_env_file_and_unquoted_legacy_flags(self):
        result = self.run_validator("-f base.yml -f overlays/cpu.yml", quiet=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Compose stack validation passed", result.stdout)
        self.assertEqual(self.calls()[-1], [
            "docker", "compose", "-f", "base.yml", "-f", "overlays/cpu.yml", "config",
        ])

    def test_real_paths_with_quotes_newline_backslash_and_metacharacters(self):
        names = ["single'quote", 'double"quote', "line\nbreak", r"back\slash", "[*]$;`literal`"]
        files = []
        for name in names:
            path = self.root / (name + ".yml")
            path.write_text("services: {}\n", encoding="utf-8")
            files += ["-f", str(path)]
        result = self.run_validator(" ".join(shlex.quote(token) for token in files))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1], ["docker", "compose", *files, "config"])

    def test_backslash_escaped_space(self):
        result = self.run_validator(r"-f a\ path/compose.yml")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1], ["docker", "compose", "-f", "a path/compose.yml", "config"])

    def test_shell_expansions_stay_literal(self):
        (self.root / "glob.yml").touch()
        self.env["COMPOSE_TEST_VALUE"] = "expanded.yml"
        flags = '-f "$(touch marker-dollar)" -f "`touch marker-backtick`" -f $COMPOSE_TEST_VALUE -f *.yml'
        result = self.run_validator(flags)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "marker-dollar").exists())
        self.assertFalse((self.root / "marker-backtick").exists())
        self.assertEqual(self.calls()[-1], [
            "docker", "compose", "-f", "$(touch marker-dollar)", "-f", "`touch marker-backtick`",
            "-f", "$COMPOSE_TEST_VALUE", "-f", "*.yml", "config",
        ])

    def test_shell_array_escape_stays_literal(self):
        result = self.run_validator("-f base.yml); touch marker-command; #")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "marker-command").exists())
        self.assertEqual(self.calls()[-1], [
            "docker", "compose", "-f", "base.yml);", "touch", "marker-command;", "#", "config",
        ])

    def test_malformed_quotes_fail_before_docker(self):
        for flags in ('-f "unfinished', "-f 'unfinished", "-f trailing\\", '-f "$(touch marker)" "'):
            with self.subTest(flags=flags):
                result = self.run_validator(flags)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid --compose-flags", result.stderr)
                self.assertEqual(self.calls(), [])
                self.assertFalse((self.root / "marker").exists())

    def test_empty_and_whitespace_flags_fail_before_docker(self):
        for flags in ("", " \t\n"):
            with self.subTest(flags=flags):
                result = self.run_validator(flags)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--compose-flags", result.stderr)
                self.assertEqual(self.calls(), [])

    def test_quoted_empty_argument_is_preserved(self):
        result = self.run_validator('-f ""')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1], ["docker", "compose", "-f", "", "config"])

    def test_docker_compose_fallback(self):
        self.env["MOCK_VERSION_EXIT"] = "1"
        env_file = self.root / "environment file"
        env_file.touch()
        result = self.run_validator('-f "compose file.yml"', env_file)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [
            ["docker", "compose", "version"],
            ["docker-compose", "--env-file", str(env_file), "-f", "compose file.yml", "config"],
        ])

    def test_compose_failure_is_reported_and_cleaned_up(self):
        self.env["MOCK_CONFIG_EXIT"] = "1"
        result = self.run_validator("-f base.yml")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Compose stack validation FAILED", result.stderr)

    @unittest.skipUnless(importlib.util.find_spec("yaml"), "actual resolver requires PyYAML")
    def test_phase02_actual_producer_with_space_bearing_install_root(self):
        installed = phase02_fixture(self.root)
        for overlays in ("", "docker-compose.base.yml,docker-compose.cpu.yml"):
            with self.subTest(overlays=overlays):
                result = subprocess.run(
                    ["bash", "-s", "--", str(installed), str(SCRIPT), overlays],
                    input=PHASE02_CALL, env={**self.env, "NATIVE_LLM_BASE_URL": "", "EXTERNAL_LLM_URL": ""},
                    text=True, capture_output=True, check=False, timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                flags = "-f docker-compose.base.yml -f docker-compose.cpu.yml"
                self.assertEqual((installed / "resolved-flags").read_text(encoding="utf-8"), flags)
                self.assertEqual(self.calls()[-1], [
                    "docker", "compose", "--env-file", str(installed / ".env"),
                    "-f", "docker-compose.base.yml", "-f", "docker-compose.cpu.yml", "config",
                ])


if __name__ == "__main__":
    unittest.main()
