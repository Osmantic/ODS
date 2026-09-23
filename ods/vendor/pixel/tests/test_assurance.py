import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "security-evals" / "assurance" / "manifest.py"
SPEC = importlib.util.spec_from_file_location("pixel_assurance_manifest", MODULE_PATH)
manifest = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(manifest)


class AssuranceManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "source"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Pixel Test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "pixel-test@example.invalid"], cwd=self.root, check=True)
        (self.root / "security-evals" / "assurance").mkdir(parents=True)
        (self.root / "VERSION").write_text("9.9.9\n", encoding="utf-8")
        (self.root / "RELEASE-MANIFEST.json").write_text(
            json.dumps({"pixel": "9.9.9", "openclaw": "1.2.3"}) + "\n",
            encoding="utf-8",
        )
        (self.root / "security-evals" / "assurance" / "cases.json").write_text(
            json.dumps({"schemaVersion": 1, "cases": [{"id": "test", "invariant": "safe"}]}) + "\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.root, check=True)

    def tearDown(self):
        self.temporary.cleanup()

    def test_clean_manifest_binds_source_release_catalog_and_files(self):
        result = manifest.build_manifest(self.root)
        self.assertTrue(result["source"]["clean"])
        self.assertEqual(result["source"]["commit"], self.git("rev-parse", "HEAD"))
        self.assertEqual(result["source"]["tree"], self.git("rev-parse", "HEAD^{tree}"))
        self.assertEqual(result["release"], {"pixel": "9.9.9", "openclaw": "1.2.3"})
        self.assertEqual(result["attackCatalog"]["caseCount"], 1)
        paths = [record["path"] for record in result["source"]["files"]]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(result["source"]["trackedFileCount"], len(paths))

    def test_dirty_worktree_is_refused(self):
        (self.root / "VERSION").write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(manifest.AssuranceError, "dirty worktree"):
            manifest.build_manifest(self.root)

    def test_external_output_is_private_and_cannot_be_overwritten(self):
        output = Path(self.temporary.name) / "evidence.json"
        manifest.write_manifest(output, self.root)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(output.stat().st_mode) & 0o077, 0)
        loaded = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(loaded["source"]["commit"], self.git("rev-parse", "HEAD"))
        with self.assertRaisesRegex(manifest.AssuranceError, "overwrite"):
            manifest.write_manifest(output, self.root)

    def test_relative_and_inside_worktree_outputs_are_refused(self):
        with self.assertRaisesRegex(manifest.AssuranceError, "absolute"):
            manifest.write_manifest(Path("evidence.json"), self.root)
        with self.assertRaisesRegex(manifest.AssuranceError, "outside"):
            manifest.write_manifest(self.root / "evidence.json", self.root)

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True, text=True
        ).stdout.strip()


if __name__ == "__main__":
    unittest.main()
