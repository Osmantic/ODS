"""Exercise the workflow's tool preparation without network or root access."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / "workflows/test-ci-suite.yml"
SHELL = shutil.which("bash")


@unittest.skipUnless(SHELL and os.name == "posix", "POSIX bash is required")
class ToolSetupTests(unittest.TestCase):
    def run_case(self, mode):
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        step = next(step for step in workflow["jobs"]["suite"]["steps"]
                    if step.get("name") == "Install test tools")
        self.assertEqual(step["timeout-minutes"], 10)
        # Simulate tool presence and APT results; the workflow's actual shell
        # selects which packages to install and propagates all failures.
        fake = r'''
HAS_JQ=1
HAS_SHELLCHECK=1
case "$MODE" in
  missing_jq) HAS_JQ=0 ;;
  missing_shellcheck) HAS_SHELLCHECK=0 ;;
  missing_both|update_fail|install_fail) HAS_JQ=0; HAS_SHELLCHECK=0 ;;
esac
python() { :; }
command() {
  if [ "$1" = -v ]; then
    case "$2" in
      jq) test "$HAS_JQ" = 1; return ;;
      shellcheck) test "$HAS_SHELLCHECK" = 1; return ;;
    esac
  fi
  builtin command "$@"
}
sudo() {
  printf 'APT %s\n' "$*"
  case " $* " in
    *" update "*) test "$MODE" != update_fail || return 100 ;;
    *" install "*)
      test "$MODE" != install_fail || return 124
      for arg in "$@"; do
        case "$arg" in
          jq) HAS_JQ=1 ;;
          shellcheck) HAS_SHELLCHECK=1 ;;
        esac
      done ;;
    *) return 99 ;;
  esac
}
jq() { test "$HAS_JQ" = 1 || return 127; printf 'JQ-VERIFIED\n'; }
shellcheck() {
  test "$HAS_SHELLCHECK" = 1 || return 127
  test "$MODE" != broken_tool || return 37
  printf 'SHELLCHECK-VERIFIED\n'
}
'''
        result = subprocess.run(
            [SHELL, "-e", "-c", fake + step["run"]],
            env={**os.environ, "MODE": mode}, capture_output=True, text=True, timeout=10,
        )
        calls = [line for line in result.stdout.splitlines() if line.startswith("APT ")]
        for call in calls:
            self.assertIn("timeout 180 apt-get", call)
            self.assertIn("Acquire::Retries=2", call)
            self.assertIn("Acquire::http::Timeout=30", call)
            self.assertIn("Acquire::https::Timeout=30", call)
            self.assertNotIn("-qq", call)
            self.assertNotIn("allow-unauthenticated", call)
            if "update" in call.split():
                self.assertIn("APT::Update::Error-Mode=any", call)
            else:
                self.assertIn("DEBIAN_FRONTEND=noninteractive", call)
        return result, calls

    def test_present_tools_are_verified_without_package_network(self):
        result, calls = self.run_case("present")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])
        self.assertIn("JQ-VERIFIED", result.stdout)
        self.assertIn("SHELLCHECK-VERIFIED", result.stdout)

    def test_only_missing_tools_are_installed_and_then_verified(self):
        for mode, packages in (("missing_jq", ["jq"]), ("missing_shellcheck", ["shellcheck"]),
                               ("missing_both", ["jq", "shellcheck"])):
            with self.subTest(mode=mode):
                result, calls = self.run_case(mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(calls), 2)
                self.assertTrue(calls[0].endswith(" update"))
                self.assertTrue(calls[1].endswith(" install -y " + " ".join(packages)))
                self.assertIn("JQ-VERIFIED", result.stdout)
                self.assertIn("SHELLCHECK-VERIFIED", result.stdout)

    def test_failed_refresh_never_installs_from_stale_indexes(self):
        result, calls = self.run_case("update_fail")
        self.assertEqual(result.returncode, 100)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith(" update"))
        self.assertNotIn("JQ-VERIFIED", result.stdout)

    def test_install_timeout_stops_before_running_tests(self):
        result, calls = self.run_case("install_fail")
        self.assertEqual(result.returncode, 124)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("JQ-VERIFIED", result.stdout)

    def test_broken_existing_tool_still_fails(self):
        result, calls = self.run_case("broken_tool")
        self.assertEqual(result.returncode, 37)
        self.assertEqual(calls, [])
        self.assertNotIn("SHELLCHECK-VERIFIED", result.stdout)


if __name__ == "__main__":
    unittest.main()
