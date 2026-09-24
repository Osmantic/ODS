#!/usr/bin/env python3
"""Exercise product metadata drift without changing dependency or historical versions."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("version_gate", REPO / "ods/scripts/check-version-consistency.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class VersionConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        files = [
            "ARCHITECTURE.md", "installer/package.json", "installer/package-lock.json",
            "installer/src-tauri/tauri.conf.json", "installer/src-tauri/Cargo.toml",
            "installer/src-tauri/Cargo.lock", "ods/manifest.json", "ods/CHANGELOG.md",
            "ods/bin/ods-host-agent.py",
            "ods/extensions/services/dashboard/src/hooks/useSystemStatus.js",
            "ods/.env.example", "ods/ods-cli", "ods/installers/lib/constants.sh",
            "ods/installers/lib/pixel-host-install.sh", "ods/installers/macos/lib/constants.sh",
            "ods/installers/windows/lib/constants.ps1", "ods/installers/phases/06-directories.sh",
            "ods/extensions/services/dashboard-api/main.py",
            "ods/extensions/services/dashboard/package.json",
            "ods/extensions/services/dashboard/package-lock.json",
        ]
        for relative in files:
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / relative, target)
        original_root = gate.ROOT
        self.addCleanup(setattr, gate, "ROOT", original_root)
        gate.ROOT = self.repo / "ods"

    def run_gate(self, expected):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = gate.main()
        self.assertEqual(result, expected, out.getvalue())
        return out.getvalue()

    def update_json(self, relative, mutate):
        path = self.repo / relative
        data = json.loads(path.read_text(encoding="utf-8"))
        mutate(data)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_current_candidate_and_published_baseline_can_differ(self):
        self.run_gate(0)

    def test_package_lock_root_drift_is_detected(self):
        self.update_json("installer/package-lock.json", lambda d: d["packages"][""].update(version="0.1.0"))
        self.assertIn("root package version", self.run_gate(1))

    def test_dashboard_package_drift_is_detected(self):
        self.update_json("ods/extensions/services/dashboard/package.json", lambda d: d.update(version="0.1.0"))
        self.assertIn("dashboard/package.json", self.run_gate(1))

    def test_cargo_installer_drift_is_detected(self):
        path = self.repo / "installer/src-tauri/Cargo.lock"
        source = path.read_text(encoding="utf-8")
        source = source.replace('name = "ods-installer"\nversion = "3.0.0"', 'name = "ods-installer"\nversion = "0.1.0"')
        path.write_text(source, encoding="utf-8")
        self.assertIn("Cargo.lock", self.run_gate(1))

    def test_stable_channel_cannot_claim_an_older_published_version(self):
        self.update_json("ods/manifest.json", lambda d: d["release"].update(channel="stable"))
        self.assertIn("stable channel", self.run_gate(1))

    def test_stable_version_cannot_exceed_candidate(self):
        self.update_json("ods/manifest.json", lambda d: d["release"].update(stable_version="99.0.0"))
        self.assertIn("cannot exceed", self.run_gate(1))

    def test_malformed_product_version_is_reported(self):
        self.update_json("ods/manifest.json", lambda d: d.update(ods_version="V3"))
        self.assertIn("must be x.y.z", self.run_gate(1))

    def test_host_agent_product_version_is_not_the_component_version(self):
        path = self.repo / "ods/bin/ods-host-agent.py"
        source = path.read_text(encoding="utf-8")
        source = source.replace('ODS_VERSION = "3.0.0"', 'ODS_VERSION = "1.0.0"', 1)
        path.write_text(source, encoding="utf-8")
        self.assertIn("ods-host-agent.py ODS version", self.run_gate(1))

    def test_dependency_versions_are_independent(self):
        self.update_json("installer/package.json", lambda d: d["dependencies"].update(react="99.1.2"))
        self.run_gate(0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
