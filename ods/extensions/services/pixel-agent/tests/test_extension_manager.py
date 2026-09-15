#!/usr/bin/env python3
"""Unit tests for Pixel's scoped ODS extension lifecycle manager."""

from __future__ import annotations

import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires POSIX host ownership, file locks, or Unix sockets; run under Linux/WSL")

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
        "source": "library" if status == "not_installed" else "user",
        "update_status": "unavailable" if status == "not_installed" else "current",
        "env_vars": [
            {"key": key, "required": True, "value": "must-not-leak"}
            for key in required
        ],
    }


def assistant_plan_response() -> dict[str, object]:
    return {
        "schema": "ods.assistant-first.assistant-plan-proposal.v1",
        "transactionId": "txn-" + "c" * 24,
        "planHash": "d" * 64,
        "state": "awaiting_approval",
        "validUntil": "2026-09-12T12:15:00Z",
        "requested": {"action": "install", "serviceId": "crewai"},
        "selectedExtensions": [
            {
                "id": "crewai",
                "reason": "requested",
                "plannedAction": "install",
                "dependencyCount": 1,
            },
            {
                "id": "qdrant",
                "reason": "dependency",
                "plannedAction": "noop",
                "dependencyCount": 0,
            },
        ],
        "impact": {
            "downloadBytes": 100,
            "diskBytes": 200,
            "cpuMillicores": 300,
            "ramBytes": 400,
            "vramBytes": 0,
            "gpuCount": 0,
            "hostPortCount": 1,
            "permissionCount": 0,
            "dataPathCount": 1,
        },
        "configuration": {
            "required": True,
            "requiredFieldCount": 1,
            "secretsRequired": True,
            "requiredSecretCount": 1,
        },
        "warningCount": 2,
        "rollbackAvailable": True,
        "approvalRequired": True,
        "executionAvailable": False,
        "duplicate": False,
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

    def test_inspection_does_not_promote_declared_keys_to_runtime_readiness(self) -> None:
        for state, keys, configured in [
            ("not_installed", (), False),
            ("disabled", ("APP_TOKEN",), False),
            ("enabled", ("APP_TOKEN",), True),
        ]:
            with self.subTest(state=state):
                if configured:
                    with self.env_path.open("a", encoding="utf-8") as handle:
                        handle.write("APP_TOKEN=private-test-value\n")
                with mock.patch.object(manager, "_detail", return_value=detail(state, required=keys)):
                    result = self.execute("inspect")
                self.assertEqual(result["configurationScope"], "declared-environment-keys")
                self.assertIs(result["runtimeRequirementsVerified"], False)
                self.assertEqual(result["currentStatus"], state)
                self.assertEqual(result["outcome"], "blocked" if keys and not configured else "inspected")
                self.assertEqual(result["missingConfiguration"], list(keys) if not configured else [])
                self.assertFalse(result["changed"])
                self.assertNotIn("private-test-value", json.dumps(result))
                self.assertNotIn("must-not-leak", json.dumps(result))

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
        proposal = json.dumps(
            {
                "schemaVersion": 1,
                "action": "request-plan",
                "extensionId": "install:crewai",
            }
        ).encode()
        self.assertEqual(
            manager._parse_request(proposal), ("request-plan", "install:crewai")
        )
        hyphenated_proposal = json.dumps(
            {
                "schemaVersion": 1,
                "action": "request-plan",
                "extensionId": "enable:tools-v2",
            }
        ).encode()
        self.assertEqual(
            manager._parse_request(hyphenated_proposal),
            ("request-plan", "enable:tools-v2"),
        )
        for invalid in (
            {"schemaVersion": 1, "action": "list", "extensionId": "crewai"},
            {"schemaVersion": 1, "action": "inspect", "extensionId": "all"},
            {
                "schemaVersion": 1,
                "action": "request-plan",
                "extensionId": "execute:crewai",
            },
            {
                "schemaVersion": 1,
                "action": "request-plan",
                "extensionId": "install:tools.",
            },
            {
                "schemaVersion": 1,
                "action": "request-plan",
                "extensionId": "install:tools.v2",
            },
        ):
            with self.assertRaises(manager.ManagerError):
                manager._parse_request(json.dumps(invalid).encode())

    def test_assistant_plan_is_exact_nonsecret_and_nonmutating(self) -> None:
        upstream = assistant_plan_response()
        with (
            mock.patch.object(manager.secrets, "token_hex", return_value="b" * 64),
            mock.patch.object(
                manager, "_request_json", return_value=(201, upstream)
            ) as request,
            mock.patch.object(manager, "_detail") as detail_request,
            mock.patch.object(manager, "_mutate") as mutation,
        ):
            result = manager._execute(
                env_path=self.env_path,
                port=3002,
                action="request-plan",
                extension_id="install:crewai",
            )

        request.assert_called_once_with(
            port=3002,
            credential="a" * 64,
            method="POST",
            path="/api/extensions/transactions/assistant-plan",
            timeout=60,
            request_body={
                "request": "install:crewai",
                "idempotencyKey": "b" * 64,
            },
        )
        detail_request.assert_not_called()
        mutation.assert_not_called()
        self.assertEqual(result["kind"], manager.PLAN_KIND)
        self.assertEqual(result["outcome"], "proposed")
        self.assertEqual(result["transactionId"], "txn-" + "c" * 24)
        self.assertEqual(result["planHash"], "d" * 64)
        self.assertEqual(result["state"], "awaiting_approval")
        self.assertFalse(result["externalEffectOccurred"])
        serialized = json.dumps(result)
        self.assertNotIn("API_KEY", serialized)
        self.assertNotIn("catalogRevision", serialized)
        self.assertNotIn("operations", serialized)

    def test_assistant_plan_rejects_extra_or_inconsistent_projection(self) -> None:
        for mutate in (
            lambda value: value.update({"secretValues": {"TOKEN": "leak"}}),
            lambda value: value["configuration"].update(
                {"required": False, "requiredFieldCount": 1}
            ),
            lambda value: value.update({"approvalRequired": False}),
            lambda value: value["selectedExtensions"][0].update(
                {"plannedAction": "delete"}
            ),
        ):
            value = assistant_plan_response()
            mutate(value)
            with mock.patch.object(manager, "_request_json", return_value=(201, value)):
                with self.assertRaises(manager.ManagerError):
                    manager._assistant_plan(3002, "a" * 64, "install:crewai")

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

    def test_install_does_not_retry_or_rollback_recovery_required_state(self) -> None:
        with (
            mock.patch.object(manager, "_detail", return_value=detail("error")),
            mock.patch.object(manager, "_mutate") as mutate,
        ):
            result = self.execute("install")
        mutate.assert_not_called()
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(result["currentStatus"], "error")
        self.assertFalse(result["externalEffectOccurred"])
        self.assertFalse(result["rollback"]["attempted"])

    def test_install_success_is_reconciled_to_the_expected_state(self) -> None:
        with (
            mock.patch.object(
                manager, "_detail",
                side_effect=[detail("not_installed"), detail("enabled"), detail("enabled")],
            ),
            mock.patch.object(manager, "_mutate") as mutate,
            mock.patch.object(manager, "_wait_for_status", return_value="enabled") as wait,
            mock.patch.object(manager, "_wait_for_install_progress", return_value="started") as progress,
        ):
            result = self.execute("install")
        mutate.assert_called_once()
        wait.assert_called_once()
        progress.assert_called_once()
        self.assertEqual(
            wait.call_args.kwargs["deadline"], progress.call_args.kwargs["deadline"]
        )
        self.assertEqual(result["outcome"], "succeeded")
        self.assertTrue(result["changed"])
        self.assertTrue(result["externalEffectOccurred"])
        self.assertEqual(result["rollback"], {"attempted": False, "succeeded": None})

    def test_ambiguous_install_timeout_preserves_failed_owner_state(self) -> None:
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
                side_effect=manager.ManagerError("timeout"),
            ) as mutate,
            mock.patch.object(manager, "_wait_for_status") as wait,
        ):
            result = self.execute("install")
        self.assertEqual(mutate.call_count, 1)
        wait.assert_not_called()
        self.assertEqual(result["outcome"], "failed")
        self.assertTrue(result["changed"])
        self.assertTrue(result["externalEffectOccurred"])
        self.assertEqual(result["currentStatus"], "error")
        self.assertEqual(result["rollback"], {"attempted": False, "succeeded": None})

    def test_one_click_install_needs_a_tracked_library_receipt(self) -> None:
        untracked = {**detail("enabled"), "update_status": "untracked"}
        with (
            mock.patch.object(
                manager, "_detail",
                side_effect=[detail("not_installed"), untracked, untracked],
            ),
            mock.patch.object(manager, "_mutate") as mutate,
            mock.patch.object(
                manager, "_wait_for_status", return_value="enabled"
            ),
        ):
            result = self.execute("install")
        self.assertEqual(mutate.call_count, 1)
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["currentStatus"], "enabled")
        self.assertEqual(result["rollback"], {"attempted": False, "succeeded": None})

    def test_one_click_never_claims_stale_copy_without_terminal_host_progress(self) -> None:
        for phase in ("pulling", "error", "idle"):
            with self.subTest(phase=phase):
                with (
                    mock.patch.object(
                        manager, "_detail",
                        side_effect=[detail("not_installed"), detail("cli_installed"), detail("cli_installed")],
                    ),
                    mock.patch.object(manager, "_mutate"),
                    mock.patch.object(manager, "_wait_for_status", return_value="cli_installed"),
                    mock.patch.object(manager, "_wait_for_install_progress", return_value=phase) as progress,
                ):
                    result = self.execute("install")
                progress.assert_called_once()
                self.assertEqual(result["outcome"], "failed")
                self.assertEqual(result["currentStatus"], "cli_installed")
                self.assertTrue(result["externalEffectOccurred"])
                self.assertFalse(result["rollback"]["attempted"])

    def test_one_shot_one_click_succeeds_on_the_same_started_host_receipt(self) -> None:
        with (
            mock.patch.object(
                manager, "_detail",
                side_effect=[detail("not_installed"), detail("cli_installed"), detail("cli_installed")],
            ),
            mock.patch.object(manager, "_mutate"),
            mock.patch.object(manager, "_wait_for_status", return_value="cli_installed"),
            mock.patch.object(manager, "_wait_for_install_progress", return_value="started") as progress,
        ):
            result = self.execute("install")
        progress.assert_called_once()
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(result["currentStatus"], "cli_installed")
        self.assertFalse(result["runtimeRequirementsVerified"])

    def test_ambiguous_one_click_post_cannot_reuse_prior_started_progress(self) -> None:
        with (
            mock.patch.object(
                manager, "_detail",
                side_effect=[detail("not_installed"), detail("cli_installed"),
                             detail("cli_installed"), detail("cli_installed")],
            ),
            mock.patch.object(manager, "_mutate", side_effect=manager.ManagerError("timeout")),
            mock.patch.object(manager, "_wait_for_status") as wait,
            mock.patch.object(manager, "_wait_for_install_progress") as progress,
        ):
            result = self.execute("install")
        wait.assert_not_called()
        progress.assert_not_called()
        self.assertEqual(result["outcome"], "failed")
        self.assertTrue(result["externalEffectOccurred"])

    def test_one_click_progress_projection_rejects_foreign_or_malformed_receipts(self) -> None:
        base = {"service_id": "crewai", "status": "started",
                "started_at": "2026-09-15T15:00:00+00:00",
                "updated_at": "2026-09-15T15:00:01+00:00"}
        for response in (
            (200, {**base, "service_id": "other"}),
            (200, {**base, "status": {"unsafe": "value"}}),
            (200, {"service_id": "crewai", "status": "started"}),
            (503, base),
        ):
            with self.subTest(response=response), mock.patch.object(
                manager, "_request_json", return_value=response
            ):
                with self.assertRaises(manager.ManagerError):
                    manager._install_progress_status(3002, "a" * 64, "crewai")
        with mock.patch.object(manager, "_request_json", return_value=(200, base)):
            self.assertEqual(
                manager._install_progress_status(3002, "a" * 64, "crewai"), "started"
            )

    def test_one_click_progress_wait_is_bounded_when_host_never_completes(self) -> None:
        with (
            mock.patch.object(manager, "_install_progress_status", return_value="pulling") as progress,
            mock.patch.object(manager.time, "monotonic", side_effect=[0, 1, 31]),
            mock.patch.object(manager.time, "sleep") as sleep,
        ):
            phase = manager._wait_for_install_progress(
                port=3002, credential="a" * 64, extension_id="crewai", deadline=30
            )
        self.assertEqual(phase, "pulling")
        self.assertEqual(progress.call_count, 2)
        self.assertEqual(sleep.call_count, 2)

    def test_one_click_progress_wait_tolerates_only_transient_read_failures(self) -> None:
        with (
            mock.patch.object(
                manager, "_install_progress_status",
                side_effect=[manager.ManagerError("temporary"), "starting", "started"],
            ) as progress,
            mock.patch.object(manager.time, "sleep") as sleep,
        ):
            phase = manager._wait_for_install_progress(
                port=3002, credential="a" * 64, extension_id="crewai",
                deadline=manager.time.monotonic() + 30,
            )
        self.assertEqual(phase, "started")
        self.assertEqual(progress.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

        with (
            mock.patch.object(
                manager, "_install_progress_status",
                side_effect=manager.ManagerError("persistent"),
            ) as progress,
            mock.patch.object(manager.time, "sleep"),
        ):
            with self.assertRaises(manager.ManagerError):
                manager._wait_for_install_progress(
                    port=3002, credential="a" * 64, extension_id="crewai",
                    deadline=manager.time.monotonic() + 30,
                )
        self.assertEqual(progress.call_count, 3)

    def test_one_click_rechecks_the_library_receipt_after_host_completion(self) -> None:
        untracked = {**detail("enabled"), "update_status": "untracked"}
        with (
            mock.patch.object(
                manager, "_detail",
                side_effect=[detail("not_installed"), detail("enabled"), untracked, untracked],
            ),
            mock.patch.object(manager, "_mutate"),
            mock.patch.object(manager, "_wait_for_status", return_value="enabled"),
            mock.patch.object(manager, "_wait_for_install_progress", return_value="started"),
        ):
            result = self.execute("install")
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["currentStatus"], "enabled")
        self.assertFalse(result["rollback"]["attempted"])

    def test_unhealthy_one_click_install_is_not_claimed_or_removed(self) -> None:
        with (
            mock.patch.object(
                manager,
                "_detail",
                side_effect=[
                    detail("not_installed"),
                    detail("unhealthy"),
                ],
            ),
            mock.patch.object(manager, "_mutate") as mutate,
            mock.patch.object(
                manager, "_wait_for_status", return_value="unhealthy"
            ) as wait,
        ):
            result = self.execute("install")
        self.assertEqual(mutate.call_count, 1)
        self.assertEqual(wait.call_count, 1)
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["currentStatus"], "unhealthy")
        self.assertEqual(result["rollback"], {"attempted": False, "succeeded": None})

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
