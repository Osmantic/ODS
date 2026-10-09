#!/usr/bin/env python3
"""Exercise the installed updater's changelog command with controlled HTTP."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ChangelogFallbackTests(unittest.TestCase):
    def check_case(self, scenario, expected_calls, succeeds=False, explicit=False, local=False):
        with tempfile.TemporaryDirectory() as temporary:
            private = Path(temporary)
            install = private / "install"
            install.mkdir()
            home = private / "home"
            home.mkdir()
            foreign = private / "foreign"
            foreign.mkdir()
            (foreign / "sentinel").write_text("foreign installation\n")
            (install / ".env").write_text("ODS_VERSION=3.0.0\n")
            shutil.copyfile(ROOT / "ods-update.sh", install / "ods-update.sh")
            if local:
                (install / "CHANGELOG.md").write_text("".join(f"line {n}\n" for n in range(55)))
            commands = private / "bin"
            commands.mkdir()
            curl = commands / "curl"
            curl.write_text("""#!/usr/bin/env bash
set -eu
printf '%s\\n' "${@: -1}" >> "$CAPTURE"
if [[ "${@: -1}" == */releases/latest ]]; then
    case "$SCENARIO" in
        network) sleep 0.05; exit 7 ;;
        http) sleep 0.05; exit 22 ;;
        latest-partial) printf '{"tag_name":"v3.0.0"}'; exit 18 ;;
        malformed) printf '[' ;;
        missing) printf '{}' ;;
        null) printf '{"tag_name":null}' ;;
        empty) printf '{"tag_name":""}' ;;
        number) printf '{"tag_name":3}' ;;
        object) printf '{"tag_name":{}}' ;;
        array) printf '[]' ;;
        *) printf '{"tag_name":"v3.0.0"}' ;;
    esac
else
    [[ "${@: -1}" == */releases/tags/v3.0.0 ]] || exit 99
    case "$SCENARIO" in
        body-failure) exit 22 ;;
        body-partial) printf '{"body":"Release notes ready."}'; exit 18 ;;
        body-malformed) printf '[' ;;
        *) printf '{"body":"Release notes ready."}' ;;
    esac
fi
""")
            curl.chmod(0o755)
            for command in ("docker", "sudo", "systemctl", "service", "git", "launchctl"):
                stub = commands / command
                stub.write_text("#!/usr/bin/env bash\necho unexpected-host-command >&2\nexit 99\n")
                stub.chmod(0o755)
            before = {p.relative_to(private): p.read_bytes() for p in private.rglob("*") if p.is_file()}
            capture = private / "requests"
            env = {**os.environ, "HOME": str(home), "INSTALL_DIR": str(install), "ODS_HOME": str(install),
                   "ODS_INSTALL_DIR": str(foreign), "CAPTURE": str(capture), "SCENARIO": scenario,
                   "GITHUB_REPO": "Osmantic/ODS", "PATH": str(commands) + os.pathsep + os.environ["PATH"]}
            args = ["bash", str(install / "ods-update.sh"), "changelog"]
            if explicit:
                args.append("v3.0.0")
            result = subprocess.run(args, env=env, cwd=foreign, stdin=subprocess.DEVNULL,
                                    text=True, capture_output=True, timeout=3)
            if succeeds:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                expected = "".join(f"line {n}\n" for n in range(50)) if local else "Release notes ready.\n"
                self.assertTrue(result.stdout.endswith(expected), result.stdout)
            else:
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("Release notes ready.", result.stdout)
                if scenario not in ("body-failure", "body-malformed", "body-partial"):
                    self.assertIn("Could not determine the latest release", result.stderr)
            calls = capture.read_text().splitlines() if capture.exists() else []
            self.assertEqual(calls, expected_calls)
            self.assertNotIn("unexpected-host-command", result.stderr)
            after = {p.relative_to(private): p.read_bytes() for p in private.rglob("*")
                     if p.is_file() and p != capture}
            self.assertEqual(before, after)

    def test_local_file_bypasses_network(self):
        self.check_case("network", [], succeeds=True, local=True)

    def test_explicit_version_fetches_only_requested_release(self):
        self.check_case("success", ["https://api.github.com/repos/Osmantic/ODS/releases/tags/v3.0.0"],
                        succeeds=True, explicit=True)

    def test_latest_release_fetches_body_once(self):
        self.check_case("success", ["https://api.github.com/repos/Osmantic/ODS/releases/latest",
                                   "https://api.github.com/repos/Osmantic/ODS/releases/tags/v3.0.0"], succeeds=True)

    def test_unresolved_latest_release_fails_without_recursion(self):
        for scenario in ("network", "http", "latest-partial", "malformed", "missing", "null", "empty", "number", "object", "array"):
            with self.subTest(scenario=scenario):
                self.check_case(scenario, ["https://api.github.com/repos/Osmantic/ODS/releases/latest"])

    def test_latest_release_body_failure_is_preserved(self):
        for scenario in ("body-failure", "body-malformed", "body-partial"):
            with self.subTest(scenario=scenario):
                self.check_case(scenario, ["https://api.github.com/repos/Osmantic/ODS/releases/latest",
                                           "https://api.github.com/repos/Osmantic/ODS/releases/tags/v3.0.0"])

    def test_explicit_version_failure_is_preserved(self):
        self.check_case("body-failure", ["https://api.github.com/repos/Osmantic/ODS/releases/tags/v3.0.0"], explicit=True)


if __name__ == "__main__":
    unittest.main()
