"""Exercise the actual pre-checkout shell without network or package mutations."""
import os
from pathlib import Path
import shutil
import subprocess
import textwrap
import unittest


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/matrix-smoke.yml"
SHELL = os.environ.get("ODS_TEST_BASH") or shutil.which("bash")


@unittest.skipUnless(SHELL, "bash is required")
class CheckoutRetryTests(unittest.TestCase):
    def run_case(self, mode):
        source = WORKFLOW.read_text(encoding="utf-8")
        start = source.index("          retry() {")
        end = source.index("          if command -v apt-get", start)
        functions = textwrap.dedent(source[start:end])
        fake = r'''
updates=0
installs=0
sleep() { :; }
timeout() { test "$1" = 180 || return 99; shift; "$@"; }
apt-get() {
  test "$DEBIAN_FRONTEND" = noninteractive || return 98
  printf 'APT %s\n' "$*"
  case " $* " in
    *" update "*)
      updates=$((updates + 1))
      if [ "$MODE" = update_timeout ] || { [ "$MODE" = update_timeout_retry ] && [ "$updates" = 1 ]; }; then
        return 124
      fi
      if [ "$MODE" = update_fail ] || { [ "$MODE" = update_retry ] && [ "$updates" = 1 ]; }; then
        return 100
      fi ;;
    *" install "*)
      installs=$((installs + 1))
      if [ "$MODE" = install_timeout ] || { [ "$MODE" = install_timeout_retry ] && [ "$installs" = 1 ]; }; then
        return 124
      fi
      if [ "$MODE" = install_fail ] || { [ "$MODE" = install_retry ] && [ "$installs" = 1 ]; }; then
        return 100
      fi ;;
    *) return 99 ;;
  esac
  return 0
}
retry install_apt_checkout_prerequisites
'''
        completed = subprocess.run(
            [SHELL, "-e", "-c", functions + fake],
            env={**os.environ, "MODE": mode}, capture_output=True, text=True, timeout=10,
        )
        calls = [line for line in completed.stdout.splitlines() if line.startswith("APT ")]
        for call in calls:
            self.assertIn("Acquire::http::No-Cache=true", call)
            self.assertIn("Acquire::https::No-Cache=true", call)
            self.assertIn("Acquire::http::Timeout=30", call)
            self.assertIn("Acquire::https::Timeout=30", call)
            self.assertIn("Acquire::Retries=2", call)
            self.assertNotIn("-qq", call)
            self.assertNotIn("allow-unauthenticated", call)
            if "update" in call.split():
                self.assertIn("APT::Update::Error-Mode=any", call)
        return completed.returncode, ["update" if "update" in call.split() else "install" for call in calls]

    def test_success_needs_no_retry(self):
        self.assertEqual(self.run_case("success"), (0, ["update", "install"]))

    def test_missing_package_refreshes_indexes_before_retry(self):
        self.assertEqual(self.run_case("install_retry"), (0, ["update", "install"] * 2))

    def test_failed_refresh_does_not_install_with_old_indexes(self):
        self.assertEqual(self.run_case("update_retry"), (0, ["update", "update", "install"]))

    def test_package_failure_exhausts_bound_and_fails_job(self):
        self.assertEqual(self.run_case("install_fail"), (100, ["update", "install"] * 3))

    def test_refresh_failure_exhausts_bound_and_fails_job(self):
        self.assertEqual(self.run_case("update_fail"), (100, ["update"] * 3))

    def test_stalled_refresh_can_recover_without_installing_stale_indexes(self):
        self.assertEqual(self.run_case("update_timeout_retry"), (0, ["update", "update", "install"]))

    def test_stalled_install_refreshes_indexes_before_retry(self):
        self.assertEqual(self.run_case("install_timeout_retry"), (0, ["update", "install"] * 2))

    def test_persistent_refresh_timeout_fails_after_three_attempts(self):
        self.assertEqual(self.run_case("update_timeout"), (124, ["update"] * 3))

    def test_persistent_install_timeout_fails_after_three_attempts(self):
        self.assertEqual(self.run_case("install_timeout"), (124, ["update", "install"] * 3))


if __name__ == "__main__":
    unittest.main()
