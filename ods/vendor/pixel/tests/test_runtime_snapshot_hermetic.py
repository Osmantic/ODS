import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "deploy/release-operator/build-runtime-snapshot.mjs"
PKG = ROOT / "deploy/release-operator"
sys.path.insert(0, str(PKG))
os.environ["PIXEL_RELEASE_MANAGED_TESTING"] = "1"
import pixel_operator as pop  # noqa: E402

KINDS = {
    "goal": "deploy/work-controller/goal-service-lifecycle-cli.mjs",
    "fleet-goal": "deploy/work-controller/goal-fleet-service-lifecycle-cli.mjs",
    "deep-work-soak": "deploy/work-controller/deep-work-soak-service-cli.mjs",
}
Z = "0" * 64


def build_snapshot():
    out = Path(tempfile.mkdtemp(prefix="pixel-snapshot-"))
    tree = subprocess.run(
        ["node", str(BUILDER), str(ROOT), str(out)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if tree.returncode != 0:
        raise RuntimeError(f"snapshot build failed: {tree.stderr.decode()}")
    return out, tree.stdout.decode().strip()


class RuntimeSnapshotHermeticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Build into a fresh private temp dir, then move/copy it away from the repo so the
        # CLI executions resolve every dependency only from the snapshot.
        built, cls.tree_sha = build_snapshot()
        cls.hermetic = Path(tempfile.mkdtemp(prefix="pixel-hermetic-")) / "snapshot"
        shutil.copytree(str(built), str(cls.hermetic))
        shutil.rmtree(str(built))

    def test_operator_goal_path_is_lifecycle_cli(self):
        # The wrong render-only goal path must be a regression test.
        self.assertEqual(pop.KIND_CLI["goal"], "deploy/work-controller/goal-service-lifecycle-cli.mjs")

    def test_manifest_present_and_bound(self):
        manifest = self.hermetic / "runtime-manifest.json"
        self.assertTrue(manifest.is_file())
        payload = manifest.read_bytes()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), self.tree_sha)

    def _run(self, *argv):
        return subprocess.run([*argv], cwd="/", stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def test_real_inspection_all_three_kinds(self):
        missing = "/nonexistent-bundle-hermetic"
        for kind, cli in KINDS.items():
            node_cli = self.hermetic / cli
            result = self._run("node", str(node_cli), "inspect", "--bundle", missing)
            self.assertNotEqual(result.returncode, 0, kind)
            message = (result.stderr + result.stdout).decode("utf-8", "replace")
            # The error is a genuine lifecycle error (not an ESM/module-resolution failure),
            # proving the snapshot is dependency-complete and the real CLI inspection ran.
            self.assertIn("bundle is not a real directory", message, kind)
            self.assertNotIn("Cannot find module", message, kind)

    def test_mutation_path_parsing_all_three_kinds(self):
        for kind, cli in KINDS.items():
            node_cli = self.hermetic / cli
            result = self._run(
                "node", str(node_cli), "install", "--bundle", "/nonexistent-bundle-hermetic",
                "--confirm-manifest-sha256", Z,
            )
            self.assertNotEqual(result.returncode, 0, kind)
            message = (result.stderr + result.stdout).decode("utf-8", "replace")
            # Non-root mutations reach the real CLI's root-authority boundary (mutation-path
            # parsing works), rather than a module-resolution failure.
            self.assertIn("root", message, kind)


if __name__ == "__main__":
    unittest.main()
