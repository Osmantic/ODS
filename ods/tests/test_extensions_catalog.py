"""Tests for merging first-party services into the extensions catalog."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "scripts/generate-extensions-catalog.py"
SPEC = importlib.util.spec_from_file_location("catalog_generator", GENERATOR_PATH)
generator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
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

    def manifest(
        self,
        root: Path,
        service_id: str,
        *,
        manifest_id: str | None = None,
        name: str | None = None,
        disabled: bool = False,
        service_type: str = "docker",
    ) -> Path:
        directory = root / service_id
        directory.mkdir()
        is_docker = service_type == "docker"
        value = {
            "schema_version": "ods.services.v1",
            "service": {
                "id": manifest_id or service_id,
                "name": name or service_id.title(),
                "type": service_type,
                "compose_file": "compose.yaml" if is_docker else "",
                "env_vars": [{"key": "APP_TOKEN", "required": True}],
            },
            "features": [
                {
                    "name": "Image Generation",
                    "description": "Create images locally.",
                }
            ],
        }
        (directory / "manifest.yaml").write_text(
            yaml.safe_dump(value, sort_keys=False),
            encoding="utf-8",
        )
        if is_docker:
            compose_name = "compose.yaml.disabled" if disabled else "compose.yaml"
            (directory / compose_name).write_text("services: {}\n", encoding="utf-8")
        return directory

    def test_discovers_disabled_and_host_first_party_services(self):
        self.manifest(self.services, "native-editor", disabled=True)
        self.manifest(self.services, "native-host", service_type="host-systemd")

        entries = generator.generate_catalog(self.library, self.services)

        self.assertEqual([entry["id"] for entry in entries], ["native-editor", "native-host"])
        for entry in entries:
            self.assertEqual(entry["catalog_source"], "builtin")
            self.assertEqual(entry["category"], "optional")
            self.assertEqual(entry["description"], "Create images locally.")
            self.assertEqual(entry["env_vars"], [{"key": "APP_TOKEN", "required": True}])

    def test_library_only_compatibility_and_native_precedence(self):
        self.manifest(self.library, "same", name="Library Definition")
        self.manifest(self.services, "same", name="Native Definition", disabled=True)
        self.manifest(self.services, "native-only")

        library_only = generator.generate_catalog(self.library)
        merged = {
            entry["id"]: entry
            for entry in generator.generate_catalog(self.library, self.services)
        }

        self.assertEqual([entry["id"] for entry in library_only], ["same"])
        self.assertNotIn("catalog_source", library_only[0])
        self.assertEqual(merged["same"]["name"], "Native Definition")
        self.assertEqual(merged["same"]["catalog_source"], "builtin")
        self.assertIn("native-only", merged)

    def test_generation_is_deterministic(self):
        self.manifest(self.library, "beta")
        self.manifest(self.library, "alpha")
        self.manifest(self.services, "beta", name="Native Beta", disabled=True)
        self.manifest(self.services, "gamma")

        first = generator.generate_catalog(self.library, self.services)
        second = generator.generate_catalog(self.library, self.services)

        self.assertEqual(first, second)
        self.assertEqual([entry["id"] for entry in first], ["alpha", "beta", "gamma"])

    def test_cli_requires_explicit_services_directory(self):
        self.manifest(self.library, "library-only")
        self.manifest(self.services, "native-only")
        output = self.root / "catalog.json"

        subprocess.run(
            [
                sys.executable,
                str(GENERATOR_PATH),
                "--library-dir",
                str(self.library),
                "--output",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        default_ids = [entry["id"] for entry in json.loads(output.read_text())["extensions"]]

        subprocess.run(
            [
                sys.executable,
                str(GENERATOR_PATH),
                "--library-dir",
                str(self.library),
                "--services-dir",
                str(self.services),
                "--output",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        merged_ids = [entry["id"] for entry in json.loads(output.read_text())["extensions"]]

        self.assertEqual(default_ids, ["library-only"])
        self.assertEqual(merged_ids, ["library-only", "native-only"])

    @unittest.skipIf(sys.platform == "win32", "requires Unix symlink semantics")
    def test_rejects_symlinked_first_party_root(self):
        root_link = self.root / "services-link"
        root_link.symlink_to(self.services, target_is_directory=True)

        with self.assertRaisesRegex(ValueError, "unavailable or unsafe"):
            generator.generate_catalog(self.library, root_link)

        completed = subprocess.run(
            [
                sys.executable,
                str(GENERATOR_PATH),
                "--library-dir",
                str(self.library),
                "--services-dir",
                str(root_link),
                "--output",
                str(self.root / "unsafe.json"),
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("unavailable or unsafe", completed.stderr)

    @unittest.skipIf(sys.platform == "win32", "requires Unix symlink semantics")
    def test_skips_symlinked_or_mismatched_service_inputs(self):
        outside = self.root / "outside"
        outside.mkdir()
        linked_target = self.manifest(outside, "linked")
        (self.services / "linked").symlink_to(linked_target, target_is_directory=True)
        self.manifest(self.services, "actual", manifest_id="different")

        manifest_target = self.manifest(outside, "manifest-link") / "manifest.yaml"
        manifest_link_dir = self.services / "manifest-link"
        manifest_link_dir.mkdir()
        (manifest_link_dir / "manifest.yaml").symlink_to(manifest_target)

        self.assertEqual(generator.generate_catalog(self.library, self.services), [])


if __name__ == "__main__":
    unittest.main()
