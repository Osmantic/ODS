#!/usr/bin/env python3
"""Opt-in WSL/Docker Desktop regression; never restarts WSL or the ODS stack.

Run with ODS_WSL_RECOVERY_LIVE=1. Uses an already-installed image with Python,
stat and tar (ODS_RECOVERY_TEST_IMAGE can override the dashboard API image).
Only the unique fixture Compose project is removed; artifacts stay in /tmp.
"""
import importlib.util
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
import uuid

spec = importlib.util.spec_from_file_location(
    "recovery", Path(__file__).resolve().parents[1] / "scripts/wsl-bind-recovery.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


@unittest.skipUnless(os.environ.get("ODS_WSL_RECOVERY_LIVE") == "1", "opt-in live Docker test")
class LiveRecovery(unittest.TestCase):
    def test_replaced_source_preserves_original_view_and_recreates_only_fixture(self):
        self.assertTrue(recovery.is_wsl_docker_desktop())
        image = os.environ.get("ODS_RECOVERY_TEST_IMAGE")
        if not image:
            image = recovery.run(["docker", "inspect", "ods-dashboard-api", "--format", "{{.Image}}"], 15).stdout.strip()
        root = Path(tempfile.mkdtemp(prefix="ods-bind-recovery-"))
        source = root / "current"
        source.mkdir(mode=0o755)
        (source / "old-marker").write_text("preserve-original-view")
        project = "ods-bind-test-" + uuid.uuid4().hex[:12]
        compose = root / "compose.json"
        compose.write_text(json.dumps({"name": project, "services": {"fixture": {
            "image": image, "entrypoint": [],
            "command": ["python", "-c", "import time; time.sleep(600)"],
            "network_mode": "none", "volumes": [{"type": "bind", "source": str(source), "target": "/data"}]
        }}}))
        flags = ["--project-directory", str(root), "-p", project, "-f", str(compose)]
        cli = ["--install-dir", str(root), "--service", "fixture", "--", *flags]
        def current_id():
            return recovery.run(["docker", "compose", *flags, "ps", "-q", "fixture"], 15).stdout.strip()
        try:
            self.assertEqual(recovery.main(["--check", *cli]), 0)
            self.assertEqual(recovery.main(cli), 0)
            self.assertEqual(current_id(), "")
            recovery.run(["docker", "compose", *flags, "up", "-d", "--no-build", "--pull", "never"], 60)
            initial = current_id()
            self.assertEqual(recovery.main(["--check", *cli]), 0)
            self.assertEqual(recovery.main(cli), 0)
            self.assertEqual(current_id(), initial)
            # A different selected manifest leaves this same-project orphan
            # alone, as Compose does after an extension is disabled or removed.
            selected = root / "selected.json"
            selected.write_text(json.dumps({"name": project, "services": {"selected": {
                "image": image, "network_mode": "none"
            }}}))
            selected_cli = ["--install-dir", str(root), "--", "-p", project, "-f", str(selected)]
            self.assertEqual(recovery.main(["--check", *selected_cli]), 0)
            self.assertEqual(recovery.main(selected_cli), 0)
            self.assertEqual(current_id(), initial)
            source.rename(root / "retained-original")
            source.mkdir(mode=0o755)
            (source / "new-marker").write_text("new-source-generation")
            self.assertEqual(recovery.run(["docker", "exec", initial, "cat", "/data/old-marker"], 15).stdout, "preserve-original-view")
            self.assertEqual(recovery.main(["--check", *cli]), 2)
            self.assertEqual(current_id(), initial)
            self.assertEqual(recovery.main(cli), 0)
            repaired = current_id()
            self.assertNotEqual(repaired, initial)
            self.assertEqual(recovery.run(["docker", "exec", repaired, "cat", "/data/new-marker"], 15).stdout, "new-source-generation")
            archives = list((root / recovery.BACKUP_ROOT_REL).glob("*/*.tar"))
            self.assertEqual(len(archives), 1)
            self.assertEqual(archives[0].stat().st_mode & 0o777, 0o600)
            with tarfile.open(archives[0]) as archive:
                self.assertEqual(archive.extractfile("./old-marker").read(), b"preserve-original-view")
                self.assertNotIn("./new-marker", archive.getnames())
            self.assertEqual(recovery.main(["--check", *cli]), 0)
            self.assertEqual(recovery.main(cli), 0)
            self.assertEqual(current_id(), repaired)
            print(json.dumps({"live_recovery": "passed", "artifacts": str(root), "project": project,
                              "stale_readonly_check": True, "old_view_preserved": True, "repeat_idempotent": True}))
        finally:
            recovery._deadline = None
            recovery.run(["docker", "compose", *flags, "down", "--timeout", "5"], 30)

    def test_failed_oci_file_mount_recovers_after_source_is_available(self):
        self.assertTrue(recovery.is_wsl_docker_desktop())
        image = os.environ.get("ODS_RECOVERY_TEST_IMAGE")
        if not image:
            image = recovery.run(["docker", "inspect", "ods-dashboard-api", "--format", "{{.Image}}"], 15).stdout.strip()
        root = Path(tempfile.mkdtemp(prefix="ods-bind-recovery-oci-"))
        source = root / "current"
        source.mkdir(mode=0o755)
        project = "ods-bind-oci-" + uuid.uuid4().hex[:12]
        target = "/etc/passwd"
        compose = root / "compose.json"
        compose.write_text(json.dumps({"name": project, "services": {"fixture": {
            "image": image, "entrypoint": [],
            "command": ["python", "-c", "import time; time.sleep(600)"],
            "network_mode": "none", "volumes": [{"type": "bind", "source": str(source),
                                                    "target": target, "read_only": True}]
        }}}))
        flags = ["--project-directory", str(root), "-p", project, "-f", str(compose)]
        cli = ["--install-dir", str(root), "--service", "fixture", "--", *flags]

        def current_id():
            return recovery.run(["docker", "compose", *flags, "ps", "-a", "-q", "fixture"], 15).stdout.strip()

        try:
            self.assertEqual(recovery.main(["--check", *cli]), 0)
            self.assertEqual(recovery.main(cli), 0)
            self.assertEqual(current_id(), "")
            up = recovery.run(["docker", "compose", *flags, "up", "-d", "--no-build", "--pull", "never"],
                              60, check=False)
            self.assertNotEqual(up.returncode, 0, "directory over regular image file must fail")
            stopped = current_id()
            self.assertTrue(stopped, "expected an actual failed OCI container")
            state = recovery.inspect_container(stopped)["State"]
            self.assertIn(state["Status"], ("created", "exited", "dead"))
            self.assertIn(target, state["Error"])
            self.assertIn("not a directory", state["Error"].lower())
            source.rename(root / "retained-original")
            source.write_text("new-source-generation")
            self.assertEqual(recovery.main(["--check", *cli]), 2)
            self.assertEqual(current_id(), stopped)
            self.assertEqual(recovery.main(cli), 0)
            repaired = current_id()
            self.assertNotEqual(repaired, stopped)
            self.assertEqual(recovery.run(["docker", "exec", repaired, "cat", target], 15).stdout,
                             "new-source-generation")
            self.assertEqual(list((root / recovery.BACKUP_ROOT_REL).glob("*/*.tar")), [])
            self.assertEqual(recovery.main(["--check", *cli]), 0)
            self.assertEqual(recovery.main(cli), 0)
            self.assertEqual(current_id(), repaired)
            print(json.dumps({"stopped_oci_recovery": "passed", "artifacts": str(root), "project": project,
                              "fresh_empty_noop": True, "repeat_idempotent": True}))
        finally:
            recovery._deadline = None
            recovery.run(["docker", "compose", *flags, "down", "--timeout", "5"], 30)


if __name__ == "__main__":
    unittest.main()
