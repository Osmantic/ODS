"""Regression tests for Phase 06 orphaned Pixel host state cleanup."""
import json
import os
from pathlib import Path
import shutil
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
        self.source_dir = self.base / "source"
        self.systemd_dir = self.base / "systemd"
        self.etc_dir = self.base / "etc"
        self.libexec_dir = self.base / "libexec"
        self.mock_bin = self.base / "bin"
        self.log_file = self.base / "phase06.log"

        for p in (self.home, self.installed, self.source_dir, self.systemd_dir,
                  self.etc_dir, self.libexec_dir, self.mock_bin):
            p.mkdir(parents=True, exist_ok=True)

        self.marker_dir = self.home / ".config" / "ods"
        self.marker_dir.mkdir(parents=True, exist_ok=True)
        self.marker = self.marker_dir / "pixel-managed.json"

        # Setup verified source tree in self.source_dir
        (self.source_dir / "installers" / "phases").mkdir(parents=True)
        shutil.copytree(ROOT / "installers" / "lib", self.source_dir / "installers" / "lib")
        shutil.copytree(ROOT / "lib", self.source_dir / "lib")
        (self.source_dir / "bin").mkdir(parents=True)
        (self.source_dir / "vendor").mkdir(parents=True)
        (self.source_dir / "vendor" / "pixel.bundle").write_bytes((ROOT / "vendor" / "pixel.bundle").read_bytes())
        (self.source_dir / "extensions" / "services" / "pixel-agent" / "host").mkdir(parents=True)

        # Include incoming config/ tree in source directory
        (self.source_dir / "config").mkdir(parents=True, exist_ok=True)
        (self.source_dir / "config" / "network-exposure-policy.json").write_text('{"policy":"fixture"}\n', encoding="utf-8")

        for rel in [
            "installers/phases/06-directories.sh",
            "bin/ods-pixel-approve",
            "extensions/services/pixel-agent/host/ods-pixel-access.service",
            "extensions/services/pixel-agent/host/pixel_ingress.mjs",
        ]:
            src = ROOT / rel
            dst = self.source_dir / rel
            if src.exists():
                dst.write_bytes(src.read_bytes())
                dst.chmod(src.stat().st_mode)

        # Mock bin utilities to isolate privileged host mutation
        systemctl_sh = self.mock_bin / "systemctl"
        systemctl_sh.write_text("""#!/bin/sh
case " $* " in
    *" is-active --quiet "*) exit 1 ;;
esac
exit 0
""", encoding="utf-8")
        systemctl_sh.chmod(0o755)

        sudo_sh = self.mock_bin / "sudo"
        sudo_sh.write_text('#!/bin/sh\nexec "$@"\n', encoding="utf-8")
        sudo_sh.chmod(0o755)

        docker_sh = self.mock_bin / "docker"
        docker_sh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        docker_sh.chmod(0o755)

        getent_sh = self.mock_bin / "getent"
        getent_sh.write_text("""#!/bin/sh
case "${1:-}:${2:-}" in
    passwd:pixel-ops-broker|group:pixel-ops) exit 2 ;;
    *) exec /usr/bin/getent "$@" 2>/dev/null || exit 2 ;;
esac
""", encoding="utf-8")
        getent_sh.chmod(0o755)

    def _write_marker(self, payload):
        self.marker.write_text(json.dumps(payload), encoding="utf-8")
        self.marker.chmod(0o600)

    def test_cleanup_refusal_preserves_marker_and_aborts(self):
        """Retained access unit with drifted bytes refuses cleanup and preserves marker."""
        payload = {
            "manager": "ods",
            "schema_version": 2,
            "state": "installing",
            "initial_active_state": "absent",
            "install_dir": str(self.installed),
            "pixel_source_ref": "d2a2b6be552126f294fb30ee5fb46872acf82c89",
        }
        self._write_marker(payload)

        # Retain managed host artifact while removing the installation tree
        if self.installed.exists():
            shutil.rmtree(self.installed)

        access_unit = self.systemd_dir / "ods-pixel-access.service"
        access_unit.write_text("[Unit]\nDescription=Drifted\n", encoding="utf-8")
        access_unit.chmod(0o644)
        canary = self.installed / "source-canary.txt"

        script = r'''
set -euo pipefail
SCRIPT_DIR="$1"; INSTALL_DIR="$2"; HOME="$3"; ODS_PYTHON_CMD="$4"; CANARY="$5"; LOG_FILE="$6"; MOCK_BIN="$7"
export PATH="$MOCK_BIN:$PATH"
export ODS_PIXEL_UNINSTALL_SYSTEMD_DIR="$8"
export ODS_PIXEL_UNINSTALL_ETC_DIR="$9"
export ODS_PIXEL_UNINSTALL_LIBEXEC_DIR="${10}"
export ODS_PIXEL_UNINSTALL_OPS_ENV="${9}/pixel-ops-broker.env"
export ODS_PIXEL_UNINSTALL_OPS_POLICY="${9}/pixel-ops-broker/policy.json"
export ODS_PIXEL_UNINSTALL_OPS_INSTALL_DIR="${10}/pixel-ops-broker"
export ODS_PIXEL_UNINSTALL_OPS_STATE_DIR="${9}/var/lib/pixel-ops-broker"
export ODS_PIXEL_UNINSTALL_ACCESS_STATE_DIR="${9}/var/lib/ods-pixel-access"
export ODS_PIXEL_UNINSTALL_ACCESS_PROBE_DIR="${9}/var/lib/ods-pixel-access-probes"
export ODS_PIXEL_UNINSTALL_PREVIEW_STATE_DIR="${9}/var/lib/ods-pixel-preview"
export ODS_PIXEL_UNINSTALL_ROOT_UID="$(id -u)"
export ODS_PIXEL_UNINSTALL_ROOT_GID="$(id -g)"

DRY_RUN=false; ENABLE_PIXEL_RUNTIME=true; ODS_SUDO_AVAILABLE=true; ODS_ASSUME_ROOTLESS=false
source "$SCRIPT_DIR/installers/lib/pixel-integration.sh"
source "$SCRIPT_DIR/lib/pixel-uninstall.sh"
_ods_pixel_project_cleanup(){ :; }
_ods_pixel_inspection_cleanup(){ :; }
ods_progress(){ :; }; chapter(){ :; }; log(){ :; }
ai(){
    if [[ "$*" == *"Copying source files"* ]]; then
        touch "$CANARY"
    fi
}
error(){ printf '%s\n' "$*" >&2; return 1; }
ods_pixel_install_owner(){ id -un; }
ods_pixel_owner_home(){ printf '%s\n' "$HOME"; }

source "$SCRIPT_DIR/installers/phases/06-directories.sh"
'''
        result = subprocess.run(
            ["bash", "-c", script, "phase-fixture",
             str(self.source_dir), str(self.installed), str(self.home),
             sys.executable, str(canary), str(self.log_file), str(self.mock_bin),
             str(self.systemd_dir), str(self.etc_dir), str(self.libexec_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertNotEqual(result.returncode, 0, f"Phase should fail when cleanup fails: {result.stdout}\n{result.stderr}")
        self.assertTrue(self.marker.exists(), "Management marker must be preserved on cleanup failure")
        self.assertEqual(json.loads(self.marker.read_text(encoding="utf-8")), payload, "Marker content must remain intact")
        self.assertTrue(access_unit.exists(), "Drifted access unit must remain untouched")
        self.assertEqual(access_unit.read_text(encoding="utf-8"), "[Unit]\nDescription=Drifted\n")
        self.assertFalse(canary.exists(), "Source copy / bootstrap must not proceed after cleanup refusal")
        self.assertIn("drifted", result.stderr.lower())

    def test_cleanup_success_proceeds_to_clean_install(self):
        """Supported retained access unit is cleanly removed and installer proceeds."""
        payload = {
            "manager": "ods",
            "schema_version": 2,
            "state": "installing",
            "initial_active_state": "absent",
            "install_dir": str(self.installed),
            "pixel_source_ref": "d2a2b6be552126f294fb30ee5fb46872acf82c89",
        }
        self._write_marker(payload)

        # Retain managed host artifact while removing the installation tree
        if self.installed.exists():
            shutil.rmtree(self.installed)

        access_unit = self.systemd_dir / "ods-pixel-access.service"
        genuine_source = ROOT / "extensions/services/pixel-agent/host/ods-pixel-access.service"
        access_unit.write_bytes(genuine_source.read_bytes())
        access_unit.chmod(0o644)
        canary = self.installed / "source-canary.txt"

        script = r'''
set -euo pipefail
SCRIPT_DIR="$1"; INSTALL_DIR="$2"; HOME="$3"; ODS_PYTHON_CMD="$4"; CANARY="$5"; LOG_FILE="$6"; MOCK_BIN="$7"
export PATH="$MOCK_BIN:$PATH"
export ODS_PIXEL_UNINSTALL_SYSTEMD_DIR="$8"
export ODS_PIXEL_UNINSTALL_ETC_DIR="$9"
export ODS_PIXEL_UNINSTALL_LIBEXEC_DIR="${10}"
export ODS_PIXEL_UNINSTALL_OPS_ENV="${9}/pixel-ops-broker.env"
export ODS_PIXEL_UNINSTALL_OPS_POLICY="${9}/pixel-ops-broker/policy.json"
export ODS_PIXEL_UNINSTALL_OPS_INSTALL_DIR="${10}/pixel-ops-broker"
export ODS_PIXEL_UNINSTALL_OPS_STATE_DIR="${9}/var/lib/pixel-ops-broker"
export ODS_PIXEL_UNINSTALL_ACCESS_STATE_DIR="${9}/var/lib/ods-pixel-access"
export ODS_PIXEL_UNINSTALL_ACCESS_PROBE_DIR="${9}/var/lib/ods-pixel-access-probes"
export ODS_PIXEL_UNINSTALL_PREVIEW_STATE_DIR="${9}/var/lib/ods-pixel-preview"
export ODS_PIXEL_UNINSTALL_ROOT_UID="$(id -u)"
export ODS_PIXEL_UNINSTALL_ROOT_GID="$(id -g)"

DRY_RUN=false; ENABLE_PIXEL_RUNTIME=true; ODS_SUDO_AVAILABLE=true; ODS_ASSUME_ROOTLESS=false
source "$SCRIPT_DIR/installers/lib/pixel-integration.sh"
source "$SCRIPT_DIR/lib/pixel-uninstall.sh"
_ods_pixel_project_cleanup(){ :; }
_ods_pixel_inspection_cleanup(){ :; }
ods_progress(){ :; }; chapter(){ :; }; log(){ :; }
ai(){
    if [[ "$*" == *"Copying source files"* ]]; then
        touch "$CANARY"
        # Stop at source-copy boundary so test isolates transition logic from full compose stack
        exit 42
    fi
}
error(){ printf '%s\n' "$*" >&2; return 1; }
ods_pixel_install_owner(){ id -un; }
ods_pixel_owner_home(){ printf '%s\n' "$HOME"; }

source "$SCRIPT_DIR/installers/phases/06-directories.sh"
'''
        result = subprocess.run(
            ["bash", "-c", script, "phase-fixture",
             str(self.source_dir), str(self.installed), str(self.home),
             sys.executable, str(canary), str(self.log_file), str(self.mock_bin),
             str(self.systemd_dir), str(self.etc_dir), str(self.libexec_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 42, f"Phase should reach copy-source step: {result.stdout}\n{result.stderr}")
        self.assertTrue(canary.exists(), "Source copy / bootstrap step must be reached on successful cleanup")
        self.assertFalse(self.marker.exists(), "Marker should be unlinked upon successful cleanup")
        self.assertFalse(access_unit.exists(), "Retained unit must be removed upon successful cleanup")

    def test_cleanup_refusal_repair_retry_with_incoming_config(self):
        """Cleanup refusal remains retryable even when incoming source tree contains config/."""
        payload = {
            "manager": "ods",
            "schema_version": 2,
            "state": "installing",
            "initial_active_state": "absent",
            "install_dir": str(self.installed),
            "pixel_source_ref": "d2a2b6be552126f294fb30ee5fb46872acf82c89",
        }
        self._write_marker(payload)

        # Ensure installation tree is absent initially
        if self.installed.exists():
            shutil.rmtree(self.installed)

        # Verify incoming config/ is present in source tree
        self.assertTrue((self.source_dir / "config").is_dir(), "Source must contain incoming config/")

        access_unit = self.systemd_dir / "ods-pixel-access.service"
        access_unit.write_text("[Unit]\nDescription=Drifted\n", encoding="utf-8")
        access_unit.chmod(0o644)
        canary = self.installed / "source-canary.txt"

        script = r'''
set -euo pipefail
SCRIPT_DIR="$1"; INSTALL_DIR="$2"; HOME="$3"; ODS_PYTHON_CMD="$4"; CANARY="$5"; LOG_FILE="$6"; MOCK_BIN="$7"
export PATH="$MOCK_BIN:$PATH"
export ODS_PIXEL_UNINSTALL_SYSTEMD_DIR="$8"
export ODS_PIXEL_UNINSTALL_ETC_DIR="$9"
export ODS_PIXEL_UNINSTALL_LIBEXEC_DIR="${10}"
export ODS_PIXEL_UNINSTALL_OPS_ENV="${9}/pixel-ops-broker.env"
export ODS_PIXEL_UNINSTALL_OPS_POLICY="${9}/pixel-ops-broker/policy.json"
export ODS_PIXEL_UNINSTALL_OPS_INSTALL_DIR="${10}/pixel-ops-broker"
export ODS_PIXEL_UNINSTALL_OPS_STATE_DIR="${9}/var/lib/pixel-ops-broker"
export ODS_PIXEL_UNINSTALL_ACCESS_STATE_DIR="${9}/var/lib/ods-pixel-access"
export ODS_PIXEL_UNINSTALL_ACCESS_PROBE_DIR="${9}/var/lib/ods-pixel-access-probes"
export ODS_PIXEL_UNINSTALL_PREVIEW_STATE_DIR="${9}/var/lib/ods-pixel-preview"
export ODS_PIXEL_UNINSTALL_ROOT_UID="$(id -u)"
export ODS_PIXEL_UNINSTALL_ROOT_GID="$(id -g)"

DRY_RUN=false; ENABLE_PIXEL_RUNTIME=true; ODS_SUDO_AVAILABLE=true; ODS_ASSUME_ROOTLESS=false
source "$SCRIPT_DIR/installers/lib/pixel-integration.sh"
source "$SCRIPT_DIR/lib/pixel-uninstall.sh"
_ods_pixel_project_cleanup(){ :; }
_ods_pixel_inspection_cleanup(){ :; }
ods_progress(){ :; }; chapter(){ :; }; log(){ :; }
ai(){
    if [[ "$*" == *"Copying source files"* ]]; then
        touch "$CANARY"
        exit 42
    fi
}
error(){ printf '%s\n' "$*" >&2; return 1; }
ods_pixel_install_owner(){ id -un; }
ods_pixel_owner_home(){ printf '%s\n' "$HOME"; }

source "$SCRIPT_DIR/installers/phases/06-directories.sh"
'''
        # Attempt 1: Run with drifted host unit
        result1 = subprocess.run(
            ["bash", "-c", script, "phase-fixture",
             str(self.source_dir), str(self.installed), str(self.home),
             sys.executable, str(canary), str(self.log_file), str(self.mock_bin),
             str(self.systemd_dir), str(self.etc_dir), str(self.libexec_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertNotEqual(result1.returncode, 0, f"Attempt 1 must fail closed on drifted unit: {result1.stdout}\n{result1.stderr}")
        self.assertTrue(self.marker.exists(), "Marker must remain protected on refusal")
        self.assertEqual(json.loads(self.marker.read_text(encoding="utf-8")), payload, "Marker payload must stay intact")
        self.assertTrue(access_unit.exists(), "Drifted access unit must remain untouched")
        self.assertEqual(access_unit.read_text(encoding="utf-8"), "[Unit]\nDescription=Drifted\n")
        self.assertFalse(canary.exists(), "Clean install canary must not be touched on refusal")
        self.assertFalse((self.installed / "config").exists(), "Pre-cleanup staging must not leave incoming config/ behind")

        # Repair: replace drifted host unit with genuine unit matching source
        genuine_source = ROOT / "extensions/services/pixel-agent/host/ods-pixel-access.service"
        access_unit.write_bytes(genuine_source.read_bytes())

        # Attempt 2 (Retry): Re-run on the same $INSTALL_DIR
        result2 = subprocess.run(
            ["bash", "-c", script, "phase-fixture",
             str(self.source_dir), str(self.installed), str(self.home),
             sys.executable, str(canary), str(self.log_file), str(self.mock_bin),
             str(self.systemd_dir), str(self.etc_dir), str(self.libexec_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(result2.returncode, 42, f"Attempt 2 retry must resume cleanup and proceed to clean install: {result2.stdout}\n{result2.stderr}")
        self.assertTrue(canary.exists(), "Canary must be touched on successful clean install transition")
        self.assertFalse(self.marker.exists(), "Marker must be unlinked upon successful cleanup")
        self.assertFalse(access_unit.exists(), "Retained unit must be removed upon successful cleanup")

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
