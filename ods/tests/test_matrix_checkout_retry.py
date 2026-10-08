"""Exercise the actual pre-checkout shell without network or package mutations."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
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
configure_apt_ci_mirror() { printf 'MIRROR-FALLBACK\n'; }
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
        updates = sum("update" in call.split() for call in calls)
        self.assertEqual(completed.stdout.count("MIRROR-FALLBACK"), updates - 1)
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

    def test_mirror_fallback_preserves_suites_keys_and_unrelated_repositories(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        start = source.index("          configure_apt_ci_mirror() {")
        end = source.index("          install_apt_checkout_prerequisites() {", start)
        function = textwrap.dedent(source[start:end])
        entries = {
            "sources.list": (
                "deb [signed-by=/keys/ubuntu.gpg] http://archive.ubuntu.com/ubuntu jammy main\n"
                "deb-src https://security.ubuntu.com/ubuntu/ jammy-security main\n"
                "# deb http://archive.ubuntu.com/ubuntu jammy main\n"
                "deb http://archive.ubuntu.com.evil.example/ubuntu jammy main\n"
                "deb http://user@archive.ubuntu.com/ubuntu jammy main\n"
                "deb http://archive.ubuntu.com:8080/ubuntu jammy main\n"
                "deb http://archive.ubuntu.com/ubuntu/other jammy main\n"
            ),
            "sources.list.d/ubuntu.sources": (
                "Types: deb deb-src\n"
                "URIs: https://archive.ubuntu.com/ubuntu/\n"
                "Suites: noble noble-updates\nComponents: main universe\n"
                "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
            ),
            "sources.list.d/custom.list": (
                "deb http://deb.debian.org/debian bookworm main\n"
                "deb http://ports.ubuntu.com/ubuntu-ports noble main\n"
                "deb https://custom.example/ubuntu jammy main\n"
            ),
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sources.list.d").mkdir()
            for name, value in entries.items():
                (root / name).write_text(value)
            command = [SHELL, "-e", "-c", function + '\nconfigure_apt_ci_mirror "$1"', "fixture", tmp]
            subprocess.run(command, check=True, capture_output=True, timeout=10)
            expected = dict(entries)
            expected["sources.list"] = entries["sources.list"].replace(
                "http://archive.ubuntu.com/ubuntu jammy main", "http://azure.archive.ubuntu.com/ubuntu jammy main", 1
            ).replace("https://security.ubuntu.com/ubuntu/", "https://azure.archive.ubuntu.com/ubuntu/")
            expected["sources.list.d/ubuntu.sources"] = entries["sources.list.d/ubuntu.sources"].replace(
                "https://archive.ubuntu.com/ubuntu/", "https://azure.archive.ubuntu.com/ubuntu/"
            )
            for name, value in expected.items():
                self.assertEqual((root / name).read_text(), value)
            subprocess.run(command, check=True, capture_output=True, timeout=10)
            for name, value in expected.items():
                self.assertEqual((root / name).read_text(), value)


if __name__ == "__main__":
    unittest.main()
