#!/usr/bin/env python3
"""Unit tests for Pixel's scoped ODS extension lifecycle manager."""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).parents[1] / "host" / "extension_manager.py"
SPEC = importlib.util.spec_from_file_location("ods_pixel_extension_manager", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


def detail(status: str, *, required: tuple[str, ...] = ()) -> dict[str, object]:
    return {
        "id": "crewai",
        "status": status,
        "env_vars": [
            {"key": key, "required": True, "value": "must-not-leak"}
            for key in required
        ],
    }


class ExtensionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.env_path = pathlib.Path(self.temporary.name) / ".env"
        self.env_path.write_text(
            "DASHBOARD_API_KEY=" + "a" * 64 + "\n",
            encoding="utf-8",
        )
        os.chmod(self.env_path, 0o600)

    def execute(self, action: str) -> dict[str, object]:
        return manager._execute(
            env_path=self.env_path,
            port=3002,
            action=action,
            extension_id="crewai",
        )

    def test_enabled_runtime_states_are_effectively_equivalent(self) -> None:
        self.assertTrue(manager._same_effective_status("enabled", "cli_installed"))
        self.assertTrue(manager._same_effective_status("enabled", "enabled"))
        self.assertFalse(manager._same_effective_status("enabled", "disabled"))

    def test_request_grammar_is_exact(self) -> None:
        request = json.dumps(
            {"schemaVersion": 1, "action": "inspect", "extensionId": "crewai"}
        ).encode()
        self.assertEqual(manager._parse_request(request), ("inspect", "crewai"))
        with self.assertRaises(manager.ManagerError):
            manager._parse_request(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "action": "inspect",
                        "extensionId": "crewai",
                        "command": "id",
                    }
                ).encode()
            )
        dotted = json.dumps(
            {"schemaVersion": 1, "action": "inspect", "extensionId": "tools.v2"}
        ).encode()
        self.assertEqual(manager._parse_request(dotted), ("inspect", "tools.v2"))
        with self.assertRaises(manager.ManagerError):
            manager._parse_request(
                json.dumps(
                    {"schemaVersion": 1, "action": "inspect", "extensionId": "crewai."}
                ).encode()
            )
        with self.assertRaises(manager.ManagerError):
            manager._parse_request(
                json.dumps(
                    {"schemaVersion": 1, "action": "exec", "extensionId": "crewai"}
                ).encode()
            )
        inventory = json.dumps(
            {"schemaVersion": 1, "action": "list", "extensionId": "all"}
        ).encode()
        self.assertEqual(manager._parse_request(inventory), ("list", "all"))
        for invalid in (
            {"schemaVersion": 1, "action": "list", "extensionId": "crewai"},
            {"schemaVersion": 1, "action": "inspect", "extensionId": "all"},
        ):
            with self.assertRaises(manager.ManagerError):
                manager._parse_request(json.dumps(invalid).encode())

    def test_live_inventory_projects_only_bounded_status_metadata(self) -> None:
        response = {
            "extensions": [
                {
                    "id": "open-webui",
                    "name": "Open WebUI",
                    "description": "must not cross the boundary",
                    "category": "interfaces",
                    "status": "enabled",
                    "source": "core",
                    "installable": False,
                    "public_url": "http://secret.internal",
                    "env_vars": [{"key": "SECRET", "value": "must-not-leak"}],
                },
                {
                    "id": "crewai",
                    "name": "CrewAI",
                    "category": "agents",
                    "status": "not_installed",
                    "source": "library",
                    "installable": True,
                },
                {
                    "id": "continue",
                    "name": "Continue",
                    "category": "development",
                    "status": "disabled",
                    "source": "user",
                    "installable": False,
                },
            ],
            "summary": {"untrusted": "ignored"},
            "gpu_backend": "nvidia",
            "agent_available": True,
        }
        with mock.patch.object(manager, "_request_json", return_value=(200, response)) as request:
            result = manager._extension_inventory(3002, "a" * 64)
        request.assert_called_once_with(
            port=3002,
            credential="a" * 64,
            method="GET",
            path="/api/extensions/catalog",
            timeout=30,
        )
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(result["summary"]["total"], 3)
        self.assertEqual(result["summary"]["installed"], 2)
        self.assertEqual(result["summary"]["enabled"], 1)
        self.assertEqual(result["summary"]["disabled"], 1)
        self.assertEqual(result["summary"]["notInstalled"], 1)
        self.assertEqual(
            set(result["extensions"][0]),
            {"id", "name", "category", "status", "source", "installable"},
        )
        serialized = json.dumps(result)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("secret.internal", serialized)
        self.assertNotIn("a" * 64, serialized)

    def test_live_inventory_rejects_duplicate_or_unbounded_rows(self) -> None:
        duplicate = {
            "extensions": [
                {
                    "id": "crewai",
                    "name": "CrewAI",
                    "category": "agents",
                    "status": "enabled",
                    "source": "user",
                    "installable": False,
                },
                {
                    "id": "crewai",
                    "name": "CrewAI Again",
                    "category": "agents",
                    "status": "disabled",
                    "source": "library",
                    "installable": True,
                },
            ]
        }
        with mock.patch.object(manager, "_request_json", return_value=(200, duplicate)):
            with self.assertRaises(manager.ManagerError):
                manager._extension_inventory(3002, "a" * 64)

    def test_operations_status_request_is_exact_and_hash_bound(self) -> None:
        job_id = "ops-1788127319657-f3262c99a419"
        plan_hash = "e" * 64
        request = json.dumps(
            {
                "schemaVersion": 1,
                "action": "opsStatus",
                "jobId": job_id,
                "planHash": plan_hash,
            }
        ).encode()
        self.assertEqual(manager._parse_status_request(request), (job_id, plan_hash))
        with self.assertRaises(manager.ManagerError):
            manager._parse_status_request(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "action": "opsStatus",
                        "jobId": job_id,
                        "planHash": plan_hash,
                        "path": "/etc/shadow",
                    }
                ).encode()
            )

    def test_operations_status_projects_only_exact_nonsecret_receipt(self) -> None:
        results = pathlib.Path(self.temporary.name) / "results"
        results.mkdir(mode=0o750)
        job_id = "ops-1788127319657-f3262c99a419"
        plan_hash = "e" * 64
        status_path = results / f"{job_id}.json"
        status_path.write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "jobId": job_id,
                    "planHash": plan_hash,
                    "status": "awaiting-approval",
                    "riskTier": "managed",
                    "approvalRequired": True,
                    "updatedAt": "2026-08-30T22:01:59Z",
                    "authorityReceipt": {"secret": "must-not-project"},
                }
            ),
            encoding="utf-8",
        )
        os.chmod(status_path, 0o640)
        with mock.patch.object(manager, "OPS_RESULTS_DIR", results):
            projection = manager._read_operations_status(
                results_dir=results,
                broker_uid=os.getuid(),
                job_id=job_id,
                plan_hash=plan_hash,
            )
            with self.assertRaises(manager.ManagerError):
                manager._read_operations_status(
                    results_dir=results,
                    broker_uid=os.getuid(),
                    job_id=job_id,
                    plan_hash="f" * 64,
                )
        self.assertEqual(
            set(projection),
            {
                "schemaVersion",
                "kind",
                "jobId",
                "planHash",
                "status",
                "riskTier",
                "approvalRequired",
                "updatedAt",
            },
        )
        self.assertNotIn("must-not-project", json.dumps(projection))

    def test_environment_reader_rejects_ambiguity_symlinks_and_weak_permissions(self) -> None:
        self.env_path.write_text(
            "DASHBOARD_API_KEY=" + "a" * 64 + "\nDASHBOARD_API_KEY=" + "b" * 64 + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(manager.ManagerError, "duplicate"):
            manager._read_env(self.env_path)

        self.env_path.write_text("DASHBOARD_API_KEY=" + "a" * 64 + "\n", encoding="utf-8")
        os.chmod(self.env_path, 0o622)
        with self.assertRaisesRegex(manager.ManagerError, "unsafe"):
            manager._read_env(self.env_path)

        os.chmod(self.env_path, 0o600)
        link = self.env_path.with_name("linked.env")
        link.symlink_to(self.env_path)
        with self.assertRaises(manager.ManagerError):
            manager._read_env(link)

    def test_inspection_reports_only_missing_key_names_without_a_change(self) -> None:
        with mock.patch.object(
            manager,
            "_detail",
            return_value=detail("not_installed", required=("CREWAI_API_KEY",)),
        ):
            result = self.execute("inspect")
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(result["missingConfiguration"], ["CREWAI_API_KEY"])
        self.assertFalse(result["changed"])
        self.assertFalse(result["externalEffectOccurred"])
        self.assertNotIn("must-not-leak", json.dumps(result))
        self.assertNotIn("a" * 64, json.dumps(result))

    def test_configuration_metadata_cannot_classify_one_key_twice(self) -> None:
        with self.assertRaisesRegex(manager.ManagerError, "ambiguous"):
            manager._configuration_keys(
                {
                    "env_vars": [
                        {"key": "TOKEN", "required": True},
                        {"key": "TOKEN", "required": False},
                    ]
                }
            )

    def test_unsafe_state_transition_is_a_verified_no_effect_block(self) -> None:
        with (
            mock.patch.object(manager, "_detail", return_value=detail("unhealthy")),
            mock.patch.object(manager, "_mutate") as mutate,
        ):
            result = self.execute("disable")
        mutate.assert_not_called()
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(result["currentStatus"], "unhealthy")
        self.assertFalse(result["externalEffectOccurred"])

    def test_install_success_is_reconciled_to_the_expected_state(self) -> None:
        with (
            mock.patch.object(manager, "_detail", return_value=detail("not_installed")),
            mock.patch.object(manager, "_mutate") as mutate,
            mock.patch.object(manager, "_wait_for_status", return_value="enabled") as wait,
        ):
            result = self.execute("install")
        mutate.assert_called_once()
        wait.assert_called_once()
        self.assertEqual(result["outcome"], "succeeded")
        self.assertTrue(result["changed"])
        self.assertTrue(result["externalEffectOccurred"])
        self.assertEqual(result["rollback"], {"attempted": False, "succeeded": None})

    def test_ambiguous_install_timeout_reconciles_and_rolls_back(self) -> None:
        with (
            mock.patch.object(
                manager,
                "_detail",
                side_effect=[
                    detail("not_installed"),
                    detail("error"),
                    detail("error"),
                ],
            ),
            mock.patch.object(
                manager,
                "_mutate",
                side_effect=[manager.ManagerError("timeout"), None, None],
            ) as mutate,
            mock.patch.object(
                manager,
                "_wait_for_status",
                side_effect=["disabled", "not_installed"],
            ),
        ):
            result = self.execute("install")
        self.assertEqual(mutate.call_count, 3)
        self.assertEqual(result["outcome"], "failed")
        self.assertFalse(result["changed"])
        self.assertTrue(result["externalEffectOccurred"])
        self.assertEqual(result["currentStatus"], "not_installed")
        self.assertEqual(result["rollback"], {"attempted": True, "succeeded": True})

    def test_remove_enabled_extension_safely_disables_then_preserves_data_on_remove(self) -> None:
        with (
            mock.patch.object(manager, "_detail", return_value=detail("enabled")),
            mock.patch.object(manager, "_mutate") as mutate,
            mock.patch.object(
                manager,
                "_wait_for_status",
                side_effect=["disabled", "not_installed"],
            ) as wait,
        ):
            result = self.execute("remove")
        self.assertEqual(
            [call.kwargs["action"] for call in mutate.call_args_list],
            ["disable", "remove"],
        )
        self.assertEqual(wait.call_count, 2)
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(result["previousStatus"], "enabled")
        self.assertEqual(result["currentStatus"], "not_installed")
        self.assertTrue(result["changed"])
        self.assertTrue(result["externalEffectOccurred"])
        self.assertEqual(result["rollback"], {"attempted": False, "succeeded": None})

    def test_remove_failure_after_safe_disable_restores_enabled_state(self) -> None:
        with (
            mock.patch.object(
                manager,
                "_detail",
                side_effect=[
                    detail("enabled"),
                    detail("disabled"),
                    detail("disabled"),
                ],
            ),
            mock.patch.object(
                manager,
                "_mutate",
                side_effect=[None, manager.ManagerError("timeout"), None],
            ) as mutate,
            mock.patch.object(
                manager,
                "_wait_for_status",
                side_effect=["disabled", "enabled"],
            ),
        ):
            result = self.execute("remove")
        self.assertEqual(
            [call.kwargs["action"] for call in mutate.call_args_list],
            ["disable", "remove", "enable"],
        )
        self.assertEqual(result["outcome"], "failed")
        self.assertFalse(result["changed"])
        self.assertTrue(result["externalEffectOccurred"])
        self.assertEqual(result["currentStatus"], "enabled")
        self.assertEqual(result["rollback"], {"attempted": True, "succeeded": True})

    def test_enable_never_auto_enables_dependencies(self) -> None:
        class Response:
            status = 200

            class Headers:
                @staticmethod
                def get_content_type() -> str:
                    return "application/json"

                @staticmethod
                def get(_name: str) -> None:
                    return None

            headers = Headers()

            @staticmethod
            def read(_maximum: int) -> bytes:
                return b"{}"

            @staticmethod
            def close() -> None:
                return None

        connection = mock.Mock()
        connection.getresponse.return_value = Response()
        with mock.patch.object(
            manager.http.client, "HTTPConnection", return_value=connection
        ) as connection_factory:
            manager._mutate(
                port=3002,
                credential="a" * 64,
                action="enable",
                extension_id="crewai",
                previous="disabled",
            )
        connection_factory.assert_called_once_with("127.0.0.1", 3002, timeout=120)
        self.assertIn("auto_enable_deps=false", connection.request.call_args.args[1])

    def test_internal_http_rejects_non_api_and_absolute_urls(self) -> None:
        with mock.patch.object(manager.http.client, "HTTPConnection") as connection:
            for path in ("http://example.com/api/extensions/crewai", "/api/models"):
                with self.assertRaisesRegex(manager.ManagerError, "invalid internal ODS request"):
                    manager._request_json(
                        port=3002,
                        credential="a" * 64,
                        method="GET",
                        path=path,
                        timeout=1.0,
                    )
        connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
