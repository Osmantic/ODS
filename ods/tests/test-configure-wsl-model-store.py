"""Installer store registration in disposable directories, never live interop."""

import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("configure_wsl_store", SOURCE / "scripts/configure-wsl-model-store.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class ConfigureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "installation"
        (self.root / "data/models").mkdir(parents=True)
        (self.root / ".env").write_text('LEMONADE_HOST_TRANSPORT="model-router"\nHF_TOKEN=not-read-or-exposed\n')
        self.models = self.root.parent / "Windows Model Store"
        self.models.mkdir()
        self.plan = self.root.parent / "runtime.json"
        self.plan.write_text("{}")
        self.metadata = self.root / "data/wsl-lemonade-runtime.json"
        for name, value in (("candidate", True), ("status", {"managed": True}),
                            ("model_store", self.models), ("plan_path", self.plan)):
            patcher = patch.object(helper.wsl_lemonade, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_registers_store_and_private_metadata_before_compose_without_selecting_model(self):
        result = helper.configure(self.root)
        self.assertEqual(result, {"configured": True, "modelStoreId": "windows-lemonade"})
        self.assertEqual(json.loads(self.metadata.read_text()),
                         {"schemaVersion": 1, "planPath": self.plan.as_posix(), "modelStoreId": "windows-lemonade"})
        mounts = json.loads((self.root / ".model-stores.compose.json").read_text())["services"]
        self.assertEqual(set(mounts), {"dashboard-api"})
        mount = mounts["dashboard-api"]["volumes"][0]
        self.assertEqual(mount["source"], self.models.as_posix())
        self.assertTrue(mount["read_only"])
        self.assertFalse(mount["bind"]["create_host_path"])
        self.assertNotIn("ODS_ACTIVE_MODEL_STORE", (self.root / ".env").read_text())
        self.assertNotIn("HF_TOKEN", str(result))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.metadata.stat().st_mode), 0o600)

    def test_system_directory_is_forwarded_literally_without_reading_secrets(self):
        (self.root / '.env').write_text("LEMONADE_HOST_TRANSPORT=model-router\n"
                                      "ODS_WINDOWS_SYSTEM_DIRECTORY='D:\\Operating $ystem\\System32'\n"
                                      "HF_TOKEN=private\n", encoding='utf-8')
        with patch.object(helper.wsl_lemonade, 'status', return_value={'managed': False}) as status:
            helper.configure(self.root)
            self.assertEqual(status.call_args.args[1], {
                'LEMONADE_HOST_TRANSPORT': 'model-router',
                'ODS_HOST_LLM_TRANSPORT': 'model-router',
                'ODS_WINDOWS_SYSTEM_DIRECTORY': r'D:\Operating $ystem\System32'})

    def test_migrated_environment_reaches_the_bridge_under_both_names(self):
        # The Lemonade migration writes the round-F keys; the bridge still
        # reads the Lemonade-era names for one release.
        (self.root / '.env').write_text("ODS_HOST_LLM_TRANSPORT=model-router\n"
                                      "NATIVE_LLM_BASE_URL=http://localhost:13305\n"
                                      "NATIVE_LLM_CONTAINER_BASE_URL=http://host.docker.internal:13305\n"
                                      "AMD_INFERENCE_PORT=13305\n", encoding='utf-8')
        with patch.object(helper.wsl_lemonade, 'status', return_value={'managed': False}) as status:
            helper.configure(self.root)
            self.assertEqual(status.call_args.args[1], {
                'ODS_HOST_LLM_TRANSPORT': 'model-router', 'LEMONADE_HOST_TRANSPORT': 'model-router',
                'NATIVE_LLM_BASE_URL': 'http://localhost:13305', 'LEMONADE_BASE_URL': 'http://localhost:13305',
                'NATIVE_LLM_CONTAINER_BASE_URL': 'http://host.docker.internal:13305',
                'LEMONADE_CONTAINER_BASE_URL': 'http://host.docker.internal:13305',
                'AMD_INFERENCE_PORT': '13305'})

    def test_existing_directory_registration_keeps_its_id_and_profiles(self):
        helper.registration.register(self.root, "owner-store", self.models)
        registry = self.root / "data/model-stores.json"
        document = json.loads(registry.read_text())
        document["stores"][0]["profiles"] = {"sample.gguf": {"owner": "preserved"}}
        registry.write_text(json.dumps(document))
        result = helper.configure(self.root)
        self.assertEqual(result["modelStoreId"], "owner-store")
        rows = json.loads(registry.read_text())["stores"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["profiles"], {"sample.gguf": {"owner": "preserved"}})

    def test_idempotent_reconfiguration_and_no_temporary_files_left(self):
        helper.configure(self.root)
        initial = self.metadata.read_bytes()
        helper.configure(self.root)
        self.assertEqual(self.metadata.read_bytes(), initial)
        self.assertEqual(list((self.root / "data").glob("wsl-lemonade-runtime.json.*.tmp")), [])

    def test_other_backends_do_not_read_status_or_create_files(self):
        with patch.object(helper.wsl_lemonade, "candidate", return_value=False), \
                patch.object(helper.wsl_lemonade, "status") as status:
            self.assertEqual(helper.configure(self.root)["reason"], "not_wsl_lemonade")
            status.assert_not_called()
        self.assertFalse(self.metadata.exists())
        self.assertFalse((self.root / ".model-stores.compose.json").exists())

    def test_external_unmanaged_runtime_is_left_alone(self):
        with patch.object(helper.wsl_lemonade, "status", return_value={"managed": False}):
            self.assertEqual(helper.configure(self.root)["reason"], "external_runtime_unmanaged")
        self.assertFalse(self.metadata.exists())

    def test_first_unavailable_proof_skips_but_registered_runtime_fails(self):
        with patch.object(helper.wsl_lemonade, "status", side_effect=OSError("unavailable")):
            self.assertEqual(helper.configure(self.root)["reason"], "windows_runtime_proof_unavailable")
        helper.configure(self.root)
        before = self.metadata.read_bytes()
        with patch.object(helper.wsl_lemonade, "status", side_effect=OSError("unavailable")), self.assertRaises(OSError):
            helper.configure(self.root)
        self.assertEqual(self.metadata.read_bytes(), before)

    def test_ownership_loss_after_registration_is_not_silently_skipped(self):
        helper.configure(self.root)
        with patch.object(helper.wsl_lemonade, "status", return_value={"managed": False}), self.assertRaises(ValueError):
            helper.configure(self.root)

    def test_changed_plan_location_rejected_without_rewriting_metadata(self):
        helper.configure(self.root)
        before = self.metadata.read_bytes()
        with patch.object(helper.wsl_lemonade, "plan_path", return_value=self.root / "other.json"), self.assertRaises(ValueError):
            helper.configure(self.root)
        self.assertEqual(self.metadata.read_bytes(), before)

    def test_conflicting_store_id_does_not_claim_another_directory(self):
        other = self.root.parent / "Other store"
        other.mkdir()
        helper.registration.register(self.root, "windows-lemonade", other)
        with self.assertRaises(ValueError):
            helper.configure(self.root)
        self.assertFalse(self.metadata.exists())

    def test_invalid_previous_metadata_is_not_overwritten(self):
        for data in ('[]', '{"schemaVersion":2}', 'x' * 65537):
            with self.subTest(data=data[:20]):
                self.metadata.write_text(data)
                with self.assertRaises(ValueError):
                    helper.configure(self.root)
                self.assertEqual(self.metadata.read_text(), data)

    def test_metadata_atomic_replace_failure_preserves_previous_bytes(self):
        self.metadata.write_text("original")
        with patch.object(helper.os, "replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            helper._write_metadata(self.metadata, {"schemaVersion": 1})
        self.assertEqual(self.metadata.read_text(), "original")
        self.assertEqual(list(self.metadata.parent.glob("wsl-lemonade-runtime.json.*.tmp")), [])

    def test_helper_hook_precedes_compose_flag_resolution(self):
        source = (SOURCE / "installers/phases/11-services.sh").read_text()
        self.assertLess(source.index('"$INSTALL_DIR/scripts/configure-wsl-model-store.py"'),
                        source.index('_refreshed_flags=$("$INSTALL_DIR/scripts/resolve-compose-stack.sh"'))


if __name__ == "__main__":
    unittest.main()
