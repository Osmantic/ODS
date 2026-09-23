#!/usr/bin/env python3
"""Unit tests for Pixel's scoped ODS extension lifecycle manager."""

from __future__ import annotations

import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires POSIX host ownership, file locks, or Unix sockets; run under Linux/WSL")

import importlib.util
import hashlib
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
    def test_session_resolution_validates_binding_and_uses_fixed_endpoint(self):
        session_hash = hashlib.sha256(b'chat').hexdigest()
        payload = json.dumps({'schemaVersion':1,'action':'github-request-resolve','sessionHash':session_hash}).encode()
        value = {'schemaVersion':1,'kind':'ods-extension-request-scope','sessionHash':session_hash,
                 'authorizationMode':'install','request':{'chatId':'chat','requestId':'original'}}
        with mock.patch.object(manager, '_request_json', return_value=(200,value)) as request:
            self.assertEqual(manager._resolve_request(self.env_path,3002,payload),value)
            self.assertEqual(request.call_args.kwargs['path'],'/api/extensions/github/requests/resolve')
            self.assertEqual(request.call_args.kwargs['body'],{'sessionHash':session_hash})
        for bad in [{'chatId':'other','requestId':'original'}, {'chatId':'chat','requestId':'../bad'}]:
            with self.subTest(bad=bad), mock.patch.object(manager, '_request_json',
                    return_value=(200,{**value,'request':bad})), self.assertRaises(manager.ManagerError):
                manager._resolve_request(self.env_path,3002,payload)
        with mock.patch.object(manager, '_request_json', return_value=(200,{**value,'request':None,'authorizationMode':None})):
            self.assertIsNone(manager._resolve_request(self.env_path,3002,payload)['request'])
        for bad in [{'authorizationMode': 'unknown'}, {'authorizationMode': None}]:
            with self.subTest(bad=bad), mock.patch.object(manager, '_request_json',
                    return_value=(200,{**value,**bad})), self.assertRaises(manager.ManagerError):
                manager._resolve_request(self.env_path,3002,payload)

    def test_session_resolution_crosses_the_http_request_boundary(self):
        session_hash = hashlib.sha256(b'chat').hexdigest()
        value = {'schemaVersion':1,'kind':'ods-extension-request-scope',
                 'sessionHash':session_hash,'authorizationMode':'install',
                 'request':{'chatId':'chat','requestId':'original'}}
        response=mock.Mock(status=200)
        response.headers.get_content_type.return_value='application/json'
        response.headers.get.return_value=None
        response.read.return_value=json.dumps(value).encode()
        connection=mock.Mock()
        connection.getresponse.return_value=response
        payload=json.dumps({'schemaVersion':1,'action':'github-request-resolve','sessionHash':session_hash}).encode()
        with mock.patch.object(manager.http.client,'HTTPConnection',return_value=connection):
            self.assertEqual(manager._resolve_request(self.env_path,3002,payload),value)
        sent=connection.request.call_args
        self.assertEqual(sent.args,('POST','/api/extensions/github/requests/resolve'))
        self.assertEqual(json.loads(sent.kwargs['body']),{'sessionHash':session_hash})
        connection.close.assert_called_once()

    def test_failed_lookup_does_not_invent_an_uninstalled_state(self):
        for action in ('inspect', 'install', 'enable', 'disable', 'remove'):
            with self.subTest(action=action):
                result = manager._error_result(action, 'unknown-extension')
                self.assertEqual(result['outcome'], 'failed')
                self.assertEqual(result['previousStatus'], 'unknown')
                self.assertEqual(result['currentStatus'], 'unknown')
                self.assertFalse(result['changed'])
                self.assertFalse(result['externalEffectOccurred'])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.env_path = pathlib.Path(self.temporary.name) / ".env"
        self.env_path.write_text(
            "DASHBOARD_API_KEY=" + "a" * 64 + "\n",
            encoding="utf-8",
        )
        os.chmod(self.env_path, 0o600)
        self.prerequisites_patch = mock.patch.object(manager, "_installation_prerequisites",
            return_value={"state": "unavailable", "steps": []})
        self.prerequisites_patch.start()
        self.addCleanup(self.prerequisites_patch.stop)

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

    def test_newer_healthy_plan_reconciles_stale_detail_without_mutation(self):
        plan = {'state': 'ready', 'steps': [{'extensionId': 'crewai', 'status': 'enabled',
                                           'action': 'none', 'missingConfiguration': []}]}
        for action in ('inspect', 'install', 'enable'):
            with self.subTest(action=action), \
                 mock.patch.object(manager, '_detail', return_value=detail('stopped')), \
                 mock.patch.object(manager, '_installation_prerequisites', return_value=plan), \
                 mock.patch.object(manager, '_mutate') as mutate:
                result = self.execute(action)
            self.assertEqual(result['currentStatus'], 'enabled')
            self.assertFalse(result['externalEffectOccurred'])
            self.assertEqual(result['outcome'], 'inspected' if action == 'inspect' else 'noop')
            mutate.assert_not_called()

    def test_newer_pending_plan_prevents_duplicate_enable(self):
        plan = {'state': 'pending', 'steps': [{'extensionId': 'crewai', 'status': 'installing',
                                             'action': 'wait', 'missingConfiguration': []}]}
        with mock.patch.object(manager, '_detail', return_value=detail('disabled')), \
             mock.patch.object(manager, '_installation_prerequisites', return_value=plan), \
             mock.patch.object(manager, '_mutate') as mutate:
            result = self.execute('enable')
        self.assertEqual(result['currentStatus'], 'installing')
        self.assertEqual(result['outcome'], 'blocked')
        mutate.assert_not_called()

    def test_environment_rotation_is_read_on_the_next_operation(self):
        with mock.patch.object(manager, '_detail', return_value=detail('enabled')) as read:
            self.execute('inspect')
            replacement = self.env_path.with_suffix('.new')
            replacement.write_text('DASHBOARD_API_KEY=' + 'b' * 64 + '\n')
            replacement.chmod(0o600)
            os.replace(replacement, self.env_path)
            self.execute('inspect')
        self.assertEqual([call.args[1] for call in read.call_args_list], ['a' * 64, 'b' * 64])

    def test_enable_starts_existing_stopped_extension_without_reinstalling(self) -> None:
        with (
            mock.patch.object(manager, "_detail", return_value=detail("stopped")),
            mock.patch.object(manager, "_mutate") as mutate,
            mock.patch.object(manager, "_wait_for_status", return_value="enabled"),
        ):
            result = self.execute("enable")
        self.assertEqual(mutate.call_count, 1)
        self.assertEqual(mutate.call_args.kwargs["action"], "enable")
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(result["currentStatus"], "enabled")

    def test_failed_start_does_not_disable_existing_compose_definition(self) -> None:
        with (
            mock.patch.object(manager, "_detail", return_value=detail("stopped")),
            mock.patch.object(manager, "_mutate") as mutate,
            mock.patch.object(manager, "_wait_for_status", return_value="unhealthy"),
        ):
            result = self.execute("enable")
        self.assertEqual(mutate.call_count, 1)
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["currentStatus"], "unhealthy")
        self.assertEqual(result["rollback"], {"attempted": False, "succeeded": None})
        self.assertTrue(result["externalEffectOccurred"])

    def test_stopped_extension_missing_configuration_is_not_started(self) -> None:
        with (
            mock.patch.object(manager, "_detail", return_value=detail("stopped", required=("APP_TOKEN",))),
            mock.patch.object(manager, "_mutate") as mutate,
        ):
            result = self.execute("enable")
        mutate.assert_not_called()
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(result["missingConfiguration"], ["APP_TOKEN"])

    def test_repeated_enable_after_start_is_noop(self) -> None:
        with (
            mock.patch.object(manager, "_detail", side_effect=[detail("stopped"), detail("enabled"), detail("enabled")]),
            mock.patch.object(manager, "_mutate") as mutate,
            mock.patch.object(manager, "_wait_for_status", return_value="enabled"),
        ):
            first = self.execute("enable")
            second = self.execute("enable")
            third = self.execute("enable")
        self.assertEqual(first["outcome"], "succeeded")
        self.assertEqual(second["outcome"], "noop")
        self.assertEqual(third["outcome"], "noop")
        self.assertEqual(mutate.call_count, 1)

    def test_start_timeout_reconciles_success_without_second_mutation(self) -> None:
        with (
            mock.patch.object(manager, "_detail", side_effect=[detail("stopped"), detail("enabled")]),
            mock.patch.object(manager, "_mutate", side_effect=manager.ManagerError("timeout")) as mutate,
        ):
            result = self.execute("enable")
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(mutate.call_count, 1)
        self.assertEqual(result["currentStatus"], "enabled")

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

    def test_live_inventory_does_not_count_unconfirmed_installations(self) -> None:
        states = ["enabled", "cli_installed", "disabled", "stopped", "unhealthy",
                  "installing", "setting_up", "error", "not_installed", "incompatible"]
        response = {"extensions": [
            {"id": state, "name": state, "category": "tools", "status": state,
             "source": "user", "installable": True}
            for state in states
        ]}
        with mock.patch.object(manager, "_request_json", return_value=(200, response)):
            result = manager._extension_inventory(3002, "a" * 64)
        self.assertEqual(result["summary"]["total"], 10)
        self.assertEqual(result["summary"]["installed"], 5)
        for key in ["installing", "settingUp", "error"]:
            self.assertEqual(result["summary"][key], 1)
        self.assertEqual({row["status"] for row in result["extensions"]}, set(states))

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

    def test_inspection_uses_current_host_configuration_not_stale_local_projection(self):
        for missing in ([], ['CREWAI_API_KEY']):
            with self.subTest(missing=missing), \
                 mock.patch.object(manager, '_detail', return_value=detail('not_installed', required=('CREWAI_API_KEY',))), \
                 mock.patch.object(manager, '_installation_prerequisites', return_value={
                     'state': 'configuration_required' if missing else 'ready',
                     'steps': [{'extensionId': 'crewai', 'status': 'not_installed', 'action': 'install', 'missingConfiguration': missing}]}):
                self.env_path.write_text('DASHBOARD_API_KEY=' + 'a' * 64 + '\nCREWAI_API_KEY=stale\n')
                result = self.execute('inspect')
            self.assertEqual(result['missingConfiguration'], missing)
            self.assertEqual(result['outcome'], 'blocked' if missing else 'inspected')

    def test_enable_reconciles_host_configuration_before_mutating(self):
        with mock.patch.object(manager, '_detail', return_value=detail('disabled', required=('CREWAI_API_KEY',))), \
             mock.patch.object(manager, '_installation_prerequisites', return_value={
                 'state': 'ready', 'steps': [{'extensionId': 'crewai', 'status': 'disabled', 'action': 'enable', 'missingConfiguration': []}]}), \
             mock.patch.object(manager, '_mutate') as mutate, \
             mock.patch.object(manager, '_wait_for_status', return_value='enabled'):
            result = self.execute('enable')
        mutate.assert_called_once()
        self.assertEqual(result['outcome'], 'succeeded')
        self.assertEqual(result['missingConfiguration'], [])

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

    def test_invalid_required_metadata_cannot_trigger_lifecycle_changes(self) -> None:
        for value in ("true", "false", 1, 0, None, [], {}):
            for action in ("inspect", "install", "enable"):
                with self.subTest(value=value, action=action):
                    metadata = detail("stopped" if action == "enable" else "not_installed")
                    metadata["env_vars"] = [{"key": "TOKEN", "required": value}]
                    with (
                        mock.patch.object(manager, "_detail", return_value=metadata),
                        mock.patch.object(manager, "_mutate") as mutate,
                        mock.patch.object(manager, "_wait_for_status") as wait,
                    ):
                        with self.assertRaisesRegex(manager.ManagerError, "metadata is invalid"):
                            self.execute(action)
                    mutate.assert_not_called()
                    wait.assert_not_called()

    def test_optional_configuration_can_omit_required_flag(self) -> None:
        self.assertEqual(
            manager._configuration_keys({"env_vars": [
                {"key": "OPTIONAL"}, {"key": "EXPLICIT", "required": False},
                {"key": "REQUIRED", "required": True},
            ]}),
            (["REQUIRED"], ["EXPLICIT", "OPTIONAL"]),
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

    def test_late_lifecycle_success_is_not_rolled_back(self) -> None:
        for action, previous, desired in (
            ("install", "not_installed", "enabled"),
            ("enable", "disabled", "enabled"),
            ("disable", "enabled", "disabled"),
        ):
            with (
                self.subTest(action=action),
                mock.patch.object(manager, "_detail", side_effect=[detail(previous), detail(desired)]),
                mock.patch.object(manager, "_mutate") as mutate,
                mock.patch.object(manager, "_wait_for_status", return_value="setting_up"),
            ):
                result = self.execute(action)
            mutate.assert_called_once()
            self.assertEqual(result["outcome"], "succeeded")
            self.assertEqual(result["currentStatus"], desired)
            self.assertEqual(result["rollback"], {"attempted": False, "succeeded": None})

    def test_install_still_running_is_not_replayed_or_removed(self) -> None:
        for active in ("installing", "setting_up"):
            with (
                self.subTest(status=active),
                mock.patch.object(manager, "_detail", side_effect=[detail("not_installed"), detail(active)]),
                mock.patch.object(manager, "_mutate") as mutate,
                mock.patch.object(manager, "_wait_for_status", return_value=active),
            ):
                result = self.execute("install")
            mutate.assert_called_once()
            self.assertEqual(result["outcome"], "pending")
            self.assertEqual(result["currentStatus"], active)
            self.assertEqual(result["rollback"], {"attempted": False, "succeeded": None})

    def test_failed_reconciliation_does_not_claim_rollback(self) -> None:
        with (
            mock.patch.object(manager, "_detail", side_effect=[detail("not_installed"), manager.ManagerError("unavailable")]),
            mock.patch.object(manager, "_mutate") as mutate,
            mock.patch.object(manager, "_wait_for_status", return_value="installing"),
        ):
            result = self.execute("install")
        mutate.assert_called_once()
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


class PrerequisiteProjectionTests(unittest.TestCase):
    def read(self, rows, **override):
        value = {"schemaVersion": 1, "extensionId": "crewai", "steps": rows, **override}
        with mock.patch.object(manager, "_request_json", return_value=(200, value)) as request:
            result = manager._installation_prerequisites(3002, "credential", "crewai")
        self.assertEqual(request.call_args.kwargs['method'], 'GET')
        self.assertEqual(request.call_args.kwargs['path'], '/api/extensions/crewai/install-plan')
        return result

    def row(self, key="crewai", status="not_installed", action="install", missing=None):
        return {"extensionId": key, "status": status, "action": action,
                "missingConfiguration": missing or [], "configuration": [{"value": "secret-never-return"}]}

    def test_dependency_metadata_is_projected_without_credentials(self):
        result = self.read([self.row('db'), self.row()])
        self.assertEqual(result['state'], 'dependencies_required')
        self.assertNotIn('secret-never-return', json.dumps(result))

    def test_missing_dependency_configuration_is_visible(self):
        result = self.read([self.row('db', missing=['DB_PASSWORD']), self.row()])
        self.assertEqual(result['state'], 'configuration_required')
        self.assertEqual(result['steps'][0]['missingConfiguration'], ['DB_PASSWORD'])

    def test_pending_failed_and_ready_prerequisites(self):
        for status, action, expected in [('installing', 'wait', 'pending'),
                ('error', 'blocked', 'blocked'), ('enabled', 'none', 'ready')]:
            self.assertEqual(self.read([self.row('db', status, action), self.row()])['state'], expected)

    def test_invalid_or_unavailable_plan_never_means_no_dependencies(self):
        for rows in [[], [self.row(), self.row()], [self.row('other')],
                     [self.row(action='none')], [self.row(missing=['SECRET=value'])]]:
            self.assertEqual(self.read(rows), {'state': 'unavailable', 'steps': []})
        with mock.patch.object(manager, '_request_json', side_effect=manager.ManagerError('private')):
            self.assertEqual(manager._installation_prerequisites(3002, 'credential', 'crewai'),
                             {'state': 'unavailable', 'steps': []})


class InstallationStepTests(unittest.TestCase):
    def response(self, state='pending', dispatched=True, status='not_installed', action='install', active='crewai'):
        return {'schemaVersion': 1, 'extensionId': 'crewai', 'state': state,
                'dispatched': dispatched, 'activeExtensionId': active,
                'plan': {'schemaVersion': 1, 'extensionId': 'crewai', 'steps': [
                    {'extensionId': 'crewai', 'status': status, 'action': action,
                     'missingConfiguration': [], 'configuration': [{'value': 'private'}]}]}}

    def test_broker_request_grammar_accepts_only_named_target(self):
        payload = json.dumps({'schemaVersion': 1, 'action': 'install-next', 'extensionId': 'crewai'}).encode()
        self.assertEqual(manager._parse_request(payload), ('install-next', 'crewai'))
        with self.assertRaises(manager.ManagerError):
            manager._parse_request(payload.replace(b'crewai', b'../arbitrary'))

    def test_step_projects_acceptance_without_claiming_readiness(self):
        with mock.patch.object(manager, '_request_json', return_value=(200, self.response())) as request:
            result = manager._install_next(3002, 'credential', 'crewai')
        self.assertEqual(result['kind'], manager.INSTALLATION_KIND)
        self.assertEqual(result['state'], 'pending')
        self.assertTrue(result['externalEffectAttempted'])
        self.assertNotIn('private', json.dumps(result))
        self.assertEqual(request.call_args.kwargs['path'], '/api/extensions/crewai/install-next')
        self.assertEqual(request.call_args.kwargs['method'], 'POST')

    def test_success_requires_all_observed_actions_complete(self):
        for response, expected in [
            (self.response('succeeded', False, 'enabled', 'none', None), 'succeeded'),
            (self.response('succeeded', False, 'not_installed', 'install', None), 'reconciliation_required'),
            (self.response('succeeded', True, 'enabled', 'none', None), 'reconciliation_required'),
            (self.response(active='other'), 'reconciliation_required'),
        ]:
            with mock.patch.object(manager, '_request_json', return_value=(200, response)):
                self.assertEqual(manager._install_next(3002, 'credential', 'crewai')['state'], expected)

    def test_unavailable_reply_is_not_retried_or_reported_unchanged(self):
        with mock.patch.object(manager, '_request_json', side_effect=manager.ManagerError('private')) as request:
            result = manager._install_next(3002, 'credential', 'crewai')
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result['state'], 'reconciliation_required')
        self.assertTrue(result['externalEffectAttempted'])
        self.assertNotIn('private', json.dumps(result))

    def test_observation_of_active_download_reports_no_new_dispatch(self):
        with mock.patch.object(manager, '_request_json', return_value=(200,
                self.response('pending', False, 'installing', 'wait'))):
            result = manager._install_next(3002, 'credential', 'crewai')
        self.assertFalse(result['externalEffectAttempted'])
        self.assertEqual(result['state'], 'pending')


class RepositoryEvidenceTests(unittest.TestCase):
    def evidence(self):
        return {'schemaVersion': 1, 'repository': 'https://github.com/owner/repo',
                'commit': 'a' * 40, 'archived': False, 'existingExtensionIds': ['app'],
                'licenseIdentifier': 'MIT', 'readme': 'Read the actual source.', 'licenseText': 'License evidence.',
                'contentTrust': 'untrusted-upstream-evidence', 'evidenceScope': 'repository-documents-at-commit',
                'installationStarted': False, 'registered': False, 'requiresRecipeReview': True}

    def inspect(self, value):
        with mock.patch.object(manager, '_read_env', return_value={'DASHBOARD_API_KEY': 'f' * 64}), \
             mock.patch.object(manager, '_request_json', return_value=(200, value)) as request:
            result = manager._inspect_repository(pathlib.Path('/unused'), 3002, 'https://github.com/owner/repo.git')
        self.assertEqual(request.call_args.kwargs['path'], '/api/extensions/github/inspect')
        self.assertEqual(request.call_args.kwargs['body'], {'url': 'https://github.com/owner/repo'})
        return result

    def test_fixed_observation_returns_only_bounded_evidence(self):
        value = self.evidence()
        value['credential'] = 'never-return-this'
        result = self.inspect(value)
        self.assertEqual(result['kind'], manager.REPOSITORY_KIND)
        self.assertFalse(result['registered'])
        self.assertNotIn('never-return-this', json.dumps(result))
        self.assertNotIn('f' * 64, json.dumps(result))

    def test_verified_pypa_packaging_dual_license_evidence_can_be_pinned(self):
        repository = 'https://github.com/pypa/packaging'
        evidence = {**self.evidence(), 'repository': repository,
                    'commit': '10590c194edb33c82f84a127883d6097c56b7840',
                    'licenseIdentifier': 'Apache-2.0 OR BSD-2-Clause',
                    'licenseText': 'See LICENSE.APACHE and LICENSE.BSD at this commit.'}
        current = {'schemaVersion': 1, 'chatId': 'chat', 'requestId': 'turn',
                   'state': 'pending', 'authorizationMode': 'install',
                   'repository': repository, 'installationStarted': False}
        def response(**kwargs):
            return (200, current if kwargs['path'] == '/api/extensions/github/requests'
                    else evidence)
        with mock.patch.object(manager, '_read_env', return_value={'DASHBOARD_API_KEY': 'f' * 64}), \
             mock.patch.object(manager, '_request_json', side_effect=response):
            pinned = manager._pin_request_repository(pathlib.Path('/unused'), 3002,
                json.dumps({'schemaVersion': 1, 'action': 'github-request-pin',
                            'chatId': 'chat', 'requestId': 'turn'}).encode())
        self.assertEqual(pinned['repository'], repository)
        self.assertEqual(pinned['commit'], evidence['commit'])
        self.assertFalse(pinned['installationStarted'])
        for expression in ('Apache-2.0 OR BSD-2-Clause',
                           '(MIT OR Apache-2.0) AND BSD-3-Clause'):
            self.assertTrue(manager._bounded_spdx_identifier(expression))
        for expression in ('MIT OR', 'MIT OR MIT', 'MIT WITH GPL-3.0',
                           'LicenseRef-private OR MIT', 'MIT; rm -rf / OR ISC',
                           '(MIT OR Apache-2.0', 'MIT AND OR ISC', 'x' * 257):
            self.assertFalse(manager._bounded_spdx_identifier(expression))

    def test_scoped_source_read_projects_only_saved_request_without_github_lookup(self):
        envelope = {'schemaVersion': 1, 'action': 'github-request-read',
                    'chatId': 'chat', 'requestId': 'turn'}
        current = {'schemaVersion': 1, 'chatId': 'chat', 'requestId': 'turn',
                   'state': 'pending', 'authorizationMode': 'install',
                   'repository': 'https://github.com/owner/repo',
                   'installationStarted': False, 'private': 'do-not-return'}
        with mock.patch.object(manager, '_read_env', return_value={'DASHBOARD_API_KEY': 'f' * 64}), \
             mock.patch.object(manager, '_request_json', return_value=(200, current)) as request, \
             mock.patch.object(manager, '_inspect_repository') as inspect:
            result = manager._read_request_source(pathlib.Path('/unused'), 3002,
                json.dumps(envelope).encode())
        self.assertEqual(result, {'schemaVersion': 1, 'kind': 'ods-extension-request-source',
            'chatId': 'chat', 'requestId': 'turn', 'repository': current['repository'],
            'authorizationMode': 'install', 'requestState': 'pending',
            'installationStarted': False})
        self.assertEqual(request.call_args.kwargs['body'],
                         {'action': 'read', 'chatId': 'chat', 'requestId': 'turn'})
        inspect.assert_not_called()
        for change in ({'requestId': 'other'}, {'state': 'expired'},
                       {'installationStarted': True}, {'repository': 'http://localhost/repo'}):
            with self.subTest(change=change), \
                 mock.patch.object(manager, '_read_env', return_value={'DASHBOARD_API_KEY': 'f' * 64}), \
                 mock.patch.object(manager, '_request_json', return_value=(200, {**current, **change})), \
                 self.assertRaises(manager.ManagerError):
                manager._read_request_source(pathlib.Path('/unused'), 3002,
                    json.dumps(envelope).encode())
        with self.assertRaises(manager.ManagerError):
            manager._read_request_source(pathlib.Path('/unused'), 3002,
                json.dumps({**envelope, 'url': 'https://github.com/other/repo'}).encode())

    def test_request_pin_uses_saved_pending_repository_and_returns_only_immutable_commit(self):
        envelope = {'schemaVersion': 1, 'action': 'github-request-pin',
                    'chatId': 'chat', 'requestId': 'turn'}
        current = {'schemaVersion': 1, 'chatId': 'chat', 'requestId': 'turn',
                   'state': 'pending', 'authorizationMode': 'install',
                   'repository': 'https://github.com/owner/repo',
                   'installationStarted': False}
        inspected = {'repository': current['repository'], 'commit': 'a' * 40,
                     'evidenceScope': 'repository-documents-at-commit',
                     'installationStarted': False}
        with mock.patch.object(manager, '_read_env', return_value={'DASHBOARD_API_KEY': 'f' * 64}), \
             mock.patch.object(manager, '_request_json', return_value=(200, current)) as request, \
             mock.patch.object(manager, '_inspect_repository', return_value=inspected) as inspect:
            result = manager._pin_request_repository(pathlib.Path('/unused'), 3002,
                json.dumps(envelope).encode())
        self.assertEqual(request.call_args.kwargs['path'], '/api/extensions/github/requests')
        self.assertEqual(request.call_args.kwargs['body'],
                         {'action': 'read', 'chatId': 'chat', 'requestId': 'turn'})
        inspect.assert_called_once_with(pathlib.Path('/unused'), 3002, current['repository'])
        self.assertEqual(result, {'schemaVersion': 1, 'kind': 'ods-extension-request-commit',
            'chatId': 'chat', 'requestId': 'turn', 'repository': current['repository'],
            'commit': 'a' * 40, 'evidenceScope': 'repository-default-branch-at-inspection',
            'installationStarted': False})

        for change in ({'state': 'expired'}, {'repository': 'http://localhost/repo'},
                       {'requestId': 'other'}, {'authorizationMode': 'invalid'},
                       {'installationStarted': True}):
            with self.subTest(change=change), \
                 mock.patch.object(manager, '_read_env', return_value={'DASHBOARD_API_KEY': 'f' * 64}), \
                 mock.patch.object(manager, '_request_json', return_value=(200, {**current, **change})), \
                 mock.patch.object(manager, '_inspect_repository') as inspect, \
                 self.assertRaises(manager.ManagerError):
                manager._pin_request_repository(pathlib.Path('/unused'), 3002,
                    json.dumps(envelope).encode())
            inspect.assert_not_called()
        with self.assertRaises(manager.ManagerError):
            manager._pin_request_repository(pathlib.Path('/unused'), 3002,
                json.dumps({**envelope, 'repositoryUrl': 'https://github.com/other/repo'}).encode())

    def test_request_pin_reads_saved_request_through_real_http_body_filter(self):
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import threading

        repository = 'https://github.com/owner/repo'
        current = {'schemaVersion': 1, 'chatId': 'chat', 'requestId': 'turn',
                   'state': 'pending', 'authorizationMode': 'install',
                   'repository': repository, 'installationStarted': False}
        observed = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                observed.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                payload = json.dumps(current).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = HTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                env = pathlib.Path(directory) / '.env'
                env.write_text('DASHBOARD_API_KEY=' + 'a' * 64 + '\n')
                env.chmod(0o600)
                inspection = {'repository': repository, 'commit': 'a' * 40,
                              'evidenceScope': 'repository-documents-at-commit',
                              'installationStarted': False}
                with mock.patch.object(manager, '_inspect_repository', return_value=inspection):
                    result = manager._pin_request_repository(env, server.server_port,
                        json.dumps({'schemaVersion': 1, 'action': 'github-request-pin',
                                    'chatId': 'chat', 'requestId': 'turn'}).encode())
                self.assertEqual(result['commit'], inspection['commit'])
                self.assertEqual(observed, [('/api/extensions/github/requests',
                    {'action': 'read', 'chatId': 'chat', 'requestId': 'turn'})])
                for changed in ({'action': 'create'}, {'requestId': '../turn'}, {'extra': 'x'}):
                    with self.assertRaises(manager.ManagerError):
                        manager._request_json(port=server.server_port, credential='a' * 64,
                            method='POST', path='/api/extensions/github/requests', timeout=2,
                            body={'action': 'read', 'chatId': 'chat', 'requestId': 'turn', **changed})
                self.assertEqual(len(observed), 1)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_documents_are_explicitly_truncated_for_model_context(self):
        value = self.evidence()
        value['readme'] = 'x' * 30000
        result = self.inspect(value)
        self.assertEqual(len(result['readme']), 24000)
        self.assertTrue(result['readmeTruncated'])
        self.assertFalse(result['licenseTextTruncated'])

    def test_wrong_identity_mutation_claim_or_mutable_revision_is_rejected(self):
        for field, bad in [('repository', 'https://github.com/other/repo'), ('commit', 'main'),
                           ('registered', True), ('installationStarted', True), ('contentTrust', 'trusted')]:
            with self.subTest(field=field), self.assertRaises(manager.ManagerError):
                self.inspect({**self.evidence(), field: bad})

    def test_repository_grammar_rejects_arbitrary_hosts_paths_and_extra_arguments(self):
        for url in ['https://github.com/owner/repo?token=x', 'http://github.com/owner/repo',
                    'https://user:pass@github.com/owner/repo', 'https://github.com/owner/repo/tree/main',
                    'https://127.0.0.1/private', ' https://github.com/owner/repo']:
            with self.subTest(url=url), self.assertRaises(manager.ManagerError):
                manager._repository_url(url)
        payload = {'schemaVersion': 1, 'action': 'github-inspect',
                   'repositoryUrl': 'https://github.com/owner/repo', 'shell': 'anything'}
        with self.assertRaises(manager.ManagerError):
            manager._parse_repository_request(json.dumps(payload).encode())

    def test_repository_payload_cannot_be_sent_to_mutating_endpoint(self):
        with self.assertRaises(manager.ManagerError):
            manager._request_json(port=3002, credential='unused', method='POST',
                path='/api/extensions/app/install', timeout=1, body={'url': 'https://github.com/owner/repo'})


class RepositoryFileTests(unittest.TestCase):
    def evidence(self, content='services:\n  app: {}\n'):
        raw = content.encode()
        return {'schemaVersion': 1, 'repository': 'https://github.com/owner/repo',
                'commit': 'a' * 40, 'path': 'docker/compose.yaml', 'content': content,
                'blob': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest(),
                'contentTrust': 'untrusted-upstream-evidence', 'evidenceScope': 'repository-file-at-commit',
                'installationStarted': False, 'registered': False}

    def inspect(self, value):
        fields = {'url': 'https://github.com/owner/repo', 'commit': 'a' * 40, 'path': 'docker/compose.yaml'}
        with mock.patch.object(manager, '_read_env', return_value={'DASHBOARD_API_KEY': 'f' * 64}), \
             mock.patch.object(manager, '_request_json', return_value=(200, value)) as request:
            result = manager._inspect_repository_file(pathlib.Path('/unused'), 3002, fields)
        self.assertEqual(request.call_args.kwargs['path'], '/api/extensions/github/file')
        self.assertEqual(request.call_args.kwargs['body'], fields)
        return result

    def test_exact_file_at_exact_commit_is_projected(self):
        result = self.inspect(self.evidence())
        self.assertEqual(result['kind'], manager.REPOSITORY_FILE_KIND)
        self.assertFalse(result['installationStarted'])
        self.assertFalse(result['contentTruncated'])

    def test_truncation_does_not_hide_that_evidence_is_incomplete(self):
        result = self.inspect(self.evidence('x' * 33000))
        self.assertEqual(len(result['content']), 32000)
        self.assertTrue(result['contentTruncated'])

    def test_wrong_file_commit_content_or_mutation_receipt_is_rejected(self):
        for key, bad in [('path', 'other'), ('commit', 'b' * 40), ('blob', 'c' * 40),
                         ('content', 'altered'), ('registered', True)]:
            with self.subTest(key=key), self.assertRaises(manager.ManagerError):
                self.inspect({**self.evidence(), key: bad})

    def test_path_traversal_and_mutable_commit_cannot_reach_api(self):
        for path in ['/etc/passwd', '../secret', 'dir/../secret', 'dir//file', 'dir\\file', 'dir/./file']:
            with self.subTest(path=path), self.assertRaises(manager.ManagerError):
                manager._repository_file_fields('https://github.com/owner/repo', 'a' * 40, path)
        with self.assertRaises(manager.ManagerError):
            manager._repository_file_fields('https://github.com/owner/repo', 'main', 'Dockerfile')

    def test_socket_grammar_has_no_arbitrary_http_fields(self):
        payload = {'schemaVersion': 1, 'action': 'github-file', 'repositoryUrl': 'https://github.com/owner/repo',
                   'commit': 'a' * 40, 'path': 'Dockerfile'}
        self.assertEqual(manager._parse_repository_file_request(json.dumps(payload).encode())['path'], 'Dockerfile')
        with self.assertRaises(manager.ManagerError):
            manager._parse_repository_file_request(json.dumps({**payload, 'headers': {'Authorization': 'x'}}).encode())


class RecipeValidationTests(unittest.TestCase):
    def test_preparation_binds_returned_id_and_digest_to_saved_recipe(self):
        recipe = self.proposal()
        recipe['manifest'] = {'service': {'id': 'example'}}
        receipt = {'schemaVersion': 1, 'state': 'available', 'extensionId': 'example',
                   'recipeDigest': self.evidence(recipe)['recipeDigest'], 'installationStarted': False,
                   'registered': False, 'runtimeVerified': False}
        with mock.patch.object(manager, '_read_repository_draft', return_value={'candidate': recipe}), \
             mock.patch.object(manager, '_read_env', return_value={'DASHBOARD_API_KEY': 'f' * 64}), \
             mock.patch.object(manager, '_request_json', return_value=(200, receipt)) as request:
            result = manager._prepare_repository_draft(pathlib.Path('/unused'), 3002, 'd' * 64)
        request.assert_called_once()
        self.assertEqual(request.call_args.kwargs['path'], '/api/extensions/github/drafts/' + 'd' * 64 + '/prepare')
        self.assertEqual(request.call_args.kwargs['body'], {})
        self.assertEqual(result['kind'], manager.RECIPE_PREPARATION_KIND)
        self.assertEqual(result['draftId'], 'd' * 64)
        for key, bad in [('extensionId', 'other'), ('recipeDigest', '0' * 64),
                         ('installationStarted', True), ('registered', True), ('state', 'installed')]:
            with self.subTest(key=key), self.assertRaises(manager.ManagerError):
                manager._preparation_receipt({**receipt, key: bad}, 'd' * 64, recipe)

    def test_preparation_rejection_is_not_retried(self):
        with mock.patch.object(manager, '_read_repository_draft', return_value={'candidate': self.proposal()}), \
             mock.patch.object(manager, '_read_env', return_value={'DASHBOARD_API_KEY': 'f' * 64}), \
             mock.patch.object(manager, '_request_json', return_value=(409, {})) as request:
            with self.assertRaises(manager.ManagerError):
                manager._prepare_repository_draft(pathlib.Path('/unused'), 3002, 'd' * 64)
        request.assert_called_once()

    def test_preparation_client_uses_fixed_action_and_validates_draft(self):
        value = {'schemaVersion': 1, 'kind': manager.RECIPE_PREPARATION_KIND, 'draftId': 'd' * 64,
                 'state': 'available', 'extensionId': 'example', 'recipeDigest': 'a' * 64,
                 'installationStarted': False, 'registered': False, 'runtimeVerified': False}
        connection = mock.Mock()
        connection.recv.return_value = (json.dumps(value) + '\n').encode()
        with mock.patch.object(manager.socket, 'socket', return_value=connection), mock.patch.object(manager.sys, 'stdout'):
            self.assertEqual(manager.repository_client(manager.SOCKET_PATH,
                draft_id='d' * 64, prepare_draft=True), 0)
        self.assertEqual(json.loads(connection.sendall.call_args.args[0]),
                         {'schemaVersion': 1, 'action': 'github-draft-prepare', 'draftId': 'd' * 64})
        with self.assertRaises(manager.ManagerError):
            manager._preparation_receipt(value, 'e' * 64)

    def test_large_recipe_parts_preserve_content_and_single_dispatch(self):
        recipe = self.proposal()
        recipe['manifest']['description'] = 'configuration ' * 800
        payload = json.dumps(recipe)
        parts = [payload[i:i + 4096] for i in range(0, len(payload), 4096)]
        parts += [''] * (8 - len(parts))
        self.assertEqual(manager._recipe_parts(parts), recipe)
        for mode in ('validate', 'draft-save'):
            with mock.patch.object(manager, 'repository_client', return_value=0) as client:
                self.assertEqual(manager.main(['manager', 'repository-' + mode + '-parts',
                                              '/run/ods-pixel-manager/extension-manager.sock', *parts]), 0)
            client.assert_called_once_with(manager.SOCKET_PATH,
                                           recipe=recipe, save_draft=mode == 'draft-save')

    def test_invalid_recipe_parts_never_dispatch(self):
        for parts in (['{}'] * 7, ['x' * 4097] + [''] * 7,
                      ['\0'] + [''] * 7, ['é' * 4096] * 8,
                      ['{"repository":'] + [''] * 7, ['{}'] + [''] * 7):
            with self.subTest(lengths=[len(p) for p in parts]), \
                 mock.patch.object(manager, 'repository_client') as client, \
                 mock.patch('sys.stderr'):
                self.assertEqual(manager.main(['manager', 'repository-validate-parts',
                                              '/run/ods-pixel-manager/extension-manager.sock', *parts]), 1)
                client.assert_not_called()

    def proposal(self):
        return {'repository': 'https://github.com/owner/repo', 'commit': 'a' * 40,
                'manifest': {'id': 'example'}, 'compose': {'services': {}}}

    def evidence(self, recipe):
        return {'schemaVersion': 1, 'repository': recipe['repository'], 'commit': recipe['commit'],
                'recipeDigest': hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest(),
                'valid': True, 'errors': [], 'existingExtensionIds': [],
                'validationScope': 'static-manifest-and-compose', 'provenanceVerified': False,
                'runtimeVerified': False, 'installationStarted': False, 'registered': False}

    def test_validation_uses_fixed_route_and_omits_credentials_and_proposal(self):
        recipe = self.proposal()
        evidence = {**self.evidence(recipe), 'credential': 'never-forward'}
        with mock.patch.object(manager, '_read_env', return_value={'DASHBOARD_API_KEY': 'f' * 64}), \
             mock.patch.object(manager, '_request_json', return_value=(200, evidence)) as request:
            result = manager._validate_repository_recipe(pathlib.Path('/unused'), 3002, recipe)
        self.assertEqual(request.call_args.kwargs['path'], '/api/extensions/github/validate-recipe')
        self.assertEqual(request.call_args.kwargs['body'], recipe)
        self.assertEqual(result['kind'], manager.RECIPE_VALIDATION_KIND)
        self.assertNotIn('manifest', result)
        self.assertNotIn('never-forward', json.dumps(result))
        self.assertFalse(result['installationStarted'])

    def test_receipt_is_bound_to_proposal_and_cannot_claim_installation(self):
        recipe = self.proposal()
        for key, bad in [('recipeDigest', '0' * 64), ('commit', 'b' * 40),
                         ('registered', True), ('runtimeVerified', True), ('existingExtensionIds', ['example'])]:
            with self.subTest(key=key), self.assertRaises(manager.ManagerError):
                manager._validation_receipt({**self.evidence(recipe), key: bad}, recipe)

    def test_diagnostics_are_returned_without_false_success(self):
        recipe = self.proposal()
        errors = [{'code': 'image-pin-required', 'path': 'compose/services/example/image'}]
        receipt = manager._validation_receipt({**self.evidence(recipe), 'valid': False, 'errors': errors}, recipe)
        self.assertEqual(receipt['errors'], errors)
        self.assertFalse(receipt['valid'])

    def test_extra_fields_and_oversized_proposals_are_rejected(self):
        for recipe in [{**self.proposal(), 'command': 'anything'},
                       {**self.proposal(), 'manifest': {'text': 'x' * 32768}}]:
            with self.assertRaises(manager.ManagerError):
                manager._recipe_candidate(recipe)

    def test_draft_save_uses_fixed_route_and_returns_recovery_id_only(self):
        recipe = self.proposal()
        receipt = {'schemaVersion': 1, 'draftId': 'd' * 64,
                   'recipeDigest': self.evidence(recipe)['recipeDigest'], 'state': 'draft',
                   'requiresRevalidation': True, 'installationStarted': False, 'registered': False,
                   'private': 'never-forward'}
        with mock.patch.object(manager, '_read_env', return_value={'DASHBOARD_API_KEY': 'f' * 64}), \
             mock.patch.object(manager, '_request_json', return_value=(200, receipt)) as request:
            result = manager._save_repository_draft(pathlib.Path('/unused'), 3002, recipe)
        self.assertEqual(request.call_args.kwargs['path'], '/api/extensions/github/drafts')
        self.assertEqual(result['draftId'], 'd' * 64)
        self.assertNotIn('never-forward', json.dumps(result))
        for key, bad in [('registered', True), ('state', 'installed'), ('recipeDigest', 'a' * 64)]:
            with self.subTest(key=key), self.assertRaises(manager.ManagerError):
                manager._draft_receipt({**receipt, key: bad}, recipe)

    def test_draft_mutation_cannot_masquerade_as_validation(self):
        request = json.dumps({'schemaVersion': 1, 'action': 'github-draft-save', 'recipe': self.proposal()}).encode()
        with self.assertRaises(manager.ManagerError):
            manager._parse_recipe_request(request)
        self.assertEqual(manager._parse_recipe_request(request, 'github-draft-save'), self.proposal())

    def test_draft_failure_does_not_return_success_or_retry(self):
        with mock.patch.object(manager, '_read_env', return_value={'DASHBOARD_API_KEY': 'f' * 64}), \
             mock.patch.object(manager, '_request_json', return_value=(409, {'detail': 'private'})) as request:
            with self.assertRaises(manager.ManagerError):
                manager._save_repository_draft(pathlib.Path('/unused'), 3002, self.proposal())
        request.assert_called_once()

    def test_draft_recovery_uses_exact_id_and_keeps_proposal_untrusted(self):
        value = {'schemaVersion': 1, 'draftId': 'd' * 64, 'state': 'draft',
                 'candidate': self.proposal(), 'requiresRevalidation': True,
                 'installationStarted': False, 'registered': False, 'private': 'never-forward'}
        with mock.patch.object(manager, '_read_env', return_value={'DASHBOARD_API_KEY': 'f' * 64}), \
             mock.patch.object(manager, '_request_json', return_value=(200, value)) as request:
            result = manager._read_repository_draft(pathlib.Path('/unused'), 3002, 'd' * 64)
        self.assertEqual(request.call_args.kwargs['method'], 'GET')
        self.assertEqual(request.call_args.kwargs['path'], '/api/extensions/github/drafts/' + 'd' * 64)
        self.assertEqual(result['contentTrust'], 'untrusted-recipe-proposal')
        self.assertNotIn('never-forward', json.dumps(result))
        for key, bad in [('draftId', 'e' * 64), ('registered', True), ('requiresRevalidation', False)]:
            with self.subTest(key=key), self.assertRaises(manager.ManagerError):
                manager._recover_draft_receipt({**value, key: bad}, 'd' * 64)

    def test_draft_recovery_rejects_paths_and_oversized_content(self):
        for draft_id in ['../private', 'a' * 65, 'https://github.com/a/b', None]:
            with self.assertRaises(manager.ManagerError):
                manager._draft_id(draft_id)
        with self.assertRaises(manager.ManagerError):
            manager._recover_draft_receipt({'schemaVersion': 1, 'draftId': 'd' * 64, 'state': 'draft',
                'candidate': {**self.proposal(), 'manifest': {'large': 'x' * 32768}},
                'requiresRevalidation': True, 'installationStarted': False, 'registered': False}, 'd' * 64)

    def test_draft_recovery_client_emits_exact_socket_request(self):
        value = {'schemaVersion': 1, 'kind': manager.RECIPE_RECOVERY_KIND,
                 'draftId': 'd' * 64, 'state': 'draft', 'candidate': self.proposal(),
                 'requiresRevalidation': True, 'installationStarted': False, 'registered': False}
        connection = mock.Mock()
        connection.recv.return_value = (json.dumps(value) + '\n').encode()
        with mock.patch.object(manager.socket, 'socket', return_value=connection), \
             mock.patch.object(manager.sys, 'stdout') as output:
            result = manager.repository_client(manager.SOCKET_PATH, draft_id='d' * 64)
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(connection.sendall.call_args.args[0]),
                         {'schemaVersion': 1, 'action': 'github-draft-read', 'draftId': 'd' * 64})
        self.assertEqual(json.loads(output.write.call_args.args[0])['candidate'], self.proposal())
        connection.close.assert_called_once()


    def test_scoped_request_status_is_fixed_read_and_rejects_inconsistent_receipts(self):
        envelope = {'schemaVersion': 1, 'action': 'github-request-status', 'chatId': 'chat', 'requestId': 'turn'}
        receipt = {'schemaVersion': 1, 'kind': 'ods-extension-request-status', 'chatId': 'chat', 'requestId': 'turn',
                   'requestState': 'pending', 'authorizationMode': 'install',
                   'proposalAccepted': True, 'prepared': False,
                   'extensionId': 'example', 'runtimeStatus': 'not_observed'}
        with mock.patch.object(manager, '_read_env', return_value={'DASHBOARD_API_KEY': 'a' * 64}), \
             mock.patch.object(manager, '_request_json', return_value=(200, receipt)) as request:
            self.assertEqual(manager._read_request_status(pathlib.Path('/unused'), 3002, json.dumps(envelope).encode()), receipt)
            self.assertEqual(request.call_args.kwargs['path'], '/api/extensions/github/requests/status')
            self.assertEqual(request.call_args.kwargs['body'], {'chatId': 'chat', 'requestId': 'turn'})
            failed = {**receipt, 'prepared': True, 'runtimeStatus': 'error', 'runtimeError': 'missing pyproject.toml'}
            request.return_value = (200, failed)
            self.assertEqual(manager._read_request_status(pathlib.Path('/unused'), 3002, json.dumps(envelope).encode()), failed)
            failed['runtimeError'] = 'Source image build failed. ' + 'x' * 7000
            request.return_value = (200, failed)
            self.assertEqual(manager._read_request_status(pathlib.Path('/unused'), 3002, json.dumps(envelope).encode()), failed)
            failed['runtimeError'] = 'x' * 8192
            self.assertEqual(manager._read_request_status(pathlib.Path('/unused'), 3002, json.dumps(envelope).encode()), failed)
            for change in [{'runtimeError': ''}, {'runtimeError': None}, {'runtimeError': 42},
                           {'runtimeError': 'x' * 8193}, {'runtimeStatus': 'enabled'}, {'requestId': 'other'}]:
                request.return_value = (200, {**failed, **change})
                with self.assertRaises(manager.ManagerError):
                    manager._read_request_status(pathlib.Path('/unused'), 3002, json.dumps(envelope).encode())
            for matches in [[], ['existing-a']]:
                request.return_value = (200, {**receipt, 'existingExtensionIds': matches})
                self.assertEqual(manager._read_request_status(pathlib.Path('/unused'), 3002, json.dumps(envelope).encode())['existingExtensionIds'], matches)
            verified = {**receipt, 'prepared': True, 'runtimeStatus': 'cli_installed',
                        'installationVerified': True}
            request.return_value = (200, verified)
            self.assertTrue(manager._read_request_status(pathlib.Path('/unused'), 3002,
                json.dumps(envelope).encode())['installationVerified'])
            for change in ({'installationVerified': False}, {'installationVerified': 'true'},
                           {'requestState': 'cancelled'}, {'runtimeStatus': 'stopped'}):
                request.return_value = (200, {**verified, **change})
                with self.assertRaises(manager.ManagerError):
                    manager._read_request_status(pathlib.Path('/unused'), 3002, json.dumps(envelope).encode())
            for change in [{'existingExtensionIds': None}, {'existingExtensionIds': ['../escape']},
                           {'existingExtensionIds': ['same', 'same']}, {'existingExtensionIds': ['a'] * 65},
                            {'requestId': 'other'}, {'authorizationMode': 'unknown'},
                            {'runtimeStatus': 'enabled'}, {'extensionId': '../escape'}, {'secret': 'never-return'}]:
                request.return_value = (200, {**receipt, **change})
                with self.assertRaises(manager.ManagerError):
                    manager._read_request_status(pathlib.Path('/unused'), 3002, json.dumps(envelope).encode())

    def test_scoped_proposal_only_posts_to_bound_api_route(self):
        env_patch = mock.patch.object(manager, '_read_env', return_value={'DASHBOARD_API_KEY': 'a' * 64})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.env_path = pathlib.Path('/unused-test-env')
        candidate = {**self.proposal(), 'manifest': {'service': {'id': 'example'}}}
        envelope = {'schemaVersion': 1, 'action': 'github-request-propose',
                    'chatId': 'chat', 'requestId': 'turn', 'candidate': candidate}
        digest = hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        receipt = {'schemaVersion': 1, 'chatId': 'chat', 'requestId': 'turn',
                   'repository': candidate['repository'], 'state': 'pending', 'installationStarted': False,
                   'proposal': {'draftId': 'd' * 64, 'recipeDigest': digest,
                                'extensionId': candidate['manifest']['service']['id']}}
        with mock.patch.object(manager, '_request_json', return_value=(200, receipt)) as request:
            result = manager._submit_request_proposal(self.env_path, 3002, json.dumps(envelope).encode())
        self.assertEqual(result['kind'], 'ods-extension-request-proposal')
        self.assertFalse(result['installationStarted'])
        self.assertEqual(request.call_args.kwargs['path'], '/api/extensions/github/requests/proposal')
        self.assertEqual(set(request.call_args.kwargs['body']), {'chatId', 'requestId', 'candidate'})
        self.assertNotIn('credential', json.dumps(result))
        for changed in ({'state': 'cancelled'}, {'chatId': 'other'}, {'installationStarted': True}, {'proposal': {}}):
            with mock.patch.object(manager, '_request_json', return_value=(200, {**receipt, **changed})):
                with self.assertRaises(manager.ManagerError):
                    manager._submit_request_proposal(self.env_path, 3002, json.dumps(envelope).encode())
        with mock.patch.object(manager, '_request_json') as request:
            for changed in ({'chatId': '../other'}, {'action': 'install'}, {'url': 'https://elsewhere'}):
                with self.assertRaises(manager.ManagerError):
                    manager._submit_request_proposal(self.env_path, 3002, json.dumps({**envelope, **changed}).encode())
            request.assert_not_called()

    def test_scoped_recipe_diagnostics_are_value_free_and_bounded(self):
        candidate = {**self.proposal(), 'manifest': {'service': {'id': 'example'}}}
        envelope = {'schemaVersion': 1, 'action': 'github-request-propose',
                    'chatId': 'chat', 'requestId': 'turn', 'candidate': candidate}
        error = {'code': 'manifest-schema', 'path': 'manifest/required'}
        with mock.patch.object(manager, '_read_env', return_value={'DASHBOARD_API_KEY': 'a' * 64}):
            with mock.patch.object(manager, '_request_json', return_value=(422, {'detail': {
                    'code': 'recipe-validation-failed', 'errors': [error]}})):
                result = manager._submit_request_proposal(pathlib.Path('/unused-env'), 3002, json.dumps(envelope).encode())
            self.assertEqual(result['state'], 'invalid-recipe')
            self.assertEqual(result['errors'], [error])
            self.assertFalse(result['installationStarted'])
            with mock.patch.object(manager, '_request_json', return_value=(422, {'detail': {
                    'code': 'recipe-validation-failed', 'errors': [error],
                    'existingExtensionIds': ['registered-click']}})):
                result = manager._submit_request_proposal(pathlib.Path('/unused-env'), 3002, json.dumps(envelope).encode())
                self.assertEqual(result['existingExtensionIds'], ['registered-click'])
            for existing in ('registered-click', ['../private'], ['x'] * 65):
                with mock.patch.object(manager, '_request_json', return_value=(422, {'detail': {
                        'code': 'recipe-validation-failed', 'errors': [error], 'existingExtensionIds': existing}})):
                    with self.assertRaises(manager.ManagerError):
                        manager._submit_request_proposal(pathlib.Path('/unused-env'), 3002, json.dumps(envelope).encode())
            for errors in ([{**error, 'value': 'secret'}], [{**error, 'path': 'unexpected\\nvalue'}], [error] * 33):
                with mock.patch.object(manager, '_request_json', return_value=(422, {'detail': {
                        'code': 'recipe-validation-failed', 'errors': errors}})):
                    with self.assertRaises(manager.ManagerError):
                        manager._submit_request_proposal(pathlib.Path('/unused-env'), 3002, json.dumps(envelope).encode())

    def test_owner_peer_can_propose_but_cannot_dispatch_lifecycle(self):
        self.env_path = pathlib.Path('/unused-test-env')
        connection = mock.Mock()
        connection.recv.return_value = b'{"schemaVersion":1,"action":"github-request-propose"}\n'
        with mock.patch.object(manager, 'peer_ids', return_value=(os.getuid(), 0)), \
             mock.patch.object(manager, '_submit_request_proposal', return_value={'installationStarted': False}) as propose, \
             mock.patch.object(manager, '_execute') as execute:
            manager._serve_connection(connection, expected_uid=os.getuid() + 1, env_path=self.env_path, port=3002)
            propose.assert_called_once()
            execute.assert_not_called()
            connection.recv.return_value = b'{"schemaVersion":1,"action":"install","extensionId":"crewai"}\n'
            manager._serve_connection(connection, expected_uid=os.getuid() + 1, env_path=self.env_path, port=3002)
            execute.assert_not_called()

    def test_owner_peer_can_read_request_pin_without_dispatching_lifecycle(self):
        connection = mock.Mock()
        connection.recv.return_value = (b'{"schemaVersion":1,"action":"github-request-pin",'
                                        b'"chatId":"chat","requestId":"turn"}\n')
        receipt = {'schemaVersion': 1, 'kind': 'ods-extension-request-commit',
                   'chatId': 'chat', 'requestId': 'turn',
                   'repository': 'https://github.com/owner/repo', 'commit': 'a' * 40,
                   'evidenceScope': 'repository-default-branch-at-inspection',
                   'installationStarted': False}
        with mock.patch.object(manager, 'peer_ids', return_value=(os.getuid(), 0)), \
             mock.patch.object(manager, '_pin_request_repository', return_value=receipt) as pin, \
             mock.patch.object(manager, '_execute') as execute:
            manager._serve_connection(connection, expected_uid=os.getuid() + 1,
                                      env_path=pathlib.Path('/unused-test-env'), port=3002)
        pin.assert_called_once()
        execute.assert_not_called()
        self.assertEqual(json.loads(connection.sendall.call_args.args[0]), receipt)


    def test_integration_guidance_is_bounded_evidence_not_runtime_proof(self):
        value = {'schemaVersion': 1, 'extensionId': 'demo', 'scope': 'recipe-integration-guidance',
                 'contentTrust': 'untrusted-recipe-evidence', 'description': '',
                 'declaredConnection': {'container_name': 'ods-demo', 'port': 8080},
                 'documentation': 'Read the API docs.\nUse the real project framework.',
                 'documentationTruncated': False, 'connectivityVerified': False,
                 'projectIntegrationVerified': False}
        self.assertEqual(manager._integration_guidance(value, 'demo'), value)
        self.assertIsNone(manager._integration_guidance(None, 'demo'))
        for change in ({'extensionId': 'other'}, {'projectIntegrationVerified': True},
                       {'documentation': 'x' * 24001}, {'declaredConnection': {'password': 'private'}},
                       {'scope': 'trusted-instructions'}):
            with self.assertRaises(manager.ManagerError):
                manager._integration_guidance({**value, **change}, 'demo')


    def test_scoped_proposal_crosses_real_http_transport_without_lifecycle_access(self):
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import threading
        candidate = {**self.proposal(), 'manifest': {'service': {'id': 'example'}}}
        envelope = {'schemaVersion': 1, 'action': 'github-request-propose',
                    'chatId': 'chat', 'requestId': 'turn', 'candidate': candidate}
        digest = hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        receipt = {'schemaVersion': 1, 'chatId': 'chat', 'requestId': 'turn',
                   'repository': candidate['repository'], 'state': 'pending', 'installationStarted': False,
                   'proposal': {'draftId': 'd' * 64, 'recipeDigest': digest, 'extensionId': 'example'}}
        observed = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                observed.append((self.path, body))
                data = json.dumps(receipt).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        server = HTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True);worker.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                env = pathlib.Path(directory) / '.env'
                env.write_text('DASHBOARD_API_KEY=' + 'a' * 64 + '\n');env.chmod(0o600)
                result = manager._submit_request_proposal(env, server.server_port, json.dumps(envelope).encode())
            self.assertEqual(result['proposal']['recipeDigest'], digest)
            self.assertEqual(observed, [('/api/extensions/github/requests/proposal',
                {key: envelope[key] for key in ('chatId', 'requestId', 'candidate')})])
            receipt = {'schemaVersion': 1, 'kind': 'ods-extension-request-status',
                       'chatId': 'chat', 'requestId': 'turn', 'requestState': 'pending',
                       'authorizationMode': 'install',
                       'proposalAccepted': True, 'prepared': False, 'extensionId': 'example',
                       'runtimeStatus': 'not_observed'}
            status_envelope = {'schemaVersion': 1, 'action': 'github-request-status',
                               'chatId': 'chat', 'requestId': 'turn'}
            with tempfile.TemporaryDirectory() as directory:
                env = pathlib.Path(directory) / '.env'
                env.write_text('DASHBOARD_API_KEY=' + 'a' * 64 + '\n');env.chmod(0o600)
                result = manager._read_request_status(env, server.server_port, json.dumps(status_envelope).encode())
            self.assertEqual(result, receipt)
            self.assertEqual(observed[-1], ('/api/extensions/github/requests/status',
                                            {'chatId': 'chat', 'requestId': 'turn'}))
            receipt = {**receipt, 'prepared': True, 'runtimeStatus': 'error',
                       'runtimeError': 'Source image build failed. ' + 'x' * 7000}
            with tempfile.TemporaryDirectory() as directory:
                env = pathlib.Path(directory) / '.env'
                env.write_text('DASHBOARD_API_KEY=' + 'a' * 64 + '\n');env.chmod(0o600)
                result = manager._read_request_status(env, server.server_port, json.dumps(status_envelope).encode())
            self.assertEqual(result['runtimeError'], receipt['runtimeError'])
            self.assertEqual(observed[-1], ('/api/extensions/github/requests/status',
                                            {'chatId': 'chat', 'requestId': 'turn'}))
            receipt = {'schemaVersion': 1, 'kind': 'ods-extension-request-preparation',
                       'chatId': 'chat', 'requestId': 'turn', 'draftId': 'd' * 64,
                       'recipeDigest': digest, 'extensionId': 'example', 'state': 'available',
                       'installationStarted': False, 'registered': False, 'runtimeVerified': False}
            prepare_envelope = {**status_envelope, 'action': 'github-request-prepare'}
            with tempfile.TemporaryDirectory() as directory:
                env = pathlib.Path(directory) / '.env'
                env.write_text('DASHBOARD_API_KEY=' + 'a' * 64 + '\n');env.chmod(0o600)
                result = manager._prepare_request(env, server.server_port, json.dumps(prepare_envelope).encode())
            self.assertEqual(result, receipt)
            self.assertEqual(observed[-1], ('/api/extensions/github/requests/prepare',
                                           {'chatId': 'chat', 'requestId': 'turn'}))
            receipt = {'schemaVersion': 1, 'kind': 'ods-extension-request-binding',
                       'chatId': 'chat', 'requestId': 'turn', 'extensionId': 'example',
                       'definitionDigest': 'd' * 64, 'state': 'bound',
                       'installationStarted': False, 'runtimeVerified': False}
            with tempfile.TemporaryDirectory() as directory:
                env = pathlib.Path(directory) / '.env'
                env.write_text('DASHBOARD_API_KEY=' + 'a' * 64 + '\n');env.chmod(0o600)
                binding_envelope = {**prepare_envelope, 'extensionId': 'example'}
                result = manager._prepare_request(env, server.server_port, json.dumps(binding_envelope).encode())
                self.assertEqual(result, receipt)
                self.assertEqual(observed[-1], ('/api/extensions/github/requests/prepare',
                    {'chatId': 'chat', 'requestId': 'turn', 'extensionId': 'example'}))
                receipt['runtimeVerified'] = True
                with self.assertRaises(manager.ManagerError):
                    manager._prepare_request(env, server.server_port, json.dumps(binding_envelope).encode())
            receipt = {'schemaVersion': 1, 'kind': 'ods-extension-request-installation',
                       'chatId': 'chat', 'requestId': 'turn', 'extensionId': 'example',
                       'state': 'pending', 'activeExtensionId': 'example',
                       'operationId': 'a' * 32, 'dispatched': True}
            advance_envelope = {**status_envelope, 'action': 'github-request-advance'}
            with tempfile.TemporaryDirectory() as directory:
                env = pathlib.Path(directory) / '.env'
                env.write_text('DASHBOARD_API_KEY=' + 'a' * 64 + '\n');env.chmod(0o600)
                result = manager._advance_request(env, server.server_port, json.dumps(advance_envelope).encode())
                self.assertEqual(result, receipt)
                receipt['state'] = 'succeeded'  # Acceptance cannot masquerade as completion.
                with self.assertRaises(manager.ManagerError):
                    manager._advance_request(env, server.server_port, json.dumps(advance_envelope).encode())
            self.assertEqual(observed[-1], ('/api/extensions/github/requests/advance',
                                           {'chatId': 'chat', 'requestId': 'turn'}))
            receipt['state'] = 'pending'
            retry_envelope = {**status_envelope, 'action': 'github-request-retry'}
            with tempfile.TemporaryDirectory() as directory:
                env = pathlib.Path(directory) / '.env'
                env.write_text('DASHBOARD_API_KEY=' + 'a' * 64 + '\n');env.chmod(0o600)
                result = manager._advance_request(env, server.server_port, json.dumps(retry_envelope).encode())
                self.assertEqual(result, receipt)
            self.assertEqual(observed[-1], ('/api/extensions/github/requests/retry',
                                           {'chatId': 'chat', 'requestId': 'turn'}))
            for path, body in [('/api/extensions/github/requests', {'action': 'create'}),
                               ('/api/extensions/github/requests/status', {'chatId': '../chat', 'requestId': 'turn'}),
                               ('/api/extensions/github/requests/prepare', {'chatId': 'chat', 'requestId': 'turn', 'draftId': 'd' * 64}),
                               ('/api/extensions/github/requests/proposal', {'chatId': '../chat', 'requestId': 'turn', 'candidate': candidate}),
                               ('/api/extensions/example/install', {'candidate': candidate})]:
                with self.assertRaises(manager.ManagerError):
                    manager._request_json(port=server.server_port, credential='a' * 64, method='POST',
                                          path=path, timeout=2, body=body)
            self.assertEqual(len(observed), 9)
        finally:
            server.shutdown();server.server_close();worker.join(timeout=2)


    def test_preparation_rejection_preserves_only_verified_scope_and_reason(self):
        from unittest.mock import patch
        envelope = {'schemaVersion':1, 'action':'github-request-prepare', 'chatId':'chat', 'requestId':'turn'}
        rejection = {'schemaVersion':1, 'kind':'ods-extension-request-preparation-rejected',
                     'chatId':'chat', 'requestId':'turn', 'reason':'proposal_required', 'installationStarted':False}
        with tempfile.TemporaryDirectory() as directory:
            env = pathlib.Path(directory) / '.env'
            env.write_text('DASHBOARD_API_KEY=' + 'a' * 64 + '\n'); env.chmod(0o600)
            with patch.object(manager, '_request_json') as transport:
                for reason in ('proposal_required', 'integration_selection_required',
                               'license_review_required', 'repository_evidence_unavailable',
                               'recipe_inspection_required', 'request_changed'):
                    value = {**rejection, 'reason':reason}
                    transport.return_value = (409, {'detail':value})
                    self.assertEqual(manager._prepare_request(env, 3002, json.dumps(envelope).encode()), value)
                for change in ({'chatId':'other'}, {'requestId':'other'}, {'reason':'untrusted'},
                               {'installationStarted':True}, {'installationStarted':0}, {'extra':'secret'}):
                    transport.return_value = (409, {'detail':{**rejection, **change}})
                    with self.assertRaises(manager.ManagerError):
                        manager._prepare_request(env, 3002, json.dumps(envelope).encode())


class CredentialProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = pathlib.Path(self.temporary.name)
        (root / 'source').mkdir()
        (root / 'private').mkdir(mode=0o700)
        self.source = root / 'source/.env'
        self.destination = root / 'private/.env'
        self.source.write_text('DASHBOARD_API_KEY=' + 'b' * 64 + '\nOTHER_SECRET=not-copied\n')
        self.source.chmod(0o777)  # DrvFS mode synthesis is not native POSIX custody.
        self.destination.write_text('DASHBOARD_API_KEY=' + 'a' * 64 + '\n')
        self.destination.chmod(0o600)

    def test_explicit_projection_rotates_only_api_key_and_preserves_strict_reader(self):
        with self.assertRaises(manager.ManagerError):
            manager._read_env(self.source)
        manager._refresh_projected_credential(self.source, self.destination)
        self.assertEqual(manager._read_env(self.destination), {'DASHBOARD_API_KEY': 'b' * 64})
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)
        before = self.destination.stat().st_ino
        manager._refresh_projected_credential(self.source, self.destination)
        self.assertEqual(self.destination.stat().st_ino, before)
        self.assertEqual(list(self.destination.parent.iterdir()), [self.destination])

    def test_invalid_or_duplicate_source_never_replaces_good_projection(self):
        before = self.destination.read_bytes()
        for text in ['DASHBOARD_API_KEY=bad', 'OTHER_SECRET=value',
                     'DASHBOARD_API_KEY=' + 'b' * 64 + '\nDASHBOARD_API_KEY=' + 'c' * 64]:
            self.source.write_text(text)
            with self.assertRaises(manager.ManagerError):
                manager._refresh_projected_credential(self.source, self.destination)
            self.assertEqual(self.destination.read_bytes(), before)

    def test_symlinks_and_nonprivate_destination_are_rejected(self):
        real = self.source.with_name('actual'); self.source.rename(real); self.source.symlink_to(real)
        with self.assertRaises(manager.ManagerError):
            manager._refresh_projected_credential(self.source, self.destination)
        self.source.unlink();real.rename(self.source)
        self.destination.chmod(0o644)
        with self.assertRaises(manager.ManagerError):
            manager._refresh_projected_credential(self.source, self.destination)

    def test_projection_paths_cannot_be_supplied_through_client_action(self):
        with mock.patch.object(manager, 'serve', return_value=0) as serve:
            self.assertEqual(manager.main(['manager', 'serve', '/run/ods-pixel-manager/extension-manager.sock',
                str(self.destination), '3002', '--credential-source', str(self.source)]), 0)
            self.assertEqual(serve.call_args.kwargs['credential_source'], self.source)
        with self.assertRaises(manager.ManagerError):
            manager._parse_request(json.dumps({'schemaVersion': 1, 'action': 'inspect',
                'extensionId': 'demo', 'credential_source': str(self.source)}).encode())


if __name__ == "__main__":
    unittest.main()
