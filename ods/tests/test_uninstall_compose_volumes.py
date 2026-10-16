"""Behavioral checks for uninstalling volumes from disabled Compose fragments."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "uninstall_compose_volumes", ROOT / "scripts/uninstall-compose-volumes.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTAINER_ID = "a" * 64


class FakeDocker:
    def __init__(self, root):
        self.root = root
        self.config = {"name": "ods", "volumes": {}}
        self.containers = [self._container(root)]
        self.volumes = {
            "ods_perplexica-data": self._volume("ods_perplexica-data", "perplexica-data"),
            "ods_perplexica-uploads": self._volume("ods_perplexica-uploads", "perplexica-uploads"),
        }
        self.foreign = {"ods-pixel-retired-research", "ods-unrelated"}
        self.foreign_consumers = {}
        self.removed = []
        self.uninspectable = set()

    @staticmethod
    def _volume(name, key):
        return {
            "Name": name, "Driver": "local", "CreatedAt": "2026-10-01T00:00:00Z",
            "Labels": {
                "com.docker.compose.project": "ods",
                "com.docker.compose.volume": key,
                "com.docker.compose.version": "5.1.1",
            },
        }

    @staticmethod
    def _container(root):
        return {
            "Id": CONTAINER_ID,
            "Config": {"Labels": {
                "com.docker.compose.project": "ods",
                "com.docker.compose.service": "perplexica",
                "com.docker.compose.project.working_dir": str(root),
                "com.docker.compose.project.config_files": str(root / "docker-compose.base.yml")
                + "," + str(root / "extensions/services/perplexica/compose.yaml"),
            }},
            "Mounts": [
                {"Type": "volume", "Name": "ods_perplexica-data"},
                {"Type": "volume", "Name": "ods_perplexica-uploads"},
            ],
        }

    def __call__(self, root, *args):
        assert root == self.root
        if args[0] == "compose" and args[-3:] == ("config", "--format", "json"):
            return json.dumps(self.config)
        if args[0] == "ps":
            visible = [
                row for row in self.containers
                if "--all" in args or row.get("State", {}).get("Status") != "exited"
            ]
            project_filter = next((arg.split("=", 2)[2] for arg in args
                                   if arg.startswith("label=com.docker.compose.project=")), None)
            if project_filter is not None:
                visible = [row for row in visible if row["Config"]["Labels"].get(
                    "com.docker.compose.project") == project_filter]
            elif "label=com.docker.compose.project" in args:
                visible = [row for row in visible if "com.docker.compose.project" in
                           row["Config"]["Labels"]]
            volume_filter = next((arg.split("=", 1)[1] for arg in args
                                  if arg.startswith("volume=")), None)
            if volume_filter is not None:
                ids = [row["Id"] for row in visible if any(
                    mount.get("Name") == volume_filter for mount in row["Mounts"]
                )]
                ids.extend(self.foreign_consumers.get(volume_filter, []))
                return "\n".join(ids)
            if "--format" in args:
                return "\n".join(json.dumps({
                    "Id": row["Id"],
                    "workingDir": row["Config"]["Labels"].get(
                        "com.docker.compose.project.working_dir", ""),
                    "configFiles": row["Config"]["Labels"].get(
                        "com.docker.compose.project.config_files", ""),
                }) for row in visible)
            return "\n".join(row["Id"] for row in visible)
        if args[0] == "inspect":
            wanted = set(args[1:])
            if wanted & self.uninspectable:
                raise ValueError("Docker inspect inspection failed (exit 1)")
            return json.dumps([row for row in self.containers if row["Id"] in wanted])
        if args[:2] == ("volume", "ls"):
            if any(arg.startswith("label=com.docker.compose.project=") for arg in args):
                return "\n".join(sorted(name for name, row in self.volumes.items()
                                        if (row.get("Labels") or {}).get(
                                            "com.docker.compose.project") == "ods"))
            return "\n".join(sorted(self.volumes))
        if args[:2] == ("volume", "inspect"):
            return json.dumps([self.volumes[name] for name in args[2:]])
        if args[:2] == ("volume", "rm"):
            self.removed.append(args[2])
            del self.volumes[args[2]]
            return args[2]
        raise AssertionError(f"Unexpected Docker operation: {args}")


NATIVE_KEYS = ("pixel-native-preview-runtime", "pixel-native-previews", "pixel-native-runtime")


class NativePixelStackVolumeTests(unittest.TestCase):
    """#7071: a macOS native-Pixel install that stops after starting its stack
    leaves ODS-owned volumes before the recipe reaches .compose-flags."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ods-native-volume-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "ods"
        (self.root / "installers/macos").mkdir(parents=True)
        (self.root / "docker-compose.base.yml").write_text("services: {}\n", encoding="utf-8")
        self.native = self.root / "installers/macos/pixel-native.compose.yaml.disabled"
        shutil.copyfile(ROOT / "installers/macos/pixel-native.compose.yaml.disabled", self.native)
        self.snapshot = Path(self.temp.name) / "snapshot.json"
        self.snapshot.touch()
        self.fake = FakeDocker(self.root)
        self.fake.volumes = {f"ods_{key}": FakeDocker._volume(f"ods_{key}", key) for key in NATIVE_KEYS}
        patcher = patch.object(MODULE, "docker", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _native_container(self, service="pixel-native-ingress", recipe=None):
        recipe = recipe or self.native
        return {
            "Id": CONTAINER_ID,
            "Config": {"Labels": {
                "com.docker.compose.project": "ods",
                "com.docker.compose.service": service,
                "com.docker.compose.project.working_dir": str(self.root),
                "com.docker.compose.project.config_files":
                    str(self.root / "docker-compose.base.yml") + "," + str(recipe),
            }},
            "Mounts": [{"Type": "volume", "Name": name} for name in self.fake.volumes],
        }

    def test_failed_native_install_volumes_are_owned_and_purged(self):
        self.fake.containers = [self._native_container()]
        MODULE.preflight(self.root, self.snapshot, ["-f", "docker-compose.base.yml"])
        self.fake.containers = []
        MODULE.complete(self.root, self.snapshot)
        self.assertEqual(self.fake.volumes, {})

    def test_service_not_declared_by_the_native_recipe_cannot_claim_its_volumes(self):
        self.fake.containers = [self._native_container(service="perplexica")]
        with self.assertRaisesRegex(ValueError, "not linked to this installation"):
            MODULE.preflight(self.root, self.snapshot, ["-f", "docker-compose.base.yml"])

    def test_copy_of_the_native_recipe_elsewhere_cannot_claim_its_volumes(self):
        copy = self.root / "data/user-extensions/look-alike/pixel-native.compose.yaml.disabled"
        copy.parent.mkdir(parents=True)
        shutil.copyfile(self.native, copy)
        self.fake.containers = [self._native_container(recipe=copy)]
        with self.assertRaisesRegex(ValueError, "not linked to this installation"):
            MODULE.preflight(self.root, self.snapshot, ["-f", "docker-compose.base.yml"])

    def _shared_edge_container(self, service="pixel-edge", *, all_volumes=False):
        edge = self.root / "extensions/services/pixel-edge/compose.yaml.disabled"
        edge.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "extensions/services/pixel-edge/compose.yaml.disabled", edge)
        keys = (*NATIVE_KEYS, "pixel-transition-state") if all_volumes else ("pixel-transition-state",)
        self.fake.volumes = {f"ods_{key}": FakeDocker._volume(f"ods_{key}", key) for key in keys}
        container = self._native_container(service=service, recipe=edge)
        container["Config"]["Labels"]["com.docker.compose.project.config_files"] += "," + str(self.native)
        self.fake.containers = [container]
        return edge

    def test_failed_native_stack_includes_shared_edge_transition_volume(self):
        self._shared_edge_container(all_volumes=True)
        MODULE.preflight(self.root, self.snapshot, ["-f", "docker-compose.base.yml"])
        captured = json.loads(self.snapshot.read_text(encoding="utf-8"))
        self.assertEqual(set(captured["volumes"]), {f"ods_{key}" for key in (*NATIVE_KEYS, "pixel-transition-state")})
        self.fake.containers = []
        MODULE.complete(self.root, self.snapshot)
        self.assertEqual(self.fake.volumes, {})

    def test_wrong_service_cannot_claim_shared_edge_transition_volume(self):
        self._shared_edge_container(service="perplexica")
        with self.assertRaisesRegex(ValueError, "not linked to this installation"):
            MODULE.preflight(self.root, self.snapshot, ["-f", "docker-compose.base.yml"])

    def test_copied_shared_edge_recipe_cannot_claim_transition_volume(self):
        edge = self._shared_edge_container()
        copy = self.root / "data/user-extensions/look-alike/edge.compose.yaml.disabled"
        copy.parent.mkdir(parents=True)
        shutil.copyfile(edge, copy)
        labels = self.fake.containers[0]["Config"]["Labels"]
        labels["com.docker.compose.project.config_files"] = labels["com.docker.compose.project.config_files"].replace(str(edge), str(copy))
        with self.assertRaisesRegex(ValueError, "not linked to this installation"):
            MODULE.preflight(self.root, self.snapshot, ["-f", "docker-compose.base.yml"])

    def test_foreign_consumer_blocks_shared_edge_transition_volume(self):
        self._shared_edge_container()
        self.fake.foreign_consumers["ods_pixel-transition-state"] = ["b" * 64]
        with self.assertRaisesRegex(ValueError, "outside this installation"):
            MODULE.preflight(self.root, self.snapshot, ["-f", "docker-compose.base.yml"])
        self.assertFalse(self.fake.removed)

    def test_candidate_must_also_declare_shared_edge_transition_volume(self):
        self._shared_edge_container()
        trusted = Path(self.temp.name) / "candidate"
        (trusted / "installers/macos").mkdir(parents=True)
        shutil.copyfile(self.native, trusted / "installers/macos/pixel-native.compose.yaml.disabled")
        with self.assertRaisesRegex(ValueError, "not linked to this installation"):
            MODULE.preflight(self.root, self.snapshot, ["-f", "docker-compose.base.yml"], trusted_root=trusted)


class UninstallVolumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ods-uninstall-volume-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "ods"
        (self.root / "extensions/services/perplexica").mkdir(parents=True)
        (self.root / "docker-compose.base.yml").write_text("services: {}\n", encoding="utf-8")
        shutil.copyfile(
            ROOT / "extensions/services/perplexica/compose.yaml",
            self.root / "extensions/services/perplexica/compose.yaml.disabled",
        )
        self.snapshot = Path(self.temp.name) / "snapshot.json"
        self.snapshot.touch()
        self.fake = FakeDocker(self.root)
        self.patcher = patch.object(MODULE, "docker", self.fake)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_disabled_perplexica_volumes_are_purged_without_foreign_prefix_cleanup(self):
        MODULE.preflight(self.root, self.snapshot, ["-f", "docker-compose.base.yml"])
        captured = json.loads(self.snapshot.read_text(encoding="utf-8"))
        self.assertEqual(set(captured["volumes"]), set(self.fake.volumes))
        # Current-stack Compose down succeeds but omits the disabled fragment.
        self.fake.containers = []
        MODULE.complete(self.root, self.snapshot)
        self.assertEqual(self.fake.volumes, {})
        self.assertEqual(set(self.fake.removed), set(captured["volumes"]))
        self.assertEqual(self.fake.foreign, {"ods-pixel-retired-research", "ods-unrelated"})

    def test_selected_volume_is_removed_only_after_down_and_reinspection(self):
        self.fake.config["volumes"] = {
            "perplexica-data": {"name": "ods_perplexica-data"}
        }
        MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)
        # The shell uses Compose down without -v, so this volume still exists.
        self.fake.containers = []
        MODULE.complete(self.root, self.snapshot)
        self.assertIn("ods_perplexica-data", self.fake.removed)

    def test_candidate_recipe_requires_installed_disabled_receipt(self):
        trusted = Path(self.temp.name) / "candidate"
        recipe = trusted / "extensions/services/perplexica/compose.yaml"
        recipe.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "extensions/services/perplexica/compose.yaml", recipe)
        installed_recipe = self.root / "extensions/services/perplexica/compose.yaml.disabled"
        original = installed_recipe.read_text(encoding="utf-8")
        installed_recipe.unlink()
        with self.assertRaisesRegex(ValueError, "not linked"):
            MODULE.preflight(self.root, self.snapshot, [], trusted_root=trusted)
        installed_recipe.write_text(original, encoding="utf-8")
        MODULE.preflight(self.root, self.snapshot, [], trusted_root=trusted)
        self.fake.containers = []
        MODULE.complete(self.root, self.snapshot, trusted_root=trusted)
        self.assertEqual(self.fake.volumes, {})

    def _enable_unselected_recipe(self):
        disabled = self.root / "extensions/services/perplexica/compose.yaml.disabled"
        enabled = disabled.with_name("compose.yaml")
        disabled.rename(enabled)
        return enabled

    def test_enabled_unselected_recipe_retains_mount_proof_through_purge(self):
        self._enable_unselected_recipe()
        # A failed native activation leaves only base Compose flags available.
        MODULE.preflight(self.root, self.snapshot, ["-f", "docker-compose.base.yml"])
        captured = json.loads(self.snapshot.read_text(encoding="utf-8"))
        self.assertEqual(set(captured["volumes"]), set(self.fake.volumes))
        self.assertFalse(self.fake.removed)
        self.fake.containers = []
        MODULE.complete(self.root, self.snapshot)
        self.assertEqual(self.fake.volumes, {})
        self.assertEqual(self.fake.foreign, {"ods-pixel-retired-research", "ods-unrelated"})

    def test_enabled_unselected_recipe_requires_exact_service_and_recipe_path(self):
        recipe = self._enable_unselected_recipe()
        labels = self.fake.containers[0]["Config"]["Labels"]
        for field, bad_value in (
            ("com.docker.compose.service", "searxng"),
            ("com.docker.compose.project.config_files", str(self.root / "docker-compose.base.yml")),
        ):
            original = labels[field]
            with self.subTest(field=field):
                labels[field] = bad_value
                with self.assertRaisesRegex(ValueError, "not linked"):
                    MODULE.preflight(self.root, self.snapshot, [])
                self.assertFalse(self.fake.removed)
                self.assertEqual(self.snapshot.stat().st_size, 0)
            labels[field] = original
        self.assertTrue(recipe.is_file())

    def test_enabled_unselected_recipe_requires_a_container_mount(self):
        self._enable_unselected_recipe()
        self.fake.containers[0]["Mounts"] = []
        with self.assertRaisesRegex(ValueError, "not linked"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)

    def test_enabled_unselected_recipe_requires_trusted_candidate(self):
        recipe = self._enable_unselected_recipe()
        trusted = Path(self.temp.name) / "candidate"
        candidate_recipe = trusted / recipe.relative_to(self.root)
        candidate_recipe.parent.mkdir(parents=True)
        candidate_recipe.write_text("services: {}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not linked"):
            MODULE.preflight(self.root, self.snapshot, [], trusted_root=trusted)
        self.assertFalse(self.fake.removed)
        shutil.copyfile(recipe, candidate_recipe)
        MODULE.preflight(self.root, self.snapshot, [], trusted_root=trusted)
        self.fake.containers = []
        MODULE.complete(self.root, self.snapshot, trusted_root=trusted)
        self.assertEqual(self.fake.volumes, {})

    def test_enabled_unselected_external_recipe_is_not_owned(self):
        recipe = self._enable_unselected_recipe()
        recipe.write_text("services: {}\nvolumes:\n  perplexica-data:\n    external: true\n"
                          "  perplexica-uploads:\n    external: true\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not linked"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)

    def test_enabled_unselected_volume_foreign_consumer_blocks_preflight(self):
        self._enable_unselected_recipe()
        self.fake.foreign_consumers["ods_perplexica-data"] = ["b" * 64]
        with self.assertRaisesRegex(ValueError, "outside this installation"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)
        self.assertEqual(self.snapshot.stat().st_size, 0)

    def test_enabled_recipe_cannot_override_external_volume_intent(self):
        self._enable_unselected_recipe()
        override = self.root / "docker-compose.override.yml"
        override.write_text("volumes:\n  perplexica-data:\n    external: true\n",
                            encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not linked"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)

    def test_enabled_recipe_policy_change_blocks_completion(self):
        recipe = self._enable_unselected_recipe()
        MODULE.preflight(self.root, self.snapshot, [])
        self.fake.containers = []
        recipe.write_text("services: {}\nvolumes:\n  perplexica-data:\n    external: true\n"
                          "  perplexica-uploads:\n    external: true\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "ownership changed"):
            MODULE.complete(self.root, self.snapshot)
        self.assertFalse(self.fake.removed)

    def test_enabled_unselected_recipe_symlink_is_not_owned(self):
        recipe = self._enable_unselected_recipe()
        outside = Path(self.temp.name) / "outside.yaml"
        recipe.rename(outside)
        recipe.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "not linked"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)

    def test_other_installation_in_same_compose_project_blocks_preflight(self):
        self.fake.containers[0]["Config"]["Labels"][
            "com.docker.compose.project.working_dir"
        ] = "/other/ods"
        with self.assertRaisesRegex(ValueError, "another installation"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)
        self.assertEqual(self.snapshot.stat().st_size, 0)

    def test_unaccounted_volume_blocks_before_mutation(self):
        self.fake.containers[0]["Mounts"] = []
        with self.assertRaisesRegex(ValueError, "not linked"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)

    def test_no_path_binding_blocks_purge_but_keep_data_can_proceed(self):
        self.fake.containers = []
        with self.assertRaisesRegex(ValueError, "no container proves"):
            MODULE.preflight(self.root, self.snapshot, [])
        MODULE.preflight(self.root, self.snapshot, [], keep_data=True)
        self.assertEqual(json.loads(self.snapshot.read_text())["volumes"], {})
        self.assertFalse(self.fake.removed)

    def test_stopped_container_retains_path_binding_for_purge(self):
        self.fake.containers[0]["State"] = {"Status": "exited"}
        MODULE.preflight(self.root, self.snapshot, [])
        self.assertEqual(
            set(json.loads(self.snapshot.read_text())["volumes"]),
            set(self.fake.volumes),
        )
        self.fake.containers = []
        MODULE.complete(self.root, self.snapshot)
        self.assertEqual(set(self.fake.removed), {
            "ods_perplexica-data", "ods_perplexica-uploads",
        })

    def test_bind_only_stopped_container_blocks_uninstall_postflight(self):
        self.fake.volumes = {}
        self.fake.containers[0]["State"] = {"Status": "exited"}
        self.fake.containers[0]["Config"]["Labels"]["com.docker.compose.service"] = "open-webui"
        self.fake.containers[0]["Mounts"] = [{
            "Type": "bind", "Source": str(self.root / "data/open-webui"),
            "Destination": "/app/backend/data",
        }]
        MODULE.preflight(self.root, self.snapshot, ["-f", "docker-compose.base.yml"])
        with self.assertRaisesRegex(ValueError, "ODS containers remain after Compose cleanup"):
            MODULE.postflight_containers(self.root, self.snapshot)
        self.fake.containers = []
        MODULE.postflight_containers(self.root, self.snapshot)

    def test_keep_data_still_checks_owned_containers_after_down(self):
        self.fake.containers[0]["State"] = {"Status": "exited"}
        MODULE.preflight(self.root, self.snapshot, [], keep_data=True)
        with self.assertRaisesRegex(ValueError, "ODS containers remain after Compose cleanup"):
            MODULE.postflight_containers(self.root, self.snapshot)
        self.fake.containers = []
        MODULE.postflight_containers(self.root, self.snapshot)

    def test_container_ownership_drift_blocks_postflight(self):
        MODULE.preflight(self.root, self.snapshot, [])
        self.fake.containers[0]["Config"]["Labels"][
            "com.docker.compose.project.working_dir"] = "/other/ods"
        with self.assertRaisesRegex(ValueError, "another installation"):
            MODULE.postflight_containers(self.root, self.snapshot)

    def test_old_project_label_is_caught_before_uninstall_mutation(self):
        self.fake.containers[0]["Config"]["Labels"]["com.docker.compose.project"] = "ods-old"
        self.fake.volumes = {}
        with self.assertRaisesRegex(ValueError, "another project still reference"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertEqual(self.snapshot.stat().st_size, 0)

    def test_project_label_change_after_preflight_blocks_postflight(self):
        self.fake.volumes = {}
        self.fake.containers[0]["Mounts"] = []
        MODULE.preflight(self.root, self.snapshot, [])
        self.fake.containers[0]["Config"]["Labels"]["com.docker.compose.project"] = "ods-old"
        with self.assertRaisesRegex(ValueError, "ODS containers remain after Compose cleanup"):
            MODULE.postflight_containers(self.root, self.snapshot)

    def test_unrelated_compose_project_does_not_block_postflight(self):
        MODULE.preflight(self.root, self.snapshot, [])
        other = self.fake._container(Path("/other/ods"))
        other["Id"] = "b" * 64
        other["Config"]["Labels"]["com.docker.compose.project"] = "other"
        self.fake.containers = [other]
        MODULE.postflight_containers(self.root, self.snapshot)

    def test_uninspectable_foreign_record_does_not_block_owned_cleanup(self):
        other = self.fake._container(Path("/other/project"))
        other["Id"] = "b" * 64
        other["Config"]["Labels"]["com.docker.compose.project"] = "other"
        other["Mounts"] = []
        self.fake.containers.append(other)
        self.fake.uninspectable.add(other["Id"])
        MODULE.preflight(self.root, self.snapshot, [])
        self.fake.containers = [other]
        MODULE.postflight_containers(self.root, self.snapshot)
        MODULE.complete(self.root, self.snapshot)
        self.assertEqual(set(self.fake.removed), {
            "ods_perplexica-data", "ods_perplexica-uploads",
        })

    def test_uninspectable_owned_record_still_blocks_before_mutation(self):
        self.fake.uninspectable.add(CONTAINER_ID)
        with self.assertRaisesRegex(ValueError, "Docker inspect"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)
        self.assertEqual(self.snapshot.stat().st_size, 0)

    def test_renamed_uninspectable_project_with_only_config_path_still_blocks(self):
        labels = self.fake.containers[0]["Config"]["Labels"]
        labels["com.docker.compose.project"] = "ods-old"
        labels["com.docker.compose.project.working_dir"] = "/other/project"
        self.fake.uninspectable.add(CONTAINER_ID)
        with self.assertRaisesRegex(ValueError, "another project still reference"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)

    def test_invalid_path_listing_is_not_treated_as_empty(self):
        for listing in ('{}', '{"Id": "bad", "workingDir":"", "configFiles":""}',
                        json.dumps({"Id":CONTAINER_ID,"workingDir":[],"configFiles":""})):
            with self.subTest(listing=listing), patch.object(MODULE, "docker", return_value=listing):
                with self.assertRaisesRegex(ValueError, "invalid container path listing"):
                    MODULE.install_root_containers(self.root)

    def test_volume_replacement_after_preflight_is_not_deleted(self):
        MODULE.preflight(self.root, self.snapshot, [])
        self.fake.volumes["ods_perplexica-data"]["CreatedAt"] = "2026-10-01T01:00:00Z"
        self.fake.containers = []
        with self.assertRaisesRegex(ValueError, "changed during uninstall"):
            MODULE.complete(self.root, self.snapshot)
        self.assertFalse(self.fake.removed)

    def test_selected_external_volume_is_retained(self):
        self.fake.config["volumes"] = {"shared": {"name": "ods_shared", "external": True}}
        self.fake.volumes["ods_shared"] = self.fake._volume("ods_shared", "shared")
        MODULE.preflight(self.root, self.snapshot, [])
        self.fake.containers = []
        MODULE.complete(self.root, self.snapshot)
        self.assertNotIn("ods_shared", self.fake.removed)
        self.assertIn("ods_shared", self.fake.volumes)

    def test_foreign_consumer_blocks_before_pixel_retirement(self):
        self.fake.foreign_consumers["ods_perplexica-data"] = ["b" * 64]
        with self.assertRaisesRegex(ValueError, "outside this installation"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)

    def test_changed_compose_volume_policy_blocks_completion(self):
        MODULE.preflight(self.root, self.snapshot, [])
        self.fake.containers = []
        self.fake.config["volumes"] = {
            "perplexica-data": {"name": "ods_perplexica-data", "external": True}
        }
        with self.assertRaisesRegex(ValueError, "ownership changed"):
            MODULE.complete(self.root, self.snapshot)
        self.assertFalse(self.fake.removed)

    def test_disabled_external_recipe_is_not_inferred_as_owned(self):
        recipe = self.root / "extensions/services/perplexica/compose.yaml.disabled"
        recipe.write_text("volumes:\n  shared:\n    external: \"true\" # owner supplied\n",
                          encoding="utf-8")
        self.fake.volumes = {"ods_shared": self.fake._volume("ods_shared", "shared")}
        self.fake.containers[0]["Mounts"] = [{"Type": "volume", "Name": "ods_shared"}]
        with self.assertRaisesRegex(ValueError, "not linked"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)

    def test_candidate_plain_recipe_cannot_override_installed_external_intent(self):
        trusted = Path(self.temp.name) / "candidate"
        recipe = trusted / "extensions/services/perplexica/compose.yaml"
        recipe.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "extensions/services/perplexica/compose.yaml", recipe)
        installed = self.root / "extensions/services/perplexica/compose.yaml.disabled"
        installed.write_text("volumes:\n  perplexica-data:\n    external: true\n"
                             "  perplexica-uploads:\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not linked"):
            MODULE.preflight(self.root, self.snapshot, [], trusted_root=trusted)
        self.assertFalse(self.fake.removed)

    def test_other_ods_service_mount_cannot_authorize_disabled_volume(self):
        self.fake.containers[0]["Config"]["Labels"]["com.docker.compose.service"] = "searxng"
        with self.assertRaisesRegex(ValueError, "not linked"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)

    def test_selected_volume_requires_matching_compose_volume_key(self):
        self.fake.config["volumes"] = {
            "perplexica-data": {"name": "ods_perplexica-data"}
        }
        self.fake.volumes["ods_perplexica-data"]["Labels"][
            "com.docker.compose.volume"
        ] = "foreign-data"
        with self.assertRaisesRegex(ValueError, "conflicting Compose label"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)

    def test_detached_selected_volume_is_not_assumed_to_belong_to_this_install(self):
        self.fake.config["volumes"] = {
            "foreign-data": {"name": "ods_foreign-data"}
        }
        self.fake.volumes["ods_foreign-data"] = self.fake._volume(
            "ods_foreign-data", "foreign-data"
        )
        with self.assertRaisesRegex(ValueError, "no verified ODS container mount"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)

    def test_anonymous_volume_mounted_only_by_ods_is_accounted_for(self):
        name = "b" * 64
        self.fake.volumes[name] = {
            "Name": name, "Driver": "local",
            "CreatedAt": "2026-10-01T00:00:00Z",
            "Labels": {"com.docker.volume.anonymous": ""},
        }
        self.fake.containers[0]["Mounts"].append({"Type": "volume", "Name": name})
        MODULE.preflight(self.root, self.snapshot, [])
        self.fake.containers = []
        MODULE.complete(self.root, self.snapshot)
        self.assertIn(name, self.fake.removed)

    def test_unlabelled_named_volume_is_not_deleted(self):
        name = "owner-shared-data"
        self.fake.volumes[name] = {
            "Name": name, "Driver": "local",
            "CreatedAt": "2026-10-01T00:00:00Z", "Labels": None,
        }
        self.fake.containers[0]["Mounts"].append({"Type": "volume", "Name": name})
        with self.assertRaisesRegex(ValueError, "unproven ownership"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)

    def test_user_named_hex_volume_without_anonymous_marker_is_not_deleted(self):
        name = "c" * 64
        self.fake.volumes[name] = {
            "Name": name, "Driver": "local",
            "CreatedAt": "2026-10-01T00:00:00Z", "Labels": None,
        }
        self.fake.containers[0]["Mounts"].append({"Type": "volume", "Name": name})
        with self.assertRaisesRegex(ValueError, "unproven ownership"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)

    def test_detached_unlabelled_recipe_volume_blocks_success(self):
        self.fake.volumes["ods_perplexica-data"]["Labels"] = None
        self.fake.containers[0]["Mounts"] = [
            mount for mount in self.fake.containers[0]["Mounts"]
            if mount["Name"] != "ods_perplexica-data"
        ]
        with self.assertRaisesRegex(ValueError, "lacks Compose ownership labels"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)

    def interrupt_after_compose_down(self):
        """A purge preflight passed, Compose down removed the containers, then
        the uninstall stopped before volume cleanup (e.g. a stray container)."""
        MODULE.preflight(self.root, self.snapshot, [])
        self.assertTrue((self.root / MODULE.RESUME_RECORD).is_file())
        self.assertEqual((self.root / MODULE.RESUME_RECORD).stat().st_mode & 0o777, 0o600)
        self.fake.containers = []
        retry = Path(self.temp.name) / "retry-snapshot.json"
        retry.touch()
        return retry

    def test_interrupted_purge_resumes_from_verified_record(self):
        retry = self.interrupt_after_compose_down()
        MODULE.preflight(self.root, retry, [])
        self.assertEqual(set(json.loads(retry.read_text())["volumes"]), set(self.fake.volumes))
        MODULE.complete(self.root, retry)
        self.assertEqual(self.fake.volumes, {})
        self.assertEqual(self.fake.foreign, {"ods-pixel-retired-research", "ods-unrelated"})

    def test_interrupted_purge_without_record_still_refuses(self):
        retry = self.interrupt_after_compose_down()
        (self.root / MODULE.RESUME_RECORD).unlink()
        with self.assertRaisesRegex(ValueError, "--keep-data"):
            MODULE.preflight(self.root, retry, [])
        self.assertFalse(self.fake.removed)

    def test_resume_refuses_a_volume_whose_identity_changed(self):
        retry = self.interrupt_after_compose_down()
        self.fake.volumes["ods_perplexica-data"]["CreatedAt"] = "2026-10-02T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "no container proves"):
            MODULE.preflight(self.root, retry, [])
        self.assertFalse(self.fake.removed)

    def test_resume_refuses_a_volume_with_a_foreign_consumer(self):
        retry = self.interrupt_after_compose_down()
        self.fake.foreign_consumers["ods_perplexica-data"] = ["b" * 64]
        with self.assertRaisesRegex(ValueError, "outside this installation"):
            MODULE.preflight(self.root, retry, [])
        self.assertFalse(self.fake.removed)

    def test_resume_record_from_another_installation_is_rejected(self):
        retry = self.interrupt_after_compose_down()
        path = self.root / MODULE.RESUME_RECORD
        record = json.loads(path.read_text())
        record["installDir"] = "/other/ods"
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "does not belong"):
            MODULE.preflight(self.root, retry, [])
        self.assertFalse(self.fake.removed)

    def test_keep_data_preflight_writes_no_resume_record(self):
        MODULE.preflight(self.root, self.snapshot, [], keep_data=True)
        self.assertFalse((self.root / MODULE.RESUME_RECORD).exists())

    def test_unknown_same_prefix_volume_is_retained_and_reported(self):
        name = "ods_obsolete-unknown-probe"
        self.fake.volumes[name] = {
            "Name": name, "Driver": "local",
            "CreatedAt": "2026-10-01T00:00:00Z", "Labels": None,
        }
        MODULE.preflight(self.root, self.snapshot, [])
        self.fake.containers = []
        diagnostic = io.StringIO()
        with contextlib.redirect_stderr(diagnostic):
            MODULE.complete(self.root, self.snapshot)
        self.assertIn(name, self.fake.volumes)
        self.assertNotIn(name, self.fake.removed)
        self.assertIn(name, diagnostic.getvalue())

    RETIRED = ("lemonade-cache", "lemonade-llama", "lemonade-recipe")

    def add_retired_lemonade_volumes(self):
        """The Lemonade-era AMD overlay's volumes, which no container mounts
        after the llama.cpp upgrade."""
        for key in self.RETIRED:
            self.fake.volumes[f"ods_{key}"] = self.fake._volume(f"ods_{key}", key)
        return {f"ods_{key}" for key in self.RETIRED}

    def write_retired_record(self, install_dir=None, keys=RETIRED):
        path = self.root / "data/lemonade-retired-volumes.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schemaVersion": 1, "installDir": str(install_dir or self.root),
            "volumeKeys": list(keys),
        }), encoding="utf-8")

    def test_recorded_retired_lemonade_volumes_are_purged(self):
        retired = self.add_retired_lemonade_volumes()
        self.write_retired_record()
        MODULE.preflight(self.root, self.snapshot, [])
        captured = json.loads(self.snapshot.read_text(encoding="utf-8"))
        self.assertTrue(retired <= set(captured["volumes"]))
        # Completion re-checks trustedUsed against disabled recipes only.
        self.assertEqual(captured["trustedUsed"], ["perplexica-data", "perplexica-uploads"])
        self.fake.containers = []
        MODULE.complete(self.root, self.snapshot)
        self.assertEqual(self.fake.volumes, {})
        self.assertTrue(retired <= set(self.fake.removed))
        self.assertEqual(self.fake.foreign, {"ods-pixel-retired-research", "ods-unrelated"})

    def test_unrecorded_retired_lemonade_volume_is_not_deleted(self):
        self.add_retired_lemonade_volumes()
        with self.assertRaisesRegex(ValueError, "retired Lemonade volume this installation did not record"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)
        self.assertEqual(self.snapshot.stat().st_size, 0)

    def test_retired_record_is_read_only_when_retired_volumes_exist(self):
        self.write_retired_record(install_dir="/other/ods")
        # Nothing to prove: a stale record does not block the uninstall.
        MODULE.preflight(self.root, self.snapshot, [])
        self.add_retired_lemonade_volumes()
        with self.assertRaisesRegex(ValueError, "does not belong to this installation"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)

    def test_retired_record_cannot_name_other_volumes(self):
        self.add_retired_lemonade_volumes()
        self.write_retired_record(keys=self.RETIRED + ("perplexica-data",))
        with self.assertRaisesRegex(ValueError, "does not belong to this installation"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)

    def test_retired_volume_with_a_foreign_consumer_is_not_deleted(self):
        self.add_retired_lemonade_volumes()
        self.write_retired_record()
        self.fake.foreign_consumers["ods_lemonade-cache"] = ["b" * 64]
        with self.assertRaisesRegex(ValueError, "outside this installation"):
            MODULE.preflight(self.root, self.snapshot, [])
        self.assertFalse(self.fake.removed)

    def test_retired_volume_recreated_after_preflight_is_not_deleted(self):
        self.add_retired_lemonade_volumes()
        self.write_retired_record()
        MODULE.preflight(self.root, self.snapshot, [])
        self.fake.volumes["ods_lemonade-llama"]["CreatedAt"] = "2026-10-02T00:00:00Z"
        self.fake.containers = []
        with self.assertRaisesRegex(ValueError, "changed during uninstall"):
            MODULE.complete(self.root, self.snapshot)
        self.assertFalse(self.fake.removed)


if __name__ == "__main__":
    unittest.main()
