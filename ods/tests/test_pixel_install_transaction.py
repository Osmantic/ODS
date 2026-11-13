#!/usr/bin/env python3
"""Regression tests for ODS Pixel install transaction manifest and ambient conflict resolution."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux test execution required")
class PixelInstallTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ods-pixel-tx-")
        self.addCleanup(self.temporary.cleanup)
        self.base = pathlib.Path(self.temporary.name)
        self.home = self.base / "home"
        self.install_dir = self.base / "ods"
        self.home.mkdir(parents=True, exist_ok=True)
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.owner = subprocess.check_output(["id", "-un"], text=True).strip()
        self.uid = os.getuid()
        self.pixel_source_ref = "b" * 40

    def _run_shell(self, script):
        env = os.environ.copy()
        env["INSTALL_DIR"] = str(self.install_dir)
        env["PIXEL_SOURCE_REF"] = self.pixel_source_ref
        env["ODS_PIXEL_GATEWAY_UNIT_PATH"] = str(self.home / "nonexistent.service")
        cmd = [
            "bash",
            "-euo",
            "pipefail",
            "-c",
            f"""
source '{ROOT}/installers/lib/pixel-host-install.sh'
ai(){{ :; }}; ai_ok(){{ :; }}; ai_bad(){{ :; }}; ai_warn(){{ :; }}
{script}
""",
        ]
        return subprocess.run(cmd, env=env, capture_output=True, text=True)

    def test_ambient_without_transaction_fails_closed(self):
        """Ambient OpenClaw deployment without transaction fails closed."""
        openclaw_dir = self.home / ".openclaw"
        openclaw_dir.mkdir(parents=True, exist_ok=True)
        (openclaw_dir / "openclaw.json").write_text("{}", encoding="utf-8")

        res = self._run_shell(f'_ods_pixel_assert_managed_state "{self.owner}" "{self.home}"')
        self.assertNotEqual(res.returncode, 0)
        marker = self.home / ".config/ods/pixel-managed.json"
        self.assertFalse(marker.exists())

    def test_interrupted_transaction_in_home_allows_resumption(self):
        """Valid install transaction in home directory permits resuming interrupted setup."""
        openclaw_dir = self.home / ".openclaw"
        openclaw_dir.mkdir(parents=True, exist_ok=True)
        (openclaw_dir / "openclaw.json").write_text("{}", encoding="utf-8")

        tx_dir = self.home / ".config/ods"
        tx_dir.mkdir(parents=True, exist_ok=True)
        tx_file = tx_dir / ".pixel-install-transaction.json"
        tx_file.write_text(
            json.dumps({
                "schema_version": 1,
                "manager": "ods",
                "install_dir": str(self.install_dir),
                "pixel_source_ref": self.pixel_source_ref,
                "uid": self.uid,
            }),
            encoding="utf-8",
        )
        tx_file.chmod(0o600)

        res = self._run_shell(f'_ods_pixel_assert_managed_state "{self.owner}" "{self.home}"')
        self.assertEqual(res.returncode, 0, f"Expected success but got: {res.stderr}")
        marker = self.home / ".config/ods/pixel-managed.json"
        self.assertTrue(marker.exists())
        data = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(data.get("manager"), "ods")
        self.assertEqual(data.get("state"), "installing")
        self.assertEqual(data.get("initial_active_state"), "absent")

    def test_interrupted_transaction_in_install_dir_allows_resumption(self):
        """Valid install transaction in INSTALL_DIR permits resuming interrupted setup."""
        openclaw_dir = self.home / ".openclaw"
        openclaw_dir.mkdir(parents=True, exist_ok=True)
        (openclaw_dir / "openclaw.json").write_text("{}", encoding="utf-8")

        local_tx = self.install_dir / ".ods-pixel-transaction"
        local_tx.write_text(
            json.dumps({
                "schema_version": 1,
                "manager": "ods",
                "install_dir": str(self.install_dir),
                "pixel_source_ref": self.pixel_source_ref,
                "uid": self.uid,
            }),
            encoding="utf-8",
        )
        local_tx.chmod(0o600)

        res = self._run_shell(f'_ods_pixel_assert_managed_state "{self.owner}" "{self.home}"')
        self.assertEqual(res.returncode, 0, f"Expected success but got: {res.stderr}")
        marker = self.home / ".config/ods/pixel-managed.json"
        self.assertTrue(marker.exists())

    def test_onboarding_without_transaction_fails_closed(self):
        """Ambient OpenClaw with mode-0666 onboarding.json but no transaction fails closed."""
        openclaw_dir = self.home / ".openclaw"
        openclaw_dir.mkdir(parents=True, exist_ok=True)
        (openclaw_dir / "openclaw.json").write_text("{}", encoding="utf-8")

        plugin_dir = self.install_dir / "extensions/services/pixel-agent/plugin"
        plugin_dir.mkdir(parents=True, exist_ok=True)

        deployment_dir = self.home / ".config/pixel-deployment"
        deployment_dir.mkdir(parents=True, exist_ok=True)
        onboarding_file = deployment_dir / "onboarding.json"
        onboarding_file.write_text(
            json.dumps({
                "deploymentName": "ods-default",
                "gatewayExtensions": [
                    {
                        "id": "pixel-ods",
                        "path": str(plugin_dir),
                    }
                ],
            }),
            encoding="utf-8",
        )
        onboarding_file.chmod(0o666)

        res = self._run_shell(f'_ods_pixel_assert_managed_state "{self.owner}" "{self.home}"')
        self.assertNotEqual(res.returncode, 0)
        marker = self.home / ".config/ods/pixel-managed.json"
        self.assertFalse(marker.exists())

    def test_dangling_onboarding_without_transaction_fails_closed(self):
        """Ambient OpenClaw with dangling onboarding plugin path and no transaction fails closed."""
        openclaw_dir = self.home / ".openclaw"
        openclaw_dir.mkdir(parents=True, exist_ok=True)
        (openclaw_dir / "openclaw.json").write_text("{}", encoding="utf-8")

        deployment_dir = self.home / ".config/pixel-deployment"
        deployment_dir.mkdir(parents=True, exist_ok=True)
        onboarding_file = deployment_dir / "onboarding.json"
        onboarding_file.write_text(
            json.dumps({
                "deploymentName": "ods-default",
                "gatewayExtensions": [
                    {
                        "id": "pixel-ods",
                        "path": "/nonexistent/path/extensions/services/pixel-agent/plugin",
                    }
                ],
            }),
            encoding="utf-8",
        )

        res = self._run_shell(f'_ods_pixel_assert_managed_state "{self.owner}" "{self.home}"')
        self.assertNotEqual(res.returncode, 0)
        marker = self.home / ".config/ods/pixel-managed.json"
        self.assertFalse(marker.exists())

    def test_mismatched_uid_transaction_fails_closed(self):
        """Transaction recording a different UID fails closed."""
        openclaw_dir = self.home / ".openclaw"
        openclaw_dir.mkdir(parents=True, exist_ok=True)
        (openclaw_dir / "openclaw.json").write_text("{}", encoding="utf-8")

        tx_dir = self.home / ".config/ods"
        tx_dir.mkdir(parents=True, exist_ok=True)
        tx_file = tx_dir / ".pixel-install-transaction.json"
        tx_file.write_text(
            json.dumps({
                "schema_version": 1,
                "manager": "ods",
                "install_dir": str(self.install_dir),
                "pixel_source_ref": self.pixel_source_ref,
                "uid": self.uid + 999,
            }),
            encoding="utf-8",
        )
        tx_file.chmod(0o600)

        res = self._run_shell(f'_ods_pixel_assert_managed_state "{self.owner}" "{self.home}"')
        self.assertNotEqual(res.returncode, 0)
        marker = self.home / ".config/ods/pixel-managed.json"
        self.assertFalse(marker.exists())

    def test_mismatched_source_ref_transaction_fails_closed(self):
        """Transaction recording a different pixel_source_ref fails closed."""
        openclaw_dir = self.home / ".openclaw"
        openclaw_dir.mkdir(parents=True, exist_ok=True)
        (openclaw_dir / "openclaw.json").write_text("{}", encoding="utf-8")

        tx_dir = self.home / ".config/ods"
        tx_dir.mkdir(parents=True, exist_ok=True)
        tx_file = tx_dir / ".pixel-install-transaction.json"
        tx_file.write_text(
            json.dumps({
                "schema_version": 1,
                "manager": "ods",
                "install_dir": str(self.install_dir),
                "pixel_source_ref": "c" * 40,
                "uid": self.uid,
            }),
            encoding="utf-8",
        )
        tx_file.chmod(0o600)

        res = self._run_shell(f'_ods_pixel_assert_managed_state "{self.owner}" "{self.home}"')
        self.assertNotEqual(res.returncode, 0)
        marker = self.home / ".config/ods/pixel-managed.json"
        self.assertFalse(marker.exists())

    def test_mismatched_install_dir_transaction_fails_closed(self):
        """Transaction referencing a different install directory fails closed."""
        openclaw_dir = self.home / ".openclaw"
        openclaw_dir.mkdir(parents=True, exist_ok=True)
        (openclaw_dir / "openclaw.json").write_text("{}", encoding="utf-8")

        tx_dir = self.home / ".config/ods"
        tx_dir.mkdir(parents=True, exist_ok=True)
        tx_file = tx_dir / ".pixel-install-transaction.json"
        tx_file.write_text(
            json.dumps({
                "schema_version": 1,
                "manager": "ods",
                "install_dir": "/opt/unrelated-ods",
                "pixel_source_ref": self.pixel_source_ref,
                "uid": self.uid,
            }),
            encoding="utf-8",
        )
        tx_file.chmod(0o600)

        res = self._run_shell(f'_ods_pixel_assert_managed_state "{self.owner}" "{self.home}"')
        self.assertNotEqual(res.returncode, 0)
        marker = self.home / ".config/ods/pixel-managed.json"
        self.assertFalse(marker.exists())

    def test_insecure_transaction_permissions_fails_closed(self):
        """Transaction file with group/world-writable permissions is refused."""
        openclaw_dir = self.home / ".openclaw"
        openclaw_dir.mkdir(parents=True, exist_ok=True)
        (openclaw_dir / "openclaw.json").write_text("{}", encoding="utf-8")

        tx_dir = self.home / ".config/ods"
        tx_dir.mkdir(parents=True, exist_ok=True)
        tx_file = tx_dir / ".pixel-install-transaction.json"
        tx_file.write_text(
            json.dumps({
                "schema_version": 1,
                "manager": "ods",
                "install_dir": str(self.install_dir),
                "pixel_source_ref": self.pixel_source_ref,
                "uid": self.uid,
            }),
            encoding="utf-8",
        )
        tx_file.chmod(0o666)

        res = self._run_shell(f'_ods_pixel_assert_managed_state "{self.owner}" "{self.home}"')
        self.assertNotEqual(res.returncode, 0)
        marker = self.home / ".config/ods/pixel-managed.json"
        self.assertFalse(marker.exists())

    def test_symlinked_transaction_fails_closed(self):
        """Symlinked transaction file is refused."""
        openclaw_dir = self.home / ".openclaw"
        openclaw_dir.mkdir(parents=True, exist_ok=True)
        (openclaw_dir / "openclaw.json").write_text("{}", encoding="utf-8")

        tx_dir = self.home / ".config/ods"
        tx_dir.mkdir(parents=True, exist_ok=True)
        real_file = self.base / "target-tx.json"
        real_file.write_text(
            json.dumps({
                "schema_version": 1,
                "manager": "ods",
                "install_dir": str(self.install_dir),
            }),
            encoding="utf-8",
        )
        real_file.chmod(0o600)
        (tx_dir / ".pixel-install-transaction.json").symlink_to(real_file)

        res = self._run_shell(f'_ods_pixel_assert_managed_state "{self.owner}" "{self.home}"')
        self.assertNotEqual(res.returncode, 0)
        marker = self.home / ".config/ods/pixel-managed.json"
        self.assertFalse(marker.exists())

    def test_writer_refuses_ambient_foreign_deployment(self):
        """Writer refuses to create transaction evidence against ambient foreign deployment."""
        openclaw_dir = self.home / ".openclaw"
        openclaw_dir.mkdir(parents=True, exist_ok=True)
        (openclaw_dir / "openclaw.json").write_text("{}", encoding="utf-8")

        res = self._run_shell(f'_ods_pixel_write_install_transaction "{self.owner}" "{self.home}"')
        self.assertNotEqual(res.returncode, 0)
        tx_marker = self.home / ".config/ods/.pixel-install-transaction.json"
        local_tx = self.install_dir / ".ods-pixel-transaction"
        self.assertFalse(tx_marker.exists())
        self.assertFalse(local_tx.exists())

    def test_production_sequence_foreign_deployment_fails_closed(self):
        """Writer-then-guard sequence against ambient foreign deployment fails closed without marker."""
        openclaw_dir = self.home / ".openclaw"
        openclaw_dir.mkdir(parents=True, exist_ok=True)
        (openclaw_dir / "openclaw.json").write_text("{}", encoding="utf-8")

        # Run production sequence (writer then guard)
        res = self._run_shell(
            f'_ods_pixel_write_install_transaction "{self.owner}" "{self.home}" || true; '
            f'_ods_pixel_assert_managed_state "{self.owner}" "{self.home}"'
        )
        self.assertNotEqual(res.returncode, 0)
        marker = self.home / ".config/ods/pixel-managed.json"
        tx_marker = self.home / ".config/ods/.pixel-install-transaction.json"
        local_tx = self.install_dir / ".ods-pixel-transaction"
        self.assertFalse(marker.exists())
        self.assertFalse(tx_marker.exists())
        self.assertFalse(local_tx.exists())

    def test_production_sequence_interrupted_transaction_allows_resumption(self):
        """Production sequence allows resumption when valid interrupted transaction already exists."""
        openclaw_dir = self.home / ".openclaw"
        openclaw_dir.mkdir(parents=True, exist_ok=True)
        (openclaw_dir / "openclaw.json").write_text("{}", encoding="utf-8")

        # Prior interrupted install left valid transaction evidence
        tx_dir = self.home / ".config/ods"
        tx_dir.mkdir(parents=True, exist_ok=True)
        tx_file = tx_dir / ".pixel-install-transaction.json"
        tx_file.write_text(
            json.dumps({
                "schema_version": 1,
                "manager": "ods",
                "install_dir": str(self.install_dir),
                "pixel_source_ref": self.pixel_source_ref,
                "uid": self.uid,
            }),
            encoding="utf-8",
        )
        tx_file.chmod(0o600)

        # Both guard-then-writer and writer-then-guard should succeed for genuine interrupted install
        res = self._run_shell(
            f'_ods_pixel_assert_managed_state "{self.owner}" "{self.home}" && '
            f'_ods_pixel_write_install_transaction "{self.owner}" "{self.home}"'
        )
        self.assertEqual(res.returncode, 0, f"Expected success but got: {res.stderr}")
        marker = self.home / ".config/ods/pixel-managed.json"
        self.assertTrue(marker.exists())
        data = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(data.get("manager"), "ods")
        self.assertEqual(data.get("state"), "installing")
        self.assertEqual(data.get("initial_active_state"), "absent")

    def test_transaction_lifecycle_write_and_clear(self):
        """Write and clear lifecycle functions manage markers cleanly."""
        res_write = self._run_shell(f'_ods_pixel_write_install_transaction "{self.owner}" "{self.home}"')
        self.assertEqual(res_write.returncode, 0, f"Write failed: {res_write.stderr}")
        tx_marker = self.home / ".config/ods/.pixel-install-transaction.json"
        local_tx = self.install_dir / ".ods-pixel-transaction"
        self.assertTrue(tx_marker.is_file())
        self.assertTrue(local_tx.is_file())
        self.assertEqual(tx_marker.stat().st_mode & 0o077, 0)
        self.assertEqual(local_tx.stat().st_mode & 0o077, 0)

        data = json.loads(tx_marker.read_text(encoding="utf-8"))
        self.assertEqual(data.get("manager"), "ods")
        self.assertEqual(data.get("schema_version"), 1)
        self.assertEqual(data.get("install_dir"), str(self.install_dir))

        res_clear = self._run_shell(f'_ods_pixel_clear_install_transaction "{self.owner}" "{self.home}"')
        self.assertEqual(res_clear.returncode, 0, f"Clear failed: {res_clear.stderr}")
        self.assertFalse(tx_marker.exists())
        self.assertFalse(local_tx.exists())


if __name__ == "__main__":
    unittest.main()
