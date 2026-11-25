#!/usr/bin/env python3
"""Public CLI validation must preserve hard failures from installed validators."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class ConfigValidationTests(unittest.TestCase):
    def check_case(self, case, expected):
        with tempfile.TemporaryDirectory() as temporary:
            private = Path(temporary)
            install = private / "install"
            foreign = private / "foreign"
            foreign.mkdir()
            (foreign / "sentinel").write_text("foreign\n")
            (install / "scripts").mkdir(parents=True)
            (install / "extensions/services/searxng").mkdir(parents=True)
            (install / "extensions/schema").mkdir()
            (install / "docker-compose.base.yml").write_text("services: {}\n")
            (install / ".env").write_text("ODS_VERSION=3.0.0\n")
            shutil.copyfile(ROOT / "manifest.json", install / "manifest.json")
            shutil.copyfile(ROOT / "extensions/schema/service-manifest.v1.json",
                            install / "extensions/schema/service-manifest.v1.json")
            validator = install / "scripts/validate-manifests.sh"
            shutil.copyfile(ROOT / "scripts/validate-manifests.sh", validator)
            validator.chmod(0o755)
            manifest = yaml.safe_load((ROOT / "extensions/services/searxng/manifest.yaml").read_text())
            if case == "invalid-schema":
                manifest["service"]["port"] = "invalid"
            if case == "compatibility-warning":
                manifest["compatibility"]["ods_min"] = "99.0.0"
            manifest_path = install / "extensions/services/searxng/manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            if case == "missing-manifest":
                (install / "manifest.json").unlink()
            # Isolate the separate env-validator contract, including its failure.
            env_validator = install / "scripts/validate-env.sh"
            env_validator.write_text("#!/usr/bin/env bash\nexit " + ("7" if case == "env-failure" else "0") + "\n")
            env_validator.chmod(0o755)
            blocked = private / "bin"
            blocked.mkdir()
            for command in ("docker", "sudo", "systemctl", "service", "curl", "launchctl"):
                stub = blocked / command
                stub.write_text("#!/usr/bin/env bash\necho unexpected-host-command >&2\nexit 99\n")
                stub.chmod(0o755)
            before = {p.relative_to(private): p.read_bytes() for p in private.rglob("*") if p.is_file()}
            env = {**os.environ, "INSTALL_DIR": str(install), "ODS_HOME": str(install),
                   "ODS_INSTALL_DIR": str(foreign), "PATH": str(blocked) + os.pathsep + os.environ["PATH"]}
            result = subprocess.run(["bash", str(ROOT / "ods-cli"), "config", "validate"],
                                    env=env, cwd=foreign, stdin=subprocess.DEVNULL,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            if case == "missing-manifest":
                self.assertIn("manifest.json not found", result.stdout)
            if case == "invalid-schema":
                self.assertIn("Schema validation failed", result.stdout)
            if case == "compatibility-warning":
                self.assertIn("incompatible", result.stdout)
            self.assertNotIn("unexpected-host-command", result.stderr)
            after = {p.relative_to(private): p.read_bytes() for p in private.rglob("*") if p.is_file()}
            self.assertEqual(before, after)

    def test_missing_manifest_fails(self):
        self.check_case("missing-manifest", 1)

    def test_invalid_extension_schema_fails(self):
        self.check_case("invalid-schema", 1)

    def test_valid_config_succeeds(self):
        self.check_case("valid", 0)

    def test_compatibility_warning_stays_successful(self):
        self.check_case("compatibility-warning", 0)

    def test_environment_failure_is_preserved(self):
        self.check_case("env-failure", 7)


if __name__ == "__main__":
    unittest.main()
