import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pixel_doctor_test", ROOT / "control" / "doctor.py")
doctor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(doctor)


class DoctorTests(unittest.TestCase):
    def facts(self, **changes):
        value = {
            "system": "linux",
            "osId": "ubuntu",
            "osVersion": "24.04",
            "cpuCount": 8,
            "memoryBytes": 32 * 1024 ** 3,
            "storageFreeBytes": 100 * 1024 ** 3,
            "accelerators": ["nvidia"],
            "containerSocketDetected": True,
            "pythonMajor": 3,
            "pythonMinor": 11,
        }
        value.update(changes)
        return value

    def test_supported_reference_host_gets_rounded_advisory_recommendation(self):
        report = doctor.doctor_report(ROOT, "reference", facts=self.facts())
        self.assertEqual(report["summary"], {
            "state": "ready", "attentionChecks": 0, "unavailableChecks": 0, "supportedHost": True,
        })
        self.assertEqual(report["host"], {
            "contract": "supported", "family": "ubuntu", "cpuCapacity": "8-15",
            "memoryCapacityGiB": "32-63", "storageFreeCapacityGiB": "100-plus",
            "accelerator": "nvidia", "containerRuntime": "socket-detected",
        })
        self.assertEqual(report["recommendation"]["localModelClass"], "larger-local")
        self.assertEqual(report["recommendation"]["contextGuidance"], "expanded-after-measurement")
        self.assertFalse(report["recommendation"]["fitIsGuaranteed"])
        self.assertTrue(all(item["state"] == "pass" for item in report["checks"][:5]))
        self.assertEqual(report["checks"][-1], {
            "id": "generated-model-configuration", "state": "not-required",
            "guidanceCode": "model-not-configured",
        })

    def test_low_capacity_or_unsupported_facts_produce_fixed_review_guidance(self):
        report = doctor.doctor_report(ROOT, "reference", facts=self.facts(
            system="windows", osId=None, osVersion=None, cpuCount=2,
            memoryBytes=6 * 1024 ** 3, storageFreeBytes=9 * 1024 ** 3,
            accelerators=[], containerSocketDetected=False, pythonMinor=10,
        ))
        self.assertEqual(report["summary"]["state"], "attention")
        self.assertFalse(report["summary"]["supportedHost"])
        self.assertEqual(report["host"]["family"], "windows")
        self.assertEqual(report["recommendation"]["localModelClass"], "remote-or-compact")
        self.assertEqual({item["id"] for item in report["checks"] if item["state"] == "review"}, {
            "supported-host", "python-runtime", "memory-headroom", "storage-headroom",
            "reference-container-runtime",
        })

    def test_missing_core_facts_are_unavailable_without_raw_host_data(self):
        facts = self.facts(
            osId=None, osVersion=None, cpuCount=None, memoryBytes=None, storageFreeBytes=None,
            accelerators="PRIVATE_GPU_NAME", containerSocketDetected=None,
        )
        facts["hostname"] = "PRIVATE_HOSTNAME"
        facts["deviceSerial"] = "PRIVATE_SERIAL"
        facts["path"] = "/private/model/path"
        report = doctor.doctor_report(ROOT, "prepared", facts=facts)
        encoded = json.dumps(report)
        self.assertEqual(report["summary"]["state"], "unavailable")
        self.assertIsNone(report["summary"]["supportedHost"])
        self.assertEqual(report["recommendation"]["localModelClass"], "unavailable")
        unavailable_guidance = {item["guidanceCode"] for item in report["checks"] if item["state"] == "unavailable"}
        self.assertIn("confirm-host-release", unavailable_guidance)
        self.assertIn("confirm-memory", unavailable_guidance)
        self.assertIn("confirm-storage", unavailable_guidance)
        for forbidden in ("PRIVATE_HOSTNAME", "PRIVATE_SERIAL", "PRIVATE_GPU_NAME", "/private/model/path"):
            self.assertNotIn(forbidden, encoded)
        self.assertFalse(report["privacy"]["networkProbesPerformed"])
        self.assertFalse(report["privacy"]["providerCallsPerformed"])

    def test_human_output_is_plain_language_and_content_free(self):
        report = doctor.doctor_report(ROOT, "prepared", facts=self.facts())
        output = doctor.format_human(report)
        self.assertIn("Pixel Doctor: Ready", output)
        self.assertIn("No changes were made", output)
        self.assertIn("No network probe or provider call", output)
        self.assertEqual(output.count("Generated model configuration:"), 1)
        self.assertIn("Generated model configuration: not configured", output)
        self.assertNotIn(str(ROOT), output)

    def test_capacity_and_model_boundaries_are_deterministic(self):
        gib = 1024 ** 3
        cases = (
            (8, "8-15", "compact-local", "start-small"),
            (16, "16-31", "balanced-local", "moderate"),
            (32, "32-63", "larger-local", "expanded-after-measurement"),
            (64, "64-plus", "large-memory-local", "expanded-after-measurement"),
        )
        for memory, bucket, model_class, context in cases:
            with self.subTest(memory=memory):
                report = doctor.doctor_report(ROOT, "prepared", facts=self.facts(memoryBytes=memory * gib))
                self.assertEqual(report["host"]["memoryCapacityGiB"], bucket)
                self.assertEqual(report["recommendation"]["localModelClass"], model_class)
                self.assertEqual(report["recommendation"]["contextGuidance"], context)
        invalid = doctor.doctor_report(ROOT, "prepared", facts=self.facts(
            cpuCount=True, memoryBytes=True, storageFreeBytes=-1,
        ))
        self.assertEqual(invalid["host"]["cpuCapacity"], "unavailable")
        self.assertEqual(invalid["host"]["memoryCapacityGiB"], "unavailable")
        self.assertEqual(invalid["host"]["storageFreeCapacityGiB"], "unavailable")
        self.assertEqual(invalid["summary"]["state"], "unavailable")

    def test_generated_model_discovery_projects_only_broad_configuration_state(self):
        configuration = {
            "configured": True,
            "contextWindow": 65_536,
            "reasoning": True,
            "modelProvider": "PRIVATE_PROVIDER",
            "modelId": "PRIVATE_MODEL_ID",
            "modelBaseUrl": "http://private-model.invalid/v1",
        }
        report = doctor.doctor_report(ROOT, "prepared", facts=self.facts(), model_configuration=configuration)
        self.assertEqual(report["model"], {
            "configured": True,
            "discoveryState": "configured",
            "contextCapacity": "16k-64k",
            "reasoningConfigured": True,
            "fitAssessment": "manual-validation-required",
        })
        encoded = json.dumps(report)
        for forbidden in ("PRIVATE_PROVIDER", "PRIVATE_MODEL_ID", "private-model.invalid", "65536"):
            self.assertNotIn(forbidden, encoded)
        self.assertFalse(report["privacy"]["modelIdentityProjected"])

    def test_generated_model_collector_is_fixed_bounded_and_nofollow(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = root / ".generated"
            generated.mkdir()
            deployment = generated / "deployment.json"
            deployment.write_text(json.dumps({
                "modelProvider": "private-provider", "modelId": "private-id",
                "modelBaseUrl": "http://127.0.0.1:8000/v1", "modelContextWindow": 131_072,
                "modelReasoning": False,
            }), encoding="utf-8")
            collected = doctor.collect_model_configuration(root)
            self.assertEqual(collected, {
                "configured": True, "unavailable": False,
                "contextWindow": 131_072, "reasoning": False,
            })
            deployment.write_text('{"modelProvider":"first","modelProvider":"second","modelId":"id","modelBaseUrl":"http://127.0.0.1","modelContextWindow":8192,"modelReasoning":true}', encoding="utf-8")
            self.assertEqual(doctor.collect_model_configuration(root), {
                "configured": False, "unavailable": True, "contextWindow": None, "reasoning": None,
            })

            deployment.write_text(json.dumps({
                "modelProvider": "private-provider", "modelId": "private-id",
                "modelBaseUrl": "http://127.0.0.1:8000/v1",
            }), encoding="utf-8")
            self.assertEqual(doctor.collect_model_configuration(root), {
                "configured": False, "unavailable": True, "contextWindow": None, "reasoning": None,
            })
            deployment.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
            self.assertEqual(doctor.collect_model_configuration(root), {
                "configured": False, "unavailable": True, "contextWindow": None, "reasoning": None,
            })
            deployment.unlink()
            target = root / "private-target.json"
            target.write_text(json.dumps({
                "modelProvider": "private-provider", "modelId": "private-id",
                "modelBaseUrl": "http://127.0.0.1:8000/v1", "modelContextWindow": 131_072,
                "modelReasoning": False,
            }), encoding="utf-8")
            try:
                deployment.symlink_to(target)
            except OSError:
                return
            self.assertEqual(doctor.collect_model_configuration(root), {
                "configured": False, "unavailable": True, "contextWindow": None, "reasoning": None,
            })

    def test_generated_model_collector_rejects_symlinked_parent_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "private-generated"
            target.mkdir()
            (target / "deployment.json").write_text(json.dumps({
                "modelProvider": "private-provider", "modelId": "private-id",
                "modelBaseUrl": "http://127.0.0.1:8000/v1", "modelContextWindow": 131_072,
                "modelReasoning": False,
            }), encoding="utf-8")
            try:
                (root / ".generated").symlink_to(target, target_is_directory=True)
            except OSError:
                return
            self.assertEqual(doctor.collect_model_configuration(root), {
                "configured": False, "unavailable": True, "contextWindow": None, "reasoning": None,
            })

    def test_generated_model_discovery_distinguishes_absent_from_unreadable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            absent = doctor.collect_model_configuration(root)
            self.assertEqual(absent, {
                "configured": False, "unavailable": False, "contextWindow": None, "reasoning": None,
            })
            absent_report = doctor.doctor_report(root, "prepared", facts=self.facts(), model_configuration=absent)
            self.assertEqual(absent_report["model"]["discoveryState"], "not-configured")
            self.assertEqual(absent_report["checks"][-1], {
                "id": "generated-model-configuration", "state": "not-required",
                "guidanceCode": "model-not-configured",
            })

            (root / ".generated").mkdir()
            (root / ".generated" / "deployment.json").write_text("not-json", encoding="utf-8")
            unavailable = doctor.collect_model_configuration(root)
            unavailable_report = doctor.doctor_report(
                root, "prepared", facts=self.facts(), model_configuration=unavailable,
            )
            self.assertTrue(unavailable["unavailable"])
            self.assertEqual(unavailable_report["summary"]["state"], "unavailable")
            self.assertEqual(unavailable_report["model"], {
                "configured": False, "discoveryState": "unavailable", "contextCapacity": "unavailable",
                "reasoningConfigured": None, "fitAssessment": "unavailable",
            })
            self.assertEqual(unavailable_report["checks"][-1]["guidanceCode"], "confirm-generated-model")

    def test_claimed_model_with_invalid_context_or_reasoning_fails_closed(self):
        for configuration in (
            {"configured": True, "contextWindow": 4_095, "reasoning": True},
            {"configured": True, "contextWindow": 10_000_001, "reasoning": True},
            {"configured": True, "contextWindow": 8_192, "reasoning": 1},
        ):
            with self.subTest(configuration=configuration):
                report = doctor.doctor_report(
                    ROOT, "prepared", facts=self.facts(), model_configuration=configuration,
                )
                self.assertEqual(report["summary"]["state"], "unavailable")
                self.assertEqual(report["model"]["discoveryState"], "unavailable")
                self.assertEqual(report["checks"][-1]["guidanceCode"], "confirm-generated-model")

    def test_collector_has_no_process_or_network_execution_surface(self):
        source = (ROOT / "control" / "doctor.py").read_text(encoding="utf-8")
        for forbidden in ("subprocess", "socket.", "urlopen", "requests.", "http.client", "os.system", "Popen"):
            self.assertNotIn(forbidden, source)
        collected = doctor.collect_facts(ROOT)
        self.assertEqual(set(collected), {
            "system", "osId", "osVersion", "cpuCount", "memoryBytes", "storageFreeBytes",
            "accelerators", "containerSocketDetected", "pythonMajor", "pythonMinor",
        })


if __name__ == "__main__":
    unittest.main()
