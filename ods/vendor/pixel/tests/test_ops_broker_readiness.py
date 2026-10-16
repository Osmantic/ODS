"""Real delayed-file publication; systemd/sudo boundaries are fixtures."""
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "scripts/lib/broker-reader-acls.sh"
SHELL = r'''
set -euo pipefail
source "$TEST_LIBRARY"
sudo() {
  if [[ "$1" == systemctl ]]; then
    case "$2" in
      is-active) [[ "$TEST_SERVICE" == active ]] ;;
      is-failed) [[ "$TEST_SERVICE" == failed ]] ;;
      *) return 2 ;;
    esac
  else
    "$@"
  fi
}
pixel_die() { printf '%s\n' "$*" >&2; exit 1; }
pixel_wait_ops_inventory "$TEST_TIMEOUT"
pixel_broker_acl_file "$PIXEL_OPS_INVENTORY_PATH" 'Operations inventory projection'
'''


@unittest.skipUnless(os.name == "posix", "Linux shell readiness contract")
class OperationsStartup(unittest.TestCase):
    def run_wait(self, *, delay=None, service="active", timeout=3, symlink=False):
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "inventory.json"
            if symlink:
                target = Path(directory) / "other.json"
                target.write_text("{}")
                inventory.symlink_to(target)
            writer = None
            started = time.monotonic()
            if delay is not None:
                def publish():
                    time.sleep(delay)
                    staged = Path(directory) / "inventory.partial"
                    staged.write_text("{}")
                    staged.replace(inventory)
                writer = threading.Thread(target=publish)
                writer.start()
            try:
                result = subprocess.run(["bash", "-c", SHELL], capture_output=True, text=True,
                    timeout=timeout + 10, env={**os.environ, "TEST_LIBRARY": str(LIBRARY),
                        "TEST_SERVICE": service, "TEST_TIMEOUT": str(timeout),
                        "PIXEL_OPS_BROKER_UNIT": "fixture.service",
                        "PIXEL_OPS_INVENTORY_PATH": str(inventory)})
                return result, time.monotonic() - started
            finally:
                if writer:
                    writer.join()

    def test_inventory_after_old_ten_second_window(self):
        result, elapsed = self.run_wait(delay=12, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(elapsed, 12)

    def test_ready_without_fixed_delay(self):
        result, elapsed = self.run_wait(delay=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(elapsed, 2)

    def test_active_without_inventory_is_not_ready(self):
        result, elapsed = self.run_wait(timeout=2)
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 5)

    def test_failed_service_stops_wait(self):
        result, elapsed = self.run_wait(service="failed", timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 2)

    def test_inventory_without_active_service_is_not_ready(self):
        result, _ = self.run_wait(delay=0, service="inactive", timeout=2)
        self.assertNotEqual(result.returncode, 0)

    def test_linked_inventory_still_fails_existing_acl_boundary(self):
        result, _ = self.run_wait(symlink=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("linked", result.stderr)


if __name__ == "__main__":
    unittest.main()
