"""Exercise manifest discovery through the catalog Pixel actually reads."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("catalog_generator", ROOT / "scripts/generate-extensions-catalog.py")
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.library = self.root / "extensions/library/services"
        self.services = self.root / "extensions/services"
        self.library.mkdir(parents=True)
        self.services.mkdir(parents=True)

    def manifest(self, root, service_id, *, disabled=False, compose=True):
        directory = root / service_id
        directory.mkdir()
        value = {"schema_version": "ods.services.v1", "service": {
            "id": service_id, "name": service_id.title(), "type": "docker" if compose else "host-systemd",
            "compose_file": "compose.yaml" if compose else "",
            "env_vars": [{"key": "APP_TOKEN", "required": True}],
        }, "features": [{"name": "Image Generation", "description": "Create images locally."}]}
        (directory / "manifest.yaml").write_text(json.dumps(value))
        if compose:
            (directory / ("compose.yaml.disabled" if disabled else "compose.yaml")).write_text("services: {}\n")
        return directory

    def build(self, entries):
        catalog = self.root / "catalog.json"
        catalog.write_text(json.dumps({"extensions": entries}))
        output = self.root / "private"
        output.mkdir(mode=0o700, exist_ok=True)
        script = (ROOT / "installers/lib/pixel-host-install.sh").read_text()
        function = script.split("_ods_pixel_write_extension_catalog() {", 1)[1]
        body = function.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        result = subprocess.run([sys.executable, "-", str(catalog), str(self.library), str(output / "catalog.json")],
                                input=body, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads((output / "catalog.json").read_text())

    def test_disabled_builtin_and_host_service_survive_pixel_projection(self):
        self.manifest(self.services, "native-editor", disabled=True)
        self.manifest(self.services, "native-host", compose=False)
        entries = generator.generate_catalog(self.library, self.services)
        self.assertEqual([entry["id"] for entry in entries], ["native-editor", "native-host"])
        result = self.build(entries)
        self.assertEqual([entry["id"] for entry in result["extensions"]], ["native-editor", "native-host"])
        for entry in result["extensions"]:
            self.assertEqual(entry["catalogSource"], "builtin")
            self.assertEqual(entry["configurationScope"], "declared-environment-keys")
            self.assertEqual(entry["requiredConfiguration"], ["APP_TOKEN"])
            self.assertNotIn("runtimeReady", entry)

    def test_library_only_call_and_native_collision_precedence(self):
        self.manifest(self.library, "same")
        self.manifest(self.services, "same", disabled=True)
        self.manifest(self.services, "native-only")
        old = generator.generate_catalog(self.library)
        self.assertEqual([entry["id"] for entry in old], ["same"])
        self.assertNotIn("catalog_source", old[0])
        merged = {entry["id"]: entry for entry in generator.generate_catalog(self.library, self.services)}
        self.assertEqual(merged["same"]["catalog_source"], "builtin")

    def test_shipped_comfyui_manifest_reaches_pixel_catalog(self):
        function = (ROOT / "installers/lib/pixel-host-install.sh").read_text().split("_ods_pixel_write_extension_catalog() {", 1)[1]
        body = function.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        output = self.root / "catalog.json"
        result = subprocess.run([sys.executable, "-", str(ROOT / "config/extensions-catalog.json"),
                                 str(ROOT / "extensions/library/services"), str(output)],
                                input=body, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = {entry["id"]: entry for entry in json.loads(output.read_text())["extensions"]}
        self.assertEqual(entries["comfyui"]["catalogSource"], "builtin")
        self.assertIn("Image Generation", entries["comfyui"]["featureNames"])
        self.assertIn("crewai", entries)

    @unittest.skipIf(os.name == "nt", "Linux filesystem custody qualification")
    def test_symlinked_service_and_mismatched_manifest_id_are_not_discovered(self):
        outside = self.root / "outside"
        outside.mkdir()
        target = self.manifest(outside, "linked")
        (self.services / "linked").symlink_to(target, target_is_directory=True)
        wrong = self.manifest(self.services, "actual") / "manifest.yaml"
        value = json.loads(wrong.read_text())
        value["service"]["id"] = "different"
        wrong.write_text(json.dumps(value))
        self.assertEqual(generator.generate_catalog(self.library, self.services), [])

    @unittest.skipIf(os.name == "nt", "Linux filesystem custody qualification")
    def test_projector_refuses_symlinked_service_directory(self):
        directory = self.manifest(self.services, "native-editor")
        entries = generator.generate_catalog(self.library, self.services)
        moved = self.root / "relocated"
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(AssertionError, "unsafe ODS extension directory"):
            self.build(entries)


if __name__ == "__main__":
    unittest.main()
