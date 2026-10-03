"""Library discovery follows the same native-over-library precedence as the catalog."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("extension_audit_library", ROOT / "scripts/audit-extensions.py")
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


class LibraryAuditTests(unittest.TestCase):
    def test_non_http_service_requires_its_own_native_probe(self):
        cases = [
            ({"test": ["CMD", "mqtt-probe"]}, False),
            ({"test": "mqtt-probe"}, False),
            ({"test": ["CMD", "mqtt-probe"], "disable": True}, True),
            ({"test": ["NONE"]}, True),
            ({"test": []}, True),
            ({"test": [[], "probe"]}, True),
            ({}, True),
        ]
        for check, rejected in cases:
            with self.subTest(check=check), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp)
                directory = project / "extensions/services/broker"
                directory.mkdir(parents=True)
                (directory / "manifest.yaml").write_text(yaml.safe_dump({
                    "schema_version": "ods.services.v1",
                    "service": {"id": "broker", "port": 1883, "type": "docker",
                                "category": "optional", "health": "", "startup_check": False,
                                "compose_file": "compose.yaml"},
                }), encoding="utf-8")
                (directory / "compose.yaml").write_text(yaml.safe_dump({"services": {
                    "broker": {"image": "broker:1", "healthcheck": check},
                    "dependency": {"image": "db:1", "healthcheck": {"test": ["CMD", "probe"]}},
                }}), encoding="utf-8")
                records, issues = audit.discover_services(project)
                audit.validate_records(records, issues)
                codes = {issue.code for issue in records[0].issues}
                self.assertNotIn("service-health-invalid", codes)
                self.assertEqual("native-healthcheck-required" in codes, rejected)

    def test_opt_in_and_native_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            native = project / "extensions/services"
            library = project / "extensions/library/services"
            for root, name, port in [(native, "same", 8000), (library, "same", 9000), (library, "extra", 7000)]:
                directory = root / name
                directory.mkdir(parents=True)
                (directory / "manifest.yaml").write_text(
                    f"schema_version: ods.services.v1\nservice:\n  id: {name}\n  port: {port}\n  type: docker\n  category: optional\n",
                    encoding="utf-8",
                )
            default, issues = audit.discover_services(project)
            self.assertFalse(issues)
            self.assertEqual([r.service_id for r in default], ["same"])
            expanded, issues = audit.discover_services(project, include_library=True)
            self.assertFalse(issues)
            self.assertEqual([r.service_id for r in expanded], ["extra", "same"])
            self.assertEqual(expanded[1].service["port"], 8000)


if __name__ == "__main__":
    unittest.main()
