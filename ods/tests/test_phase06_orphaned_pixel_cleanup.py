"""Regression tests for Phase 06 orphaned Pixel host state cleanup."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "posix", "POSIX phase execution required")
class Phase06OrphanedPixelCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ods-phase06-orphan-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / "home"
        self.installed = self.base / "installed"
        self.log_file = self.base / "phase06.log"
        self.home.mkdir(parents=True)
        self.installed.mkdir(parents=True)
        self.marker_dir = self.home / ".config" / "ods"
        self.marker_dir.mkdir(parents=True)
        self.marker = self.marker_dir / "pixel-managed.json"

    def _write_marker(self, payload):
        self.marker.write_text(json.dumps(payload), encoding="utf-8")

    def test_cleanup_refusal_preserves_marker_and_aborts(self):
        """When ods_pixel_uninstall_managed fails, marker is preserved and phase aborts."""
        payload = {
            "manager": "ods",
            "schema_version": 2,
            "state": "ready",
            "install_dir": str(self.installed),
        }
        self._write_marker(payload)
        canary = self.installed / "source-canary.txt"

        script = r'''
set -euo pipefail
SCRIPT_DIR="$1"; INSTALL_DIR="$2"; HOME="$3"; ODS_PYTHON_CMD="$4"; CANARY="$5"; LOG_FILE="$6"
DRY_RUN=false; ENABLE_PIXEL_RUNTIME=true; ODS_SUDO_AVAILABLE=true; ODS_ASSUME_ROOTLESS=false
ods_progress(){ :; }; chapter(){ :; }; log(){ :; }
ai(){
    if [[ "$*" == *"Copying source files"* ]]; then
        touch "$CANARY"
    fi
}
error(){ printf '%s\n' "$*" >&2; return 1; }
source "$SCRIPT_DIR/installers/lib/pixel-integration.sh"
ods_pixel_install_owner(){ id -un; }
ods_pixel_owner_home(){ printf '%s\n' "$HOME"; }
# Simulate failure in ods_pixel_uninstall_managed (fail closed)
ods_pixel_uninstall_managed(){ return 73; }
source "$SCRIPT_DIR/installers/phases/06-directories.sh"
'''
        result = subprocess.run(
            ["bash", "-c", script, "phase-fixture", str(ROOT), str(self.installed), str(self.home), sys.executable, str(canary), str(self.log_file)],
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertNotEqual(result.returncode, 0, f"Phase should fail when cleanup fails: {result.stdout}\n{result.stderr}")
        self.assertTrue(self.marker.exists(), "Management marker must be preserved on cleanup failure")
        self.assertEqual(json.loads(self.marker.read_text(encoding="utf-8")), payload, "Marker content must remain intact")
        self.assertFalse(canary.exists(), "Source copy / bootstrap must not proceed after cleanup refusal")
        self.assertIn("Could not clean orphaned Pixel host state", result.stderr)

    def test_cleanup_success_proceeds_to_clean_install(self):
        """When ods_pixel_uninstall_managed succeeds, phase proceeds to clean install."""
        payload = {
            "manager": "ods",
            "schema_version": 2,
            "state": "ready",
            "install_dir": str(self.installed),
        }
        self._write_marker(payload)
        canary = self.installed / "source-canary.txt"

        script = r'''
set -euo pipefail
SCRIPT_DIR="$1"; INSTALL_DIR="$2"; HOME="$3"; ODS_PYTHON_CMD="$4"; CANARY="$5"; LOG_FILE="$6"
DRY_RUN=false; ENABLE_PIXEL_RUNTIME=true; ODS_SUDO_AVAILABLE=true; ODS_ASSUME_ROOTLESS=false
ods_progress(){ :; }; chapter(){ :; }; log(){ :; }
ai(){
    if [[ "$*" == *"Copying source files"* ]]; then
        touch "$CANARY"
        # Stop at source-copy boundary so test isolates transition logic from full compose stack
        exit 42
    fi
}
error(){ printf '%s\n' "$*" >&2; return 1; }
source "$SCRIPT_DIR/installers/lib/pixel-integration.sh"
ods_pixel_install_owner(){ id -un; }
ods_pixel_owner_home(){ printf '%s\n' "$HOME"; }
# Simulate successful uninstallation which removes marker
ods_pixel_uninstall_managed(){
    rm -f "$HOME/.config/ods/pixel-managed.json"
    return 0
}
source "$SCRIPT_DIR/installers/phases/06-directories.sh"
'''
        result = subprocess.run(
            ["bash", "-c", script, "phase-fixture", str(ROOT), str(self.installed), str(self.home), sys.executable, str(canary), str(self.log_file)],
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 42, f"Phase should reach copy-source step: {result.stdout}\n{result.stderr}")
        self.assertTrue(canary.exists(), "Source copy / bootstrap step must be reached on successful cleanup")
        self.assertFalse(self.marker.exists(), "Marker should be unlinked upon successful cleanup")

    def test_normal_managed_upgrade_preserved(self):
        """When an installation directory is active, normal upgrade path is taken."""
        payload = {
            "manager": "ods",
            "schema_version": 2,
            "state": "ready",
            "install_dir": str(self.installed),
        }
        self._write_marker(payload)
        # Create active installation markers (.env and config)
        (self.installed / ".env").write_text("ACTIVE=true\n", encoding="utf-8")
        (self.installed / "config").mkdir()

        script = r'''
set -euo pipefail
SCRIPT_DIR="$1"; INSTALL_DIR="$2"; HOME="$3"; ODS_PYTHON_CMD="$4"; LOG_FILE="$5"
DRY_RUN=false; ENABLE_PIXEL_RUNTIME=true; ODS_SUDO_AVAILABLE=true; ODS_ASSUME_ROOTLESS=false
ods_progress(){ :; }; chapter(){ :; }; log(){ :; }; ai(){ :; }
error(){ printf '%s\n' "$*" >&2; return 1; }
source "$SCRIPT_DIR/installers/lib/pixel-integration.sh"
ods_pixel_install_owner(){ id -un; }
ods_pixel_owner_home(){ printf '%s\n' "$HOME"; }
_ods_pixel_start_stopped_access_service(){ :; }
# If orphan cleanup is called on active install, fail loudly
ods_pixel_uninstall_managed(){
    printf "ERROR: ods_pixel_uninstall_managed called on active install\n" >&2
    exit 99
}
# Upgrade path transition check
_ods_pixel_source_transition_required(){
    exit 77
}
source "$SCRIPT_DIR/installers/phases/06-directories.sh"
'''
        result = subprocess.run(
            ["bash", "-c", script, "phase-fixture", str(ROOT), str(self.installed), str(self.home), sys.executable, str(self.log_file)],
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 77, f"Phase should proceed to normal source transition check: {result.stdout}\n{result.stderr}")
        self.assertTrue(self.marker.exists(), "Marker must remain present in normal upgrade")


if __name__ == "__main__":
    unittest.main()
