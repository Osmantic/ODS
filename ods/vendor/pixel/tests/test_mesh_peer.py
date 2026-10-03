"""Unit tests for deploy/mesh pixel_mesh_peer transport and evidence contract.

These tests never use real SSH, systemd, Discord, credentials, live profiles,
or live runtime state. Remote/status behavior is mocked.
"""

from collections.abc import Mapping
import copy
import hashlib
import importlib.util
import io
import os
import json
import select
import re
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "mesh_pixel_mesh_peer", Path(__file__).resolve().parents[1] / "deploy/mesh/pixel_mesh_peer.py"
)
peer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(peer)
REAL_MESH_EXEC_SHELL = peer.mesh_exec_shell


class PeerContractTests(unittest.TestCase):
    def setUp(self):
        self.exec_shell_patcher = patch.object(
            peer, "mesh_exec_shell", return_value=Path("/tmp/pixel-mesh-test-exec-shell"),
        )
        self.exec_shell_patcher.start()
        self.addCleanup(self.exec_shell_patcher.stop)

    class JsonResponse:
        def __init__(self, value):
            self.payload = json.dumps(value).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, maximum):
            return self.payload[:maximum]

    def test_router_status_exposes_raw_bindings_and_canonical_delegation(self):
        primary = {
            "status": "ok", "model": "dream-fleet-agent", "available": True,
            "endpoints": [
                {"name": "tower2", "healthy": True, "active": 0, "max_active": 1,
                 "model_id": "GLM-5.3-Flash-TR3-4bpw", "url": "must-not-leak"},
                {"name": "tower1", "healthy": True, "active": 0, "max_active": 1,
                 "model_id": "Qwen3.6-27B-UD-Q4_K_XL"},
            ],
        }
        work = {
            "status": "ok", "model": "dream-fleet-agent", "available": True,
            "endpoints": [{"name": "canonical", "healthy": True, "active": 0,
                           "max_active": 3, "model_id": "dream-fleet-agent"}],
        }
        responses = [self.JsonResponse(primary), self.JsonResponse(work)]
        with patch.object(peer.urllib.request, "urlopen", side_effect=responses):
            observed = peer.router_states()
        self.assertTrue(observed["canonicalDelegationObserved"])
        self.assertEqual(observed["primary"]["endpoints"][0]["modelId"],
                         "GLM-5.3-Flash-TR3-4bpw")
        self.assertNotIn("url", observed["primary"]["endpoints"][0])
        self.assertEqual(observed["workProvider"]["endpoints"][0]["name"], "canonical")

    def test_router_status_reports_unknown_delegation_when_a_router_is_unreachable(self):
        with patch.object(peer.urllib.request, "urlopen", side_effect=OSError("offline")):
            observed = peer.router_states()
        self.assertIsNone(observed["canonicalDelegationObserved"])
        self.assertFalse(observed["primary"]["reachable"])
        self.assertFalse(observed["workProvider"]["reachable"])

    def test_router_status_ports_are_fixed(self):
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            peer.router_state(12345)

    def test_router_status_fails_closed_on_oversized_endpoint_set(self):
        response = self.JsonResponse({
            "status": "ok", "model": "dream-fleet-agent", "available": True,
            "endpoints": [
                {"name": f"worker-{index}", "healthy": True, "active": 0,
                 "max_active": 1, "model_id": "local"}
                for index in range(17)
            ],
        })
        with patch.object(peer.urllib.request, "urlopen", return_value=response):
            observed = peer.router_state(18080)
        self.assertEqual(observed, {"reachable": False, "error": "ValueError"})

    def test_exact_case_insensitive_allowlist(self):
        self.assertEqual(peer.peer_name("Tower1"), "tower1")
        self.assertEqual(peer.peer_name("tower3"), "tower3")
        for invalid in ("tower4", "tower1.example", "tower1;id", "", "../tower2"):
            with self.assertRaises(ValueError):
                peer.peer_name(invalid)

    def test_message_bytes_stay_on_stdin_and_out_of_fixed_argv(self):
        hostile = b"$(touch /tmp/NO) ; `id` && echo pwned\n"
        completed = subprocess.CompletedProcess([], 0, b"", b"")
        with patch.object(peer.socket, "gethostname", return_value="Tower2"), \
             patch.object(peer.subprocess, "run", return_value=completed) as run:
            self.assertEqual(peer.send_message("Tower3", hostile), 0)
        args, kwargs = run.call_args
        argv = args[0]
        self.assertEqual(kwargs["input"], hostile)
        self.assertNotIn(hostile.decode(), " ".join(argv))
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("ConnectTimeout=8", argv)
        self.assertEqual(argv[-3:], ["tower3", "~/.local/bin/pixel-mesh-peer", "receive-message"])
        self.assertNotIn("shell", kwargs)

        with patch.object(peer.socket, "gethostname", return_value="Tower2"), \
             patch.object(peer.subprocess, "run", return_value=completed) as run:
            self.assertEqual(peer.send_message("Tower1", hostile, read_only=True), 0)
        self.assertEqual(
            run.call_args.args[0][-3:],
            ["tower1", "~/.local/bin/pixel-mesh-peer", "receive-message-readonly"],
        )

        with patch.object(peer.socket, "gethostname", return_value="Tower2"), \
             patch.object(peer.subprocess, "run", return_value=completed) as run:
            self.assertEqual(peer.send_message(
                "Tower3", hostile, read_only=True, contract=True,
            ), 0)
        self.assertEqual(
            run.call_args.args[0][-3:],
            ["tower3", "~/.local/bin/pixel-mesh-peer", "receive-message-readonly-contract"],
        )

    def test_self_addressed_message_uses_local_transport_without_ssh(self):
        payload = b"local owner task"
        with patch.object(peer.socket, "gethostname", return_value="Tower2.example"), \
             patch.object(peer, "local_agent", return_value=11) as local, \
             patch.object(peer.subprocess, "run") as run:
            self.assertEqual(peer.send_message(
                "Tower2", payload, read_only=True, contract=True,
            ), 11)
        local.assert_called_once_with(payload, read_only=True, contract=True)
        run.assert_not_called()

    def test_contract_request_and_result_use_bounded_strict_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
                "count": {"type": "integer", "minimum": 0, "maximum": 10},
                "notes": {"type": "array", "items": {"type": "string", "maxLength": 40},
                          "maxItems": 3},
            },
            "required": ["verdict", "count", "notes"],
            "additionalProperties": False,
        }
        request = json.dumps({
            "schemaVersion": 1, "task": "Audit the exact source.",
            "schemaName": "audit_result", "schema": schema,
        }).encode()
        task, name, observed_schema, digest = peer.parse_contract_request(request)
        self.assertEqual(task, b"Audit the exact source.")
        self.assertEqual(name, "audit_result")
        self.assertEqual(observed_schema, schema)
        self.assertRegex(digest, r"^[a-f0-9]{64}$")
        exploration = peer.contract_exploration_message(
            task, name, observed_schema, digest,
        )
        self.assertTrue(exploration.startswith(b"Audit the exact source.\n\n"))
        self.assertIn(b"PIXEL STRICT RESPONSE CONTRACT", exploration)
        self.assertIn(f"Schema SHA-256: {digest}".encode(), exploration)
        self.assertIn(
            json.dumps(schema, sort_keys=True, separators=(",", ":")).encode(), exploration,
        )
        task_with_significant_trailing_bytes = b"Audit the exact source. \n"
        self.assertTrue(peer.contract_exploration_message(
            task_with_significant_trailing_bytes, name, observed_schema, digest,
        ).startswith(task_with_significant_trailing_bytes + b"\n\nPIXEL STRICT RESPONSE CONTRACT"))
        peer.validate_contract_value(
            {"verdict": "PASS", "count": 6, "notes": []}, observed_schema,
        )
        safe_integer_schema = peer.validate_contract_schema({
            "type": "integer", "minimum": -peer.MAX_CONTRACT_SAFE_INTEGER,
            "maximum": peer.MAX_CONTRACT_SAFE_INTEGER,
        })
        peer.validate_contract_value(peer.MAX_CONTRACT_SAFE_INTEGER, safe_integer_schema)
        for invalid in (
            {"verdict": "PASS", "count": 6},
            {"verdict": "PASS", "count": 6, "notes": [], "extra": True},
            {"verdict": "MAYBE", "count": 6, "notes": []},
            {"verdict": "PASS", "count": True, "notes": []},
        ):
            with self.assertRaises(ValueError):
                peer.validate_contract_value(invalid, observed_schema)

        for invalid_request in (
            {"schemaVersion": True, "task": "Audit.", "schemaName": "result", "schema": schema},
            {"schemaVersion": 1, "task": "Audit.", "schemaName": 7, "schema": schema},
            {"schemaVersion": 1, "task": "\ud800", "schemaName": "result", "schema": schema},
        ):
            with self.assertRaises(ValueError):
                peer.parse_contract_request(json.dumps(invalid_request).encode())
        with self.assertRaisesRegex(ValueError, "byte limit"):
            peer.contract_exploration_message(
                b"x" * peer.MAX_MESSAGE_BYTES, name, observed_schema, digest,
            )

    def test_contract_schema_rejects_weak_or_unsupported_shapes(self):
        too_deep = {"type": "boolean"}
        for _ in range(peer.MAX_CONTRACT_SCHEMA_DEPTH + 1):
            too_deep = {"type": "array", "items": too_deep, "maxItems": 1}
        invalid = [
            {"type": "object", "properties": {"ok": {"type": "boolean"}},
             "required": [], "additionalProperties": False},
            {"type": "object", "properties": {"ok": {"type": "boolean"}},
             "required": ["ok"], "additionalProperties": True},
            {"type": "array", "items": {"type": "string"}},
            {"type": "array", "items": {"type": "string"}, "maxItems": 257},
            {"type": "string", "pattern": ".*"},
            {"type": ["string", "null"]},
            {"type": "number", "enum": [float("nan")]},
            {"oneOf": []},
            {"oneOf": [{"type": "boolean"}, {"type": "boolean"}]},
            {"oneOf": [{"type": "boolean"}, {"type": "null"}], "type": "boolean"},
            {"type": "string", "const": "PASS", "enum": ["PASS"]},
            too_deep,
        ]
        for schema in invalid:
            with self.assertRaises(ValueError):
                peer.validate_contract_schema(schema)

        for schema in (
            {"type": "number", "minimum": 10 ** 400},
            {"type": "number", "enum": [10 ** 400]},
            {"type": "number", "enum": [float("inf")]},
            {"type": "string", "enum": ["\ud800"]},
            {"type": "string", "description": "\ud800"},
            {"type": "string", "const": "\ud800"},
        ):
            with self.assertRaises(ValueError):
                peer.validate_contract_schema(schema)

        for value in (10 ** 400, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                peer.validate_contract_value(value, {"type": "number"})

    def test_contract_one_of_physically_enforces_cross_field_coherence(self):
        pass_branch = {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "const": "pass"},
                "findings": {
                    "type": "array", "items": {"type": "string", "maxLength": 200},
                    "maxItems": 0,
                },
            },
            "required": ["verdict", "findings"],
            "additionalProperties": False,
        }
        block_branch = {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "const": "block"},
                "findings": {
                    "type": "array", "items": {"type": "string", "minLength": 1,
                                                  "maxLength": 200},
                    "minItems": 1, "maxItems": 8,
                },
            },
            "required": ["verdict", "findings"],
            "additionalProperties": False,
        }
        schema = peer.validate_contract_schema({"oneOf": [pass_branch, block_branch]})
        peer.validate_contract_value({"verdict": "pass", "findings": []}, schema)
        peer.validate_contract_value({"verdict": "block", "findings": ["real defect"]}, schema)
        for contradiction in (
            {"verdict": "pass", "findings": ["placeholder blocker"]},
            {"verdict": "block", "findings": []},
        ):
            with self.assertRaises(ValueError):
                peer.validate_contract_value(contradiction, schema)

        overlapping = peer.validate_contract_schema({
            "oneOf": [
                {"type": "string", "minLength": 0},
                {"type": "string", "maxLength": 20},
            ],
        })
        with self.assertRaises(ValueError):
            peer.validate_contract_value("matches both", overlapping)

    def test_contract_cli_commands_do_not_downgrade_mode(self):
        class Input:
            def __init__(self, value):
                self.buffer = io.BytesIO(value)

        payload = b'{"schemaVersion":1}'
        with patch.object(peer.sys, "argv", ["pixel-mesh-peer", "ask-contract"]), \
             patch.object(peer.sys, "stdin", Input(payload)), \
             patch.object(peer, "local_agent", return_value=9) as local:
            self.assertEqual(peer.main(), 9)
        local.assert_called_once_with(payload, contract=True)

        with patch.object(peer.sys, "argv", ["pixel-mesh-peer", "message-readonly-contract", "Tower1"]), \
             patch.object(peer.sys, "stdin", Input(payload)), \
             patch.object(peer, "send_message", return_value=10) as remote:
            self.assertEqual(peer.main(), 10)
        remote.assert_called_once_with("Tower1", payload, read_only=True, contract=True)

    def test_remote_failure_is_bounded_unknown_not_global_absence(self):
        completed = subprocess.CompletedProcess([], 255, b"", b"connection refused")
        with patch.object(peer.socket, "gethostname", return_value="Tower2"), \
             patch.object(peer, "bounded_run", return_value=completed):
            result = peer.remote_status("Tower1")
        self.assertFalse(result["reachable"])
        self.assertEqual(result["peer"], "tower1")
        self.assertIn("observedAt", result)
        self.assertNotIn("evidence", result)
        self.assertNotIn("nonexistent", str(result).lower())

    def test_self_addressed_status_uses_local_evidence_without_ssh(self):
        evidence = {"schemaVersion": 1, "observedAt": "2026-08-28T00:00:00Z"}
        with patch.object(peer.socket, "gethostname", return_value="Tower2"), \
             patch.object(peer, "local_status", return_value=evidence) as local, \
             patch.object(peer, "bounded_run") as run:
            result = peer.remote_status("tower2")
        self.assertTrue(result["reachable"])
        self.assertEqual(result["peer"], "tower2")
        self.assertEqual(result["evidence"], evidence)
        local.assert_called_once_with()
        run.assert_not_called()

    def test_read_only_cli_commands_propagate_explicit_authority(self):
        class Input:
            def __init__(self, value):
                self.buffer = io.BytesIO(value)

        with patch.object(peer.sys, "argv", ["pixel-mesh-peer", "ask-readonly"]), \
             patch.object(peer.sys, "stdin", Input(b"local review")), \
             patch.object(peer, "local_agent", return_value=7) as local:
            self.assertEqual(peer.main(), 7)
        local.assert_called_once_with(b"local review", read_only=True)

        with patch.object(peer.sys, "argv", ["pixel-mesh-peer", "message-readonly", "Tower3"]), \
             patch.object(peer.sys, "stdin", Input(b"remote review")), \
             patch.object(peer, "send_message", return_value=8) as remote:
            self.assertEqual(peer.main(), 8)
        remote.assert_called_once_with("Tower3", b"remote review", read_only=True)

    def test_operations_broker_cli_propagates_fixed_authority(self):
        class Input:
            def __init__(self, value):
                self.buffer = io.BytesIO(value)

        with patch.dict(os.environ, {
            peer.SANDBOXED_MODEL_PIN_ENV: "pin/model",
            peer.SANDBOXED_MODEL_BASE_URL_ENV: "http://127.0.0.1:18101/v1",
        }, clear=False), patch.object(
            peer, "parse_sandboxed_model_pin",
            side_effect=AssertionError("fixed routes must not parse sandbox inference pins"),
        ) as parse_pin, patch.object(
            peer.sys, "argv", ["pixel-mesh-peer", "ask-operations-broker"],
        ), \
             patch.object(peer.sys, "stdin", Input(b"bounded operations inventory")), \
             patch.object(peer, "local_agent", return_value=9) as local:
            self.assertEqual(peer.main(), 9)
        parse_pin.assert_not_called()
        local.assert_called_once_with(
            b"bounded operations inventory",
            contract=False,
            fixed_authority_profile="operations-broker",
        )

        with patch.object(
            peer.sys, "argv", ["pixel-mesh-peer", "ask-operations-broker-contract"],
        ), patch.object(
            peer.sys, "stdin", Input(b'{"schemaVersion":1}')
        ), patch.object(peer, "local_agent", return_value=10) as local:
            self.assertEqual(peer.main(), 10)
        local.assert_called_once_with(
            b'{"schemaVersion":1}',
            contract=True,
            fixed_authority_profile="operations-broker",
        )

        for command, contract in (
            ("ask-download-staging", False),
            ("ask-download-staging-contract", True),
        ):
            with self.subTest(command=command), patch.object(
                peer.sys, "argv", ["pixel-mesh-peer", command],
            ), patch.object(
                peer.sys, "stdin", Input(b"exact public download")
            ), patch.object(peer, "local_agent", return_value=11) as local:
                self.assertEqual(peer.main(), 11)
            local.assert_called_once_with(
                b"exact public download",
                contract=contract,
                fixed_authority_profile="download-staging",
            )

    def test_valid_remote_status_retains_remote_timestamped_evidence(self):
        payload = b'{"schemaVersion":1,"observedAt":"2026-08-17T00:00:00Z","reachable":true}'
        completed = subprocess.CompletedProcess([], 0, payload, b"")
        with patch.object(peer.socket, "gethostname", return_value="Tower2"), \
             patch.object(peer, "bounded_run", return_value=completed):
            result = peer.remote_status("Tower1")
        self.assertTrue(result["reachable"])
        self.assertEqual(result["evidence"]["observedAt"], "2026-08-17T00:00:00Z")

    def test_local_agent_uses_fresh_session_by_default_and_allows_safe_override(self):
        completed = subprocess.CompletedProcess([], 7, b"", b"")
        with patch.object(peer, "run_agent_process", return_value=completed) as run:
            self.assertEqual(peer.local_agent(b"bounded task"), 7)
        first_argv = run.call_args.args[0]
        first_key = first_argv[first_argv.index("--session-key") + 1]
        first_id = first_argv[first_argv.index("--session-id") + 1]
        self.assertTrue(first_key.startswith("pixel-mesh-"))
        self.assertEqual(str(uuid.UUID(first_id)), first_id)
        self.assertEqual(run.call_args.kwargs["session_path"].name, f"{first_id}.jsonl")
        self.assertIn("--local", first_argv)

        with patch.dict(os.environ, {peer.SESSION_KEY_ENV: "review-session-1"}, clear=False):
            with patch.object(peer, "run_agent_process", return_value=completed) as run:
                self.assertEqual(peer.local_agent(b"bounded task"), 7)
        override_argv = run.call_args.args[0]
        self.assertEqual(override_argv[override_argv.index("--session-key") + 1], "review-session-1")

    def test_unsafe_session_override_is_rejected(self):
        with patch.dict(os.environ, {peer.SESSION_KEY_ENV: "bad session;id"}, clear=False):
            with self.assertRaises(ValueError):
                peer.fresh_session_key()

    def test_contract_authority_profile_is_closed_ascii_and_read_once(self):
        class CountingEnvironment(dict):
            reads = 0

            def get(self, key, default=None):
                if key == peer.CONTRACT_AUTHORITY_PROFILE_ENV:
                    self.reads += 1
                return super().get(key, default)

        environment = CountingEnvironment({
            peer.CONTRACT_AUTHORITY_PROFILE_ENV: "web-read-only",
        })
        with patch.object(peer.os, "environ", environment):
            self.assertEqual(peer.selected_contract_authority_profile(), (
                "web-read-only", ("pixel_web_browse",),
            ))
        self.assertEqual(environment.reads, 1)

        invalid = (
            "", "Web-read-only", "web_read_only", " web-read-only",
            "web-read-only ", "web-admin", "é", "w" * 65,
        )
        for value in invalid:
            with self.subTest(value=value):
                with patch.dict(os.environ, {
                    peer.CONTRACT_AUTHORITY_PROFILE_ENV: value,
                }, clear=False):
                    with self.assertRaises(ValueError):
                        peer.selected_contract_authority_profile()
        with patch.object(peer.os, "environ", {
            peer.CONTRACT_AUTHORITY_PROFILE_ENV: 7,
        }):
            with self.assertRaisesRegex(ValueError, "must be a string"):
                peer.selected_contract_authority_profile()

    def test_contract_authority_profile_rejects_ambiguous_routes_before_launch(self):
        environment = {peer.CONTRACT_AUTHORITY_PROFILE_ENV: "web-read-only"}
        invalid_routes = (
            {},
            {"read_only": True},
            {"read_only": True, "contract": True},
            {"sandboxed": True},
        )
        with patch.dict(os.environ, environment, clear=False):
            for options in invalid_routes:
                with self.subTest(options=options):
                    with patch.object(peer, "run_agent_process") as run:
                        with self.assertRaisesRegex(ValueError, "ordinary strict contract"):
                            peer.local_agent(b"bounded task", **options)
                    run.assert_not_called()

    def test_fixed_operations_profile_rejects_ambiguous_or_environment_routes(self):
        invalid_routes = (
            {"read_only": True},
            {"sandboxed": True},
            {"sandboxed": True, "sandboxed_mailbox_readonly": True},
        )
        for profile in ("operations-broker", "download-staging"):
            for options in invalid_routes:
                with self.subTest(profile=profile, options=options):
                    with patch.object(peer, "run_agent_process") as run:
                        with self.assertRaisesRegex(ValueError, "ordinary local operations"):
                            peer.local_agent(
                                b"bounded task",
                                fixed_authority_profile=profile,
                                **options,
                            )
                    run.assert_not_called()

        with patch.object(peer, "run_agent_process") as run:
            with self.assertRaisesRegex(ValueError, "not supported"):
                peer.local_agent(b"bounded task", fixed_authority_profile="web-read-only")
        run.assert_not_called()

        with patch.dict(os.environ, {
            peer.CONTRACT_AUTHORITY_PROFILE_ENV: "web-read-only",
        }, clear=False):
            with patch.object(peer, "run_agent_process") as run:
                with self.assertRaisesRegex(ValueError, "conflicts with the owner environment"):
                    peer.local_agent(
                        b"bounded task", fixed_authority_profile="operations-broker",
                    )
            run.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "owner-private authority config is Linux-only")
    def test_read_only_config_exposes_only_read_and_is_ephemeral_owner_private(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            state.mkdir(mode=0o700)
            source = state / "openclaw.json"
            original = {
                "agents": {"list": [{"id": "pixel", "tools": {"allow": ["exec", "write"]}}]},
                "tools": {"profile": "coding"},
            }
            source.write_text(json.dumps(original) + "\n", encoding="utf-8")
            source.chmod(0o600)
            path, digest = peer.create_read_only_config(state)
            try:
                rendered = path.read_bytes()
                config = json.loads(rendered)
                self.assertEqual(config["agents"]["list"][0]["tools"], {"allow": ["read"]})
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(hashlib.sha256(rendered).hexdigest(), digest)
                self.assertEqual(json.loads(source.read_text(encoding="utf-8")), original)
            finally:
                path.unlink(missing_ok=True)

    @unittest.skipUnless(os.name == "posix", "owner-private authority config is Linux-only")
    def test_web_contract_profile_config_is_exact_private_and_preserves_unrelated_fields(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            state.mkdir(mode=0o700)
            source = state / "openclaw.json"
            original = {
                "agents": {"list": [{"id": "pixel", "tools": {"allow": ["exec"]}}]},
                "tools": {"profile": "coding", "alsoAllow": ["unrelated-tool"], "deny": []},
                "unrelated": {"preserved": True},
            }
            source.write_text(json.dumps(original) + "\n", encoding="utf-8")
            source.chmod(0o600)
            path, digest, identity = peer.create_contract_authority_config(
                state, "web-read-only", ("pixel_web_browse",),
            )
            rendered = path.read_bytes()
            config = json.loads(rendered)
            self.assertEqual(
                config["agents"]["list"][0]["tools"],
                {"allow": ["pixel_web_browse"]},
            )
            self.assertEqual(config["tools"]["alsoAllow"], ["pixel_web_browse"])
            self.assertEqual(config["tools"]["profile"], "coding")
            self.assertEqual(config["unrelated"], {"preserved": True})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_nlink, 1)
            self.assertIn(".pixel-mesh-contract-web-read-only-", path.name)
            self.assertEqual(hashlib.sha256(rendered).hexdigest(), digest)
            self.assertEqual(json.loads(source.read_text(encoding="utf-8")), original)
            peer._remove_and_verify_ephemeral_config(path, identity)
            self.assertFalse(path.exists())

            path, digest, identity = peer.create_contract_authority_config(
                state, "download-staging", peer.DOWNLOAD_STAGING_ALLOWED_TOOLS,
            )
            rendered = path.read_bytes()
            config = json.loads(rendered)
            expected = list(peer.DOWNLOAD_STAGING_ALLOWED_TOOLS)
            self.assertEqual(config["agents"]["list"][0]["tools"], {"allow": expected})
            self.assertEqual(config["tools"]["alsoAllow"], expected)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(hashlib.sha256(rendered).hexdigest(), digest)
            self.assertEqual(json.loads(source.read_text(encoding="utf-8")), original)
            peer._remove_and_verify_ephemeral_config(path, identity)
            self.assertFalse(path.exists())

            path, digest, identity = peer.create_contract_authority_config(
                state, "operations-broker", peer.OPERATIONS_BROKER_ALLOWED_TOOLS,
            )
            rendered = path.read_bytes()
            config = json.loads(rendered)
            expected = list(peer.OPERATIONS_BROKER_ALLOWED_TOOLS)
            self.assertEqual(config["agents"]["list"][0]["tools"], {"allow": expected})
            self.assertEqual(config["tools"]["alsoAllow"], expected)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(hashlib.sha256(rendered).hexdigest(), digest)
            self.assertEqual(json.loads(source.read_text(encoding="utf-8")), original)
            peer._remove_and_verify_ephemeral_config(path, identity)
            self.assertFalse(path.exists())

            with self.assertRaisesRegex(ValueError, "profile or allowlist drifted"):
                peer.create_contract_authority_config(
                    state, "web-read-only", ("pixel_web_browse", "read"),
                )
            with self.assertRaisesRegex(ValueError, "profile or allowlist drifted"):
                peer.create_contract_authority_config(state, "unknown", ("read",))

    @unittest.skipUnless(os.name == "posix", "owner-private authority transcript is Linux-only")
    def test_read_only_authority_reconciles_transcript_and_rejects_other_tools(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            transcript = sessions / "00000000-0000-0000-0000-000000000009.jsonl"

            def write_tool(name):
                transcript.write_text(json.dumps({"message": {"role": "assistant", "content": [
                    {"type": "toolCall", "name": name, "arguments": {"path": "/fixture"}},
                ]}}) + "\n", encoding="utf-8")
                transcript.chmod(0o600)

            write_tool("read")
            evidence = peer.read_only_authority_evidence(transcript, state, "a" * 64)
            self.assertEqual(evidence["profile"], "read-only")
            self.assertEqual(evidence["allowedTools"], ["read"])
            self.assertEqual(evidence["observedTools"], ["read"])
            self.assertEqual(evidence["observedToolsInOrder"], ["read"])
            self.assertFalse(evidence["externalEffectAuthority"])

            response = {"status": "ok", "result": {"payloads": [{"text": "done"}], "meta": {}}}
            attached = json.loads(peer.attach_authority_evidence(
                json.dumps(response).encode("utf-8"), evidence,
            ))
            self.assertEqual(attached["result"]["meta"]["meshAuthority"], evidence)

            write_tool("exec")
            with self.assertRaisesRegex(ValueError, "outside its authority"):
                peer.read_only_authority_evidence(transcript, state, "a" * 64)

            write_tool("pixel_web_browse")
            web = peer.read_only_authority_evidence(
                transcript, state, "b" * 64,
                profile="web-read-only", allowed_tools=("pixel_web_browse",),
            )
            self.assertEqual(web["profile"], "web-read-only")
            self.assertEqual(web["allowedTools"], ["pixel_web_browse"])
            self.assertEqual(web["observedTools"], ["pixel_web_browse"])
            self.assertIn("public read-only Web Courier", web["boundary"])
            self.assertFalse(web["externalEffectAuthority"])

            transcript.write_text("".join(
                json.dumps({"message": {"role": "assistant", "content": [
                    {"type": "toolCall", "name": name, "arguments": {}},
                ]}}) + "\n"
                for name in (
                    "pixel_ops_inventory", "pixel_ops_run", "pixel_ops_job_wait",
                    "pixel_ops_run", "pixel_ops_job_wait",
                )
            ), encoding="utf-8")
            transcript.chmod(0o600)
            operations = peer.read_only_authority_evidence(
                transcript, state, "c" * 64,
                profile="operations-broker",
                allowed_tools=peer.OPERATIONS_BROKER_ALLOWED_TOOLS,
                extra_evidence={
                    "sourceConfigSha256": "d" * 64,
                    "sourceConfigIdentityStable": True,
                },
            )
            self.assertEqual(operations["profile"], "operations-broker")
            self.assertEqual(
                operations["observedToolsInOrder"],
                [
                    "pixel_ops_inventory", "pixel_ops_run", "pixel_ops_job_wait",
                    "pixel_ops_run", "pixel_ops_job_wait",
                ],
            )
            self.assertNotIn("externalEffectAuthority", operations)
            self.assertTrue(operations["brokerMediatedAuthority"])
            self.assertTrue(operations["perRequestPolicyAuthority"])
            self.assertFalse(operations["acceptanceAuthority"])
            self.assertIn("Operations broker remains authoritative", operations["boundary"])

            transcript.write_text("".join(
                json.dumps({"message": {"role": "assistant", "content": [
                    {"type": "toolCall", "name": name, "arguments": {}},
                ]}}) + "\n"
                for name in peer.DOWNLOAD_STAGING_ALLOWED_TOOLS
            ), encoding="utf-8")
            transcript.chmod(0o600)
            download = peer.read_only_authority_evidence(
                transcript, state, "e" * 64,
                profile="download-staging",
                allowed_tools=peer.DOWNLOAD_STAGING_ALLOWED_TOOLS,
                extra_evidence={
                    "sourceConfigSha256": "f" * 64,
                    "sourceConfigIdentityStable": True,
                },
            )
            self.assertEqual(
                download["observedToolsInOrder"], list(peer.DOWNLOAD_STAGING_ALLOWED_TOOLS),
            )
            self.assertNotIn("externalEffectAuthority", download)
            self.assertTrue(download["brokerMediatedAuthority"])
            self.assertTrue(download["perRequestPolicyAuthority"])
            self.assertFalse(download["acceptanceAuthority"])
            self.assertIn("expected SHA-256", download["boundary"])

            write_tool("pixel_ops_run")
            with self.assertRaisesRegex(ValueError, "outside its authority"):
                peer.read_only_authority_evidence(
                    transcript, state, "e" * 64,
                    profile="download-staging",
                    allowed_tools=peer.DOWNLOAD_STAGING_ALLOWED_TOOLS,
                    extra_evidence={
                        "sourceConfigSha256": "f" * 64,
                        "sourceConfigIdentityStable": True,
                    },
                )

            write_tool("exec")
            with self.assertRaisesRegex(ValueError, "outside its authority"):
                peer.read_only_authority_evidence(
                    transcript, state, "c" * 64,
                    profile="operations-broker",
                    allowed_tools=peer.OPERATIONS_BROKER_ALLOWED_TOOLS,
                    extra_evidence={
                        "sourceConfigSha256": "d" * 64,
                        "sourceConfigIdentityStable": True,
                    },
                )

            write_tool("read")
            with self.assertRaisesRegex(ValueError, "outside its authority"):
                peer.read_only_authority_evidence(
                    transcript, state, "b" * 64,
                    profile="web-read-only", allowed_tools=("pixel_web_browse",),
                )
            with self.assertRaisesRegex(ValueError, "allowlist drifted"):
                peer.read_only_authority_evidence(
                    transcript, state, "b" * 64,
                    profile="web-read-only", allowed_tools=("pixel_web_browse", "read"),
                )
            with self.assertRaisesRegex(ValueError, "unknown profile"):
                peer.read_only_authority_evidence(
                    transcript, state, "b" * 64,
                    profile="web-admin", allowed_tools=("pixel_web_browse",),
                )

    @unittest.skipUnless(os.name == "posix", "authority profile matrix is Linux-only")
    def test_merged_authority_evidence_key_matrix(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            transcript = sessions / "00000000-0000-0000-0000-000000000220.jsonl"
            sandboxed_profiles = {
                "sandboxed-workspace",
                "sandboxed-readonly-workspace",
                "sandboxed-mailbox-readonly-workspace",
            }
            broker_profiles = set(peer.BROKER_MEDIATED_AUTHORITY_PROFILES)
            profiles = (
                ("read-only", peer.READ_ONLY_ALLOWED_TOOLS),
                ("web-read-only", peer.CONTRACT_AUTHORITY_PROFILES["web-read-only"]),
                ("operations-broker", peer.OPERATIONS_BROKER_ALLOWED_TOOLS),
                ("download-staging", peer.DOWNLOAD_STAGING_ALLOWED_TOOLS),
                ("sandboxed-workspace", peer.SANDBOXED_ALLOWED_TOOLS),
                ("sandboxed-readonly-workspace", peer.SANDBOXED_READONLY_ALLOWED_TOOLS),
                (
                    "sandboxed-mailbox-readonly-workspace",
                    peer.SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS,
                ),
            )
            for profile, allowed_tools in profiles:
                names = [allowed_tools[0], allowed_tools[-1], allowed_tools[0]]
                transcript.write_text("".join(
                    json.dumps({"message": {"role": "assistant", "content": [{
                        "type": "toolCall", "name": name, "arguments": {},
                    }]}}) + "\n" for name in names
                ), encoding="utf-8")
                transcript.chmod(0o600)
                extra_evidence = None
                revalidator = None
                if profile in broker_profiles:
                    extra_evidence = {
                        "sourceConfigSha256": "b" * 64,
                        "sourceConfigIdentityStable": True,
                    }
                elif profile in sandboxed_profiles:
                    extra_evidence = {
                        "workspaceIdentitySha256": "c" * 64,
                        "freshSession": True,
                        "networkEnabled": False,
                        "hostFilesystemAuthority": False,
                        "hostGitMetadataReadAuthority": False,
                        "openClawExternalBindSourceOverride": False,
                        "sandboxRuntimeRemovedBeforeSuccess": True,
                        "sandboxWorkspaceControlPathRestoredBeforeSuccess": True,
                        "acceptanceAuthority": False,
                        "inferencePinned": False,
                    }
                    revalidator = lambda: None
                with self.subTest(profile=profile):
                    evidence = peer.read_only_authority_evidence(
                        transcript, state, "a" * 64,
                        profile=profile, allowed_tools=allowed_tools,
                        extra_evidence=extra_evidence,
                        workspace_identity_revalidator=revalidator,
                    )
                    self.assertEqual(evidence["observedToolsInOrder"], names)
                    self.assertEqual(
                        "externalEffectAuthority" in evidence,
                        profile not in broker_profiles,
                    )
                    self.assertEqual(
                        "brokerMediatedAuthority" in evidence,
                        profile in broker_profiles,
                    )
                    self.assertEqual(
                        "workspaceReadAuthority" in evidence,
                        profile in sandboxed_profiles,
                    )
                    self.assertEqual(
                        "mailboxReadAuthority" in evidence,
                        profile == "sandboxed-mailbox-readonly-workspace",
                    )

    @unittest.skipUnless(os.name == "posix", "read-only live turn custody is Linux-only")
    def test_local_agent_read_only_uses_and_removes_ephemeral_config(self):
        fixed = uuid.UUID("00000000-0000-0000-0000-000000000010")
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            config = state / "openclaw.json"
            config.write_text(json.dumps({
                "agents": {"list": [{"id": "pixel"}]}, "tools": {"profile": "coding"},
            }) + "\n", encoding="utf-8")
            config.chmod(0o600)
            observed_config = {}

            def complete_read_only(_argv, env, _timeout, **kwargs):
                authority_path = Path(env["OPENCLAW_CONFIG_PATH"])
                observed_config["path"] = authority_path
                observed_config["value"] = json.loads(authority_path.read_text(encoding="utf-8"))
                transcript = kwargs["session_path"]
                transcript.write_text(json.dumps({"message": {
                    "role": "assistant", "content": [
                        {"type": "toolCall", "name": "read", "arguments": {"path": "/fixture"}},
                    ],
                }}) + "\n", encoding="utf-8")
                transcript.chmod(0o600)
                response = {"payloads": [{"text": "read-only complete"}], "meta": {
                    "agentMeta": {"sessionFile": str(transcript)},
                    "livenessState": "working", "stopReason": "stop", "error": None,
                }}
                return subprocess.CompletedProcess(
                    [], 0, json.dumps(response).encode("utf-8"), b"",
                )

            class Output:
                def __init__(self):
                    self.buffer = io.BytesIO()

            output, errors = Output(), Output()
            environment = {peer.HOME_ENV: home, peer.OPENCLAW_BIN_ENV: "/tmp/openclaw-test"}
            with patch.dict(os.environ, environment, clear=False):
                with patch.object(peer.uuid, "uuid4", return_value=fixed):
                    with patch.object(peer, "run_agent_process", side_effect=complete_read_only):
                        with patch.object(peer.sys, "stdout", output), patch.object(peer.sys, "stderr", errors):
                            self.assertEqual(peer.local_agent(b"bounded read-only task", read_only=True), 0)
            self.assertEqual(
                observed_config["value"]["agents"]["list"][0]["tools"], {"allow": ["read"]},
            )
            self.assertFalse(observed_config["path"].exists())
            parsed = json.loads(output.buffer.getvalue())
            authority = parsed["result"]["meta"]["meshAuthority"]
            self.assertEqual(authority["profile"], "read-only")
            self.assertEqual(authority["observedTools"], ["read"])
            self.assertFalse(authority["externalEffectAuthority"])

    def test_local_agent_honors_isolated_home_and_binary(self):
        completed = subprocess.CompletedProcess([], 7, b"", b"")
        env = {peer.HOME_ENV: "/tmp/pixel-mesh-test-home",
               peer.OPENCLAW_BIN_ENV: "/tmp/pixel-mesh-test-openclaw"}
        with patch.dict(os.environ, env, clear=False):
            with patch.object(peer, "run_agent_process", return_value=completed) as run:
                self.assertEqual(peer.local_agent(b"bounded task"), 7)
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], str(Path("/tmp/pixel-mesh-test-openclaw")))
        self.assertEqual(run.call_args.args[1]["OPENCLAW_STATE_DIR"],
                         str(Path("/tmp/pixel-mesh-test-home/.openclaw-mesh")))
        self.assertEqual(run.call_args.args[1]["SHELL"], "/tmp/pixel-mesh-test-exec-shell")

    @unittest.skipUnless(os.name == "posix", "exec shell custody is POSIX-only")
    def test_mesh_exec_shell_requires_content_addressed_owner_private_bytes(self):
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            release_root = home_path / ".local" / "share" / "pixel-mesh"
            release = release_root / "releases" / "mesh-0.1.0-test"
            shell = release / "exec-shell" / "bash"
            shell.parent.mkdir(parents=True)
            shell.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            shell.chmod(0o700)
            (release_root / "current").symlink_to(Path("releases") / release.name)
            self.assertEqual(REAL_MESH_EXEC_SHELL(home_path), release_root / "current/exec-shell/bash")

            shell.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "owner-private executable"):
                REAL_MESH_EXEC_SHELL(home_path)

            shell.chmod(0o700)
            outside = home_path / "outside-bash"
            outside.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            outside.chmod(0o700)
            shell.unlink()
            shell.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "content-addressed release|regular file"):
                REAL_MESH_EXEC_SHELL(home_path)

    def test_embedded_local_response_is_normalized_to_gateway_contract(self):
        local = b'{"payloads":[{"text":"ok"}],"meta":{"agentMeta":{"sessionFile":"/tmp/x"}}}'
        normalized = json.loads(peer.normalize_agent_response(local))
        self.assertEqual(normalized["status"], "ok")
        self.assertEqual(normalized["result"]["payloads"][0]["text"], "ok")

        gateway = b'{"status":"ok","result":{"payloads":[{"text":"already"}]}}'
        self.assertIs(peer.normalize_agent_response(gateway), gateway)

    def test_terminal_validation_preserves_proven_success_and_reconciled_yield(self):
        direct = {"status": "ok", "result": {
            "payloads": [{"text": "durable result"}],
            "meta": {"replayInvalid": True, "livenessState": "working",
                     "error": None, "stopReason": "stop"},
        }}
        encoded = json.dumps(direct).encode("utf-8")
        self.assertIs(peer.validate_completed_agent_response(encoded), encoded)

        reconciled = {"status": "ok", "result": {
            "payloads": [{"text": "durable parent final", "livenessState": "completed",
                          "stopReason": "stop"}],
            "meta": {"meshYieldReconciled": True, "error": None,
                     "livenessState": "paused", "stopReason": "toolUse"},
        }}
        encoded = json.dumps(reconciled).encode("utf-8")
        self.assertIs(peer.validate_completed_agent_response(encoded), encoded)

    def test_terminal_validation_rejects_exit_zero_failure_shapes(self):
        base = {"status": "ok", "summary": "completed", "result": {
            "payloads": [{"text": "untrusted secret-bearing failure text"}],
            "meta": {"replayInvalid": True, "livenessState": "blocked",
                     "error": {"kind": "context_overflow", "message": "secret"},
                     "stopReason": None},
        }}
        variants = [
            base,
            {"status": "ok", "result": {"payloads": [{"text": "x"}],
                "meta": {"livenessState": "working", "error": {}, "stopReason": "stop"}}},
            {"status": "ok", "result": {"payloads": [{"text": "x"}],
                "meta": {"livenessState": "failed", "error": None, "stopReason": "stop"}}},
            {"status": "ok", "result": {"payloads": [{"text": "x"}],
                "meta": {"livenessState": "working", "error": None, "stopReason": None}}},
            {"status": "ok", "result": {"payloads": ["not-an-object"],
                "meta": {"livenessState": "working", "error": None, "stopReason": "stop"}}},
        ]
        for response in variants:
            with self.subTest(response=response):
                with self.assertRaisesRegex(
                    ValueError, "OpenClaw agent did not report a completed terminal result",
                ):
                    peer.validate_completed_agent_response(json.dumps(response).encode("utf-8"))

    def test_yielded_cli_response_is_replaced_by_final_parent_turn(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            transcript = sessions / "00000000-0000-0000-0000-000000000001.jsonl"
            records = [
                {"message": {"role": "assistant", "content": [
                    {"type": "toolCall", "name": "sessions_yield", "arguments": {}}
                ]}},
                {"message": {"role": "assistant", "api": "cli", "content": [
                    {"type": "text", "text": "partial result"}
                ]}},
                {"message": {"role": "assistant", "api": "openai-completions", "content": [
                    {"type": "text", "text": "complete verified result"}
                ]}},
            ]
            transcript.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
            response = {
                "status": "ok",
                "result": {
                    "payloads": [{"text": "partial result", "livenessState": "paused"}],
                    "meta": {"agentMeta": {"sessionFile": str(transcript)}},
                },
            }
            reconciled = peer.reconcile_yielded_response(
                json.dumps(response).encode("utf-8"), state, time.monotonic() - 1
            )
            parsed = json.loads(reconciled)
            self.assertEqual(parsed["result"]["payloads"][0]["text"], "complete verified result")
            self.assertEqual(parsed["result"]["payloads"][0]["livenessState"], "completed")
            self.assertTrue(parsed["result"]["meta"]["meshYieldReconciled"])

    def test_partial_yield_is_accepted_only_after_parent_final_is_durable(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            transcript = sessions / "00000000-0000-0000-0000-000000000003.jsonl"
            records = [
                {"message": {"role": "assistant", "content": [
                    {"type": "toolCall", "name": "sessions_yield", "arguments": {}}
                ]}},
                {"message": {"role": "assistant", "api": "openai-completions", "content": [
                    {"type": "text", "text": "durable parent final"}
                ]}},
            ]
            transcript.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
            local = {"payloads": [], "meta": {"agentMeta": {"sessionFile": str(transcript)}}}
            completed = peer.completed_yield_from_partial(json.dumps(local).encode("utf-8"), state)
            parsed = json.loads(completed)
            self.assertEqual(parsed["result"]["payloads"][0]["text"], "durable parent final")
            self.assertTrue(parsed["result"]["meta"]["meshYieldReconciled"])
            self.assertIsNone(peer.completed_yield_from_partial(b'{"payloads":', state))

    @unittest.skipUnless(os.name == "posix", "owner-private session reconciliation is Linux-only")
    def test_known_post_final_compaction_exit_recovers_only_durable_terminal_text(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            transcript = sessions / "00000000-0000-0000-0000-000000000004.jsonl"
            records = [
                {"message": {"role": "assistant", "api": "openai-completions",
                    "provider": "tower", "model": "dream-fleet-agent", "stopReason": "stop",
                    "content": [{"type": "text", "text": "durable review"}]}},
                {"type": "compaction", "summary": "post-final metadata"},
            ]
            transcript.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
            transcript.chmod(0o600)
            stderr = (
                b"[agent] compaction completed\n"
                b"Error: CLI transcript compaction failed for tower/dream-fleet-agent: Already compacted\n"
            )
            recovered = peer.recover_post_final_compaction_failure(transcript, state, 1, stderr)
            parsed = json.loads(recovered)
            self.assertEqual(parsed["result"]["payloads"][0]["text"], "durable review")
            evidence = parsed["result"]["meta"]["meshAgentExitReconciled"]
            self.assertEqual(evidence["reason"], "post-final-cli-transcript-already-compacted")
            self.assertEqual(evidence["exitCode"], 1)
            self.assertEqual(evidence["provider"], "tower")
            self.assertEqual(evidence["model"], "dream-fleet-agent")
            self.assertFalse(evidence["completionAuthority"])
            self.assertFalse(evidence["externalEffectAuthority"])
            self.assertTrue(evidence["requiresIndependentVerification"])
            self.assertRegex(evidence["stderrSha256"], r"^[0-9a-f]{64}$")
            self.assertIn("no external-effect success", evidence["boundary"])

            self.assertIsNone(peer.recover_post_final_compaction_failure(
                transcript, state, 1, b"Error: unrelated failure\n",
            ))
            self.assertIsNone(peer.recover_post_final_compaction_failure(
                transcript, state, 1, b"Error: earlier failure\n" + stderr,
            ))
            records[0]["message"]["model"] = "substituted-model"
            transcript.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
            self.assertIsNone(peer.recover_post_final_compaction_failure(transcript, state, 1, stderr))
            records[0]["message"]["model"] = "dream-fleet-agent"
            records[0]["message"]["api"] = "cli"
            transcript.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
            self.assertIsNone(peer.recover_post_final_compaction_failure(transcript, state, 1, stderr))
            records[0]["message"]["api"] = "openai-completions"
            records[0]["message"]["stopReason"] = "error"
            transcript.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
            self.assertIsNone(peer.recover_post_final_compaction_failure(transcript, state, 1, stderr))

    @unittest.skipUnless(os.name == "posix", "owner-private session reconciliation is Linux-only")
    def test_interrupted_completion_requires_independent_verification(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            transcript = sessions / "00000000-0000-0000-0000-000000000025.jsonl"
            records = [
                {"message": {
                    "role": "assistant", "api": "openai-completions",
                    "provider": "tower", "model": "dream-fleet-agent",
                    "stopReason": "length", "content": [{"type": "thinking", "thinking": "draft"}],
                }},
                {"message": {
                    "role": "assistant", "api": "openai-completions",
                    "provider": "tower", "model": "dream-fleet-agent",
                    "stopReason": "stop", "content": [{"type": "text", "text": "terminal"}],
                }},
            ]
            transcript.write_text(
                "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8",
            )
            transcript.chmod(0o600)
            response = json.dumps({
                "status": "ok", "summary": "completed", "result": {
                    "payloads": [{"text": "terminal"}],
                    "meta": {"livenessState": "working", "stopReason": "stop", "error": None,
                             "agentMeta": {"sessionFile": str(transcript)}},
                },
            }).encode()

            attached = peer.attach_interrupted_completion_evidence(response, transcript, state)
            parsed = json.loads(attached)
            self.assertEqual(parsed["summary"], "completed-requires-independent-verification")
            evidence = parsed["result"]["meta"]["meshInterruptedCompletion"]
            self.assertEqual(evidence["reason"], "prior-output-length-exhaustion")
            self.assertEqual(evidence["sessionFile"], str(transcript))
            self.assertRegex(evidence["sessionSha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(evidence["provider"], "tower")
            self.assertEqual(evidence["model"], "dream-fleet-agent")
            self.assertEqual(evidence["interruptedModelCalls"], 1)
            self.assertFalse(evidence["completionAuthority"])
            self.assertFalse(evidence["externalEffectAuthority"])
            self.assertTrue(evidence["requiresIndependentVerification"])
            self.assertIn("no external-effect success", evidence["boundary"])

            records.pop(0)
            transcript.write_text(
                "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8",
            )
            self.assertEqual(
                peer.attach_interrupted_completion_evidence(response, transcript, state), response,
            )

    @unittest.skipUnless(os.name == "posix", "owner-private session reconciliation is Linux-only")
    def test_local_agent_marks_length_continuation_fail_closed(self):
        fixed = uuid.UUID("00000000-0000-0000-0000-000000000026")
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            (state / "agents" / "pixel" / "sessions").mkdir(parents=True)
            state.chmod(0o700)

            def completed_after_length(_argv, _env, _timeout, **kwargs):
                transcript = kwargs["session_path"]
                records = [
                    {"message": {"role": "assistant", "api": "openai-completions",
                                 "provider": "tower", "model": "model", "stopReason": "length",
                                 "content": [{"type": "thinking", "thinking": "draft"}]}},
                    {"message": {"role": "assistant", "api": "openai-completions",
                                 "provider": "tower", "model": "model", "stopReason": "stop",
                                 "content": [{"type": "text", "text": "continued result"}]}},
                ]
                transcript.write_text(
                    "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8",
                )
                transcript.chmod(0o600)
                response = {"payloads": [{"text": "continued result"}], "meta": {
                    "agentMeta": {"sessionFile": str(transcript)},
                    "livenessState": "working", "stopReason": "stop", "error": None,
                }}
                return subprocess.CompletedProcess([], 0, json.dumps(response).encode(), b"")

            class Output:
                def __init__(self):
                    self.buffer = io.BytesIO()

            output, errors = Output(), Output()
            environment = {peer.HOME_ENV: home, peer.OPENCLAW_BIN_ENV: "/tmp/openclaw-test"}
            with patch.dict(os.environ, environment, clear=False):
                with patch.object(peer.uuid, "uuid4", return_value=fixed):
                    with patch.object(peer, "run_agent_process", side_effect=completed_after_length):
                        with patch.object(peer.sys, "stdout", output), patch.object(peer.sys, "stderr", errors):
                            self.assertEqual(peer.local_agent(b"bounded task"), 0)
            parsed = json.loads(output.buffer.getvalue())
            evidence = parsed["result"]["meta"]["meshInterruptedCompletion"]
            self.assertTrue(evidence["requiresIndependentVerification"])
            self.assertFalse(evidence["completionAuthority"])
            self.assertEqual(errors.buffer.getvalue(), b"")

    @unittest.skipUnless(os.name == "posix", "owner-private session reconciliation is Linux-only")
    def test_local_agent_returns_reconciled_final_after_known_openclaw_exit(self):
        fixed = uuid.UUID("00000000-0000-0000-0000-000000000005")
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            (state / "agents" / "pixel" / "sessions").mkdir(parents=True)
            state.chmod(0o700)

            def failed_after_final(_argv, _env, _timeout, **kwargs):
                transcript = kwargs["session_path"]
                records = [
                    {"message": {
                        "role": "assistant", "api": "openai-completions", "stopReason": "stop",
                        "provider": "tower", "model": "model",
                        "content": [{"type": "text", "text": "reconciled live result"}],
                    }},
                    {"type": "compaction", "summary": "post-final metadata"},
                ]
                transcript.write_text(
                    "".join(json.dumps(item) + "\n" for item in records), encoding="utf-8",
                )
                transcript.chmod(0o600)
                stderr = b"Error: CLI transcript compaction failed for tower/model: Already compacted\n"
                return subprocess.CompletedProcess([], 1, b"", stderr)

            class Output:
                def __init__(self):
                    self.buffer = io.BytesIO()

            output, errors = Output(), Output()
            environment = {peer.HOME_ENV: home, peer.OPENCLAW_BIN_ENV: "/tmp/openclaw-test"}
            with patch.dict(os.environ, environment, clear=False):
                with patch.object(peer.uuid, "uuid4", return_value=fixed):
                    with patch.object(peer, "run_agent_process", side_effect=failed_after_final):
                        with patch.object(peer.sys, "stdout", output), patch.object(peer.sys, "stderr", errors):
                            self.assertEqual(peer.local_agent(b"bounded task"), 0)
            parsed = json.loads(output.buffer.getvalue())
            self.assertEqual(parsed["result"]["payloads"][0]["text"], "reconciled live result")
            self.assertIn(b"CLI transcript compaction failed", errors.buffer.getvalue())

    @unittest.skipUnless(os.name == "posix", "no-follow and link-count custody is Linux-only")
    def test_post_final_recovery_rejects_linked_session_and_state_paths(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            transcript = sessions / "00000000-0000-0000-0000-000000000006.jsonl"
            record = {"message": {
                "role": "assistant", "api": "openai-completions", "stopReason": "stop",
                "provider": "tower", "model": "model", "content": [{"type": "text", "text": "x"}],
            }}
            transcript.write_text(
                json.dumps(record) + "\n" + json.dumps({"type": "compaction"}) + "\n",
                encoding="utf-8",
            )
            stderr = b"Error: CLI transcript compaction failed for tower/model: Already compacted\n"

            hardlink = sessions / "hardlink.jsonl"
            os.link(transcript, hardlink)
            with self.assertRaisesRegex(ValueError, "bounded regular file"):
                peer.recover_post_final_compaction_failure(transcript, state, 1, stderr)
            hardlink.unlink()

            target = sessions / "target.jsonl"
            transcript.rename(target)
            transcript.symlink_to(target)
            with self.assertRaises(OSError):
                peer.recover_post_final_compaction_failure(transcript, state, 1, stderr)

            state_alias = Path(home) / "state-alias"
            state_alias.symlink_to(state, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "state root"):
                peer.recover_post_final_compaction_failure(target, state_alias, 1, stderr)

    def test_agent_response_rejects_session_outside_mesh_state(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as outside:
            state = Path(home) / ".openclaw-mesh"
            (state / "agents" / "pixel" / "sessions").mkdir(parents=True)
            transcript = Path(outside) / "escaped.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")
            response = {"status": "ok", "result": {"meta": {"agentMeta": {"sessionFile": str(transcript)}}}}
            with self.assertRaisesRegex(ValueError, "unsafe agent session"):
                peer.reconcile_yielded_response(
                    json.dumps(response).encode("utf-8"), state, time.monotonic() + 1
                )

    @unittest.skipUnless(os.name == "posix", "symlink contract is exercised on the Linux deployment host")
    def test_agent_response_rejects_symlinked_session(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            target = sessions / "target.jsonl"
            target.write_text("{}\n", encoding="utf-8")
            transcript = sessions / "linked.jsonl"
            transcript.symlink_to(target)
            response = {"status": "ok", "result": {"meta": {"agentMeta": {"sessionFile": str(transcript)}}}}
            with self.assertRaisesRegex(ValueError, "unsafe agent session"):
                peer.reconcile_yielded_response(
                    json.dumps(response).encode("utf-8"), state, time.monotonic() + 1
                )

    def test_yield_without_final_parent_turn_fails_closed(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            transcript = sessions / "00000000-0000-0000-0000-000000000002.jsonl"
            transcript.write_text(json.dumps({"message": {"role": "assistant", "content": [
                {"type": "toolCall", "name": "sessions_yield", "arguments": {}}
            ]}}) + "\n", encoding="utf-8")
            response = {"status": "ok", "result": {"meta": {"agentMeta": {"sessionFile": str(transcript)}}}}
            with self.assertRaisesRegex(TimeoutError, "did not produce a final parent response"):
                peer.reconcile_yielded_response(
                    json.dumps(response).encode("utf-8"), state, time.monotonic() - 1
                )

    def test_local_agent_returns_failure_when_yield_reconciliation_fails(self):
        completed = subprocess.CompletedProcess([], 0, b'{"status":"ok"}', b"")
        with patch.object(peer, "run_agent_process", return_value=completed):
            with patch.object(peer, "reconcile_yielded_response", side_effect=TimeoutError("still yielded")):
                self.assertEqual(peer.local_agent(b"bounded task"), 2)

    def test_local_agent_rejects_exit_zero_blocked_result_without_echoing_content(self):
        fixed = uuid.UUID("00000000-0000-0000-0000-000000000007")
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)

            def blocked_after_zero(_argv, _env, _timeout, **kwargs):
                transcript = kwargs["session_path"]
                transcript.write_text("{}\n", encoding="utf-8")
                response = {"payloads": [{"text": "SECRET PROMPT context overflow"}], "meta": {
                    "agentMeta": {"sessionFile": str(transcript)},
                    "replayInvalid": True,
                    "livenessState": "blocked",
                    "error": {"kind": "context_overflow", "message": "SECRET PROMPT"},
                    "stopReason": None,
                }}
                return subprocess.CompletedProcess(
                    [], 0, json.dumps(response).encode("utf-8"), b"SECRET STDERR\n",
                )

            stdout_bytes, stderr_bytes = io.BytesIO(), io.BytesIO()
            output = io.TextIOWrapper(stdout_bytes, encoding="utf-8", write_through=True)
            errors = io.TextIOWrapper(stderr_bytes, encoding="utf-8", write_through=True)
            environment = {peer.HOME_ENV: home, peer.OPENCLAW_BIN_ENV: "/tmp/openclaw-test"}
            with patch.dict(os.environ, environment, clear=False):
                with patch.object(peer.uuid, "uuid4", return_value=fixed):
                    with patch.object(peer, "run_agent_process", side_effect=blocked_after_zero):
                        with patch.object(peer.sys, "stdout", output), patch.object(peer.sys, "stderr", errors):
                            self.assertEqual(peer.local_agent(b"bounded task"), 2)
            self.assertEqual(stdout_bytes.getvalue(), b"")
            diagnostic = stderr_bytes.getvalue()
            self.assertIn(b"OpenClaw agent did not report a completed terminal result", diagnostic)
            self.assertNotIn(b"SECRET PROMPT", diagnostic)
            self.assertNotIn(b"SECRET STDERR", diagnostic)

    @unittest.skipUnless(os.name == "posix", "owner-only JSONL contract")
    def test_silent_length_budget_is_exact_replay_safe_and_resettable(self):
        def assistant(response_id, stop_reason, content, error=None, api="openai-completions"):
            return {"type": "message", "message": {
                "role": "assistant", "api": api, "provider": "local", "model": "test",
                "responseId": response_id, "stopReason": stop_reason,
                "errorMessage": error, "content": content,
            }}

        silent = [{"type": "thinking", "thinking": "bounded hidden work"}]
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "session.jsonl"
            records = [
                assistant("response-1", "length", silent),
                assistant("response-1", "length", silent),
                {"type": "message", "message": {
                    "role": "user", "content": [{"type": "text", "text": "continue"}],
                }},
                {"type": "message", "message": {
                    "role": "assistant", "api": "cli", "stopReason": "stop",
                    "content": [{"type": "text", "text": "cli handoff"}],
                }},
                {"type": "compaction", "tokensBefore": 10,
                 "summary": "retained", "details": {"reason": "threshold"}},
                assistant("response-2", "length", []),
            ]
            transcript.write_text(
                "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8",
            )
            transcript.chmod(0o600)
            budget = peer.AgentSessionBudget(transcript)
            self.assertEqual(
                budget.observe(), "reasoning-without-visible-output limit reached (2)",
            )
            self.assertEqual(budget.silent_length_streak, 2)
            self.assertEqual(budget.model_calls, 2)

        resets = [
            assistant("response-2", "stop", []),
            assistant("response-2", "length", [{"type": "text", "text": "visible"}]),
            assistant("response-2", "length", [{
                "type": "toolCall", "id": "tool-1", "name": "read",
            }]),
        ]
        for reset in resets:
            with self.subTest(reset=reset["message"]["content"]):
                with tempfile.TemporaryDirectory() as directory:
                    transcript = Path(directory) / "session.jsonl"
                    transcript.write_text(
                        json.dumps(assistant("response-1", "length", silent)) + "\n" +
                        json.dumps(reset) + "\n", encoding="utf-8",
                    )
                    transcript.chmod(0o600)
                    budget = peer.AgentSessionBudget(transcript)
                    self.assertIsNone(budget.observe())
                    self.assertEqual(budget.silent_length_streak, 0)

        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "session.jsonl"
            transcript.write_text(
                json.dumps(assistant("response-1", "length", silent)) + "\n" +
                json.dumps(assistant("response-2", "length", silent, error="provider error")) +
                "\n", encoding="utf-8",
            )
            transcript.chmod(0o600)
            budget = peer.AgentSessionBudget(transcript)
            self.assertIsNone(budget.observe())
            self.assertEqual(budget.silent_length_streak, 1)

        diagnostic = (
            "Pixel mesh agent budget exceeded: "
            "reasoning-without-visible-output limit reached (2)\n"
        ).encode()
        self.assertEqual(
            peer.recoverable_budget_synthesis_reason(
                subprocess.CompletedProcess([], 124, b"", diagnostic)
            ),
            peer.REASONING_WITHOUT_VISIBLE_OUTPUT_RECOVERY,
        )
        self.assertIsNone(peer.recoverable_budget_synthesis_reason(
            subprocess.CompletedProcess([], 1, b"", diagnostic)
        ))
        self.assertIsNone(peer.recoverable_budget_synthesis_reason(
            subprocess.CompletedProcess([], 124, b"", diagnostic + b"extra")
        ))

    def test_silent_length_terminal_classifier_rejects_visible_or_malformed_content(self):
        def terminal(content, stop="length", error=None, api="openai-completions"):
            return [{"type": "message", "message": {
                "role": "assistant", "api": api, "stopReason": stop,
                "errorMessage": error, "content": content,
            }}]

        valid = terminal([
            {"type": "text", "text": "  "},
            {"type": "thinking", "thinking": "retained finding"},
        ])
        self.assertEqual(
            peer._reasoning_without_visible_output_retained(valid), "retained finding",
        )
        self.assertEqual(peer._reasoning_without_visible_output_retained(
            valid + terminal([{"type": "text", "text": "cli"}], api="cli")
        ), "retained finding")

        invalid = [
            terminal([{"type": "thinking", "thinking": "x"}], stop="stop"),
            terminal([{"type": "thinking", "thinking": "x"}], error="provider error"),
            terminal([{"type": "toolCall", "id": "tool-1", "name": "read"}]),
            terminal([{"type": "text", "text": "visible"},
                      {"type": "thinking", "thinking": "x"}]),
            terminal([{"type": "text"}, {"type": "thinking", "thinking": "x"}]),
            terminal([{"type": "text", "text": 7},
                      {"type": "thinking", "thinking": "x"}]),
            terminal([{"type": "thinking"}]),
            terminal([{"type": "thinking", "thinking": 7}]),
            terminal([{"type": "image", "data": "x"}]),
            terminal([]),
            terminal([{"type": "thinking", "thinking": "   "}]),
            [{"type": "message", "message": {
                "role": "assistant", "api": "openai-completions",
                "stopReason": "length", "errorMessage": None, "content": {},
            }}],
        ]
        for records in invalid:
            with self.subTest(records=records):
                self.assertIsNone(peer._reasoning_without_visible_output_retained(records))

    @unittest.skipUnless(os.name == "posix", "owner-private synthesis custody is Linux-only")
    def test_deterministic_silent_recovery_is_blocked_zero_usage_and_predecessor_bound(self):
        synthesis_id = uuid.UUID("00000000-0000-0000-0000-000000000113")
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            session = state / "agents" / "pixel" / "sessions" / "exploration.jsonl"
            session.parent.mkdir(parents=True)
            state.chmod(0o700)
            session.write_text(json.dumps({
                "type": "message", "message": {
                    "role": "assistant", "api": "openai-completions",
                    "stopReason": "length", "errorMessage": None,
                    "content": [{"type": "thinking", "thinking": "untrusted hidden plan"}],
                },
            }) + "\n", encoding="utf-8")
            session.chmod(0o600)
            predecessor_sha256 = hashlib.sha256(session.read_bytes()).hexdigest()

            with patch.object(peer.urllib.request, "build_opener",
                              side_effect=AssertionError("recovery must not call a model")):
                with patch.object(peer.uuid, "uuid4", return_value=synthesis_id):
                    completed = peer.run_deterministic_reasoning_blocked_recovery(
                        session, state,
                    )
            self.assertIsNotNone(completed)
            parsed = json.loads(completed)
            self.assertEqual(parsed["summary"], "deterministic-blocked-reasoning-recovery")
            payload = parsed["result"]["payloads"][0]
            self.assertTrue(payload["blocked"])
            self.assertTrue(payload["text"].startswith("Blocked:"))
            self.assertNotIn("untrusted hidden plan", payload["text"])
            meta = parsed["result"]["meta"]
            self.assertTrue(meta["blocked"])
            self.assertEqual(meta["agentMeta"]["usage"], {
                "input": 0, "cacheRead": 0, "cacheWrite": 0,
                "output": 0, "totalTokens": 0,
            })
            evidence = meta["meshSynthesisRecovery"]
            self.assertEqual(evidence["mechanism"], "deterministic-no-model-blocked")
            self.assertEqual(evidence["explorationSessionSha256"], predecessor_sha256)
            self.assertTrue(evidence["blocked"])
            self.assertFalse(evidence["completionAuthority"])
            self.assertFalse(evidence["externalEffectAuthority"])
            self.assertFalse(evidence["acceptanceAuthority"])

            receipt = Path(meta["agentMeta"]["sessionFile"])
            self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
            durable = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(durable["meshSynthesis"]["transport"], "deterministic-no-model")
            self.assertEqual(
                durable["meshSynthesis"]["predecessorSessionSha256"], predecessor_sha256,
            )
            self.assertEqual(durable["message"]["usage"]["totalTokens"], 0)

    @unittest.skipUnless(os.name == "posix", "owner-private synthesis custody is Linux-only")
    def test_direct_synthesis_uses_no_tool_schema_and_writes_bound_receipt(self):
        synthesis_id = uuid.UUID("00000000-0000-0000-0000-000000000012")
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            session = state / "agents" / "pixel" / "sessions" / "exploration.jsonl"
            session.parent.mkdir(parents=True)
            state.chmod(0o700)
            session.write_text(json.dumps({
                "type": "message",
                "message": {"role": "assistant", "api": "openai-completions",
                            "stopReason": "length", "errorMessage": None,
                            "content": [{"type": "thinking", "thinking": "retained live evidence"}]},
            }) + "\n", encoding="utf-8")
            session.chmod(0o600)
            config = {
                "models": {"providers": {"tower": {
                    "baseUrl": "http://127.0.0.1:18080/v1", "apiKey": "local-no-auth",
                    "api": "openai-completions",
                    "models": [{"id": "dream-fleet-agent", "maxTokens": 16384}],
                }}},
                "agents": {"list": [{"id": "pixel", "model": "tower/dream-fleet-agent"}]},
            }
            (state / "openclaw.json").write_text(json.dumps(config) + "\n", encoding="utf-8")
            (state / "openclaw.json").chmod(0o600)
            provider = json.dumps({
                "id": "chatcmpl-direct", "model": "DeepSeek-V4-Flash",
                "choices": [{"message": {"role": "assistant", "content": "synthesized terminal result"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            }).encode()

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self, limit):
                    self.limit = limit
                    return provider

            class Opener:
                def open(self, request, timeout):
                    self.request = request
                    self.timeout = timeout
                    return Response()

            opener = Opener()
            with patch.object(peer.urllib.request, "build_opener", return_value=opener):
                with patch.object(peer.uuid, "uuid4", return_value=synthesis_id):
                    completed = peer.run_direct_synthesis_recovery(
                        b"bounded original objective", session, state,
                        time.monotonic() + 30, peer.OUTPUT_LENGTH_RECOVERY,
                    )
            self.assertIsNotNone(completed)
            request = json.loads(opener.request.data)
            self.assertNotIn("tools", request)
            self.assertEqual(request["model"], "dream-fleet-agent")
            self.assertIn("retained live evidence", request["messages"][1]["content"])
            self.assertEqual(opener.request.full_url, "http://127.0.0.1:18080/v1/chat/completions")
            parsed = json.loads(completed)
            self.assertEqual(parsed["result"]["payloads"][0]["text"], "synthesized terminal result")
            evidence = parsed["result"]["meta"]["meshSynthesisRecovery"]
            self.assertEqual(evidence["mechanism"], "direct-loopback-openai-compatible")
            self.assertEqual(evidence["toolAuthority"], "none")
            self.assertFalse(evidence["externalEffectAuthority"])
            receipt = Path(parsed["result"]["meta"]["agentMeta"]["sessionFile"])
            self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
            durable = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertFalse(durable["meshSynthesis"]["toolSchemaPresent"])
            self.assertEqual(durable["meshSynthesis"]["predecessorSessionFile"], str(session))
            predecessor_sha256 = hashlib.sha256(session.read_bytes()).hexdigest()
            self.assertEqual(
                durable["meshSynthesis"]["predecessorSessionSha256"], predecessor_sha256,
            )
            self.assertEqual(
                parsed["result"]["meta"]["agentMeta"]["predecessorSessionSha256"],
                predecessor_sha256,
            )
            self.assertEqual(evidence["explorationSessionSha256"], predecessor_sha256)
            self.assertEqual(durable["message"]["usage"], {
                "input": 10, "cacheRead": 0, "cacheWrite": 0,
                "output": 4, "totalTokens": 14,
            })

    @unittest.skipUnless(os.name == "posix", "owner-private synthesis custody is Linux-only")
    def test_contract_synthesis_enforces_schema_and_rejects_fences_or_truncation(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            session = state / "agents" / "pixel" / "sessions" / "exploration.jsonl"
            session.parent.mkdir(parents=True)
            state.chmod(0o700)
            session.write_text(json.dumps({
                "type": "message", "message": {
                    "role": "assistant", "api": "openai-completions",
                    "stopReason": "stop", "errorMessage": None,
                    "content": [{"type": "text", "text": "verified six changed files"}],
                },
            }) + "\n", encoding="utf-8")
            session.chmod(0o600)
            config = {
                "models": {"providers": {"tower": {
                    "baseUrl": "http://127.0.0.1:18080/v1", "apiKey": "local-no-auth",
                    "api": "openai-completions",
                    "models": [{"id": "dream-fleet-agent", "maxTokens": 16384}],
                }}},
                "agents": {"list": [{"id": "pixel", "model": "tower/dream-fleet-agent"}]},
            }
            (state / "openclaw.json").write_text(json.dumps(config) + "\n", encoding="utf-8")
            (state / "openclaw.json").chmod(0o600)
            schema = {
                "type": "object", "properties": {
                    "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
                    "count": {"type": "integer", "minimum": 0},
                },
                "required": ["verdict", "count"], "additionalProperties": False,
            }
            schema_sha256 = hashlib.sha256(json.dumps(
                schema, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()

            def provider(content, finish_reason="stop"):
                return json.dumps({
                    "id": "chatcmpl-contract", "model": "GLM-5.3-Flash",
                    "choices": [{"message": {"role": "assistant", "content": content},
                                 "finish_reason": finish_reason}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 8,
                              "total_tokens": 28},
                }).encode()

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self, _limit):
                    return opener.payload

            class Opener:
                payload = provider('{"count":6,"verdict":"PASS"}')

                def open(self, request, timeout):
                    self.request = request
                    self.timeout = timeout
                    return Response()

            opener = Opener()
            with patch.object(peer.urllib.request, "build_opener", return_value=opener):
                with patch.object(peer.uuid, "uuid4", return_value=uuid.UUID(
                    "00000000-0000-0000-0000-000000000031"
                )):
                    completed = peer.run_direct_synthesis_recovery(
                        b"audit exact source", session, state, time.monotonic() + 30,
                        peer.CONTRACT_TERMINAL_SYNTHESIS, response_schema=schema,
                        schema_name="audit_result", schema_sha256=schema_sha256,
                    )
            self.assertIsNotNone(completed)
            request = json.loads(opener.request.data)
            self.assertNotIn("tools", request)
            self.assertEqual(request["temperature"], 0)
            self.assertEqual(request["response_format"]["json_schema"], {
                "name": "audit_result", "strict": True, "schema": schema,
            })
            parsed = json.loads(completed)
            self.assertEqual(
                parsed["result"]["payloads"][0]["text"],
                '{"count":6,"verdict":"PASS"}',
            )
            evidence = parsed["result"]["meta"]["meshSynthesisRecovery"]
            self.assertTrue(evidence["contractValidated"])
            self.assertEqual(evidence["contractSchemaSha256"], schema_sha256)
            self.assertFalse(evidence["completionAuthority"])
            self.assertFalse(evidence["acceptanceAuthority"])
            unset_receipt = json.loads(Path(
                parsed["result"]["meta"]["agentMeta"]["sessionFile"]
            ).read_text(encoding="utf-8"))
            self.assertNotIn("authorityProfile", unset_receipt["meshSynthesis"])
            self.assertNotIn("authorityConfigSha256", unset_receipt["meshSynthesis"])
            self.assertNotIn("authorityProfile", evidence)
            self.assertNotIn("authorityConfigSha256", evidence)

            authority_sha256 = "b" * 64
            with patch.object(peer.urllib.request, "build_opener", return_value=opener):
                with patch.object(peer.uuid, "uuid4", return_value=uuid.UUID(
                    "00000000-0000-0000-0000-000000000090"
                )):
                    bound = peer.run_direct_synthesis_recovery(
                        b"audit exact source", session, state, time.monotonic() + 30,
                        peer.CONTRACT_TERMINAL_SYNTHESIS, response_schema=schema,
                        schema_name="audit_result", schema_sha256=schema_sha256,
                        authority_profile="web-read-only",
                        authority_config_sha256=authority_sha256,
                    )
            self.assertIsNotNone(bound)
            bound_value = json.loads(bound)
            bound_meta = bound_value["result"]["meta"]["meshSynthesisRecovery"]
            self.assertEqual(bound_meta["authorityProfile"], "web-read-only")
            self.assertEqual(bound_meta["authorityConfigSha256"], authority_sha256)
            bound_receipt_path = Path(
                bound_value["result"]["meta"]["agentMeta"]["sessionFile"]
            )
            bound_receipt = json.loads(bound_receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(
                bound_receipt["meshSynthesis"]["authorityProfile"], "web-read-only",
            )
            self.assertEqual(
                bound_receipt["meshSynthesis"]["authorityConfigSha256"], authority_sha256,
            )
            with self.assertRaisesRegex(ValueError, "authority binding"):
                peer.verify_synthesis_predecessor_binding(
                    bound_receipt_path, session, state,
                    hashlib.sha256(session.read_bytes()).hexdigest(),
                    authority_profile="web-read-only",
                    authority_config_sha256="c" * 64,
                )
            self.assertIsNone(peer.run_direct_synthesis_recovery(
                b"audit exact source", session, state, time.monotonic() + 30,
                peer.CONTRACT_TERMINAL_SYNTHESIS, response_schema=schema,
                schema_name="audit_result", schema_sha256=schema_sha256,
                authority_profile="web-read-only",
            ))
            self.assertIsNone(peer.run_direct_synthesis_recovery(
                b"audit exact source", session, state, time.monotonic() + 30,
                peer.CONTRACT_TERMINAL_SYNTHESIS, response_schema=schema,
                schema_name="audit_result", schema_sha256=schema_sha256,
                authority_profile="web-admin", authority_config_sha256=authority_sha256,
            ))

            self.assertIsNone(peer.run_direct_synthesis_recovery(
                b"audit exact source", session, state, time.monotonic() + 30,
                peer.CONTRACT_TERMINAL_SYNTHESIS, response_schema=schema,
                schema_name="audit_result", schema_sha256="0" * 64,
            ))

            provider_tool_call = json.loads(provider('{"count":6,"verdict":"PASS"}'))
            provider_tool_call["choices"][0]["message"]["tool_calls"] = [{
                "id": "call-1", "type": "function",
                "function": {"name": "exec", "arguments": "{}"},
            }]
            invalid = [
                provider('```json\n{"count":6,"verdict":"PASS"}\n```'),
                provider('{"count":6,"count":7,"verdict":"PASS"}'),
                provider('{"count":' + str(10 ** 400) + ',"verdict":"PASS"}'),
                provider('{"count":1e400,"verdict":"PASS"}'),
                provider('{"count":6,"verdict":"PASS","extra":true}'),
                provider('{"count":6,"verdict":"PASS"}', "length"),
                json.dumps(provider_tool_call).encode(),
            ]
            for index, payload in enumerate(invalid, 32):
                opener.payload = payload
                with patch.object(peer.urllib.request, "build_opener", return_value=opener):
                    with patch.object(peer.uuid, "uuid4", return_value=uuid.UUID(
                        f"00000000-0000-0000-0000-{index:012d}"
                    )):
                        self.assertIsNone(peer.run_direct_synthesis_recovery(
                            b"audit exact source", session, state, time.monotonic() + 30,
                            peer.CONTRACT_TERMINAL_SYNTHESIS, response_schema=schema,
                            schema_name="audit_result", schema_sha256=schema_sha256,
                        ))

            coherence_schema = {
                "oneOf": [
                    {
                        "type": "object", "properties": {
                            "verdict": {"type": "string", "const": "pass"},
                            "findings": {"type": "array", "items": {"type": "string"},
                                         "maxItems": 0},
                        },
                        "required": ["verdict", "findings"], "additionalProperties": False,
                    },
                    {
                        "type": "object", "properties": {
                            "verdict": {"type": "string", "const": "block"},
                            "findings": {"type": "array", "items": {"type": "string"},
                                         "minItems": 1, "maxItems": 8},
                        },
                        "required": ["verdict", "findings"], "additionalProperties": False,
                    },
                ],
            }
            coherence_sha256 = hashlib.sha256(json.dumps(
                coherence_schema, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
            opener.payload = provider('{"verdict":"pass","findings":["placeholder blocker"]}')
            with patch.object(peer.urllib.request, "build_opener", return_value=opener):
                with patch.object(peer.uuid, "uuid4", return_value=uuid.UUID(
                    "00000000-0000-0000-0000-000000000099"
                )):
                    self.assertIsNone(peer.run_direct_synthesis_recovery(
                        b"audit exact source", session, state, time.monotonic() + 30,
                        peer.CONTRACT_TERMINAL_SYNTHESIS, response_schema=coherence_schema,
                        schema_name="coherent_audit", schema_sha256=coherence_sha256,
                    ))

    @unittest.skipUnless(os.name == "posix", "owner-private synthesis custody is Linux-only")
    def test_deterministic_contract_finalization_rejects_trailing_evidence_and_bad_types(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            session = sessions / "00000000-0000-0000-0000-000000000040.jsonl"
            terminal = {
                "type": "message", "message": {
                    "role": "assistant", "api": "openai-completions",
                    "stopReason": "stop", "errorMessage": None,
                    "content": [{"type": "text", "text": '{"ok":true}'}],
                },
            }
            trailing_tool_result = {
                "type": "message", "message": {
                    "role": "toolResult", "api": "openai-completions",
                    "content": [{"type": "text", "text": "later evidence"}],
                },
            }
            session.write_text(
                json.dumps(terminal) + "\n" + json.dumps(trailing_tool_result) + "\n",
                encoding="utf-8",
            )
            session.chmod(0o600)
            schema = {
                "type": "object", "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"], "additionalProperties": False,
            }
            schema_sha256 = hashlib.sha256(json.dumps(
                schema, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
            options = {
                "response_schema": schema,
                "schema_name": "grounded_result",
                "schema_sha256": schema_sha256,
            }
            self.assertIsNone(peer.run_deterministic_contract_finalization(
                session, state, **options,
            ))
            session.write_text(json.dumps(terminal) + "\n", encoding="utf-8")
            session.chmod(0o600)
            for invalid_options in (
                {**options, "schema_name": 7},
                {**options, "schema_sha256": 7},
                {**options, "authority_profile": 7,
                 "authority_config_sha256": "a" * 64},
                {**options, "authority_profile": "web-read-only",
                 "authority_config_sha256": 7},
            ):
                self.assertIsNone(peer.run_deterministic_contract_finalization(
                    session, state, **invalid_options,
                ))

    @unittest.skipUnless(os.name == "posix", "owner-private synthesis custody is Linux-only")
    def test_local_agent_contract_propagates_schema_and_deterministically_finalizes_json(self):
        exploration_id = uuid.UUID("00000000-0000-0000-0000-000000000041")
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            (state / "agents" / "pixel" / "sessions").mkdir(parents=True)
            state.chmod(0o700)
            (state / "openclaw.json").write_text("{}\n", encoding="utf-8")
            (state / "openclaw.json").chmod(0o600)

            observed = {}

            def exploration(argv, _env, _timeout, **kwargs):
                message_path = Path(argv[argv.index("--message-file") + 1])
                observed["message"] = message_path.read_text(encoding="utf-8")
                transcript = kwargs["session_path"]
                transcript.write_text(json.dumps({
                    "type": "message", "message": {
                        "role": "assistant", "api": "openai-completions",
                        "stopReason": "stop", "errorMessage": None,
                        "content": [
                            {"type": "text", "text": '{"'},
                            {"type": "thinking", "thinking": "finish exact JSON"},
                            {"type": "text", "text": 'verdict":"PASS"}'},
                        ],
                    },
                }) + "\n", encoding="utf-8")
                transcript.chmod(0o600)
                response = {"payloads": [{"text": '{"verdict":"PASS"}'}], "meta": {
                    "agentMeta": {"sessionFile": str(transcript)},
                    "livenessState": "working", "stopReason": "stop", "error": None,
                }}
                return subprocess.CompletedProcess(
                    [], 0, json.dumps(response).encode("utf-8"), b"",
                )

            request = json.dumps({
                "schemaVersion": 1, "task": "Audit exact source",
                "schemaName": "audit_result", "schema": {
                    "type": "object",
                    "properties": {"verdict": {"type": "string", "enum": ["PASS", "FAIL"]}},
                    "required": ["verdict"], "additionalProperties": False,
                },
            }).encode("utf-8")

            class Output:
                def __init__(self):
                    self.buffer = io.BytesIO()

            output, errors = Output(), Output()
            environment = {peer.HOME_ENV: home, peer.OPENCLAW_BIN_ENV: "/tmp/openclaw-test"}
            with patch.dict(os.environ, environment, clear=False):
                with patch.object(peer.uuid, "uuid4", return_value=exploration_id):
                    with patch.object(peer, "run_agent_process", side_effect=exploration):
                        with patch.object(peer, "run_direct_synthesis_recovery") as direct:
                            with patch.object(peer.sys, "stdout", output), \
                                 patch.object(peer.sys, "stderr", errors):
                                self.assertEqual(peer.local_agent(request, contract=True), 0)
            direct.assert_not_called()
            parsed = json.loads(output.buffer.getvalue())
            self.assertEqual(parsed["result"]["payloads"][0]["text"], '{"verdict":"PASS"}')
            self.assertIn("PIXEL STRICT RESPONSE CONTRACT", observed["message"])
            self.assertIn('"verdict"', observed["message"])
            recovery = parsed["result"]["meta"]["meshSynthesisRecovery"]
            self.assertEqual(recovery["mechanism"], "deterministic-no-model-contract")
            self.assertTrue(recovery["terminalEvidenceOnly"])
            self.assertEqual(parsed["result"]["meta"]["agentMeta"]["usage"], {
                "input": 0, "cacheRead": 0, "cacheWrite": 0,
                "output": 0, "totalTokens": 0,
            })
            self.assertEqual(errors.buffer.getvalue(), b"")

    @unittest.skipUnless(os.name == "posix", "owner-private contract custody is Linux-only")
    def test_local_agent_contract_rejects_prose_without_a_finalization_model(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            (state / "agents" / "pixel" / "sessions").mkdir(parents=True)
            state.chmod(0o700)
            (state / "openclaw.json").write_text("{}\n", encoding="utf-8")
            (state / "openclaw.json").chmod(0o600)

            def exploration(_argv, _env, _timeout, **kwargs):
                transcript = kwargs["session_path"]
                transcript.write_text(json.dumps({
                    "type": "message", "message": {
                        "role": "assistant", "api": "openai-completions",
                        "stopReason": "stop", "errorMessage": None,
                        "content": [{"type": "text", "text":
                                     "PASS; the missing field is probably false"}],
                    },
                }) + "\n", encoding="utf-8")
                transcript.chmod(0o600)
                response = {"payloads": [{"text": "prose"}], "meta": {
                    "agentMeta": {"sessionFile": str(transcript)},
                    "livenessState": "working", "stopReason": "stop", "error": None,
                }}
                return subprocess.CompletedProcess(
                    [], 0, json.dumps(response).encode("utf-8"), b"",
                )

            request = json.dumps({
                "schemaVersion": 1, "task": "Return the observed result",
                "schemaName": "grounded_result", "schema": {
                    "type": "object", "properties": {
                        "ok": {"type": "boolean"},
                        "private": {"type": "boolean"},
                    },
                    "required": ["ok", "private"], "additionalProperties": False,
                },
            }).encode("utf-8")

            class Output:
                def __init__(self):
                    self.buffer = io.BytesIO()

                def write(self, value):
                    if isinstance(value, str):
                        value = value.encode("utf-8")
                    return self.buffer.write(value)

                def flush(self):
                    return None

            output, errors = Output(), Output()
            environment = {peer.HOME_ENV: home, peer.OPENCLAW_BIN_ENV: "/tmp/openclaw-test"}
            with patch.dict(os.environ, environment, clear=False):
                with patch.object(peer.uuid, "uuid4", return_value=uuid.UUID(
                    "00000000-0000-0000-0000-000000000042"
                )):
                    with patch.object(peer, "run_agent_process", side_effect=exploration):
                        with patch.object(peer, "run_direct_synthesis_recovery") as direct:
                            with patch.object(peer.sys, "stdout", output), \
                                 patch.object(peer.sys, "stderr", errors):
                                self.assertEqual(peer.local_agent(request, contract=True), 2)
            direct.assert_not_called()
            self.assertEqual(output.buffer.getvalue(), b"")
            self.assertIn(
                b"mesh contract terminal failed strict deterministic validation",
                errors.buffer.getvalue(),
            )

    @unittest.skipUnless(os.name == "posix", "owner-private contract profile is Linux-only")
    def test_local_agent_web_contract_profile_binds_config_evidence_and_synthesis(self):
        request = json.dumps({
            "schemaVersion": 1, "task": "Observe one official public page",
            "schemaName": "web_result", "schema": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"], "additionalProperties": False,
            },
        }).encode("utf-8")

        class Output:
            def __init__(self):
                self.buffer = io.BytesIO()

            def write(self, value):
                if isinstance(value, str):
                    value = value.encode("utf-8")
                return self.buffer.write(value)

            def flush(self):
                return None

        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            (state / "agents" / "pixel" / "sessions").mkdir(parents=True)
            state.chmod(0o700)
            source = state / "openclaw.json"
            source.write_text(json.dumps({
                "agents": {"list": [{"id": "pixel"}]},
                "tools": {"profile": "coding", "alsoAllow": ["existing"], "deny": []},
                "unrelated": {"preserved": True},
            }) + "\n", encoding="utf-8")
            source.chmod(0o600)

            def run_case(identifier, mismatch=False):
                observed = {}

                def exploration(_argv, env, _timeout, **kwargs):
                    config_path = Path(env["OPENCLAW_CONFIG_PATH"])
                    observed["configPath"] = config_path
                    observed["config"] = json.loads(config_path.read_text(encoding="utf-8"))
                    observed["configSha256"] = hashlib.sha256(config_path.read_bytes()).hexdigest()
                    transcript = kwargs["session_path"]
                    transcript.write_text(json.dumps({
                        "type": "message", "message": {
                            "role": "assistant", "content": [{
                                "type": "toolCall", "name": "pixel_web_browse",
                                "arguments": {"url": "https://example.invalid", "mode": "raw"},
                            }],
                        },
                    }) + "\n" + json.dumps({
                        "type": "message", "message": {
                            "role": "assistant", "api": "openai-completions",
                            "stopReason": "stop", "errorMessage": None,
                            "content": [{"type": "text", "text": '{"ok":true}'}],
                        },
                    }) + "\n", encoding="utf-8")
                    transcript.chmod(0o600)
                    response = {"payloads": [{"text": "exploration prose"}], "meta": {
                        "agentMeta": {"sessionFile": str(transcript)},
                        "livenessState": "working", "stopReason": "stop", "error": None,
                    }}
                    return subprocess.CompletedProcess(
                        [], 0, json.dumps(response).encode("utf-8"), b"",
                    )

                deterministic = peer.run_deterministic_contract_finalization

                def finalization(predecessor, synthesis_state, **options):
                    observed["finalizationOptions"] = options
                    completed = deterministic(predecessor, synthesis_state, **options)
                    if not mismatch or completed is None:
                        return completed
                    forged = json.loads(completed)
                    forged["result"]["meta"]["meshSynthesisRecovery"][
                        "authorityProfile"
                    ] = "wrong-profile"
                    return json.dumps(forged).encode("utf-8")

                output, errors = Output(), Output()
                environment = {
                    peer.HOME_ENV: home,
                    peer.OPENCLAW_BIN_ENV: "/tmp/openclaw-test",
                    peer.CONTRACT_AUTHORITY_PROFILE_ENV: "web-read-only",
                }
                with patch.dict(os.environ, environment, clear=False):
                    with patch.object(peer.uuid, "uuid4", return_value=uuid.UUID(identifier)):
                        with patch.object(peer, "run_agent_process", side_effect=exploration):
                            with patch.object(
                                peer, "run_deterministic_contract_finalization",
                                side_effect=finalization,
                            ):
                                with patch.object(peer, "run_direct_synthesis_recovery") as direct:
                                    with patch.object(peer.sys, "stdout", output), \
                                         patch.object(peer.sys, "stderr", errors):
                                        return_code = peer.local_agent(request, contract=True)
                direct.assert_not_called()
                self.assertFalse(observed["configPath"].exists())
                self.assertEqual(
                    observed["config"]["agents"]["list"][0]["tools"],
                    {"allow": ["pixel_web_browse"]},
                )
                self.assertEqual(
                    observed["config"]["tools"]["alsoAllow"], ["pixel_web_browse"],
                )
                self.assertEqual(observed["config"]["unrelated"], {"preserved": True})
                self.assertEqual(
                    observed["finalizationOptions"]["authority_profile"], "web-read-only",
                )
                self.assertEqual(
                    observed["finalizationOptions"]["authority_config_sha256"],
                    observed["configSha256"],
                )
                return return_code, output, errors, observed

            return_code, output, errors, observed = run_case(
                "00000000-0000-0000-0000-000000000191",
            )
            self.assertEqual(return_code, 0)
            parsed = json.loads(output.buffer.getvalue())
            authority = parsed["result"]["meta"]["meshAuthority"]
            self.assertEqual(authority["profile"], "web-read-only")
            self.assertEqual(authority["allowedTools"], ["pixel_web_browse"])
            self.assertEqual(authority["observedTools"], ["pixel_web_browse"])
            self.assertEqual(authority["configSha256"], observed["configSha256"])
            self.assertEqual(errors.buffer.getvalue(), b"")

            return_code, output, errors, _observed = run_case(
                "00000000-0000-0000-0000-000000000192", mismatch=True,
            )
            self.assertEqual(return_code, 2)
            self.assertEqual(output.buffer.getvalue(), b"")
            self.assertIn(b"lost its authority binding", errors.buffer.getvalue())

    @unittest.skipUnless(os.name == "posix", "owner-private synthesis custody is Linux-only")
    def test_synthesis_predecessor_binding_rejects_changed_exploration(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            receipts = state / "pixel-mesh-synthesis"
            sessions.mkdir(parents=True)
            receipts.mkdir(mode=0o700)
            state.chmod(0o700)
            session = sessions / "exploration.jsonl"
            session.write_text('{"retained":"first"}\n', encoding="utf-8")
            session.chmod(0o600)
            predecessor_sha256 = hashlib.sha256(session.read_bytes()).hexdigest()
            receipt = receipts / "synthesis.jsonl"
            receipt.write_text(json.dumps({"meshSynthesis": {
                "predecessorSessionFile": str(session),
                "predecessorSessionSha256": predecessor_sha256,
            }}) + "\n", encoding="utf-8")
            receipt.chmod(0o600)

            peer.verify_synthesis_predecessor_binding(
                receipt, session, state, predecessor_sha256,
            )
            session.write_text('{"retained":"changed"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "predecessor changed"):
                peer.verify_synthesis_predecessor_binding(
                    receipt, session, state, predecessor_sha256,
                )

    @unittest.skipUnless(os.name == "posix", "owner-private synthesis custody is Linux-only")
    def test_direct_synthesis_rejects_non_loopback_provider(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            state.mkdir()
            state.chmod(0o700)
            config = {
                "models": {"providers": {"tower": {
                    "baseUrl": "https://provider.example/v1", "apiKey": "secret",
                    "api": "openai-completions",
                    "models": [{"id": "dream-fleet-agent", "maxTokens": 4096}],
                }}},
                "agents": {"list": [{"id": "pixel", "model": "tower/dream-fleet-agent"}]},
            }
            (state / "openclaw.json").write_text(json.dumps(config) + "\n", encoding="utf-8")
            (state / "openclaw.json").chmod(0o600)
            with self.assertRaisesRegex(ValueError, "loopback"):
                peer.synthesis_provider_config(state)

    @unittest.skipUnless(os.name == "posix", "owner-private synthesis custody is Linux-only")
    def test_local_agent_recovers_silent_length_with_deterministic_blocked_receipt(self):
        exploration_id = uuid.UUID("00000000-0000-0000-0000-000000000011")
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            (state / "agents" / "pixel" / "sessions").mkdir(parents=True)
            state.chmod(0o700)

            def exploration(argv, _env, _timeout, **kwargs):
                transcript = kwargs["session_path"]
                transcript.parent.mkdir(parents=True, exist_ok=True)
                record = {"type": "message", "message": {
                    "role": "assistant", "api": "openai-completions",
                    "provider": "tower", "model": "dream-fleet-agent",
                    "stopReason": "length", "errorMessage": None,
                    "content": [{"type": "thinking", "thinking": "retained live evidence"}],
                }}
                transcript.write_text(json.dumps(record) + "\n", encoding="utf-8")
                transcript.chmod(0o600)
                response = {"payloads": [], "meta": {
                    "agentMeta": {"sessionFile": str(transcript)},
                    "livenessState": "working", "error": None, "stopReason": None,
                }}
                return subprocess.CompletedProcess([], 0, json.dumps(response).encode(), b"")

            class Output:
                def __init__(self):
                    self.buffer = io.BytesIO()

            output, errors = Output(), Output()
            environment = {peer.HOME_ENV: home, peer.OPENCLAW_BIN_ENV: "/tmp/openclaw-test"}
            with patch.dict(os.environ, environment, clear=False):
                with patch.object(peer.uuid, "uuid4", return_value=exploration_id):
                    with patch.object(peer, "run_agent_process", side_effect=exploration) as run:
                        with patch.object(
                            peer, "run_direct_synthesis_recovery",
                            side_effect=AssertionError("silent recovery must not call a model"),
                        ) as direct:
                            with patch.object(peer.sys, "stdout", output), patch.object(peer.sys, "stderr", errors):
                                self.assertEqual(peer.local_agent(b"bounded original objective"), 0)
            self.assertEqual(run.call_count, 1)
            direct.assert_not_called()
            parsed = json.loads(output.buffer.getvalue())
            payload = parsed["result"]["payloads"][0]
            self.assertTrue(payload["blocked"])
            self.assertTrue(payload["text"].startswith("Blocked:"))
            self.assertNotIn("retained live evidence", payload["text"])
            evidence = parsed["result"]["meta"]["meshSynthesisRecovery"]
            self.assertEqual(
                evidence["reason"], peer.REASONING_WITHOUT_VISIBLE_OUTPUT_RECOVERY,
            )
            self.assertEqual(evidence["synthesisAgent"], peer.SYNTHESIS_AGENT_ID)
            self.assertEqual(evidence["mechanism"], "deterministic-no-model-blocked")
            self.assertEqual(evidence["toolAuthority"], "none")
            self.assertTrue(evidence["blocked"])
            self.assertFalse(evidence["externalEffectAuthority"])
            self.assertFalse(evidence["completionAuthority"])
            self.assertFalse(evidence["acceptanceAuthority"])
            self.assertEqual(
                parsed["result"]["meta"]["agentMeta"]["predecessorSessionFile"],
                evidence["explorationSessionFile"],
            )
            self.assertEqual(parsed["result"]["meta"]["agentMeta"]["usage"], {
                "input": 0, "cacheRead": 0, "cacheWrite": 0,
                "output": 0, "totalTokens": 0,
            })
            receipt = Path(parsed["result"]["meta"]["agentMeta"]["sessionFile"])
            durable = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(durable["meshSynthesis"]["transport"], "deterministic-no-model")
            self.assertEqual(errors.buffer.getvalue(), b"")

    @unittest.skipUnless(os.name == "posix", "owner-private synthesis custody is Linux-only")
    def test_local_agent_hands_recoverable_exploration_budget_to_synthesis(self):
        fixed = uuid.UUID("00000000-0000-0000-0000-000000000021")
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            session = state / "agents" / "pixel" / "sessions" / f"{fixed}.jsonl"
            session.parent.mkdir(parents=True)
            state.chmod(0o700)
            diagnostic = (
                f"Pixel mesh agent budget exceeded: compaction limit reached "
                f"({peer.MAX_AGENT_COMPACTIONS + 1})\n"
            ).encode()
            failed = subprocess.CompletedProcess([], 124, b"", diagnostic)
            completed = json.dumps({"status": "ok", "result": {"payloads": [{"text": "done"}],
                "meta": {"livenessState": "working", "stopReason": "stop", "error": None}}}).encode()

            class Output:
                def __init__(self):
                    self.buffer = io.BytesIO()

            output, errors = Output(), Output()
            environment = {peer.HOME_ENV: home, peer.OPENCLAW_BIN_ENV: "/tmp/openclaw-test"}
            with patch.dict(os.environ, environment, clear=False):
                with patch.object(peer.uuid, "uuid4", return_value=fixed):
                    with patch.object(peer, "run_agent_process", return_value=failed):
                        with patch.object(peer, "run_direct_synthesis_recovery", return_value=completed) as recover:
                            with patch.object(peer.sys, "stdout", output), patch.object(peer.sys, "stderr", errors):
                                self.assertEqual(peer.local_agent(b"bounded objective"), 0)
            self.assertEqual(output.buffer.getvalue(), completed)
            self.assertEqual(errors.buffer.getvalue(), b"")
            self.assertEqual(recover.call_args.args[-1], peer.COMPACTION_BUDGET_RECOVERY)

    @unittest.skipUnless(os.name == "posix", "owner-private synthesis custody is Linux-only")
    def test_budget_synthesis_uses_only_a_fully_identified_compaction_summary(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            transcript = sessions / "budget.jsonl"
            compaction = {"type": "compaction", "tokensBefore": 52000,
                          "summary": "authenticated retained evidence",
                          "details": {"reason": "threshold"}}
            transcript.write_text(json.dumps(compaction) + "\n", encoding="utf-8")
            transcript.chmod(0o600)
            message = peer.synthesis_recovery_message(
                b"bounded original objective", transcript, state,
                peer.COMPACTION_BUDGET_RECOVERY,
            )
            self.assertIsNotNone(message)
            self.assertIn(b"bounded original objective", message)
            self.assertIn(b"authenticated retained evidence", message)

            compaction.pop("details")
            transcript.write_text(json.dumps(compaction) + "\n", encoding="utf-8")
            self.assertIsNone(peer.synthesis_recovery_message(
                b"bounded original objective", transcript, state,
                peer.COMPACTION_BUDGET_RECOVERY,
            ))

    @unittest.skipUnless(os.name == "posix", "owner-private synthesis custody is Linux-only")
    def test_transcript_overflow_synthesis_requires_exact_empty_terminal_error(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            transcript = sessions / "overflow.jsonl"
            compaction = {"type": "compaction", "tokensBefore": 52000,
                          "summary": "retained review findings",
                          "details": {"reason": "threshold"}}
            terminal = {"type": "message", "message": {
                "role": "assistant", "api": "openai-completions",
                "stopReason": "error", "errorMessage": peer.TOOL_LOOP_CONTEXT_OVERFLOW,
                "content": [{"type": "text", "text": ""}],
            }}
            transcript.write_text(
                json.dumps(compaction) + "\n" + json.dumps(terminal) + "\n",
                encoding="utf-8",
            )
            transcript.chmod(0o600)
            message = peer.synthesis_recovery_message(
                b"bounded objective", transcript, state, peer.TRANSCRIPT_OVERFLOW_RECOVERY,
            )
            self.assertIsNotNone(message)
            self.assertIn(b"retained review findings", message)

            terminal["message"]["errorMessage"] = "different provider error"
            transcript.write_text(
                json.dumps(compaction) + "\n" + json.dumps(terminal) + "\n",
                encoding="utf-8",
            )
            self.assertIsNone(peer.synthesis_recovery_message(
                b"bounded objective", transcript, state, peer.TRANSCRIPT_OVERFLOW_RECOVERY,
            ))

    @unittest.skipUnless(os.name == "posix", "owner-only JSONL contract")
    def test_agent_session_budget_reads_incrementally_and_limits_model_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "session.jsonl"
            transcript.write_text(json.dumps({"message": {
                "role": "assistant", "api": "openai-completions",
                "provider": "local", "model": "test", "content": [],
            }}) + "\n", encoding="utf-8")
            transcript.chmod(0o600)
            budget = peer.AgentSessionBudget(transcript, max_model_calls=2)
            self.assertIsNone(budget.observe())
            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"message": {
                    "role": "assistant", "api": "openai-completions",
                    "provider": "local", "model": "test", "content": [],
                }}) + "\n")
            self.assertEqual(budget.observe(), "model-call limit reached (2)")

    @unittest.skipUnless(os.name == "posix", "owner-only JSONL contract")
    def test_agent_session_budget_rejects_pre_call_same_inode_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "session.jsonl"
            transcript.write_text(json.dumps({"message": {
                "role": "user", "content": [{"type": "text", "text": "x" * 4096}],
            }}) + "\n", encoding="utf-8")
            transcript.chmod(0o600)
            budget = peer.AgentSessionBudget(transcript)
            self.assertIsNone(budget.observe())
            identity = transcript.stat().st_ino
            transcript.write_text("{}\n", encoding="utf-8")
            self.assertEqual(transcript.stat().st_ino, identity)
            with self.assertRaisesRegex(ValueError, "was truncated"):
                budget.observe()

    @unittest.skipUnless(os.name == "posix", "owner-only JSONL contract")
    def test_agent_session_budget_stops_third_tool_loop_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "session.jsonl"
            records = [
                {"message": {"role": "assistant", "errorMessage":
                    "Context overflow during tool loop"}},
                {"type": "compaction"},
                {"message": {"role": "assistant", "errorMessage":
                    "Context overflow DURING TOOL LOOP"}},
                {"type": "compaction"},
                {"message": {"role": "assistant", "errorMessage":
                    "Context overflow during tool loop"}},
            ]
            transcript.write_text("".join(json.dumps(record) + "\n" for record in records),
                                  encoding="utf-8")
            transcript.chmod(0o600)
            reason = peer.AgentSessionBudget(transcript).observe()
            self.assertEqual(reason, "tool-loop overflow limit reached (3)")

    @unittest.skipUnless(os.name == "posix", "owner-only JSONL contract")
    def test_agent_session_budget_does_not_double_count_compaction_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "session.jsonl"
            model = {"message": {
                "role": "assistant", "api": "openai-completions",
                "provider": "local", "model": "test", "responseId": "response-1",
                "content": [{"type": "toolCall", "id": "tool-1", "name": "read"}],
            }}
            overflow = {"message": {
                "role": "assistant", "api": "openai-completions", "provider": "local",
                "model": "test", "stopReason": "error",
                "errorMessage": "Context overflow during tool loop",
            }}
            compaction = {"type": "compaction", "tokensBefore": 53000,
                          "summary": "retained audit", "details": {"readFiles": ["README.md"]}}
            records = [model, overflow, compaction, model, overflow, compaction]
            transcript.write_text("".join(json.dumps(record) + "\n" for record in records),
                                  encoding="utf-8")
            transcript.chmod(0o600)
            budget = peer.AgentSessionBudget(transcript)
            self.assertIsNone(budget.observe())
            self.assertEqual(budget.model_calls, 1)
            self.assertEqual(budget.tool_calls, 1)
            self.assertEqual(budget.tool_loop_overflows, 1)
            self.assertEqual(budget.compactions, 1)

            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"message": {
                    "role": "assistant", "api": "openai-completions",
                    "provider": "local", "model": "test", "responseId": "response-2",
                    "content": [],
                }}) + "\n")
                handle.write(json.dumps(overflow) + "\n")
            self.assertIsNone(budget.observe())
            self.assertEqual(budget.tool_loop_overflows, 2)
            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"message": {
                    "role": "assistant", "api": "openai-completions",
                    "provider": "local", "model": "test", "responseId": "response-3",
                    "content": [],
                }}) + "\n")
                handle.write(json.dumps(overflow) + "\n")
            self.assertEqual(budget.observe(), "tool-loop overflow limit reached (3)")

    @unittest.skipUnless(os.name == "posix", "owner-only JSONL contract")
    def test_agent_session_budget_counts_identical_compactions_without_replay_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "session.jsonl"
            compaction = {"type": "compaction", "tokensBefore": 53000,
                          "summary": "same retained audit", "details": {"reason": "threshold"}}
            transcript.write_text(
                "".join(json.dumps(compaction) + "\n" for _ in range(3)), encoding="utf-8",
            )
            transcript.chmod(0o600)
            budget = peer.AgentSessionBudget(transcript)
            self.assertEqual(budget.observe(), "compaction limit reached (3)")
            self.assertEqual(budget.compactions, 3)

    @unittest.skipUnless(os.name == "posix", "owner-only JSONL contract")
    def test_agent_session_budget_limits_tool_calls_and_compactions(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "session.jsonl"
            transcript.write_text(json.dumps({"message": {"role": "assistant", "api": "cli",
                "content": [{"type": "toolCall"}, {"type": "toolCall"}]}}) + "\n",
                encoding="utf-8")
            transcript.chmod(0o600)
            budget = peer.AgentSessionBudget(transcript, max_tool_calls=2)
            self.assertEqual(budget.observe(), "tool-call limit reached (2)")

            compacted = Path(directory) / "compacted.jsonl"
            compacted.write_text("".join(json.dumps({"type": "compaction"}) + "\n"
                                         for _ in range(3)), encoding="utf-8")
            compacted.chmod(0o600)
            self.assertEqual(peer.AgentSessionBudget(compacted).observe(),
                             "compaction limit reached (3)")

    @unittest.skipUnless(os.name == "posix", "owner-only JSONL contract")
    def test_agent_session_budget_rejects_hardlinked_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "session.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")
            transcript.chmod(0o600)
            os.link(transcript, Path(directory) / "second-name.jsonl")
            with self.assertRaisesRegex(ValueError, "owner-private regular file"):
                peer.AgentSessionBudget(transcript).observe()

    @unittest.skipUnless(os.name == "posix", "Linux /proc and pidfd contract")
    def test_marked_process_pidfds_excludes_unmarked_same_uid_process(self):
        with tempfile.TemporaryDirectory() as directory:
            proc_root = Path(directory)
            for pid, environment in (("101", b"PIXEL_MESH_TREE_TOKEN=nonce\0"),
                                     ("102", b"UNRELATED=1\0")):
                root = proc_root / pid
                root.mkdir()
                (root / "environ").write_bytes(environment)
            with patch.object(peer.os, "pidfd_open", side_effect=lambda pid, _flags: pid + 1000):
                references = peer.marked_process_pidfds("nonce", proc_root)
            self.assertEqual(references, [(101, 1101)])
            for _, descriptor in references:
                # Fake descriptors must not be closed through the real OS.
                self.assertEqual(descriptor, 1101)

    @unittest.skipUnless(os.name == "posix", "Linux signal and pidfd contract")
    def test_terminate_agent_tree_uses_term_then_kill_for_only_marked_references(self):
        calls = []
        with patch.object(peer, "signal_marked_processes", side_effect=[1, 1]) as marked:
            with patch.object(peer, "send_pidfd_signal", side_effect=lambda fd, sig: calls.append((fd, sig))):
                found = peer.terminate_agent_tree(900, "nonce", grace_seconds=0)
        self.assertEqual(found, 1)
        self.assertEqual(calls, [(900, peer.signal.SIGTERM), (900, peer.signal.SIGKILL)])
        self.assertEqual(marked.call_args_list[0].args, ("nonce", peer.signal.SIGTERM))
        self.assertEqual(marked.call_args_list[1].args, ("nonce", peer.signal.SIGKILL))

    @unittest.skipUnless(os.name == "posix", "Linux signal and pidfd contract")
    def test_agent_process_timeout_terminates_tree_and_fails_closed(self):
        process = unittest.mock.Mock(pid=321, returncode=None)
        process.poll.return_value = None
        process.communicate.side_effect = subprocess.TimeoutExpired(["openclaw"], 0)
        with patch.object(peer.subprocess, "Popen", return_value=process) as popen:
            with patch.object(peer.os, "pidfd_open", return_value=654):
                with patch.object(peer.os, "close"):
                    with patch.object(peer, "process_identity", return_value=None):
                        with patch.object(peer, "terminate_agent_tree") as terminate:
                            with self.assertRaises(subprocess.TimeoutExpired):
                                peer.run_agent_process(["openclaw"], {}, 0)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(popen.call_args.kwargs["umask"], 0o077)
        self.assertRegex(popen.call_args.kwargs["env"][peer.TREE_TOKEN_ENV], r"^[a-f0-9]{64}$")
        terminate.assert_called_with(654, popen.call_args.kwargs["env"][peer.TREE_TOKEN_ENV])

    @unittest.skipUnless(os.name == "posix", "owner-private session mode is Linux-only")
    def test_agent_session_budget_rejects_observed_group_and_world_readable_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "session.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")
            transcript.chmod(0o664)
            with self.assertRaisesRegex(ValueError, "owner-private regular file"):
                peer.AgentSessionBudget(transcript).observe()

    @unittest.skipUnless(os.name == "posix", "subprocess umask is Linux-only")
    def test_agent_process_creates_owner_private_files(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "agent-created.txt"
            code = (
                "from pathlib import Path; "
                f"Path({str(artifact)!r}).write_text('private', encoding='utf-8')"
            )
            result = peer.run_agent_process([sys.executable, "-c", code], os.environ, 3)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(stat.S_IMODE(artifact.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "Linux signal and pidfd contract")
    def test_agent_process_terminates_tree_when_session_budget_fails(self):
        process = unittest.mock.Mock(pid=321, returncode=-15)
        process.poll.return_value = None
        process.communicate.return_value = (b"partial", b"agent error\n")
        budget = unittest.mock.Mock()
        budget.observe.return_value = "tool-loop overflow limit reached (2)"
        with patch.object(peer.subprocess, "Popen", return_value=process):
            with patch.object(peer.os, "pidfd_open", return_value=654):
                with patch.object(peer.os, "close"):
                    with patch.object(peer, "process_identity", return_value=None):
                        with patch.object(peer, "AgentSessionBudget", return_value=budget):
                            with patch.object(peer, "terminate_agent_tree") as terminate:
                                result = peer.run_agent_process(
                                    ["openclaw"], {}, 30, session_path=Path("session.jsonl")
                                )
        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"Pixel mesh agent budget exceeded", result.stderr)
        self.assertNotIn(b"agent error", result.stderr)
        terminate.assert_called_once()

    @unittest.skipUnless(os.name == "posix", "Linux signal and pidfd contract")
    def test_agent_process_suppresses_output_when_final_budget_observation_fails(self):
        process = unittest.mock.Mock(pid=321, returncode=0)
        process.poll.return_value = None
        process.communicate.return_value = (b"partial", b"provider secret\n")
        budget = unittest.mock.Mock()
        budget.observe.side_effect = [None, "tool-call limit reached (256)"]
        with patch.object(peer.subprocess, "Popen", return_value=process):
            with patch.object(peer.os, "pidfd_open", return_value=654):
                with patch.object(peer.os, "close"):
                    with patch.object(peer, "process_identity", return_value=None):
                        with patch.object(peer, "AgentSessionBudget", return_value=budget):
                            with patch.object(peer, "terminate_agent_tree") as terminate:
                                result = peer.run_agent_process(
                                    ["openclaw"], {}, 30, session_path=Path("session.jsonl")
                                )
        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(
            result.stderr,
            b"Pixel mesh agent budget exceeded: tool-call limit reached (256)\n",
        )
        terminate.assert_called_once()

    @unittest.skipUnless(os.name == "posix", "Linux signal and pidfd contract")
    def test_agent_process_detects_caller_parent_loss(self):
        process = unittest.mock.Mock(pid=321, returncode=-15)
        process.poll.return_value = None
        process.communicate.return_value = (b"partial", b"")
        caller = (111, 222, 333)
        with patch.object(peer.subprocess, "Popen", return_value=process):
            with patch.object(peer.os, "pidfd_open", return_value=654):
                with patch.object(peer.os, "close"):
                    with patch.object(peer, "process_identity", return_value=caller):
                        with patch.object(peer, "same_process", return_value=False):
                            with patch.object(
                                peer, "terminate_agent_tree",
                            ) as terminate:
                                result = peer.run_agent_process(["openclaw"], {}, 30)
        self.assertEqual(result.returncode, 128 + peer.signal.SIGHUP)
        terminate.assert_called_once()

    @unittest.skipUnless(os.name == "posix", "Linux signal and pidfd contract")
    def test_agent_process_detects_closed_ssh_transport(self):
        process = unittest.mock.Mock(pid=321, returncode=-15)
        process.poll.return_value = None
        process.communicate.return_value = (b"partial", b"")
        environment = {"SSH_CONNECTION": "client 1 server 2"}
        with patch.object(peer.subprocess, "Popen", return_value=process):
            with patch.object(peer.os, "pidfd_open", return_value=654):
                with patch.object(peer.os, "close"):
                    with patch.object(peer, "process_identity", return_value=None):
                        with patch.object(peer, "TRANSPORT_PROBE_INTERVAL_SECONDS", 0):
                            with patch.object(peer, "write_transport_probe", return_value=False):
                                with patch.object(peer, "terminate_agent_tree") as terminate:
                                    result = peer.run_agent_process(["openclaw"], environment, 30)
        self.assertEqual(result.returncode, 128 + peer.signal.SIGHUP)
        terminate.assert_called_once()

    @unittest.skipUnless(os.name == "posix", "Linux signal and pidfd contract")
    def test_agent_process_fails_closed_after_terminating_residual_descendant(self):
        process = unittest.mock.Mock(pid=321, returncode=0)
        process.communicate.return_value = (b'{"status":"ok"}', b"")
        with patch.object(peer.subprocess, "Popen", return_value=process):
            with patch.object(peer.os, "pidfd_open", return_value=654):
                with patch.object(peer.os, "close"):
                    with patch.object(peer, "process_identity", return_value=None):
                        with patch.object(peer, "terminate_agent_tree", return_value=1) as terminate:
                            result = peer.run_agent_process(["openclaw"], {}, 30)
        self.assertEqual(result.returncode, 125)
        self.assertEqual(result.stdout, b'{"status":"ok"}')
        self.assertIn(b"terminated 1 agent descendant", result.stderr)
        terminate.assert_called_once()

    @unittest.skipUnless(os.name == "posix", "Linux signal and pidfd contract")
    def test_agent_process_reaps_after_durable_yield_probe_completes(self):
        process = unittest.mock.Mock(pid=321, returncode=-15)
        process.poll.return_value = None
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["openclaw"], 0.25, output=b"paused"),
            (b"ignored", b"closed"),
        ]
        with patch.object(peer.subprocess, "Popen", return_value=process):
            with patch.object(peer.os, "pidfd_open", return_value=654):
                with patch.object(peer.os, "close"):
                    with patch.object(peer, "process_identity", return_value=None):
                        with patch.object(peer, "terminate_agent_tree") as terminate:
                            result = peer.run_agent_process(
                                ["openclaw"], {}, 30,
                                completion_probe=lambda output: b"reconciled" if output == b"paused" else None,
                            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"reconciled")
        self.assertEqual(result.stderr, b"closed")
        terminate.assert_called_once()

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "pidfd_open"),
                         "live Linux setsid and pidfd contract")
    def test_agent_process_preserves_output_across_communicate_poll_timeouts(self):
        code = (
            "import sys,time; "
            "sys.stdout.write('first\\n'); sys.stdout.flush(); "
            "time.sleep(0.6); "
            "sys.stdout.write('last\\n'); sys.stdout.flush()"
        )
        result = peer.run_agent_process([sys.executable, "-c", code], os.environ, 3)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"first\nlast\n")
        self.assertEqual(result.stderr, b"")

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "pidfd_open"),
                         "live Linux setsid and pidfd contract")
    def test_terminate_agent_tree_kills_real_setsid_descendant(self):
        token = "integration-nonce"
        child_code = "import os,time; os.setsid(); time.sleep(60)"
        root_code = (
            "import os,subprocess,sys,time; "
            f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}],env=os.environ); "
            "print(p.pid,flush=True); time.sleep(60)"
        )
        environment = {**os.environ, peer.TREE_TOKEN_ENV: token}
        root = subprocess.Popen(
            [sys.executable, "-c", root_code], env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, text=True,
        )
        root_pidfd = os.pidfd_open(root.pid, 0)
        child_pidfd = None
        try:
            assert root.stdout is not None
            child_pid = int(root.stdout.readline().strip())
            child_pidfd = os.pidfd_open(child_pid, 0)
            peer.terminate_agent_tree(root_pidfd, token, grace_seconds=0.2)
            root.wait(timeout=3)
            poller = select.poll()
            poller.register(child_pidfd, select.POLLIN)
            self.assertTrue(poller.poll(3000), "setsid descendant survived marked-tree termination")
        finally:
            if root.poll() is None:
                root.kill()
                root.wait(timeout=3)
            if root.stdout is not None:
                root.stdout.close()
            if root.stderr is not None:
                root.stderr.close()
            os.close(root_pidfd)
            if child_pidfd is not None:
                os.close(child_pidfd)


@unittest.skipUnless(os.name == "posix", "hard-link custody contract requires POSIX link semantics")
class SandboxCustodyAdversarialTests(unittest.TestCase):
    """PXL-LIVE-123 adversarial packet: closed custody contracts on ask-sandboxed."""

    def _owner_private_directory(self, root: Path, name: str) -> Path:
        directory = root / name
        directory.mkdir()
        os.chmod(directory, 0o700)
        return directory

    def test_sandbox_cleanup_refuses_same_owner_regular_replacement(self):
        """Cleanup must fail closed on a same-owner regular replacement, not unlink it.

        The original inode stays live through a same-directory rename, so the
        recreated replacement is a guaranteed-distinct inode instead of a
        possible reuse of the unlinked one. Cleanup is called with the original
        (dev, ino, full mode, uid, nlink) identity exactly as production does,
        so this exercises the real identity-mismatch contract rather than the
        missing-identity guard.
        """
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            state = self._owner_private_directory(root, "state")
            config_path = state / "openclaw.json"
            original_payload = b'{"model":"dream-fleet-agent"}\n'
            config_path.write_bytes(original_payload)
            os.chmod(config_path, 0o600)
            original_info = config_path.lstat()
            original_identity = (
                original_info.st_dev, original_info.st_ino,
                original_info.st_mode, original_info.st_uid,
                original_info.st_nlink,
            )
            held_path = state / "openclaw.json.held-original"
            config_path.rename(held_path)
            replacement_payload = b'{"model":"attacker-controlled"}\n'
            config_path.write_bytes(replacement_payload)
            os.chmod(config_path, 0o600)
            replacement_info = config_path.lstat()
            replacement_identity = (
                replacement_info.st_dev, replacement_info.st_ino,
                replacement_info.st_mode, replacement_info.st_uid,
                replacement_info.st_nlink,
            )
            self.assertNotEqual(original_identity, replacement_identity)
            with self.assertRaises((ValueError, OSError)):
                peer._remove_and_verify_ephemeral_config(config_path, original_identity)
            self.assertTrue(config_path.exists())
            self.assertEqual(config_path.read_bytes(), replacement_payload)
            after_info = config_path.lstat()
            self.assertEqual(
                (after_info.st_dev, after_info.st_ino, after_info.st_mode,
                 after_info.st_uid, after_info.st_nlink),
                replacement_identity,
            )
            self.assertTrue(held_path.exists())
            self.assertEqual(held_path.read_bytes(), original_payload)
            held_info = held_path.lstat()
            self.assertEqual(
                (held_info.st_dev, held_info.st_ino, held_info.st_mode,
                 held_info.st_uid, held_info.st_nlink),
                original_identity,
            )

    def test_workspace_identity_hash_covers_every_dimension(self):
        """The workspace identity hash must change when any recorded dimension changes.

        Exercises every recorded dimension independently: all four workspace
        identity elements (dev, ino, mode, uid), all five .git identity
        elements (dev, ino, mode, uid, effective_link_count), and the workspace
        path itself.
        """
        workspace = Path("/bounded/owner/worktree")
        workspace_identity = (2049, 424242, 0o040700, 1000)
        git_identity = (2049, 987654, 0o040700, 1000, peer.GIT_DIRECTORY_LINK_SENTINEL)
        baseline = peer._workspace_identity_sha256(workspace, workspace_identity, git_identity)
        self.assertEqual(len(baseline), 64)
        self.assertEqual(
            baseline,
            hashlib.sha256(json.dumps(
                {
                    "workspace": str(workspace),
                    "workspaceIdentity": list(workspace_identity),
                    "gitIdentity": list(git_identity),
                    "gitMetadataBinding": None,
                },
                sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            ).encode("utf-8")).hexdigest(),
        )
        for element in range(len(workspace_identity)):
            mutated = list(workspace_identity)
            mutated[element] += 1
            mutated_hash = peer._workspace_identity_sha256(
                workspace, tuple(mutated), git_identity,
            )
            self.assertNotEqual(
                baseline, mutated_hash,
                f"workspace identity element {element} did not change the hash",
            )
        for element in range(len(git_identity)):
            mutated = list(git_identity)
            mutated[element] += 1
            mutated_hash = peer._workspace_identity_sha256(
                workspace, workspace_identity, tuple(mutated),
            )
            self.assertNotEqual(
                baseline, mutated_hash,
                f"git identity element {element} did not change the hash",
            )
        different_path_hash = peer._workspace_identity_sha256(
            Path("/bounded/owner/other-worktree"), workspace_identity, git_identity,
        )
        self.assertNotEqual(baseline, different_path_hash, "workspace path did not change the hash")
        self.assertEqual(
            peer._workspace_identity_sha256(workspace, workspace_identity, git_identity),
            baseline,
            "hash must be deterministic for identical inputs",
        )

    def test_sandbox_runtime_cleanup_is_exact_and_absence_verified(self):
        """Cleanup targets one canonical Pixel session and rejects any residue."""
        binary = Path("/bounded/owner/bin/openclaw")
        session_key = "pixel-mesh-0123456789abcdef01234567"
        canonical = f"agent:pixel:{session_key}"
        removed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"removed one runtime\n", stderr=b"",
        )
        absent = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b"",
        )
        with tempfile.TemporaryDirectory() as raw_root:
            docker = Path(raw_root) / "docker"
            docker.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(docker, 0o600)
            environment = {"PATH": raw_root}
            with patch.object(peer.shutil, "which", return_value=str(docker)), \
                 patch.object(peer.subprocess, "run", side_effect=[removed, absent]) as run:
                peer._remove_and_verify_sandbox_runtime(binary, environment, session_key)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(
                run.call_args_list[0].args[0],
                [str(binary), "sandbox", "recreate", "--session", canonical, "--force"],
            )
            self.assertEqual(
                run.call_args_list[1].args[0],
                [
                    str(docker), "ps", "-aq", "--filter",
                    f"label=openclaw.sessionKey={canonical}",
                ],
            )

            residue = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=b"f32e70ff7a13\n", stderr=b"",
            )
            with patch.object(peer.shutil, "which", return_value=str(docker)), \
                 patch.object(peer.subprocess, "run", side_effect=[removed, residue]):
                with self.assertRaisesRegex(ValueError, "left an exact-session container"):
                    peer._remove_and_verify_sandbox_runtime(binary, environment, session_key)

        with patch.object(peer.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "invalid session key"):
                peer._remove_and_verify_sandbox_runtime(binary, environment, "../foreign")
        run.assert_not_called()

    def _container_policy_fixture(
        self, root: Path, *, workspace_access: str = "rw",
        git_common: Path | None = None,
    ):
        docker = root / "docker"
        docker.write_text("#!/bin/sh\n", encoding="utf-8")
        os.chmod(docker, 0o700)
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        session = "agent:pixel:pixel-mesh-0123456789abcdef01234567"
        config_sha256 = "b" * 64
        state = root / "state"
        control_source = (
            state / "sandbox" / "skills-workspaces"
            / f"{session.replace(':', '-')[:32]}-fixture"
            / ".openclaw" / "sandbox-skills" / "skills"
            if workspace_access == "rw"
            else state / "sandboxes" / f"{session.replace(':', '-')[:32]}-fixture"
        )
        with patch.object(peer.shutil, "which", return_value=str(docker)):
            observer = peer.SandboxContainerPolicyObserver(
                canonical_session_key=session,
                config_sha256=config_sha256,
                workspace=workspace,
                workspace_access=workspace_access,
                git_common=git_common,
                env={"PATH": str(root), "OPENCLAW_STATE_DIR": str(state)},
            )
        mounts = [
            {
                "Type": "bind", "Source": str(workspace),
                "Destination": (
                    peer.SANDBOX_DOCKER_WORKDIR
                    if workspace_access == "rw"
                    else peer.SANDBOX_DOCKER_READONLY_WORKSPACE_DESTINATION
                ),
                "RW": workspace_access == "rw",
                "Propagation": "rprivate",
            },
            {
                "Type": "bind",
                "Source": str(control_source),
                "Destination": (
                    "/workspace/.openclaw/sandbox-skills/skills"
                    if workspace_access == "rw" else peer.SANDBOX_DOCKER_WORKDIR
                ),
                "RW": False,
                "Propagation": "rprivate",
            },
        ]
        if git_common is not None:
            mounts.append({
                "Type": "bind", "Source": str(git_common),
                "Destination": str(git_common), "RW": False,
                "Propagation": "rprivate",
            })
        inspected = {
            "State": {"Status": "running"},
            "Config": {
                "Image": peer.SANDBOX_DOCKER_IMAGE,
                "WorkingDir": peer.SANDBOX_DOCKER_WORKDIR,
                "User": "1000:1000",
                "Labels": {
                    "openclaw.sessionKey": session,
                    "openclaw.configHash": config_sha256,
                },
            },
            "HostConfig": {
                "ReadonlyRootfs": True,
                "NetworkMode": "none",
                "CapDrop": ["ALL"],
                "CapAdd": None,
                "Privileged": False,
                "SecurityOpt": ["no-new-privileges"],
                "Devices": [],
                "DeviceRequests": None,
                "PortBindings": {},
                "PublishAllPorts": False,
                "PidMode": "",
                "IpcMode": "private",
                "UTSMode": "",
                "UsernsMode": "",
                "CgroupnsMode": "private",
                "PidsLimit": 256,
                "Memory": peer.SANDBOX_DOCKER_MEMORY_BYTES,
                "MemorySwap": peer.SANDBOX_DOCKER_MEMORY_BYTES,
                "NanoCpus": peer.SANDBOX_DOCKER_NANO_CPUS,
                "Tmpfs": {"/tmp": "", "/var/tmp": "", "/run": ""},
            },
            "Mounts": mounts,
        }
        return observer, inspected, workspace, session, config_sha256

    @staticmethod
    def _docker_result(stdout: bytes = b"", *, returncode: int = 0, stderr: bytes = b""):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    def test_container_policy_zero_then_valid_is_idempotent_and_private(self):
        container_id = "a" * 64
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            common = root / "common.git"
            common.mkdir()
            observer, inspected, workspace, session, config_sha256 = (
                self._container_policy_fixture(root, git_common=common)
            )
            calls = [
                self._docker_result(),
                self._docker_result(f"{container_id}\n".encode()),
                self._docker_result(json.dumps(inspected).encode()),
            ]
            with patch.object(peer.subprocess, "run", side_effect=calls) as run:
                observer.observe_once()
                observer.observe_once()
                observer.observe_once()
                evidence = observer.finalize(2, authority_config_sha256="b" * 64)
            self.assertEqual(run.call_count, 3)
            self.assertEqual(evidence["state"], "verified")
            self.assertEqual(
                observer.finalize(0, authority_config_sha256="b" * 64)["state"],
                "verified",
            )
            self.assertTrue(evidence["containerObserved"])
            self.assertEqual(evidence["observationCount"], 2)
            self.assertEqual(set(evidence), {
                "schemaVersion", "state", "containerObserved", "exactSessionMatch",
                "configLabelPresent", "authorityConfigBound", "policyMatch",
                "rootReadOnly", "networkDisabled", "privilegeIsolationMatched",
                "workspaceAccessMatched", "externalBindsReadOnly",
                "bindPropagationPrivate", "resourceLimitsMatched", "tmpfsMatched",
                "observationCount",
            })
            public = json.dumps(evidence, sort_keys=True)
            for private in (
                str(workspace), str(common), container_id, session, config_sha256,
                peer.SANDBOX_DOCKER_IMAGE, "Mounts", "Labels", "HostConfig",
                inspected["Mounts"][1]["Source"],
            ):
                self.assertNotIn(private, public)

    def test_container_policy_zero_tool_absence_is_truthful_but_tool_use_blocks(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            observer, _, _, _, _ = self._container_policy_fixture(root)
            with patch.object(peer.subprocess, "run", return_value=self._docker_result()):
                observer.observe_once()
            evidence = observer.finalize(0, authority_config_sha256="b" * 64)
            self.assertEqual(evidence["state"], "no-sandbox-tool-call")
            self.assertFalse(evidence["containerObserved"])
            with self.assertRaisesRegex(ValueError, "authority-config binding"):
                observer.finalize(0, authority_config_sha256="c" * 64)
            observer, _, _, _, _ = self._container_policy_fixture(root)
            with patch.object(peer.subprocess, "run", return_value=self._docker_result()):
                observer.observe_once()
            with self.assertRaisesRegex(ValueError, "did not verify a live runtime"):
                observer.finalize(1, authority_config_sha256="b" * 64)

    def test_container_policy_accepts_independent_well_formed_runtime_config_label(self):
        container = self._docker_result(("a" * 64 + "\n").encode())
        with tempfile.TemporaryDirectory() as raw_root:
            observer, inspected, _, _, authority_config_sha256 = (
                self._container_policy_fixture(Path(raw_root))
            )
            inspected["Config"]["Labels"]["openclaw.configHash"] = "d" * 64
            with patch.object(peer.subprocess, "run", side_effect=[
                container, self._docker_result(json.dumps(inspected).encode()),
            ]):
                observer.observe_once()
            evidence = observer.finalize(
                1, authority_config_sha256=authority_config_sha256,
            )
            self.assertTrue(evidence["configLabelPresent"])
            self.assertTrue(evidence["authorityConfigBound"])

    def test_container_policy_list_failures_freeze(self):
        cases = {
            "duplicate": self._docker_result(("a" * 64 + "\n" + "b" * 64 + "\n").encode()),
            "invalid-id": self._docker_result(b"short\n"),
            "nonzero": self._docker_result(returncode=1, stderr=b"private diagnostic"),
            "oversized": self._docker_result(b"a" * (peer.MAX_SANDBOX_DOCKER_LIST_BYTES + 1)),
        }
        for name, result in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw_root:
                observer, _, _, _, _ = self._container_policy_fixture(Path(raw_root))
                with patch.object(peer.subprocess, "run", return_value=result) as run:
                    observer.observe_once()
                    observer.observe_once()
                self.assertEqual(run.call_count, 1)
                with self.assertRaisesRegex(ValueError, "failed closed"):
                    observer.finalize(0, authority_config_sha256="b" * 64)
        with tempfile.TemporaryDirectory() as raw_root:
            observer, _, _, _, _ = self._container_policy_fixture(Path(raw_root))
            with patch.object(
                peer.subprocess, "run",
                side_effect=subprocess.TimeoutExpired(["docker"], 10),
            ):
                observer.observe_once()
            with self.assertRaisesRegex(ValueError, "failed closed"):
                observer.finalize(0, authority_config_sha256="b" * 64)

    def test_container_policy_inspect_failures_freeze(self):
        container = self._docker_result(("a" * 64 + "\n").encode())
        malformed_cases = {
            "nonzero": self._docker_result(returncode=1),
            "oversized": self._docker_result(
                b"{" + b" " * peer.MAX_SANDBOX_DOCKER_INSPECT_BYTES + b"}",
            ),
            "malformed": self._docker_result(b"not-json"),
            "excessive-depth": self._docker_result(b"[" * 1200 + b"]" * 1200),
            "duplicate-key": self._docker_result(b'{"State":{},"State":{}}'),
        }
        for name, inspected_result in malformed_cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw_root:
                observer, _, _, _, _ = self._container_policy_fixture(Path(raw_root))
                with patch.object(
                    peer.subprocess, "run", side_effect=[container, inspected_result],
                ):
                    observer.observe_once()
                with self.assertRaisesRegex(ValueError, "failed closed"):
                    observer.finalize(1, authority_config_sha256="b" * 64)
        with tempfile.TemporaryDirectory() as raw_root:
            observer, _, _, _, _ = self._container_policy_fixture(Path(raw_root))
            with patch.object(
                peer.subprocess, "run",
                side_effect=[container, subprocess.TimeoutExpired(["docker"], 10)],
            ):
                observer.observe_once()
            with self.assertRaisesRegex(ValueError, "failed closed"):
                observer.finalize(1, authority_config_sha256="b" * 64)

    def test_container_policy_rejects_every_policy_family_mismatch(self):
        container = self._docker_result(("a" * 64 + "\n").encode())

        def mutate_status(value):
            value["State"]["Status"] = "exited"

        def mutate_label(value):
            value["Config"]["Labels"]["openclaw.configHash"] = "c" * 63

        def mutate_image(value):
            value["Config"]["Image"] = "foreign/image"

        def mutate_root(value):
            value["HostConfig"]["ReadonlyRootfs"] = False

        def mutate_network(value):
            value["HostConfig"]["NetworkMode"] = "bridge"

        def mutate_caps(value):
            value["HostConfig"]["CapDrop"] = []

        def mutate_privileged(value):
            value["HostConfig"]["Privileged"] = True

        def mutate_security_opt(value):
            value["HostConfig"]["SecurityOpt"] = None

        def mutate_resource(value):
            value["HostConfig"]["PidsLimit"] = True

        def mutate_tmpfs(value):
            del value["HostConfig"]["Tmpfs"]["/run"]

        def mutate_tmpfs_options(value):
            value["HostConfig"]["Tmpfs"]["/tmp"] = "exec"

        def mutate_workspace(value):
            value["Mounts"][0]["RW"] = False

        def mutate_skills(value):
            value["Mounts"][1]["RW"] = True

        def mutate_workspace_destination(value):
            value["Mounts"][0]["Destination"] = "/wrong-workspace"

        def mutate_skills_destination(value):
            value["Mounts"][1]["Destination"] = "/wrong-skills"

        def mutate_unexpected(value):
            value["Mounts"].append({
                "Type": "bind", "Source": "/foreign", "Destination": "/foreign",
                "RW": False, "Propagation": "rprivate",
            })

        def mutate_propagation(value):
            value["Mounts"][0]["Propagation"] = "rshared"

        mutations = {
            "status": mutate_status,
            "label": mutate_label,
            "image": mutate_image,
            "root": mutate_root,
            "network": mutate_network,
            "capabilities": mutate_caps,
            "privileged": mutate_privileged,
            "no-new-privileges": mutate_security_opt,
            "bool-as-int-resource": mutate_resource,
            "tmpfs": mutate_tmpfs,
            "tmpfs-options": mutate_tmpfs_options,
            "workspace-access": mutate_workspace,
            "skills-access": mutate_skills,
            "workspace-destination": mutate_workspace_destination,
            "skills-destination": mutate_skills_destination,
            "unexpected-bind": mutate_unexpected,
            "mount-propagation": mutate_propagation,
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw_root:
                observer, inspected, _, _, _ = self._container_policy_fixture(Path(raw_root))
                mutated = copy.deepcopy(inspected)
                mutate(mutated)
                with patch.object(peer.subprocess, "run", side_effect=[
                    container, self._docker_result(json.dumps(mutated).encode()),
                ]):
                    observer.observe_once()
                with self.assertRaisesRegex(ValueError, "failed closed"):
                    observer.finalize(1, authority_config_sha256="b" * 64)

    def test_container_policy_readonly_and_linked_git_bind_contract(self):
        container = self._docker_result(("a" * 64 + "\n").encode())
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            common = root / "common.git"
            common.mkdir()
            observer, inspected, _, _, _ = self._container_policy_fixture(
                root, workspace_access="ro", git_common=common,
            )
            with patch.object(peer.subprocess, "run", side_effect=[
                container, self._docker_result(json.dumps(inspected).encode()),
            ]):
                observer.observe_once()
            self.assertEqual(
                observer.finalize(1, authority_config_sha256="b" * 64)["state"],
                "verified",
            )

            for name, mutate in {
                "managed-missing": lambda value: value["Mounts"].pop(1),
                "managed-writable": lambda value: value["Mounts"][1].__setitem__("RW", True),
                "managed-source": lambda value: value["Mounts"][1].__setitem__(
                    "Source", str(root / "sandbox" / "foreign")
                ),
                "managed-destination": lambda value: value["Mounts"][1].__setitem__(
                    "Destination", "/wrong-managed-workspace"
                ),
                "common-missing": lambda value: value["Mounts"].pop(),
                "common-writable": lambda value: value["Mounts"][-1].__setitem__("RW", True),
            }.items():
                with self.subTest(name=name):
                    observer, inspected, _, _, _ = self._container_policy_fixture(
                        root, workspace_access="ro", git_common=common,
                    )
                    mutate(inspected)
                    with patch.object(peer.subprocess, "run", side_effect=[
                        container, self._docker_result(json.dumps(inspected).encode()),
                    ]):
                        observer.observe_once()
                    with self.assertRaisesRegex(ValueError, "failed closed"):
                        observer.finalize(1, authority_config_sha256="b" * 64)

    def test_container_policy_docker_custody_is_shared_and_strict(self):
        with tempfile.TemporaryDirectory() as raw_root:
            docker = Path(raw_root) / "docker"
            docker.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(docker, 0o600)
            environment = {"PATH": raw_root}
            with patch.object(peer.shutil, "which", return_value=str(docker)):
                self.assertEqual(
                    peer._custodied_docker_binary(
                        environment, purpose="sandbox runtime cleanup",
                    ),
                    docker,
                )
                self.assertEqual(
                    peer._custodied_docker_binary(
                        environment, purpose="sandbox container policy observation",
                    ),
                    docker,
                )
                for unsafe_mode in (0o620, 0o602, 0o622):
                    with self.subTest(unsafe_mode=oct(unsafe_mode)), \
                         patch.object(peer.stat, "S_IMODE", return_value=unsafe_mode), \
                         self.assertRaisesRegex(ValueError, "failed custody checks"):
                        peer._custodied_docker_binary(
                            environment, purpose="sandbox container policy observation",
                        )

        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            observer, _, _, _, _ = self._container_policy_fixture(root)
            with patch.object(peer.stat, "S_IMODE", return_value=0o720), \
                 patch.object(peer.subprocess, "run") as run:
                observer.observe_once()
            run.assert_not_called()
            with self.assertRaisesRegex(ValueError, "failed closed"):
                observer.finalize(0, authority_config_sha256="b" * 64)

    def test_agent_supervision_polls_container_policy_and_aborts_on_fault(self):
        class Observer:
            def __init__(self, *, fault=False):
                self.calls = 0
                self.fault = fault

            def observe_once(self):
                self.calls += 1

            def raise_if_faulted(self):
                if self.fault:
                    raise ValueError("sandbox container policy observation failed closed")

        observer = Observer()
        result = peer.run_agent_process(
            ["/bin/sh", "-c", "sleep 0.05"], dict(os.environ), 2,
            container_policy_observer=observer,
        )
        self.assertEqual(result.returncode, 0)
        self.assertGreaterEqual(observer.calls, 1)

        faulted = Observer(fault=True)
        with self.assertRaisesRegex(ValueError, "container policy observation failed closed"):
            peer.run_agent_process(
                ["/bin/sh", "-c", "sleep 5"], dict(os.environ), 2,
                container_policy_observer=faulted,
            )
        self.assertEqual(faulted.calls, 1)

    def test_sandbox_workspace_control_path_is_precreated_and_exactly_restored(self):
        """Runtime mount targets preserve prior state and reject ambiguous residue."""
        with tempfile.TemporaryDirectory() as raw_root:
            workspace = self._owner_private_directory(Path(raw_root), "workspace")
            binding = peer._prepare_sandbox_workspace_control_path(workspace)
            self.assertEqual([created for _, _, created in binding], [True, True, True])
            for path, identity, _ in binding:
                self.assertEqual(
                    peer._sandbox_workspace_control_directory_identity(path), identity,
                )
                self.assertEqual(stat.S_IMODE(path.lstat().st_mode), 0o700)
            peer._remove_and_verify_sandbox_workspace_control_path(binding)
            self.assertFalse((workspace / ".openclaw").exists())

            control_root = self._owner_private_directory(workspace, ".openclaw")
            marker = control_root / "preserve.txt"
            marker.write_text("preserve\n", encoding="utf-8")
            os.chmod(marker, 0o600)
            binding = peer._prepare_sandbox_workspace_control_path(workspace)
            self.assertEqual([created for _, _, created in binding], [False, True, True])
            peer._remove_and_verify_sandbox_workspace_control_path(binding)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve\n")
            self.assertFalse((control_root / "sandbox-skills").exists())

            binding = peer._prepare_sandbox_workspace_control_path(workspace)
            residue = binding[-1][0] / "unexpected"
            residue.write_text("retained\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "pre-turn state"):
                peer._remove_and_verify_sandbox_workspace_control_path(binding)
            self.assertEqual(residue.read_text(encoding="utf-8"), "retained\n")

        with tempfile.TemporaryDirectory() as raw_root:
            workspace = self._owner_private_directory(Path(raw_root), "workspace")
            unsafe = workspace / ".openclaw"
            unsafe.mkdir(mode=0o700)
            unsafe_info = list(unsafe.lstat())
            unsafe_info[0] = stat.S_IFDIR | 0o777
            with patch.object(
                Path, "lstat", return_value=os.stat_result(unsafe_info),
            ), self.assertRaisesRegex(ValueError, "no group/other write"):
                peer._sandbox_workspace_control_directory_identity(unsafe)

    def test_source_identity_changes_when_link_count_drifts(self):
        """The source-config lstat identity must distinguish link-count drift."""
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            state = self._owner_private_directory(root, "state")
            source = state / "openclaw.json"
            source.write_bytes(b'{"model":"dream-fleet-agent"}\n')
            os.chmod(source, 0o600)
            before = peer._source_config_lstat_identity(source)
            os.link(source, state / "openclaw.json.custody-link")
            after = peer._source_config_lstat_identity(source)
            self.assertNotEqual(before, after)

    def test_git_pointer_revalidation_rejects_new_hard_link(self):
        """Revalidation must fail closed when the .git pointer gains a hard link."""
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            workspace = self._owner_private_directory(root, "worktree")
            common = self._owner_private_directory(root, "repo.git")
            worktrees = self._owner_private_directory(common, "worktrees")
            control = self._owner_private_directory(worktrees, "bounded-worktree")
            pointer = workspace / ".git"
            pointer.write_text(f"gitdir: {control}\n", encoding="utf-8")
            os.chmod(pointer, 0o600)
            fake_top = subprocess.CompletedProcess(
                args=["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
                returncode=0, stdout=(str(workspace) + "\n").encode("utf-8"), stderr=b"",
            )
            fake_common = subprocess.CompletedProcess(
                args=["git", "-C", str(workspace), "rev-parse", "--git-common-dir"],
                returncode=0, stdout=(str(common) + "\n").encode("utf-8"), stderr=b"",
            )
            previous_cwd = os.getcwd()
            os.chdir(workspace)
            try:
                with patch.object(peer.subprocess, "run", side_effect=[fake_top, fake_common]):
                    _, workspace_identity, git_identity, binding = (
                        peer._validated_sandboxed_workspace()
                    )
            finally:
                os.chdir(previous_cwd)
            self.assertIsNotNone(binding)
            os.link(pointer, workspace / ".git.custody-link")
            with self.assertRaises(ValueError):
                peer._revalidate_workspace_identity(
                    workspace, workspace_identity, git_identity, binding,
                )

    def test_linked_worktree_pointer_content_and_common_custody_fail_closed(self):
        """Malformed pointers, content drift, and writable metadata fail closed."""
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            workspace = self._owner_private_directory(root, "worktree")
            common = self._owner_private_directory(root, "repo.git")
            worktrees = self._owner_private_directory(common, "worktrees")
            control = self._owner_private_directory(worktrees, "bounded-worktree")
            pointer = workspace / ".git"
            pointer.write_text(f"gitdir: {control}\n", encoding="utf-8")
            os.chmod(pointer, 0o600)
            fake_top = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout=(str(workspace) + "\n").encode("utf-8"), stderr=b"",
            )
            fake_common = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout=(str(common) + "\n").encode("utf-8"), stderr=b"",
            )
            previous_cwd = os.getcwd()
            os.chdir(workspace)
            try:
                with patch.object(peer.subprocess, "run", side_effect=[fake_top, fake_common]):
                    validated = peer._validated_sandboxed_workspace()
            finally:
                os.chdir(previous_cwd)
            _, workspace_identity, git_identity, binding = validated
            self.assertIsNotNone(binding)
            pointer.write_text(f"gitdir: {control}/.\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "pointer content drifted|not canonical"):
                peer._revalidate_workspace_identity(
                    workspace, workspace_identity, git_identity, binding,
                )

            for malformed in (
                "gitdir: relative/path\n",
                f"gitdir: {control}:foreign\n",
                f"gitdir: {control}\nsecond\n",
                f"gitdir: {control}",
            ):
                pointer.write_text(malformed, encoding="utf-8")
                info = pointer.lstat()
                identity = (
                    info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode),
                    info.st_uid, info.st_nlink,
                )
                with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                    peer._read_linked_worktree_pointer(pointer, identity)

            pointer.write_text(f"gitdir: {control}\n", encoding="utf-8")
            def git_identity_at_mode(path: Path, mode: int):
                simulated = list(path.lstat())
                simulated[0] = stat.S_IFDIR | mode
                with patch.object(
                    Path, "lstat", return_value=os.stat_result(simulated),
                ):
                    return peer._git_directory_identity(path)

            git_identity_at_mode(common, 0o755)
            with self.assertRaisesRegex(ValueError, "no group/other write"):
                git_identity_at_mode(worktrees, 0o775)
            info = pointer.lstat()
            identity = (
                info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode),
                info.st_uid, info.st_nlink,
            )
            with self.assertRaisesRegex(ValueError, "no group/other write"):
                git_identity_at_mode(common, 0o775)

    @unittest.skipUnless(os.name == "posix", "sandboxed custody identity checks require POSIX")
    def test_sandboxed_config_preserves_unrelated_fields_and_exact_policy(self):
        """create_sandboxed_config preserves unrelated fields and writes the exact policy.

        Builds a private state root with provider/model data, a root marker,
        existing unrelated `tools.exec` data, agents.defaults, the Pixel agent,
        and another agent, plus a private workspace holding a normal private
        `.git` directory. Identity tuples are derived directly from lstat. The
        call must leave the source bytes unchanged, preserve every unrelated
        root/provider/other-agent field semantically, write the exact qualified
        workspace/six-tool/docker policy on defaults and the one Pixel agent,
        keep unrelated top-level `tools.exec` data, set the exact nested
        fs/exec/sandbox/elevated policy, produce an owner-private single-link
        0600 ephemeral file, return 64-hex-lowercase hashes, and remove only the
        exact returned identity on demand.
        """
        self.assertEqual(os.geteuid(), os.getuid(), "effective uid must not be root")
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            state = self._owner_private_directory(root, "state")
            source = state / "openclaw.json"
            unrelated_exec_data = {"notification": "sound", "path": "/bounded/owner/bin"}
            unrelated_tools_data = {"insight": "off", "web": {"search": "allowed"}}
            source.write_text(
                json.dumps({
                    "model": "dream-fleet-agent",
                    "rootMarker": "kept-verbatim",
                    "providers": {
                        "dream-fleet": {
                            "baseUrl": "http://127.0.0.1:9100/v1",
                            "apiKey": "bounded-test-only-key",
                            "api": "openai-completions",
                            "models": {
                                "dream-fleet-agent": {
                                    "name": "Dream Fleet Agent",
                                    "contextWindow": 131072,
                                    "maxTokens": 4096,
                                },
                            },
                        },
                    },
                    "tools": {
                        "exec": dict(unrelated_exec_data),
                        **unrelated_tools_data,
                    },
                    "agents": {
                        "defaults": {"preExisting": "keep-me"},
                        "list": [
                            {"id": "other-agent", "workspace": "/bounded/other", "tools": {"allow": ["read"]}},
                            {"id": "pixel", "model": "dream-fleet-agent"},
                        ],
                    },
                }, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            os.chmod(source, 0o600)
            source_payload = source.read_bytes()
            workspace = self._owner_private_directory(root, "worktree")
            pointer = workspace / ".git"
            pointer.mkdir(mode=0o700)
            ws_info = workspace.lstat()
            git_info = pointer.lstat()
            self.assertTrue(stat.S_ISDIR(git_info.st_mode))
            self.assertEqual(stat.S_IMODE(git_info.st_mode), 0o700)
            workspace_identity = (
                ws_info.st_dev, ws_info.st_ino, stat.S_IMODE(ws_info.st_mode), ws_info.st_uid,
            )
            git_identity = (
                git_info.st_dev, git_info.st_ino, stat.S_IMODE(git_info.st_mode),
                git_info.st_uid, peer.GIT_DIRECTORY_LINK_SENTINEL,
            )
            path, rendered_sha256, expected_identity, workspace_hash = peer.create_sandboxed_config(
                state, workspace, workspace_identity, git_identity,
            )
            try:
                # The parsed source is untouched on disk: same bytes, same inode.
                self.assertEqual(source.read_bytes(), source_payload)
                source_now = source.lstat()
                self.assertEqual(
                    (source_now.st_dev, source_now.st_ino, source_now.st_mode,
                     source_now.st_uid, source_now.st_nlink, source_now.st_size),
                    peer._source_config_lstat_identity(source),
                )
                config = json.loads(path.read_text(encoding="utf-8"))
                # Root-level unrelated fields survive semantically untouched.
                self.assertEqual(config["model"], "dream-fleet-agent")
                self.assertEqual(config["rootMarker"], "kept-verbatim")
                self.assertEqual(
                    config["providers"],
                    {
                        "dream-fleet": {
                            "baseUrl": "http://127.0.0.1:9100/v1",
                            "apiKey": "bounded-test-only-key",
                            "api": "openai-completions",
                            "models": {
                                "dream-fleet-agent": {
                                    "name": "Dream Fleet Agent",
                                    "contextWindow": 131072,
                                    "maxTokens": 4096,
                                },
                            },
                        },
                    },
                )
                # Defaults keep their unrelated key and gain exactly the sandboxed keys.
                defaults = config["agents"]["defaults"]
                self.assertEqual(defaults["preExisting"], "keep-me")
                self.assertEqual(
                    defaults,
                    {
                        "preExisting": "keep-me",
                        "skipBootstrap": True,
                        "contextInjection": "never",
                    },
                )
                agent_list = config["agents"]["list"]
                self.assertEqual(len(agent_list), 2)
                self.assertEqual(agent_list[0], {
                    "id": "other-agent", "workspace": "/bounded/other", "tools": {"allow": ["read"]},
                })
                pixel = agent_list[1]
                self.assertEqual(pixel["id"], "pixel")
                self.assertEqual(pixel["model"], "dream-fleet-agent")
                self.assertEqual(pixel["workspace"], str(workspace))
                self.assertEqual(pixel["tools"], {"allow": list(peer.SANDBOXED_ALLOWED_TOOLS)})
                self.assertEqual(
                    pixel["sandbox"],
                    {
                        "mode": "all",
                        "backend": "docker",
                        "scope": "session",
                        "workspaceAccess": "rw",
                        "docker": {
                            "image": peer.SANDBOX_DOCKER_IMAGE,
                            "workdir": peer.SANDBOX_DOCKER_WORKDIR,
                            "readOnlyRoot": True,
                            "tmpfs": ["/tmp", "/var/tmp", "/run"],
                            "network": "none",
                            "user": "1000:1000",
                            "capDrop": ["ALL"],
                            "pidsLimit": 256,
                            "memory": "8g",
                            "memorySwap": "8g",
                            "cpus": 4,
                        },
                    },
                )
                self.assertNotIn("binds", pixel["sandbox"]["docker"])
                self.assertNotIn(
                    "dangerouslyAllowExternalBindSources",
                    pixel["sandbox"]["docker"],
                )
                # Top-level tools: unrelated exec data preserved, exact nested policy.
                self.assertEqual(config["tools"]["exec"], {
                    **unrelated_exec_data,
                    "host": "sandbox",
                    "timeoutSec": 1800,
                    "applyPatch": {"enabled": True, "workspaceOnly": True},
                })
                self.assertEqual(config["tools"]["profile"], "coding")
                self.assertEqual(config["tools"]["fs"], {"workspaceOnly": True})
                self.assertEqual(
                    config["tools"]["sandbox"],
                    {"tools": {"allow": list(peer.SANDBOXED_ALLOWED_TOOLS), "deny": ["image"]}},
                )
                self.assertEqual(config["tools"]["elevated"], {"enabled": False})
                self.assertNotIn("sandbox", config)
                self.assertNotIn("elevated", config)
                # Ephemeral file custody: 0600, single-link, owner-owned.
                info = path.lstat()
                self.assertFalse(path.is_symlink())
                self.assertTrue(stat.S_ISREG(info.st_mode))
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
                self.assertEqual(info.st_nlink, 1)
                self.assertEqual(info.st_uid, os.geteuid())
                self.assertEqual(
                    expected_identity,
                    (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink),
                )
                # Returned hashes are 64 lowercase hex characters.
                hex_pattern = re.compile(r"^[0-9a-f]{64}$")
                self.assertRegex(rendered_sha256, hex_pattern)
                self.assertRegex(workspace_hash, hex_pattern)
                self.assertEqual(
                    workspace_hash,
                    peer._workspace_identity_sha256(workspace, workspace_identity, git_identity),
                )
            finally:
                peer._remove_and_verify_ephemeral_config(path, expected_identity)
            self.assertFalse(path.exists())
            self.assertFalse(path.is_symlink())
            with self.assertRaises(OSError):
                path.lstat()

    def test_linked_worktree_config_adds_only_exact_read_only_common_git_bind(self):
        """A linked worktree receives one same-path read-only Git metadata bind."""
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            state = self._owner_private_directory(root, "state")
            source = state / "openclaw.json"
            source.write_text(json.dumps({
                "agents": {"list": [{"id": "pixel", "model": "dream-fleet-agent"}]},
            }) + "\n", encoding="utf-8")
            os.chmod(source, 0o600)
            workspace = self._owner_private_directory(root, "worktree")
            common = self._owner_private_directory(root, "repo.git")
            worktrees = self._owner_private_directory(common, "worktrees")
            target = self._owner_private_directory(worktrees, "bounded-worktree")
            pointer = workspace / ".git"
            pointer_payload = f"gitdir: {target}\n".encode("utf-8")
            pointer.write_bytes(pointer_payload)
            os.chmod(pointer, 0o600)
            ws_info = workspace.lstat()
            git_info = pointer.lstat()
            common_info = common.lstat()
            worktrees_info = worktrees.lstat()
            target_info = target.lstat()
            workspace_identity = (
                ws_info.st_dev, ws_info.st_ino, stat.S_IMODE(ws_info.st_mode), ws_info.st_uid,
            )
            git_identity = (
                git_info.st_dev, git_info.st_ino, stat.S_IMODE(git_info.st_mode),
                git_info.st_uid, git_info.st_nlink,
            )
            binding = (
                common,
                (common_info.st_dev, common_info.st_ino,
                 stat.S_IMODE(common_info.st_mode), common_info.st_uid),
                worktrees,
                (worktrees_info.st_dev, worktrees_info.st_ino,
                 stat.S_IMODE(worktrees_info.st_mode), worktrees_info.st_uid),
                target,
                (target_info.st_dev, target_info.st_ino,
                 stat.S_IMODE(target_info.st_mode), target_info.st_uid),
                hashlib.sha256(pointer_payload).hexdigest(),
            )
            path, _, identity, workspace_hash = peer.create_sandboxed_config(
                state, workspace, workspace_identity, git_identity,
                git_metadata_binding=binding,
            )
            try:
                config = json.loads(path.read_text(encoding="utf-8"))
                pixel = config["agents"]["list"][0]
                self.assertEqual(
                    pixel["sandbox"]["docker"]["binds"],
                    [f"{common}:{common}:ro"],
                )
                self.assertIs(
                    pixel["sandbox"]["docker"][
                        "dangerouslyAllowExternalBindSources"
                    ],
                    True,
                )
                self.assertEqual(
                    workspace_hash,
                    peer._workspace_identity_sha256(
                        workspace, workspace_identity, git_identity, binding,
                    ),
                )
            finally:
                peer._remove_and_verify_ephemeral_config(path, identity)

    @unittest.skipUnless(os.name == "posix", "sandboxed source custody rejection checks require POSIX")
    def test_sandboxed_source_strict_rejections(self):
        """_load_bounded_owner_private_config rejects hostile source custody shapes.

        Each subtest gets a fresh private state root, uses bounded fixtures with
        no real credentials, and asserts the strict loader fails closed on
        duplicate JSON keys, non-finite JSON constants, malformed JSON, invalid
        UTF-8, a symlinked source, unsafe modes, a non-regular source, and a
        hard-linked source.
        """
        self.assertEqual(os.geteuid(), os.getuid(), "effective uid must not be root")

        def fresh_state() -> tuple[tempfile.TemporaryDirectory, Path, Path]:
            temporary = tempfile.TemporaryDirectory()
            root = Path(temporary.name)
            state = self._owner_private_directory(root, "state")
            source = state / "openclaw.json"
            return temporary, state, source

        # Duplicate keys: the strict object hook must reject them.
        temporary, state, source = fresh_state()
        with temporary:
            source.write_bytes(b'{"model":"a","model":"b"}\n')
            os.chmod(source, 0o600)
            with self.assertRaises(ValueError):
                peer._load_bounded_owner_private_config(state)
            self.assertTrue(source.exists())
        # Non-finite JSON constants: NaN, Infinity, -Infinity.
        for constant in ("NaN", "Infinity", "-Infinity"):
            temporary, state, source = fresh_state()
            with temporary:
                source.write_bytes(
                    ('{"model":"a","threshold":' + constant + '}\n').encode("ascii")
                )
                os.chmod(source, 0o600)
                with self.assertRaises(ValueError):
                    peer._load_bounded_owner_private_config(state)
                self.assertTrue(source.exists())
        # Malformed JSON.
        temporary, state, source = fresh_state()
        with temporary:
            source.write_bytes(b'{"model": "a",,}\n')
            os.chmod(source, 0o600)
            with self.assertRaises(ValueError):
                peer._load_bounded_owner_private_config(state)
            self.assertTrue(source.exists())
        # Invalid UTF-8 bytes.
        temporary, state, source = fresh_state()
        with temporary:
            source.write_bytes(b'{"model":"a\xff\xfe"}\n')
            os.chmod(source, 0o600)
            with self.assertRaises(ValueError):
                peer._load_bounded_owner_private_config(state)
            self.assertTrue(source.exists())
        # A symlinked source must be rejected without being followed.
        temporary, state, source = fresh_state()
        with temporary:
            target = state / "openclaw.json.real"
            target.write_bytes(b'{"model":"a"}\n')
            os.chmod(target, 0o600)
            source.symlink_to(target.name)
            with self.assertRaises(ValueError):
                peer._load_bounded_owner_private_config(state)
            self.assertTrue(source.is_symlink())
        # Unsafe modes: any group/other permission bit fails closed.
        for mode in (0o640, 0o644, 0o666, 0o641, 0o604):
            temporary, state, source = fresh_state()
            with temporary:
                source.write_bytes(b'{"model":"a"}\n')
                os.chmod(source, mode)
                with self.assertRaises(ValueError):
                    peer._load_bounded_owner_private_config(state)
                self.assertTrue(source.exists())
        # A non-regular source (here: a directory) must be rejected.
        temporary, state, source = fresh_state()
        with temporary:
            source.mkdir()
            os.chmod(source, 0o700)
            with self.assertRaises(ValueError):
                peer._load_bounded_owner_private_config(state)
        # A hard-linked source must be rejected on its link count.
        temporary, state, source = fresh_state()
        with temporary:
            source.write_bytes(b'{"model":"a"}\n')
            os.chmod(source, 0o600)
            os.link(source, state / "openclaw.json.custody-link")
            with self.assertRaises(ValueError):
                peer._load_bounded_owner_private_config(state)
            self.assertTrue(source.exists())

    @unittest.skipUnless(os.name == "posix", "sandboxed CLI routing checks require POSIX")
    def test_ask_sandboxed_cli_is_local_only_and_rejects_extra_arguments(self):
        """`ask-sandboxed` routes only as an exact two-argument local command.

        sys.argv[0] may be any absolute path; the exact two-argument invocation
        must delegate once to local_sandboxed_agent. Extra arguments, the
        `message-sandboxed` spelling, and attempted peer spellings must return
        usage/error without delegating or invoking send_message. stderr is
        captured so the suite stays quiet.
        """
        class Input:
            def __init__(self, value):
                self.buffer = io.BytesIO(value)

        payload = b'{"schemaVersion":1}'
        arbitrary_absolute_argv0 = "/bounded/owner/bin/pixel-mesh-peer"
        captured_stderr = io.StringIO()
        with patch.object(peer.sys, "argv", [arbitrary_absolute_argv0, "ask-sandboxed"]), \
             patch.object(peer.sys, "stdin", Input(payload)), \
             patch.object(peer, "local_sandboxed_agent", return_value=7) as sandboxed:
            self.assertEqual(peer.main(), 7)
        sandboxed.assert_called_once_with()

        with patch.object(peer.sys, "argv", [arbitrary_absolute_argv0, "ask-sandboxed", "tower2"]), \
             patch.object(peer.sys, "stdin", Input(payload)), \
             patch.object(peer, "local_sandboxed_agent") as sandboxed, \
             patch.object(peer, "send_message") as remote, \
             patch.object(peer.sys, "stderr", captured_stderr):
            self.assertEqual(peer.main(), 2)
        sandboxed.assert_not_called()
        remote.assert_not_called()
        captured_stderr.seek(0)
        self.assertIn("usage:", captured_stderr.read())

        with patch.object(peer.sys, "argv", [arbitrary_absolute_argv0, "message-sandboxed", "tower2"]), \
             patch.object(peer.sys, "stdin", Input(payload)), \
             patch.object(peer, "local_sandboxed_agent") as sandboxed, \
             patch.object(peer, "send_message") as remote, \
             patch.object(peer.sys, "stderr", captured_stderr):
            self.assertEqual(peer.main(), 2)
        sandboxed.assert_not_called()
        remote.assert_not_called()

        for spelling in ("ask-sandboxed tower2", "ask-sandboxed-tower2", "tower2 ask-sandboxed"):
            argv = [arbitrary_absolute_argv0, *spelling.split(" ")]
            with patch.object(peer.sys, "argv", argv), \
                 patch.object(peer.sys, "stdin", Input(payload)), \
                 patch.object(peer, "local_sandboxed_agent") as sandboxed, \
                 patch.object(peer, "send_message") as remote, \
                 patch.object(peer.sys, "stderr", captured_stderr):
                self.assertEqual(peer.main(), 2)
            sandboxed.assert_not_called()
            remote.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "sandboxed git discovery checks require POSIX")
    def test_git_discovery_uses_sanitized_fixed_argv_and_rejects_nested_workspace(self):
        """Git discovery uses sanitized fixed argv and rejects a nested cwd.

        A private workspace holds a safe .git directory; the process chdirs
        under a nested child. subprocess.run is patched to capture its
        invocation and return the parent workspace as git top-level. Multiple
        GIT_* variables are seeded. Validation must fail closed because the cwd
        is nested, the argv must be exactly `git -C <nested> rev-parse
        --show-toplevel` as a list, no shell execution may be requested, the
        captured env must contain no GIT_* keys, and stdout/stderr must be
        bounded pipes with fixed timeout/check values.
        """
        self.assertEqual(os.geteuid(), os.getuid(), "effective uid must not be root")
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            workspace = self._owner_private_directory(root, "worktree")
            git_entry = workspace / ".git"
            git_entry.mkdir()
            os.chmod(git_entry, 0o700)
            nested = self._owner_private_directory(workspace, "nested-child")
            previous_cwd = os.getcwd()
            os.chdir(nested)
            try:
                with patch.dict(peer.os.environ, {
                    "GIT_DIR": "/bounded/attacker/repo.git",
                    "GIT_WORK_TREE": "/bounded/attacker/tree",
                    "GIT_CONFIG_GLOBAL": "/bounded/attacker/config",
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "url.insteadOf",
                    "GIT_CONFIG_VALUE_0": "https://attacker.invalid/",
                    "GIT_ALLOW_PROTOCOL": "file",
                }, clear=False):
                    captured: dict[str, object] = {}

                    def capture_run(argv, **options):
                        captured["argv"] = list(argv)
                        captured["options"] = options
                        return subprocess.CompletedProcess(
                            args=list(argv),
                            returncode=0,
                            stdout=(str(workspace) + "\n").encode("utf-8"),
                            stderr=b"",
                        )

                    with patch.object(peer.subprocess, "run", side_effect=capture_run):
                        with self.assertRaises(ValueError):
                            peer._validated_sandboxed_workspace()
                argv = captured["argv"]
                options = captured["options"]
                self.assertIsInstance(argv, list)
                self.assertEqual(argv, ["git", "-C", str(nested), "rev-parse", "--show-toplevel"])
                self.assertNotIn("shell", options)
                self.assertIsNone(options.get("shell"))
                git_env = options["env"]
                self.assertIsInstance(git_env, dict)
                self.assertFalse(
                    [key for key in git_env if key.startswith("GIT_")],
                    "git discovery environment must not inherit GIT_* variables",
                )
                self.assertIs(options["stdout"], subprocess.PIPE)
                self.assertIs(options["stderr"], subprocess.PIPE)
                self.assertEqual(options["timeout"], 10)
                self.assertFalse(options["check"])
            finally:
                os.chdir(previous_cwd)
            self.assertTrue(workspace.is_dir())
            self.assertTrue(git_entry.is_dir())

    @unittest.skipUnless(os.name == "posix", "sandboxed git discovery checks require POSIX")
    def test_normal_repository_validation_returns_no_git_metadata_binding(self):
        """A normal private `.git` directory must never add a host metadata bind."""
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            workspace = self._owner_private_directory(root, "repository")
            git_entry = self._owner_private_directory(workspace, ".git")
            fake_top = subprocess.CompletedProcess(
                args=["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
                returncode=0, stdout=(str(workspace) + "\n").encode("utf-8"), stderr=b"",
            )
            previous_cwd = os.getcwd()
            os.chdir(workspace)
            try:
                with patch.object(peer.subprocess, "run", return_value=fake_top) as run:
                    validated_workspace, _, _, binding = peer._validated_sandboxed_workspace()
            finally:
                os.chdir(previous_cwd)
            self.assertEqual(validated_workspace, workspace)
            self.assertIsNone(binding)
            run.assert_called_once()
            self.assertTrue(git_entry.is_dir())

    def test_sandbox_authority_optional_fields_cannot_overwrite_allowed_tools(self):
        """Optional evidence fields must not overwrite the allowedTools core claim."""
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            state = self._owner_private_directory(root, "state")
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            session = sessions / "00000000-0000-4000-8000-000000000000.jsonl"
            session.write_text(
                json.dumps({"message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}}) + "\n",
                encoding="utf-8",
            )
            os.chmod(session, 0o600)
            conflicting_allowed_tools = [*peer.SANDBOXED_ALLOWED_TOOLS, "deploy-live"]
            forged_optional_evidence = {
                "profile": "sandboxed-workspace",
                "configSha256": "f" * 64,
                "workspaceIdentitySha256": "f" * 64,
                "freshSession": True,
                "networkEnabled": False,
                "hostFilesystemAuthority": False,
                "externalEffectAuthority": False,
                "acceptanceAuthority": False,
                "allowedTools": conflicting_allowed_tools,
            }
            with self.assertRaises(ValueError):
                peer.read_only_authority_evidence(
                    session, state, "f" * 64,
                    profile="sandboxed-workspace",
                    allowed_tools=peer.SANDBOXED_ALLOWED_TOOLS,
                    extra_evidence=forged_optional_evidence,
                    workspace_identity_revalidator=lambda: None,
                )

    def test_parse_sandboxed_allowed_tools_identity_order_and_failures(self):
        """parse_sandboxed_allowed_tools: identity default, ordered tuples, closed failures."""
        self.assertIs(peer.parse_sandboxed_allowed_tools(None), peer.SANDBOXED_ALLOWED_TOOLS)
        narrowed = peer.parse_sandboxed_allowed_tools("read,edit")
        self.assertEqual(narrowed, ("read", "edit"))
        self.assertIsInstance(narrowed, tuple)
        self.assertEqual(peer.parse_sandboxed_allowed_tools("edit,read"), ("edit", "read"))
        for invalid in (
            "",
            "read,,edit",
            ",read",
            "read,",
            " read,edit",
            "read,edit ",
            "read ,edit",
            "read\t,edit",
            "read,read",
            "read,edit,read",
            "Read,edit",
            "read,Edit",
            "read,deploy",
            "read," + "read," * 65,
            "\ud800",
        ):
            with self.subTest(raw=invalid):
                with self.assertRaisesRegex(
                    ValueError, re.escape(peer.SANDBOXED_ALLOWED_TOOLS_ENV),
                ):
                    peer.parse_sandboxed_allowed_tools(invalid)

    @unittest.skipUnless(os.name == "posix", "owner-private sandboxed config custody is POSIX-only")
    def test_create_sandboxed_config_writes_narrowed_allowlist_and_fails_closed(self):
        """allowed_tools=("read","edit") narrows both allowlists; invalid input leaves no residue."""
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            state = root / "state"
            state.mkdir(mode=0o700)
            source = state / "openclaw.json"
            source.write_text(json.dumps({
                "agents": {
                    "defaults": {"preExisting": "keep-me"},
                    "list": [
                        {"id": "other-agent", "workspace": "/bounded/other",
                         "tools": {"allow": ["read"]}},
                        {"id": "pixel", "model": "dream-fleet-agent"},
                    ],
                },
                "tools": {"profile": "coding"},
            }) + "\n", encoding="utf-8")
            os.chmod(source, 0o600)
            source_payload = source.read_bytes()
            workspace = root / "worktree"
            workspace.mkdir(mode=0o700)
            pointer = workspace / ".git"
            pointer.write_text("gitdir: /bounded/owner/repos/custody.git\n", encoding="utf-8")
            os.chmod(pointer, 0o600)
            ws_info = workspace.lstat()
            git_info = pointer.lstat()
            workspace_identity = (
                ws_info.st_dev, ws_info.st_ino, stat.S_IMODE(ws_info.st_mode), ws_info.st_uid,
            )
            git_identity = (
                git_info.st_dev, git_info.st_ino, stat.S_IMODE(git_info.st_mode),
                git_info.st_uid, git_info.st_nlink,
            )
            created = []

            def remove_created():
                while created:
                    path, identity = created.pop()
                    peer._remove_and_verify_ephemeral_config(path, identity)

            try:
                narrowed_path, _, narrowed_identity, _ = peer.create_sandboxed_config(
                    state, workspace, workspace_identity, git_identity,
                    allowed_tools=("read", "edit"),
                )
                created.append((narrowed_path, narrowed_identity))
                config = json.loads(narrowed_path.read_text(encoding="utf-8"))
                pixel = next(a for a in config["agents"]["list"] if a["id"] == "pixel")
                self.assertEqual(pixel["tools"], {"allow": ["read", "edit"]})
                self.assertEqual(
                    config["tools"]["sandbox"],
                    {"tools": {"allow": ["read", "edit"], "deny": ["image"]}},
                )
                self.assertEqual(source.read_bytes(), source_payload)
                remove_created()

                default_path, _, default_identity, _ = peer.create_sandboxed_config(
                    state, workspace, workspace_identity, git_identity,
                )
                created.append((default_path, default_identity))
                default_config = json.loads(default_path.read_text(encoding="utf-8"))
                default_pixel = next(
                    a for a in default_config["agents"]["list"] if a["id"] == "pixel"
                )
                self.assertEqual(
                    default_pixel["tools"], {"allow": list(peer.SANDBOXED_ALLOWED_TOOLS)},
                )
                self.assertEqual(
                    default_config["tools"]["sandbox"],
                    {"tools": {"allow": list(peer.SANDBOXED_ALLOWED_TOOLS), "deny": ["image"]}},
                )
                remove_created()

                for invalid in ((), ["read"], ("read", "read"), ("read", "deploy")):
                    with self.subTest(invalid=invalid):
                        with self.assertRaises(ValueError):
                            peer.create_sandboxed_config(
                                state, workspace, workspace_identity, git_identity,
                                allowed_tools=invalid,
                            )
                        self.assertEqual(list(state.glob(".pixel-mesh-sandboxed-*")), [])
                        self.assertEqual(source.read_bytes(), source_payload)
            finally:
                remove_created()
                for leftover in state.glob(".pixel-mesh-sandboxed-*"):
                    leftover.unlink(missing_ok=True)

    def test_local_sandboxed_agent_propagates_exact_narrowed_allowlist(self):
        """local_sandboxed_agent passes the parsed tuple with the existing sandbox identity."""
        workspace = Path("/fixture/pixel-mesh-narrowed-workspace")
        workspace_identity = (11, 12, 0o700, 1000)
        git_identity = (11, 13, 0o600, 1000, 1)
        git_metadata_binding = None

        class Input:
            def __init__(self, value):
                self.buffer = io.BytesIO(value)

        with patch.dict(os.environ, {peer.SANDBOXED_ALLOWED_TOOLS_ENV: "read,edit"}, clear=False), \
             patch.object(peer, "_validated_sandboxed_workspace",
                          return_value=(workspace, workspace_identity, git_identity,
                                        git_metadata_binding)), \
             patch.object(peer, "local_agent", return_value=5) as local, \
             patch.object(peer.sys, "stdin", Input(b"narrowed objective")):
            self.assertEqual(peer.local_sandboxed_agent(), 5)
        local.assert_called_once_with(
            b"narrowed objective",
            sandboxed=True,
            sandboxed_workspace=workspace,
            sandboxed_workspace_identity=workspace_identity,
            sandboxed_git_identity=git_identity,
            sandboxed_git_metadata_binding=git_metadata_binding,
            sandboxed_allowed_tools=("read", "edit"),
            sandboxed_model_pin=None,
        )

        with patch.dict(os.environ, {peer.SANDBOXED_ALLOWED_TOOLS_ENV: "read,deploy"}, clear=False), \
             patch.object(peer, "_validated_sandboxed_workspace",
                          return_value=(workspace, workspace_identity, git_identity,
                                        git_metadata_binding)), \
             patch.object(peer, "local_agent", return_value=5) as local, \
             patch.object(peer.sys, "stdin", Input(b"narrowed objective")):
            with self.assertRaises(ValueError):
                peer.local_sandboxed_agent()
        local.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "owner-private transcript custody is POSIX-only")
    def test_narrowed_allowlist_drives_transcript_authority_evidence(self):
        """A narrowed tuple is the transcript authority ceiling in both directions."""
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            state = root / "state"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            os.chmod(state, 0o700)

            def write_transcript(name, tool):
                transcript = sessions / name
                transcript.write_text(json.dumps({"message": {"role": "assistant", "content": [
                    {"type": "toolCall", "name": tool, "arguments": {"path": "/fixture"}},
                ]}}) + "\n", encoding="utf-8")
                os.chmod(transcript, 0o600)
                return transcript

            sandboxed_extra = {
                "workspaceIdentitySha256": "b" * 64,
                "freshSession": True,
                "networkEnabled": False,
                "hostFilesystemAuthority": False,
                "hostGitMetadataReadAuthority": True,
                "openClawExternalBindSourceOverride": True,
                "sandboxRuntimeRemovedBeforeSuccess": True,
                "sandboxWorkspaceControlPathRestoredBeforeSuccess": True,
                "acceptanceAuthority": False,
            }
            narrowed = write_transcript(
                "00000000-0000-4000-8000-000000000001.jsonl", "read",
            )
            evidence = peer.read_only_authority_evidence(
                narrowed, state, "a" * 64,
                profile="sandboxed-workspace",
                allowed_tools=("read", "edit"),
                extra_evidence=dict(sandboxed_extra),
                workspace_identity_revalidator=lambda: None,
            )
            self.assertEqual(evidence["profile"], "sandboxed-workspace")
            self.assertEqual(evidence["allowedTools"], ["read", "edit"])
            self.assertEqual(evidence["observedTools"], ["read"])
            self.assertTrue(evidence["hostGitMetadataReadAuthority"])
            self.assertTrue(evidence["openClawExternalBindSourceOverride"])
            self.assertTrue(evidence["sandboxRuntimeRemovedBeforeSuccess"])
            self.assertTrue(
                evidence["sandboxWorkspaceControlPathRestoredBeforeSuccess"],
            )

            exec_transcript = write_transcript(
                "00000000-0000-4000-8000-000000000002.jsonl", "exec",
            )
            with self.assertRaisesRegex(ValueError, "outside its authority"):
                peer.read_only_authority_evidence(
                    exec_transcript, state, "a" * 64,
                    profile="sandboxed-workspace",
                    allowed_tools=("read",),
                    extra_evidence=dict(sandboxed_extra),
                    workspace_identity_revalidator=lambda: None,
                )

            forged = write_transcript(
                "00000000-0000-4000-8000-000000000003.jsonl", "read",
            )
            with self.assertRaisesRegex(ValueError, "overlapped a core evidence key"):
                peer.read_only_authority_evidence(
                    forged, state, "a" * 64,
                    profile="sandboxed-workspace",
                    allowed_tools=("read",),
                    extra_evidence={**sandboxed_extra, "allowedTools": ["read", "exec"]},
                    workspace_identity_revalidator=lambda: None,
                )


@unittest.skipUnless(os.name == "posix", "execution-budget tests require POSIX")
class ExecutionBudgetAdversarialTests(unittest.TestCase):
    """PXL-LIVE-159 tests-only adversarial qualification of execution-budget source candidate."""

    # --- 1. selected_execution_budget() strict grammar ---

    def test_selected_execution_budget_defaults(self):
        env = {}
        with patch.dict(os.environ, env, clear=True):
            timeout, model, tool, source = peer.selected_execution_budget()
        self.assertEqual(timeout, peer.AGENT_TIMEOUT_SECONDS)
        self.assertEqual(model, peer.MAX_AGENT_MODEL_CALLS)
        self.assertEqual(tool, peer.MAX_AGENT_TOOL_CALLS)
        self.assertEqual(source, "defaults")

    def test_selected_execution_budget_valid_single_overrides(self):
        for env_name, expected_default in [
            (peer.EXECUTION_BUDGET_TIMEOUT_ENV, peer.AGENT_TIMEOUT_SECONDS),
            (peer.EXECUTION_BUDGET_MAX_MODEL_CALLS_ENV, peer.MAX_AGENT_MODEL_CALLS),
            (peer.EXECUTION_BUDGET_MAX_TOOL_CALLS_ENV, peer.MAX_AGENT_TOOL_CALLS),
        ]:
            with self.subTest(env=env_name):
                with patch.dict(os.environ, {env_name: "1"}, clear=True):
                    result = peer.selected_execution_budget()
                self.assertEqual(result[3], "owner-process-environment")
                # Check the overridden value is 1, others are defaults
                idx = 0 if env_name == peer.EXECUTION_BUDGET_TIMEOUT_ENV else (1 if env_name == peer.EXECUTION_BUDGET_MAX_MODEL_CALLS_ENV else 2)
                self.assertEqual(result[idx], 1)

    def test_selected_execution_budget_valid_mixed_overrides(self):
        with patch.dict(os.environ, {
            peer.EXECUTION_BUDGET_TIMEOUT_ENV: "600",
            peer.EXECUTION_BUDGET_MAX_MODEL_CALLS_ENV: "50",
            peer.EXECUTION_BUDGET_MAX_TOOL_CALLS_ENV: "30",
        }, clear=True):
            timeout, model, tool, source = peer.selected_execution_budget()
        self.assertEqual(timeout, 600)
        self.assertEqual(model, 50)
        self.assertEqual(tool, 30)
        self.assertEqual(source, "owner-process-environment")

    def _strict_error(self, name: str, default: int) -> str:
        return f"{name} must be an integer between 1 and {default} (decimal digits only)"

    def test_selected_execution_budget_strict_rejections_per_variable(self):
        """Each env variable independently rejects: empty, zero, leading zero, sign, whitespace/newline, ASCII junk, Unicode digit, 17-byte decimal, default+1."""
        invalids: list[tuple[str, str]] = [
            ("empty", ""),
            ("zero", "0"),
            ("leading_zero", "01"),
            ("sign_plus", "+1"),
            ("sign_minus", "-1"),
            ("leading_ws", " 1"),
            ("trailing_ws", "1 "),
            ("newline", "1\n"),
            ("ascii_junk", "abc"),
            ("unicode_digit", "١"),
            ("17byte_decimal", "12345678901234567"),
        ]
        for env_name, default in [
            (peer.EXECUTION_BUDGET_TIMEOUT_ENV, peer.AGENT_TIMEOUT_SECONDS),
            (peer.EXECUTION_BUDGET_MAX_MODEL_CALLS_ENV, peer.MAX_AGENT_MODEL_CALLS),
            (peer.EXECUTION_BUDGET_MAX_TOOL_CALLS_ENV, peer.MAX_AGENT_TOOL_CALLS),
        ]:
            with self.subTest(env=env_name):
                default_plus_1 = str(default + 1)
                test_values = {name: val for name, val in invalids}
                test_values["default_plus_1"] = default_plus_1
                for label, value in test_values.items():
                    with self.subTest(label=label):
                        with patch.dict(os.environ, {env_name: value}, clear=True):
                            with self.assertRaisesRegex(ValueError, re.escape(self._strict_error(env_name, default))):
                                peer.selected_execution_budget()

    # --- 2. Capture each env value exactly once ---

    def test_selected_execution_budget_captures_each_env_once(self):
        """Each budget variable is read exactly once via .get() on one controlled mapping.

        Any membership/index/later read of the mapped environment raises, so the
        source label must derive from the three already-captured raw values.
        """
        values = {
            peer.EXECUTION_BUDGET_TIMEOUT_ENV: "500",
            peer.EXECUTION_BUDGET_MAX_MODEL_CALLS_ENV: "40",
            peer.EXECUTION_BUDGET_MAX_TOOL_CALLS_ENV: "25",
        }

        class OnceEnvironment(Mapping):
            def __init__(self):
                self.gets: dict[str, int] = {}

            def __getitem__(self, name):
                raise AssertionError(f"environment indexed for {name!r}")

            def __contains__(self, name):
                raise AssertionError(f"environment membership probed for {name!r}")

            def __iter__(self):
                raise AssertionError("environment iterated")

            def __len__(self):
                raise AssertionError("environment sized")

            def get(self, name, default=None):
                if name not in values:
                    raise AssertionError(f"unexpected environment read for {name!r}")
                self.gets[name] = self.gets.get(name, 0) + 1
                if self.gets[name] > 1:
                    raise AssertionError(f"{name} read {self.gets[name]} times instead of 1")
                return values[name]

        environment = OnceEnvironment()
        with patch.object(peer.os, "environ", environment):
            timeout, model, tool, source = peer.selected_execution_budget()

        self.assertEqual(timeout, 500)
        self.assertEqual(model, 40)
        self.assertEqual(tool, 25)
        self.assertEqual(source, "owner-process-environment")
        self.assertEqual(
            environment.gets,
            {name: 1 for name in values},
        )

    # --- 3. AgentSessionBudget explicit limits ---

    def test_agent_session_budget_rejects_invalid_limits(self):
        """Reject bool, float, zero, negative, and default+1 for both limit keywords."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "session.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            path.chmod(0o600)
            for keyword, default in [
                ("max_model_calls", peer.MAX_AGENT_MODEL_CALLS),
                ("max_tool_calls", peer.MAX_AGENT_TOOL_CALLS),
            ]:
                for label, value in [
                    ("none", None),
                    ("bool_true", True),
                    ("bool_false", False),
                    ("float", 1.0),
                    ("zero", 0),
                    ("negative", -1),
                    ("default_plus_1", default + 1),
                ]:
                    with self.subTest(keyword=keyword, label=label):
                        with self.assertRaises(ValueError):
                            peer.AgentSessionBudget(path, **{keyword: value})

    def test_agent_session_budget_limits_within_range(self):
        """Accept non-bool integers 1..module default."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "session.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            path.chmod(0o600)
            budget = peer.AgentSessionBudget(path, max_model_calls=1, max_tool_calls=1)
            self.assertEqual(budget.max_model_calls, 1)
            self.assertEqual(budget.max_tool_calls, 1)
            budget2 = peer.AgentSessionBudget(path, max_model_calls=peer.MAX_AGENT_MODEL_CALLS, max_tool_calls=peer.MAX_AGENT_TOOL_CALLS)
            self.assertEqual(budget2.max_model_calls, peer.MAX_AGENT_MODEL_CALLS)
            self.assertEqual(budget2.max_tool_calls, peer.MAX_AGENT_TOOL_CALLS)

    def test_agent_session_budget_transcript_counting_exact_threshold(self):
        """Replay-safe transcript counting reaches the exact selected diagnostic threshold.

        Each transcript reaches one selected ceiling while the other remains
        below its selected ceiling, so only one diagnostic can win.
        """
        def records_for(model_calls: int, tool_calls: int):
            records = []
            for i in range(model_calls):
                content = (
                    [{"type": "toolCall", "id": f"tool-{i}", "name": "read"}]
                    if i < tool_calls else
                    [{"type": "text", "text": f"visible-{i}"}]
                )
                records.append({
                    "type": "message", "message": {
                        "role": "assistant", "api": "openai-completions",
                        "provider": "local", "model": "test",
                        "responseId": f"resp-{i}", "stopReason": "stop",
                        "content": content,
                    }
                })
            return records

        with tempfile.TemporaryDirectory() as d:
            transcript = Path(d) / "session.jsonl"
            records = records_for(5, 3)
            transcript.write_text(
                "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8",
            )
            transcript.chmod(0o600)
            budget = peer.AgentSessionBudget(
                transcript, max_model_calls=5, max_tool_calls=8,
            )
            self.assertEqual(budget.observe(), "model-call limit reached (5)")
            self.assertEqual(budget.model_calls, 5)
            self.assertEqual(budget.tool_calls, 3)

            records = records_for(4, 4)
            transcript.write_text(
                "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8",
            )
            budget2 = peer.AgentSessionBudget(
                transcript, max_model_calls=5, max_tool_calls=4,
            )
            self.assertEqual(budget2.observe(), "tool-call limit reached (4)")
            self.assertEqual(budget2.model_calls, 4)
            self.assertEqual(budget2.tool_calls, 4)

    # --- 4. _attach_selected_execution_budget ---

    def test_attach_selected_execution_budget_valid_object(self):
        evidence: dict[str, object] = {"profile": "read-only"}
        selected = {
            "timeoutSeconds": 600,
            "maxModelCalls": 50,
            "maxToolCalls": 30,
            "source": "owner-process-environment",
        }
        peer._attach_selected_execution_budget(evidence, selected)
        self.assertEqual(evidence["executionBudget"]["timeoutSeconds"], 600)
        self.assertEqual(evidence["executionBudget"]["maxModelCalls"], 50)
        self.assertEqual(evidence["executionBudget"]["maxToolCalls"], 30)
        self.assertEqual(evidence["executionBudget"]["source"], "owner-process-environment")

    def test_attach_selected_execution_budget_none_skips(self):
        evidence: dict[str, object] = {"profile": "read-only"}
        peer._attach_selected_execution_budget(evidence, None)
        self.assertNotIn("executionBudget", evidence)

    def test_attach_selected_execution_budget_rejects_invalid_shapes(self):
        base = {
            "timeoutSeconds": 1800,
            "maxModelCalls": 96,
            "maxToolCalls": 96,
            "source": "defaults",
        }
        # Non-dict/list inputs fail closed with the same stable ValueError.
        for non_dict in ("not-a-dict", [1, 2, 3]):
            with self.assertRaisesRegex(
                ValueError,
                re.escape("selected execution-budget evidence drifted from its exact shape"),
            ):
                peer._attach_selected_execution_budget({}, non_dict)
        # bool value (isinstance(int, True) is True in Python, so 1 <= True <= 1800
        # passes the range check; but not isinstance(True, bool) fails in shape_valid
        with self.assertRaisesRegex(ValueError, "drifted"):
            peer._attach_selected_execution_budget({}, {**base, "timeoutSeconds": True})
        with self.assertRaisesRegex(ValueError, "drifted"):
            peer._attach_selected_execution_budget({}, {**base, "maxModelCalls": False})
        # float value
        with self.assertRaisesRegex(ValueError, "drifted"):
            peer._attach_selected_execution_budget({}, {**base, "maxToolCalls": 1.0})
        # missing key
        reduced = {k: v for k, v in base.items() if k != "source"}
        with self.assertRaisesRegex(ValueError, "drifted"):
            peer._attach_selected_execution_budget({}, reduced)
        # extra key
        with self.assertRaisesRegex(ValueError, "drifted"):
            peer._attach_selected_execution_budget({}, {**base, "extraKey": 1})
        # invalid source
        with self.assertRaisesRegex(ValueError, "drifted"):
            peer._attach_selected_execution_budget({}, {**base, "source": "prompt"})
        # out-of-range timeout
        with self.assertRaisesRegex(ValueError, "drifted"):
            peer._attach_selected_execution_budget({}, {**base, "timeoutSeconds": 0})
        with self.assertRaisesRegex(ValueError, "drifted"):
            peer._attach_selected_execution_budget({}, {**base, "timeoutSeconds": peer.AGENT_TIMEOUT_SECONDS + 1})
        # out-of-range model calls
        with self.assertRaisesRegex(ValueError, "drifted"):
            peer._attach_selected_execution_budget({}, {**base, "maxModelCalls": 0})
        with self.assertRaisesRegex(ValueError, "drifted"):
            peer._attach_selected_execution_budget({}, {**base, "maxModelCalls": peer.MAX_AGENT_MODEL_CALLS + 1})
        # out-of-range tool calls
        with self.assertRaisesRegex(ValueError, "drifted"):
            peer._attach_selected_execution_budget({}, {**base, "maxToolCalls": 0})
        with self.assertRaisesRegex(ValueError, "drifted"):
            peer._attach_selected_execution_budget({}, {**base, "maxToolCalls": peer.MAX_AGENT_TOOL_CALLS + 1})
        # defaults with non-default values (coherence check)
        with self.assertRaisesRegex(ValueError, "drifted"):
            peer._attach_selected_execution_budget({}, {**base, "timeoutSeconds": 600})

    def test_attach_selected_execution_budget_copies_not_aliases(self):
        evidence: dict[str, object] = {"profile": "read-only"}
        selected = {
            "timeoutSeconds": 600,
            "maxModelCalls": 50,
            "maxToolCalls": 30,
            "source": "owner-process-environment",
        }
        peer._attach_selected_execution_budget(evidence, selected)
        attached = evidence["executionBudget"]
        self.assertIsNot(attached, selected)
        selected["timeoutSeconds"] = 9999
        self.assertEqual(attached["timeoutSeconds"], 600)

    def test_attach_selected_execution_budget_extra_evidence_overlap_fails(self):
        """A valid selected budget is attached, then extra_evidence overlap fails closed."""
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            state = root / "state"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            os.chmod(state, 0o700)
            transcript = sessions / "00000000-0000-4000-8000-000000000009.jsonl"
            transcript.write_text(
                json.dumps({"message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}}) + "\n",
                encoding="utf-8",
            )
            os.chmod(transcript, 0o600)
            selected = {
                "timeoutSeconds": 600,
                "maxModelCalls": 50,
                "maxToolCalls": 30,
                "source": "owner-process-environment",
            }
            evidence = peer.read_only_authority_evidence(
                transcript, state, "a" * 64,
                execution_budget=selected,
            )
            self.assertEqual(evidence["executionBudget"], selected)
            # The same key supplied through extra_evidence overlaps the attached
            # budget and must fail closed, proving the overlap check is real.
            with self.assertRaisesRegex(ValueError, "overlapped a core evidence key"):
                peer.read_only_authority_evidence(
                    transcript, state, "a" * 64,
                    execution_budget=selected,
                    extra_evidence={"executionBudget": dict(selected, timeoutSeconds=1)},
                )

    # --- 5. selected-budget deterministic recovery ---

    def test_recoverable_budget_reason_uses_exact_selected_thresholds(self):
        def completed(returncode: int, diagnostic: str):
            return subprocess.CompletedProcess(
                [], returncode, b"", diagnostic.encode("utf-8"),
            )

        model = completed(
            124, "Pixel mesh agent budget exceeded: model-call limit reached (7)\n",
        )
        tool = completed(
            124, "Pixel mesh agent budget exceeded: tool-call limit reached (5)\n",
        )
        self.assertEqual(
            peer.recoverable_budget_synthesis_reason(model, 7, 5),
            peer.SELECTED_MODEL_BUDGET_RECOVERY,
        )
        self.assertEqual(
            peer.recoverable_budget_synthesis_reason(tool, 7, 5),
            peer.SELECTED_TOOL_BUDGET_RECOVERY,
        )
        for diagnostic in (
            "Pixel mesh agent budget exceeded: model-call limit reached (6)\n",
            "Pixel mesh agent budget exceeded: model-call limit reached (8)\n",
            "Pixel mesh agent budget exceeded: tool-call limit reached (4)\n",
            "Pixel mesh agent budget exceeded: tool-call limit reached (6)\n",
        ):
            with self.subTest(diagnostic=diagnostic):
                self.assertIsNone(peer.recoverable_budget_synthesis_reason(
                    completed(124, diagnostic), 7, 5,
                ))
        self.assertIsNone(peer.recoverable_budget_synthesis_reason(
            completed(1, "Pixel mesh agent budget exceeded: tool-call limit reached (5)\n"),
            7, 5,
        ))
        for keyword, default in (
            ("max_model_calls", peer.MAX_AGENT_MODEL_CALLS),
            ("max_tool_calls", peer.MAX_AGENT_TOOL_CALLS),
        ):
            for value in (None, True, 1.0, 0, -1, default + 1):
                options = {"max_model_calls": 7, "max_tool_calls": 5, keyword: value}
                with self.subTest(keyword=keyword, value=value):
                    with self.assertRaises(ValueError):
                        peer.recoverable_budget_synthesis_reason(model, **options)

    def test_deterministic_selected_budget_recovery_is_bound_and_zero_authority(self):
        hidden = "HIDDEN-REASONING-MUST-NOT-ESCAPE"
        for reason in (
            peer.SELECTED_MODEL_BUDGET_RECOVERY,
            peer.SELECTED_TOOL_BUDGET_RECOVERY,
        ):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as raw_root:
                state = Path(raw_root) / "state"
                sessions = state / "agents" / "pixel" / "sessions"
                sessions.mkdir(parents=True)
                state.chmod(0o700)
                predecessor = sessions / f"{uuid.uuid4()}.jsonl"
                predecessor.write_text(json.dumps({
                    "type": "message", "message": {
                        "role": "assistant", "api": "openai-completions",
                        "provider": "tower", "model": "fixture",
                        "stopReason": "length",
                        "content": [{"type": "thinking", "thinking": hidden}],
                    },
                }) + "\n", encoding="utf-8")
                predecessor.chmod(0o600)
                predecessor_sha = hashlib.sha256(predecessor.read_bytes()).hexdigest()

                encoded = peer.run_deterministic_selected_budget_blocked_recovery(
                    predecessor, state, reason,
                )
                self.assertIsNotNone(encoded)
                self.assertNotIn(hidden.encode("utf-8"), encoded)
                envelope = json.loads(encoded)
                self.assertEqual(envelope["summary"], "deterministic-blocked-budget-recovery")
                payload = envelope["result"]["payloads"][0]
                self.assertTrue(payload["blocked"])
                meta = envelope["result"]["meta"]
                self.assertTrue(meta["blocked"])
                self.assertEqual(meta["agentMeta"]["usage"], {
                    "input": 0, "cacheRead": 0, "cacheWrite": 0,
                    "output": 0, "totalTokens": 0,
                })
                self.assertEqual(meta["agentMeta"]["predecessorSessionFile"], str(predecessor))
                self.assertEqual(meta["agentMeta"]["predecessorSessionSha256"], predecessor_sha)
                recovery = meta["meshSynthesisRecovery"]
                self.assertEqual(recovery["reason"], reason)
                self.assertEqual(recovery["mechanism"], "deterministic-no-model-blocked")
                self.assertEqual(recovery["toolAuthority"], "none")
                self.assertFalse(recovery["externalEffectAuthority"])
                self.assertFalse(recovery["completionAuthority"])
                self.assertFalse(recovery["acceptanceAuthority"])
                self.assertTrue(recovery["requiresIndependentVerification"])

                receipt = Path(meta["agentMeta"]["sessionFile"])
                receipt_info = receipt.stat()
                self.assertEqual(stat.S_IMODE(receipt_info.st_mode), 0o600)
                self.assertEqual(receipt_info.st_nlink, 1)
                receipt_records = receipt.read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(receipt_records), 1)
                receipt_record = json.loads(receipt_records[0])
                self.assertEqual(receipt_record["meshSynthesis"], {
                    "reason": reason,
                    "predecessorSessionFile": str(predecessor),
                    "predecessorSessionSha256": predecessor_sha,
                    "toolSchemaPresent": False,
                    "transport": "deterministic-no-model",
                })
                self.assertNotIn(hidden, receipt.read_text(encoding="utf-8"))

    def test_deterministic_selected_budget_recovery_rejects_outside_or_empty(self):
        with tempfile.TemporaryDirectory() as raw_root:
            state = Path(raw_root) / "state"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            predecessor = sessions / f"{uuid.uuid4()}.jsonl"
            predecessor.write_text("{}\n", encoding="utf-8")
            predecessor.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "outside the closed set"):
                peer.run_deterministic_selected_budget_blocked_recovery(
                    predecessor, state, peer.REASONING_WITHOUT_VISIBLE_OUTPUT_RECOVERY,
                )
            self.assertFalse((state / "pixel-mesh-synthesis").exists())

            predecessor.write_bytes(b"")
            self.assertIsNone(peer.run_deterministic_selected_budget_blocked_recovery(
                predecessor, state, peer.SELECTED_TOOL_BUDGET_RECOVERY,
            ))
            self.assertFalse((state / "pixel-mesh-synthesis").exists())

    # --- 6. local_agent budget propagation and routing ---

    class _BytesOutput:
        """Minimal sys.stdout/sys.stderr stand-in exposing a .buffer stream."""

        def __init__(self):
            self.buffer = io.BytesIO()

        def write(self, value):
            encoded = value.encode("utf-8") if isinstance(value, str) else value
            self.buffer.write(encoded)
            return len(value)

        def flush(self):
            return None

    def _run_local_agent_with_budget(self, extra_env: dict[str, str]):
        """Run local_agent over the repository's temp-home/config/binary mocks.

        run_agent_process is a controlled spy: it records argv/env/timeout/
        budget keywords and returns one successful completed read-only turn.
        Exceptions are never swallowed.
        """
        fixed = uuid.UUID("00000000-0000-4000-8000-000000000100")
        captured: dict[str, object] = {}

        def capture_run(argv, env_vars, timeout, **kwargs):
            captured["argv"] = list(argv)
            captured["env"] = dict(env_vars)
            captured["timeout"] = timeout
            captured["max_model_calls"] = kwargs.get("max_model_calls")
            captured["max_tool_calls"] = kwargs.get("max_tool_calls")
            transcript = kwargs["session_path"]
            transcript.write_text(json.dumps({"message": {
                "role": "assistant", "api": "openai-completions", "provider": "tower",
                "model": "model", "stopReason": "stop",
                "content": [{"type": "toolCall", "id": "tool-1", "name": "read",
                             "arguments": {"path": "/fixture"}}],
            }}) + "\n", encoding="utf-8")
            transcript.chmod(0o600)
            response = {"payloads": [{"text": "done"}], "meta": {
                "agentMeta": {"sessionFile": str(transcript)},
                "livenessState": "working", "stopReason": "stop", "error": None,
            }}
            return subprocess.CompletedProcess([], 0, json.dumps(response).encode(), b"")

        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            source = state / "openclaw.json"
            source.write_text(json.dumps({
                "agents": {"list": [{"id": "pixel"}]}, "tools": {"profile": "coding"},
            }) + "\n", encoding="utf-8")
            source.chmod(0o600)
            environment = {
                peer.HOME_ENV: home,
                peer.OPENCLAW_BIN_ENV: "/tmp/openclaw-test",
                **extra_env,
            }
            output, errors = self._BytesOutput(), self._BytesOutput()
            with patch.dict(os.environ, environment, clear=False):
                with patch.object(peer.uuid, "uuid4", return_value=fixed):
                    with patch.object(peer, "run_agent_process", side_effect=capture_run) as run:
                        with patch.object(peer, "mesh_exec_shell", return_value=Path("/bin/sh")):
                            with patch.object(peer.sys, "stdout", output), patch.object(peer.sys, "stderr", errors):
                                self.assertEqual(
                                    peer.local_agent(b"budget read-only task", read_only=True), 0,
                                )
        self.assertEqual(errors.buffer.getvalue(), b"")
        return captured, run, output

    def _run_local_agent_selected_tool_exhaustion(self, *, contract: bool):
        """Return one mocked exact selected-tool exhaustion through local_agent."""
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            source = state / "openclaw.json"
            source.write_text(json.dumps({
                "agents": {"list": [{"id": "pixel"}]},
                "tools": {"profile": "coding"},
            }) + "\n", encoding="utf-8")
            source.chmod(0o600)

            def exhausted(_argv, _env_vars, _timeout, **kwargs):
                transcript = kwargs["session_path"]
                transcript.write_text(json.dumps({
                    "type": "message", "message": {
                        "role": "assistant", "api": "openai-completions",
                        "provider": "tower", "model": "fixture",
                        "stopReason": "stop",
                        "content": [{
                            "type": "toolCall", "id": "tool-1", "name": "read",
                            "arguments": {"path": "/fixture"},
                        }],
                    },
                }) + "\n", encoding="utf-8")
                transcript.chmod(0o600)
                return subprocess.CompletedProcess(
                    [], 124, b"",
                    b"Pixel mesh agent budget exceeded: tool-call limit reached (5)\n",
                )

            environment = {
                peer.HOME_ENV: home,
                peer.OPENCLAW_BIN_ENV: "/tmp/openclaw-test",
                peer.EXECUTION_BUDGET_TIMEOUT_ENV: "180",
                peer.EXECUTION_BUDGET_MAX_MODEL_CALLS_ENV: "20",
                peer.EXECUTION_BUDGET_MAX_TOOL_CALLS_ENV: "5",
            }
            message = b"budget read-only task"
            if contract:
                message = json.dumps({
                    "schemaVersion": 1,
                    "task": "budget read-only contract task",
                    "schemaName": "budget_result",
                    "schema": {
                        "type": "object",
                        "properties": {"ok": {"type": "boolean"}},
                        "required": ["ok"],
                        "additionalProperties": False,
                    },
                }).encode("utf-8")
            output, errors = self._BytesOutput(), self._BytesOutput()
            with patch.dict(os.environ, environment, clear=False):
                with patch.object(peer, "run_agent_process", side_effect=exhausted):
                    with patch.object(peer, "mesh_exec_shell", return_value=Path("/bin/sh")):
                        with patch.object(peer.sys, "stdout", output), patch.object(peer.sys, "stderr", errors):
                            if contract:
                                with patch.object(
                                    peer,
                                    "run_deterministic_selected_budget_blocked_recovery",
                                    side_effect=AssertionError(
                                        "contract exhaustion must not mint a blocked recovery receipt"
                                    ),
                                ):
                                    return_code = peer.local_agent(
                                        message, read_only=True, contract=True,
                                    )
                            else:
                                return_code = peer.local_agent(message, read_only=True)
            receipt_directory = state / "pixel-mesh-synthesis"
            receipts = list(receipt_directory.glob("*.jsonl")) if receipt_directory.exists() else []
            return return_code, output.buffer.getvalue(), errors.buffer.getvalue(), len(receipts)

    def test_local_agent_selected_tool_exhaustion_returns_authority_bound_blocked(self):
        return_code, output, errors, receipt_count = (
            self._run_local_agent_selected_tool_exhaustion(contract=False)
        )
        self.assertEqual(return_code, 0)
        self.assertEqual(errors, b"")
        self.assertEqual(receipt_count, 1)
        envelope = json.loads(output)
        self.assertEqual(envelope["summary"], "deterministic-blocked-budget-recovery")
        meta = envelope["result"]["meta"]
        self.assertEqual(
            meta["meshSynthesisRecovery"]["reason"],
            peer.SELECTED_TOOL_BUDGET_RECOVERY,
        )
        self.assertEqual(meta["meshAuthority"]["executionBudget"], {
            "timeoutSeconds": 180,
            "maxModelCalls": 20,
            "maxToolCalls": 5,
            "source": "owner-process-environment",
        })
        self.assertFalse(meta["meshAuthority"]["externalEffectAuthority"])
        self.assertFalse(meta["meshSynthesisRecovery"]["acceptanceAuthority"])

    def test_contract_selected_tool_exhaustion_fails_closed_without_receipt(self):
        return_code, output, errors, receipt_count = (
            self._run_local_agent_selected_tool_exhaustion(contract=True)
        )
        self.assertEqual(return_code, 2)
        self.assertEqual(output, b"")
        self.assertEqual(receipt_count, 0)
        diagnostic = json.loads(errors)
        self.assertEqual(diagnostic["error"], "mesh contract exploration failed closed")

    def test_local_agent_budget_propagation_to_openclaw_and_runner(self):
        """Valid 600/50/30 reach OpenClaw --timeout, runner 630, runner keyword ceilings, and the returned meshAuthority.executionBudget."""
        captured, run, output = self._run_local_agent_with_budget({
            peer.EXECUTION_BUDGET_TIMEOUT_ENV: "600",
            peer.EXECUTION_BUDGET_MAX_MODEL_CALLS_ENV: "50",
            peer.EXECUTION_BUDGET_MAX_TOOL_CALLS_ENV: "30",
        })
        argv = captured["argv"]
        self.assertEqual(argv[argv.index("--timeout") + 1], "600")
        self.assertEqual(captured["timeout"], 630)
        self.assertEqual(captured["max_model_calls"], 50)
        self.assertEqual(captured["max_tool_calls"], 30)
        parsed = json.loads(output.buffer.getvalue())
        authority = parsed["result"]["meta"]["meshAuthority"]
        self.assertEqual(authority["allowedTools"], ["read"])
        self.assertEqual(authority["executionBudget"], {
            "timeoutSeconds": 600,
            "maxModelCalls": 50,
            "maxToolCalls": 30,
            "source": "owner-process-environment",
        })

    def test_missing_env_preserves_defaults(self):
        """Missing budget env preserves the 1800/96/96 path with source:defaults."""
        captured, run, output = self._run_local_agent_with_budget({})
        argv = captured["argv"]
        self.assertEqual(argv[argv.index("--timeout") + 1], "1800")
        self.assertEqual(captured["timeout"], 1830)
        self.assertEqual(captured["max_model_calls"], 96)
        self.assertEqual(captured["max_tool_calls"], 96)
        parsed = json.loads(output.buffer.getvalue())
        authority = parsed["result"]["meta"]["meshAuthority"]
        self.assertEqual(authority["executionBudget"], {
            "timeoutSeconds": 1800,
            "maxModelCalls": 96,
            "maxToolCalls": 96,
            "source": "defaults",
        })

    def test_malformed_budget_env_fails_before_launch_paths(self):
        """One malformed env variable fails closed before every agent launch path."""
        raised: dict[str, object] = {}

        def refuse(name):
            def _raise(*_args, **_kwargs):
                raised[name] = True
                raise AssertionError(f"{name} must not be reached for a malformed budget")
            return _raise

        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            source = state / "openclaw.json"
            source.write_text(json.dumps({
                "agents": {"list": [{"id": "pixel"}]}, "tools": {"profile": "coding"},
            }) + "\n", encoding="utf-8")
            source.chmod(0o600)
            environment = {
                peer.HOME_ENV: home,
                peer.OPENCLAW_BIN_ENV: "/tmp/openclaw-test",
                peer.EXECUTION_BUDGET_MAX_MODEL_CALLS_ENV: "abc",
            }
            output, errors = self._BytesOutput(), self._BytesOutput()
            with patch.dict(os.environ, environment, clear=False):
                with patch.object(peer, "create_read_only_config", side_effect=refuse("create_read_only_config")):
                    with patch.object(peer.tempfile, "NamedTemporaryFile", side_effect=refuse("tempfile.NamedTemporaryFile")):
                        with patch.object(peer, "fresh_session_key", side_effect=refuse("fresh_session_key")):
                            with patch.object(peer, "run_agent_process", side_effect=refuse("run_agent_process")):
                                with patch.object(peer.sys, "stdout", output), patch.object(peer.sys, "stderr", errors):
                                    with self.assertRaisesRegex(ValueError, re.escape(self._strict_error(
                                        peer.EXECUTION_BUDGET_MAX_MODEL_CALLS_ENV, peer.MAX_AGENT_MODEL_CALLS,
                                    ))):
                                        peer.local_agent(b"malformed budget task", read_only=True)
        self.assertEqual(raised, {})
        self.assertEqual(output.buffer.getvalue(), b"")


class SandboxedReadonlyWorkspaceTests(unittest.TestCase):
    """PXL205: read-only sandboxed workspace tests."""

    def setUp(self):
        self.exec_shell_patcher = patch.object(
            peer, "mesh_exec_shell",
            return_value=Path("/tmp/pixel-mesh-test-exec-shell"),
        )
        self.exec_shell_patcher.start()
        self.addCleanup(self.exec_shell_patcher.stop)

    def test_sandboxed_readonly_allowed_tools_fixed(self):
        """SANDBOXED_READONLY_ALLOWED_TOOLS is exactly (read, exec)."""
        self.assertEqual(peer.SANDBOXED_READONLY_ALLOWED_TOOLS, ("read", "exec"))
        self.assertNotIn("write", peer.SANDBOXED_READONLY_ALLOWED_TOOLS)
        self.assertNotIn("edit", peer.SANDBOXED_READONLY_ALLOWED_TOOLS)
        self.assertNotIn("apply_patch", peer.SANDBOXED_READONLY_ALLOWED_TOOLS)
        self.assertNotIn("process", peer.SANDBOXED_READONLY_ALLOWED_TOOLS)

    @unittest.skipUnless(os.name == "posix", "sandboxed config is POSIX-only")
    def test_sandboxed_readonly_config_exact_ro_workspace_access(self):
        """create_sandboxed_config with workspace_access='ro' writes ro and disables applyPatch."""
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            state.mkdir(mode=0o700)
            source = state / "openclaw.json"
            original = {
                "agents": {
                    "defaults": {},
                    "list": [{"id": "pixel", "tools": {"allow": ["exec"]}}],
                },
                "tools": {"profile": "coding"},
            }
            source.write_text(json.dumps(original) + "\n", encoding="utf-8")
            source.chmod(0o600)
            workspace = Path(home) / "ws"
            workspace.mkdir()
            ws_stat = workspace.lstat()
            ws_identity = (ws_stat.st_dev, ws_stat.st_ino, stat.S_IMODE(ws_stat.st_mode), ws_stat.st_uid)
            git_entry = workspace / ".git"
            git_entry.write_text("gitdir: /tmp/common\n", encoding="utf-8")
            git_stat = git_entry.lstat()
            git_identity = (git_stat.st_dev, git_stat.st_ino, stat.S_IMODE(git_stat.st_mode), git_stat.st_uid, git_stat.st_nlink)

            with self.assertRaisesRegex(ValueError, "fixed read/exec allowlist"):
                peer.create_sandboxed_config(
                    state, workspace, ws_identity, git_identity,
                    allowed_tools=("read", "write"),
                    workspace_access="ro",
                )

            path, rendered_sha256, expected_identity, workspace_hash = (
                peer.create_sandboxed_config(
                    state, workspace, ws_identity, git_identity,
                    allowed_tools=peer.SANDBOXED_READONLY_ALLOWED_TOOLS,
                    workspace_access="ro",
                )
            )
            try:
                config = json.loads(path.read_text(encoding="utf-8"))
                pixel = config["agents"]["list"][0]

                # workspaceAccess must be ro
                self.assertEqual(pixel["sandbox"]["workspaceAccess"], "ro")

                # applyPatch must be disabled
                self.assertFalse(config["tools"]["exec"]["applyPatch"]["enabled"])

                # allowlist is read+exec only
                self.assertEqual(
                    pixel["tools"], {"allow": ["read", "exec"]},
                )
                self.assertEqual(
                    config["tools"]["sandbox"]["tools"]["allow"],
                    ["read", "exec"],
                )

                # Docker security fields unchanged
                docker = pixel["sandbox"]["docker"]
                self.assertTrue(docker["readOnlyRoot"])
                self.assertEqual(docker["network"], "none")
                self.assertEqual(docker["capDrop"], ["ALL"])
                self.assertEqual(docker["pidsLimit"], 256)
                self.assertEqual(docker["memory"], "8g")
                self.assertEqual(docker["cpus"], 4)
                self.assertEqual(hashlib.sha256(
                    path.read_bytes()
                ).hexdigest(), rendered_sha256)
            finally:
                peer._remove_and_verify_ephemeral_config(path, expected_identity)
                self.assertFalse(path.exists())

    @unittest.skipUnless(os.name == "posix", "sandboxed config is POSIX-only")
    def test_sandboxed_rw_config_keeps_apply_patch_true(self):
        """workspace_access='rw' keeps applyPatch.enabled=true."""
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            state.mkdir(mode=0o700)
            source = state / "openclaw.json"
            source.write_text(json.dumps({
                "agents": {"defaults": {}, "list": [{"id": "pixel"}]},
                "tools": {"profile": "coding"},
            }) + "\n", encoding="utf-8")
            source.chmod(0o600)
            workspace = Path(home) / "ws"
            workspace.mkdir()
            ws_stat = workspace.lstat()
            ws_identity = (ws_stat.st_dev, ws_stat.st_ino, stat.S_IMODE(ws_stat.st_mode), ws_stat.st_uid)
            git_entry = workspace / ".git"
            git_entry.write_text("gitdir: /tmp/common\n", encoding="utf-8")
            git_stat = git_entry.lstat()
            git_identity = (git_stat.st_dev, git_stat.st_ino, stat.S_IMODE(git_stat.st_mode), git_stat.st_uid, git_stat.st_nlink)

            path, _, expected_identity, _ = peer.create_sandboxed_config(
                state, workspace, ws_identity, git_identity,
                workspace_access="rw",
            )
            try:
                config = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    config["agents"]["list"][0]["sandbox"]["workspaceAccess"], "rw",
                )
                self.assertTrue(
                    config["tools"]["exec"]["applyPatch"]["enabled"],
                )
            finally:
                peer._remove_and_verify_ephemeral_config(path, expected_identity)

    def test_sandboxed_readonly_rejects_invalid_workspace_access(self):
        """create_sandboxed_config rejects anything other than 'rw' or 'ro'."""
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            state.mkdir(mode=0o700)
            source = state / "openclaw.json"
            source.write_text(json.dumps({
                "agents": {"defaults": {}, "list": [{"id": "pixel"}]},
                "tools": {"profile": "coding"},
            }) + "\n", encoding="utf-8")
            source.chmod(0o600)
            workspace = Path(home) / "ws"
            workspace.mkdir()
            ws_identity = (0, 0, 0o700, os.getuid())
            git_entry = workspace / ".git"
            git_entry.write_text("gitdir: /tmp/common\n", encoding="utf-8")
            git_identity = (0, 0, 0o644, os.getuid(), 1)

            for bad in ("ro-w", "RW", "ro ", "", "r/o", "rwx"):
                with self.assertRaisesRegex(ValueError, "must be exactly"):
                    peer.create_sandboxed_config(
                        state, workspace, ws_identity, git_identity,
                        workspace_access=bad,
                    )

    def test_sandboxed_readonly_flag_requires_sandboxed(self):
        """sandboxed_readonly without sandboxed=True fails closed."""
        with self.assertRaisesRegex(ValueError, "only supported with the sandboxed"):
            peer.local_agent(
                b"task",
                sandboxed_readonly=True,
            )

    def test_sandboxed_readonly_selects_fixed_allowlist(self):
        """The internal read-only selector fails closed on a conflicting allowlist."""
        with self.assertRaisesRegex(ValueError, "fixed read/exec allowlist"):
            peer.local_agent(
                b"task",
                sandboxed=True,
                sandboxed_workspace=Path("/tmp/ws"),
                sandboxed_workspace_identity=(0, 0, 0o700, os.getuid()),
                sandboxed_git_identity=(0, 0, 0o644, os.getuid(), 1),
                sandboxed_allowed_tools=("read", "write"),
                sandboxed_readonly=True,
            )

    def test_sandboxed_readonly_cli_routing(self):
        """ask-sandboxed-readonly routes to local_sandboxed_readonly_agent."""
        class Input:
            def __init__(self, value):
                self.buffer = io.BytesIO(value)

        with patch.object(peer.sys, "argv", ["pixel-mesh-peer", "ask-sandboxed-readonly"]), \
             patch.object(peer.sys, "stdin", Input(b"test task")), \
             patch.object(peer, "local_sandboxed_readonly_agent", return_value=4) as ro_handler:
            self.assertEqual(peer.main(), 4)
        ro_handler.assert_called_once_with()

        # Extra argument must be rejected
        class StderrOutput:
            def __init__(self):
                self.buffer = io.BytesIO()

            def write(self, value):
                if isinstance(value, str):
                    value = value.encode("utf-8")
                return self.buffer.write(value)

            def flush(self):
                return None

        stderr_out = StderrOutput()
        with patch.object(peer.sys, "argv", ["pixel-mesh-peer", "ask-sandboxed-readonly", "tower1"]), \
             patch.object(peer.sys, "stdin", Input(b"test")), \
             patch.object(peer, "local_sandboxed_readonly_agent") as ro_handler, \
             patch.object(peer.sys, "stderr", stderr_out):
            result = peer.main()
        self.assertEqual(result, 2)  # usage error
        ro_handler.assert_not_called()
        self.assertIn(
            "ask-sandboxed-readonly",
            stderr_out.buffer.getvalue().decode("utf-8"),
        )

    def test_sandboxed_readonly_evidence_non_widening(self):
        """sandboxed-readonly-workspace evidence must have write=false and read=true."""
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            transcript = sessions / "00000000-0000-0000-0000-000000000100.jsonl"
            transcript.write_text(json.dumps({"message": {"role": "assistant", "content": [
                {"type": "toolCall", "name": "read", "arguments": {"path": "/fixture"}},
            ]}}) + "\n", encoding="utf-8")
            transcript.chmod(0o600)

            evidence = peer.read_only_authority_evidence(
                transcript, state, "a" * 64,
                profile="sandboxed-readonly-workspace",
                allowed_tools=peer.SANDBOXED_READONLY_ALLOWED_TOOLS,
                extra_evidence={
                    "workspaceIdentitySha256": "b" * 64,
                    "freshSession": True,
                    "networkEnabled": False,
                    "hostFilesystemAuthority": False,
                    "hostGitMetadataReadAuthority": False,
                    "openClawExternalBindSourceOverride": False,
                    "sandboxRuntimeRemovedBeforeSuccess": True,
                    "sandboxWorkspaceControlPathRestoredBeforeSuccess": True,
                    "acceptanceAuthority": False,
                },
                workspace_identity_revalidator=lambda: None,
            )
            self.assertEqual(evidence["profile"], "sandboxed-readonly-workspace")
            self.assertEqual(evidence["allowedTools"], ["read", "exec"])
            self.assertTrue(evidence["workspaceReadAuthority"])
            self.assertFalse(evidence["workspaceWriteAuthority"])
            self.assertFalse(evidence["networkEnabled"])
            self.assertFalse(evidence["hostFilesystemAuthority"])
            self.assertFalse(evidence["acceptanceAuthority"])
            self.assertFalse(evidence["externalEffectAuthority"])
            self.assertIn("sandboxed read-only workspace", evidence["boundary"])

            with self.assertRaisesRegex(ValueError, "overlapped a core evidence key"):
                peer.read_only_authority_evidence(
                    transcript, state, "a" * 64,
                    profile="sandboxed-readonly-workspace",
                    allowed_tools=peer.SANDBOXED_READONLY_ALLOWED_TOOLS,
                    extra_evidence={
                        "workspaceIdentitySha256": "b" * 64,
                        "freshSession": True,
                        "networkEnabled": False,
                        "hostFilesystemAuthority": False,
                        "hostGitMetadataReadAuthority": False,
                        "openClawExternalBindSourceOverride": False,
                        "sandboxRuntimeRemovedBeforeSuccess": True,
                        "sandboxWorkspaceControlPathRestoredBeforeSuccess": True,
                        "acceptanceAuthority": False,
                        "workspaceReadAuthority": False,
                        "workspaceWriteAuthority": True,
                    },
                    workspace_identity_revalidator=lambda: None,
                )

    def test_sandboxed_writable_evidence_has_write_true(self):
        """sandboxed-workspace evidence with readonly=False has write=true."""
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            transcript = sessions / "00000000-0000-0000-0000-000000000101.jsonl"
            transcript.write_text(json.dumps({"message": {"role": "assistant", "content": [
                {"type": "toolCall", "name": "write", "arguments": {"path": "/fixture"}},
            ]}}) + "\n", encoding="utf-8")
            transcript.chmod(0o600)

            evidence = peer.read_only_authority_evidence(
                transcript, state, "a" * 64,
                profile="sandboxed-workspace",
                allowed_tools=peer.SANDBOXED_ALLOWED_TOOLS,
                extra_evidence={
                    "workspaceIdentitySha256": "b" * 64,
                    "freshSession": True,
                    "networkEnabled": False,
                    "hostFilesystemAuthority": False,
                    "hostGitMetadataReadAuthority": False,
                    "openClawExternalBindSourceOverride": False,
                    "sandboxRuntimeRemovedBeforeSuccess": True,
                    "sandboxWorkspaceControlPathRestoredBeforeSuccess": True,
                    "acceptanceAuthority": False,
                },
                workspace_identity_revalidator=lambda: None,
            )
            self.assertEqual(evidence["profile"], "sandboxed-workspace")
            self.assertTrue(evidence["workspaceReadAuthority"])
            self.assertTrue(evidence["workspaceWriteAuthority"])
            self.assertFalse(evidence["networkEnabled"])
            self.assertFalse(evidence["hostFilesystemAuthority"])

            with self.assertRaisesRegex(ValueError, "overlapped a core evidence key"):
                peer.read_only_authority_evidence(
                    transcript, state, "a" * 64,
                    profile="sandboxed-workspace",
                    allowed_tools=peer.SANDBOXED_ALLOWED_TOOLS,
                    extra_evidence={
                        "workspaceIdentitySha256": "b" * 64,
                        "freshSession": True,
                        "networkEnabled": False,
                        "hostFilesystemAuthority": False,
                        "hostGitMetadataReadAuthority": False,
                        "openClawExternalBindSourceOverride": False,
                        "sandboxRuntimeRemovedBeforeSuccess": True,
                        "sandboxWorkspaceControlPathRestoredBeforeSuccess": True,
                        "acceptanceAuthority": False,
                        "workspaceReadAuthority": False,
                        "workspaceWriteAuthority": False,
                    },
                    workspace_identity_revalidator=lambda: None,
                )

    def test_sandboxed_readonly_rejects_disallowed_tool_in_transcript(self):
        """sandboxed-readonly-workspace rejects write/edit/apply_patch/process in transcript."""
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            transcript = sessions / "00000000-0000-0000-0000-000000000102.jsonl"

            for disallowed_tool in ("write", "edit", "apply_patch", "process"):
                transcript.write_text(json.dumps({"message": {"role": "assistant", "content": [
                    {"type": "toolCall", "name": disallowed_tool,
                     "arguments": {"path": "/fixture"}},
                ]}}) + "\n", encoding="utf-8")
                transcript.chmod(0o600)
                with self.assertRaisesRegex(ValueError, "outside its authority"):
                    peer.read_only_authority_evidence(
                        transcript, state, "a" * 64,
                        profile="sandboxed-readonly-workspace",
                        allowed_tools=peer.SANDBOXED_READONLY_ALLOWED_TOOLS,
                        workspace_identity_revalidator=lambda: None,
                    )

    def test_sandboxed_readonly_allows_exec_and_read(self):
        """sandboxed-readonly-workspace permits read and exec tool calls."""
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            transcript = sessions / "00000000-0000-0000-0000-000000000103.jsonl"
            transcript.write_text(
                json.dumps({"message": {"role": "assistant", "content": [
                    {"type": "toolCall", "name": "read", "arguments": {"path": "/a"}},
                ]}}) + "\n" +
                json.dumps({"message": {"role": "assistant", "content": [
                    {"type": "toolCall", "name": "exec", "arguments": {"command": ["ls"]}},
                ]}}) + "\n",
                encoding="utf-8",
            )
            transcript.chmod(0o600)
            evidence = peer.read_only_authority_evidence(
                transcript, state, "a" * 64,
                profile="sandboxed-readonly-workspace",
                allowed_tools=peer.SANDBOXED_READONLY_ALLOWED_TOOLS,
                extra_evidence={
                    "workspaceIdentitySha256": "b" * 64,
                    "freshSession": True,
                    "networkEnabled": False,
                    "hostFilesystemAuthority": False,
                    "hostGitMetadataReadAuthority": False,
                    "openClawExternalBindSourceOverride": False,
                    "sandboxRuntimeRemovedBeforeSuccess": True,
                    "sandboxWorkspaceControlPathRestoredBeforeSuccess": True,
                    "acceptanceAuthority": False,
                },
                workspace_identity_revalidator=lambda: None,
            )
            self.assertEqual(evidence["observedTools"], ["exec", "read"])
            self.assertEqual(evidence["observedToolsInOrder"], ["read", "exec"])
            self.assertEqual(evidence["observedToolCallRecords"], 2)
            self.assertTrue(evidence["workspaceReadAuthority"])
            self.assertFalse(evidence["workspaceWriteAuthority"])

    def test_local_sandboxed_readonly_agent_invokes_with_fixed_params(self):
        """local_sandboxed_readonly_agent calls local_agent with exact fixed params."""
        with patch.dict(os.environ, {
            peer.SANDBOXED_ALLOWED_TOOLS_ENV: "read,write,edit",
        }, clear=False), patch.object(peer, "_validated_sandboxed_workspace", return_value=(
            Path("/tmp/ws"), (0, 0, 0o700, os.getuid()),
            (0, 0, 0o644, os.getuid(), 1), None,
        )), \
             patch.object(peer, "local_agent", return_value=3) as la:
            class Input:
                def __init__(self):
                    self.buffer = io.BytesIO(b"ro task")
            with patch.object(peer.sys, "stdin", Input()):
                self.assertEqual(peer.local_sandboxed_readonly_agent(), 3)
            la.assert_called_once_with(
                b"ro task",
                sandboxed=True,
                sandboxed_workspace=Path("/tmp/ws"),
                sandboxed_workspace_identity=(0, 0, 0o700, os.getuid()),
                sandboxed_git_identity=(0, 0, 0o644, os.getuid(), 1),
                sandboxed_git_metadata_binding=None,
                sandboxed_allowed_tools=peer.SANDBOXED_READONLY_ALLOWED_TOOLS,
                sandboxed_model_pin=None,
                sandboxed_readonly=True,
            )

    @unittest.skipUnless(os.name == "posix", "sandboxed cleanup flow is POSIX-only")
    def test_sandboxed_readonly_full_mocked_cleanup_flow(self):
        """Full sandboxed read-only agent flow with mocked subprocess removes config."""
        fixed = uuid.UUID("00000000-0000-0000-0000-000000000200")
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            source = state / "openclaw.json"
            source.write_text(json.dumps({
                "agents": {"defaults": {}, "list": [{"id": "pixel"}]},
                "tools": {"profile": "coding"},
            }) + "\n", encoding="utf-8")
            source.chmod(0o600)

            workspace = Path(home) / "ws"
            workspace.mkdir()
            ws_stat = workspace.lstat()
            ws_identity = (
                ws_stat.st_dev, ws_stat.st_ino,
                stat.S_IMODE(ws_stat.st_mode), ws_stat.st_uid,
            )
            git_entry = workspace / ".git"
            git_entry.write_text("gitdir: /tmp/common\n", encoding="utf-8")
            git_stat = git_entry.lstat()
            git_identity = (
                git_stat.st_dev, git_stat.st_ino,
                stat.S_IMODE(git_stat.st_mode), git_stat.st_uid,
                git_stat.st_nlink,
            )

            config_path_observed = []
            container_policy = {
                "schemaVersion": 1,
                "state": "verified",
                "containerObserved": True,
                "exactSessionMatch": True,
                "configLabelPresent": True,
                "authorityConfigBound": True,
                "policyMatch": True,
                "rootReadOnly": True,
                "networkDisabled": True,
                "privilegeIsolationMatched": True,
                "workspaceAccessMatched": True,
                "externalBindsReadOnly": True,
                "bindPropagationPrivate": True,
                "resourceLimitsMatched": True,
                "tmpfsMatched": True,
                "observationCount": 1,
            }

            class ContainerObserver:
                def observe_once(self):
                    return None

                def raise_if_faulted(self):
                    return None

                def finalize(self, tool_call_count, *, authority_config_sha256):
                    self.finalized_tool_call_count = tool_call_count
                    self.finalized_authority_config_sha256 = authority_config_sha256
                    return dict(container_policy)

            container_observer = ContainerObserver()

            def complete_readonly_ro(argv, env, timeout, **kwargs):
                self.assertIs(kwargs["container_policy_observer"], container_observer)
                config_p = Path(env["OPENCLAW_CONFIG_PATH"])
                config_path_observed.append(config_p)
                config = json.loads(config_p.read_text(encoding="utf-8"))
                self.assertEqual(
                    config["agents"]["list"][0]["sandbox"]["workspaceAccess"], "ro",
                )
                self.assertFalse(
                    config["tools"]["exec"]["applyPatch"]["enabled"],
                )
                transcript = kwargs["session_path"]
                transcript.write_text(
                    json.dumps({"message": {"role": "assistant", "content": [
                        {"type": "toolCall", "name": "read", "arguments": {"path": "/a"}},
                    ]}}) + "\n", encoding="utf-8",
                )
                transcript.chmod(0o600)
                return subprocess.CompletedProcess(
                    [], 0,
                    json.dumps({
                        "payloads": [{"text": "ro complete"}],
                        "meta": {
                            "agentMeta": {"sessionFile": str(transcript)},
                            "livenessState": "working",
                            "stopReason": "stop",
                            "error": None,
                        },
                    }).encode("utf-8"),
                    b"",
                )

            class Output:
                def __init__(self):
                    self.buffer = io.BytesIO()

            output, errors = Output(), Output()
            environment = {peer.HOME_ENV: home, peer.OPENCLAW_BIN_ENV: "/tmp/openclaw-test"}
            with patch.dict(os.environ, environment, clear=False):
                with patch.object(peer.uuid, "uuid4", return_value=fixed):
                    with patch.object(
                        peer, "run_agent_process", side_effect=complete_readonly_ro,
                    ), patch.object(
                        peer, "_remove_and_verify_sandbox_runtime", return_value=None,
                    ), patch.object(
                        peer, "SandboxContainerPolicyObserver",
                        return_value=container_observer,
                    ):
                        with patch.object(peer.sys, "stdout", output), \
                             patch.object(peer.sys, "stderr", errors):
                            self.assertEqual(
                                peer.local_agent(
                                    b"ro task",
                                    sandboxed=True,
                                    sandboxed_workspace=workspace,
                                    sandboxed_workspace_identity=ws_identity,
                                    sandboxed_git_identity=git_identity,
                                    sandboxed_allowed_tools=peer.SANDBOXED_READONLY_ALLOWED_TOOLS,
                                    sandboxed_readonly=True,
                                ),
                                0,
                            )
            self.assertEqual(len(config_path_observed), 1)
            self.assertFalse(config_path_observed[0].exists())
            parsed = json.loads(output.buffer.getvalue())
            authority = parsed["result"]["meta"]["meshAuthority"]
            self.assertEqual(authority["profile"], "sandboxed-readonly-workspace")
            self.assertEqual(authority["allowedTools"], ["read", "exec"])
            self.assertTrue(authority["workspaceReadAuthority"])
            self.assertFalse(authority["workspaceWriteAuthority"])
            self.assertFalse(authority["acceptanceAuthority"])
            self.assertFalse(authority["externalEffectAuthority"])
            self.assertEqual(authority["sandboxContainerPolicy"], container_policy)
            self.assertEqual(container_observer.finalized_tool_call_count, 1)
            self.assertEqual(
                container_observer.finalized_authority_config_sha256,
                authority["configSha256"],
            )


class SandboxedModelPinTests(unittest.TestCase):
    """Owner-selected, loopback-only inference pinning for sandboxed turns."""

    def test_parser_absent_valid_and_fail_closed_inputs(self):
        self.assertIsNone(peer.parse_sandboxed_model_pin(None, None))
        self.assertEqual(
            peer.parse_sandboxed_model_pin(
                "tower1-pin/Qwen3.6-27B-UD-Q4_K_XL",
                "http://127.0.0.1:18101/v1",
            ),
            (
                "tower1-pin", "Qwen3.6-27B-UD-Q4_K_XL",
                "http://127.0.0.1:18101/v1",
            ),
        )
        self.assertEqual(
            peer.parse_sandboxed_model_pin(
                "tower3-pin/Qwen3.6-27B-UD-Q4_K_XL",
                "http://[::1]:18103/v1",
            )[2],
            "http://[::1]:18103/v1",
        )
        invalid = (
            ("pin/model", None),
            (None, "http://127.0.0.1:18101/v1"),
            ("pin/model/extra", "http://127.0.0.1:18101/v1"),
            ("pin bad/model", "http://127.0.0.1:18101/v1"),
            ("pin/model bad", "http://127.0.0.1:18101/v1"),
            ("pin/model", "https://127.0.0.1:18101/v1"),
            ("pin/model", "http://192.168.1.10:18101/v1"),
            ("pin/model", "http://user@127.0.0.1:18101/v1"),
            ("pin/model", "http://127.0.0.1/v1"),
            ("pin/model", "http://127.0.0.1:0/v1"),
            ("pin/model", "http://127.0.0.1:18101/v1/"),
            ("pin/model", "http://127.0.0.1:18101/v1?q=1"),
            ("pin/model", "http://127.0.0.1:18101/v1#fragment"),
            ("pin/model", "http://LOCALHOST:18101/v1"),
        )
        for pin, url in invalid:
            with self.subTest(pin=pin, url=url), self.assertRaises(ValueError):
                peer.parse_sandboxed_model_pin(pin, url)

    @unittest.skipUnless(os.name == "posix", "sandboxed config is POSIX-only")
    def test_config_clones_real_list_provider_without_changing_source(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            state.mkdir(mode=0o700)
            source = state / "openclaw.json"
            original = {
                "agents": {
                    "defaults": {},
                    "list": [{"id": "pixel", "model": "tower/dream-fleet-agent"}],
                },
                "models": {"providers": {"tower": {
                    "baseUrl": "http://127.0.0.1:18080/v1",
                    "apiKey": "fixture-secret-never-evidence",
                    "api": "openai-completions",
                    "models": [{
                        "id": "dream-fleet-agent", "contextWindow": 262144,
                        "maxTokens": 16384, "reasoning": False,
                    }],
                    "extraFixture": "preserved",
                }}},
                "tools": {"profile": "coding"},
            }
            source_payload = (json.dumps(original) + "\n").encode("utf-8")
            source.write_bytes(source_payload)
            source.chmod(0o600)
            workspace = Path(home) / "ws"
            workspace.mkdir()
            ws_stat = workspace.lstat()
            workspace_identity = (
                ws_stat.st_dev, ws_stat.st_ino, stat.S_IMODE(ws_stat.st_mode),
                ws_stat.st_uid,
            )
            git_entry = workspace / ".git"
            git_entry.write_text("gitdir: /tmp/common\n", encoding="utf-8")
            git_stat = git_entry.lstat()
            git_identity = (
                git_stat.st_dev, git_stat.st_ino, stat.S_IMODE(git_stat.st_mode),
                git_stat.st_uid, git_stat.st_nlink,
            )
            pin = (
                "tower1-pin", "Qwen3.6-27B-UD-Q4_K_XL",
                "http://127.0.0.1:18101/v1",
            )
            path, _, expected_identity, _ = peer.create_sandboxed_config(
                state, workspace, workspace_identity, git_identity,
                allowed_tools=peer.SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS,
                model_pin=pin,
            )
            try:
                rendered = json.loads(path.read_text(encoding="utf-8"))
                providers = rendered["models"]["providers"]
                self.assertEqual(rendered["agents"]["list"][0]["model"], (
                    "tower1-pin/Qwen3.6-27B-UD-Q4_K_XL"
                ))
                self.assertEqual(providers["tower"], original["models"]["providers"]["tower"])
                pinned = providers["tower1-pin"]
                self.assertEqual(pinned["baseUrl"], pin[2])
                self.assertEqual(pinned["apiKey"], "fixture-secret-never-evidence")
                self.assertEqual(pinned["api"], "openai-completions")
                self.assertEqual(pinned["extraFixture"], "preserved")
                self.assertEqual(pinned["models"], [{
                    "id": pin[1], "name": pin[1], "contextWindow": 32768,
                    "maxTokens": 4096,
                }])
                self.assertEqual(source.read_bytes(), source_payload)
            finally:
                peer._remove_and_verify_ephemeral_config(path, expected_identity)
            with self.assertRaisesRegex(ValueError, "collides"):
                peer.create_sandboxed_config(
                    state, workspace, workspace_identity, git_identity,
                    model_pin=("tower", "other-model", pin[2]),
                )
            self.assertEqual(source.read_bytes(), source_payload)

    @unittest.skipUnless(os.name == "posix", "sandboxed handlers are POSIX-only")
    def test_all_sandboxed_handlers_pass_pin_and_partial_env_stops_preflight(self):
        workspace = Path("/tmp/ws")
        workspace_identity = (0, 0, 0o700, os.getuid())
        git_identity = (0, 0, 0o644, os.getuid(), 1)
        pin = (
            "tower1-pin", "Qwen3.6-27B-UD-Q4_K_XL",
            "http://127.0.0.1:18101/v1",
        )

        class Input:
            buffer = io.BytesIO(b"task")

        environment = {
            peer.SANDBOXED_MODEL_PIN_ENV: f"{pin[0]}/{pin[1]}",
            peer.SANDBOXED_MODEL_BASE_URL_ENV: pin[2],
        }
        for handler_name, fixed in (
            ("local_sandboxed_agent", {}),
            ("local_sandboxed_readonly_agent", {
                "sandboxed_allowed_tools": peer.SANDBOXED_READONLY_ALLOWED_TOOLS,
                "sandboxed_readonly": True,
            }),
            ("local_sandboxed_mailbox_readonly_agent", {
                "sandboxed_allowed_tools": peer.SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS,
                "sandboxed_mailbox_readonly": True,
            }),
        ):
            Input.buffer = io.BytesIO(b"task")
            with self.subTest(handler=handler_name), patch.dict(
                os.environ, environment, clear=False,
            ), patch.object(
                peer, "_validated_sandboxed_workspace",
                return_value=(workspace, workspace_identity, git_identity, None),
            ), patch.object(peer, "local_agent", return_value=7) as local, patch.object(
                peer.sys, "stdin", Input(),
            ):
                self.assertEqual(getattr(peer, handler_name)(), 7)
            kwargs = local.call_args.kwargs
            self.assertEqual(kwargs["sandboxed_model_pin"], pin)
            for key, value in fixed.items():
                self.assertEqual(kwargs[key], value)

        with patch.dict(os.environ, {
            peer.SANDBOXED_MODEL_PIN_ENV: "pin/model",
        }, clear=True), patch.object(
            peer, "_validated_sandboxed_workspace",
            side_effect=AssertionError("workspace validation must not run"),
        ), patch.object(peer.sys, "stdin", Input()):
            with self.assertRaisesRegex(ValueError, "must be set together"):
                peer.local_sandboxed_mailbox_readonly_agent()

    def test_non_sandboxed_local_agent_rejects_explicit_pin_binding(self):
        with self.assertRaisesRegex(ValueError, "only supported with the sandboxed"):
            peer.local_agent(
                b"task",
                sandboxed_model_pin=("pin", "model", "http://127.0.0.1:1/v1"),
            )


class SandboxedMailboxReadonlyWorkspaceTests(unittest.TestCase):
    """PXL-LIVE-341: closed Gmail-read plus sandboxed-report authority."""

    def setUp(self):
        self.exec_shell_patcher = patch.object(
            peer, "mesh_exec_shell",
            return_value=Path("/tmp/pixel-mesh-test-exec-shell"),
        )
        self.exec_shell_patcher.start()
        self.addCleanup(self.exec_shell_patcher.stop)

    def test_mailbox_allowed_tools_are_exact_and_generic_env_cannot_select_them(self):
        self.assertEqual(
            peer.SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS,
            ("read", "write", "exec", "pixel_gmail_search", "pixel_gmail_read"),
        )
        for disallowed in (
            "edit", "apply_patch", "process", "pixel_gmail_inbox",
            "pixel_gmail_sent", "pixel_gmail_thread", "pixel_ops_shell_propose",
            "pixel_web_browse",
        ):
            self.assertNotIn(disallowed, peer.SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS)
        with self.assertRaisesRegex(ValueError, "not a permitted sandboxed tool"):
            peer.parse_sandboxed_allowed_tools("read,pixel_gmail_search")

    @unittest.skipUnless(os.name == "posix", "sandboxed config is POSIX-only")
    def test_mailbox_config_has_exact_allowlists_and_physical_policy(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            state.mkdir(mode=0o700)
            source = state / "openclaw.json"
            source.write_text(json.dumps({
                "agents": {"defaults": {}, "list": [{"id": "pixel"}]},
                "tools": {"profile": "coding"},
            }) + "\n", encoding="utf-8")
            source.chmod(0o600)
            workspace = Path(home) / "ws"
            workspace.mkdir()
            ws_stat = workspace.lstat()
            ws_identity = (
                ws_stat.st_dev, ws_stat.st_ino, stat.S_IMODE(ws_stat.st_mode), ws_stat.st_uid,
            )
            git_entry = workspace / ".git"
            git_entry.write_text("gitdir: /tmp/common\n", encoding="utf-8")
            git_stat = git_entry.lstat()
            git_identity = (
                git_stat.st_dev, git_stat.st_ino, stat.S_IMODE(git_stat.st_mode),
                git_stat.st_uid, git_stat.st_nlink,
            )

            path, _, expected_identity, _ = peer.create_sandboxed_config(
                state, workspace, ws_identity, git_identity,
                allowed_tools=peer.SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS,
                workspace_access="rw",
            )
            try:
                config = json.loads(path.read_text(encoding="utf-8"))
                pixel = config["agents"]["list"][0]
                expected = list(peer.SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS)
                self.assertEqual(pixel["tools"], {"allow": expected})
                self.assertEqual(config["tools"]["sandbox"]["tools"], {
                    "allow": expected, "deny": ["image"],
                })
                self.assertEqual(pixel["sandbox"]["workspaceAccess"], "rw")
                self.assertEqual(config["tools"]["fs"], {"workspaceOnly": True})
                self.assertEqual(config["tools"]["exec"]["host"], "sandbox")
                self.assertFalse(config["tools"]["exec"]["applyPatch"]["enabled"])
                self.assertEqual(config["tools"]["elevated"], {"enabled": False})
                docker = pixel["sandbox"]["docker"]
                self.assertTrue(docker["readOnlyRoot"])
                self.assertEqual(docker["network"], "none")
                self.assertEqual(docker["user"], "1000:1000")
                self.assertEqual(docker["capDrop"], ["ALL"])
            finally:
                peer._remove_and_verify_ephemeral_config(path, expected_identity)
                self.assertFalse(path.exists())

            with self.assertRaisesRegex(ValueError, "not permitted"):
                peer.create_sandboxed_config(
                    state, workspace, ws_identity, git_identity,
                    allowed_tools=("read", "pixel_gmail_search", "write", "exec", "pixel_gmail_read"),
                    workspace_access="rw",
                )
            with self.assertRaisesRegex(ValueError, "requires rw"):
                peer.create_sandboxed_config(
                    state, workspace, ws_identity, git_identity,
                    allowed_tools=peer.SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS,
                    workspace_access="ro",
                )

    @unittest.skipUnless(os.name == "posix", "sandboxed selector is POSIX-only")
    def test_mailbox_selector_is_closed_and_mutually_exclusive(self):
        common = {
            "sandboxed_workspace": Path("/tmp/ws"),
            "sandboxed_workspace_identity": (0, 0, 0o700, os.getuid()),
            "sandboxed_git_identity": (0, 0, 0o644, os.getuid(), 1),
        }
        with self.assertRaisesRegex(ValueError, "only supported with the sandboxed"):
            peer.local_agent(b"task", sandboxed_mailbox_readonly=True)
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            peer.local_agent(
                b"task", sandboxed=True, sandboxed_readonly=True,
                sandboxed_mailbox_readonly=True, **common,
            )
        with self.assertRaisesRegex(ValueError, "fixed mailbox read-only allowlist"):
            peer.local_agent(
                b"task", sandboxed=True, sandboxed_mailbox_readonly=True,
                sandboxed_allowed_tools=("read", "write", "exec"), **common,
            )

    @unittest.skipUnless(os.name == "posix", "sandboxed handler is POSIX-only")
    def test_mailbox_local_handler_ignores_generic_allowlist_environment(self):
        workspace = Path("/tmp/ws")
        workspace_identity = (0, 0, 0o700, os.getuid())
        git_identity = (0, 0, 0o644, os.getuid(), 1)
        with patch.dict(os.environ, {
            peer.SANDBOXED_ALLOWED_TOOLS_ENV: "read,write,edit,apply_patch,exec,process",
        }, clear=False), patch.object(
            peer, "_validated_sandboxed_workspace",
            return_value=(workspace, workspace_identity, git_identity, None),
        ), patch.object(peer, "local_agent", return_value=6) as local:
            class Input:
                def __init__(self):
                    self.buffer = io.BytesIO(b"mail task")
            with patch.object(peer.sys, "stdin", Input()):
                self.assertEqual(peer.local_sandboxed_mailbox_readonly_agent(), 6)
        local.assert_called_once_with(
            b"mail task",
            sandboxed=True,
            sandboxed_workspace=workspace,
            sandboxed_workspace_identity=workspace_identity,
            sandboxed_git_identity=git_identity,
            sandboxed_git_metadata_binding=None,
            sandboxed_allowed_tools=peer.SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS,
            sandboxed_model_pin=None,
            sandboxed_mailbox_readonly=True,
        )

    def test_mailbox_cli_requires_exact_argv(self):
        command = "ask-sandboxed-mailbox-readonly"
        with patch.object(peer.sys, "argv", ["/arbitrary/pixel-mesh-peer", command]), \
             patch.object(peer, "local_sandboxed_mailbox_readonly_agent", return_value=8) as handler:
            self.assertEqual(peer.main(), 8)
        handler.assert_called_once_with()

        for argv in (
            ["pixel-mesh-peer", command, "tower2"],
            ["pixel-mesh-peer", f"{command}-extra"],
        ):
            stderr = io.StringIO()
            with patch.object(peer.sys, "argv", argv), \
                 patch.object(peer.sys, "stderr", stderr), \
                 patch.object(peer, "local_sandboxed_mailbox_readonly_agent") as handler:
                self.assertEqual(peer.main(), 2)
            handler.assert_not_called()
            self.assertIn(command, stderr.getvalue())

    @unittest.skipUnless(os.name == "posix", "sandboxed cleanup flow is POSIX-only")
    def test_mailbox_full_mocked_turn_reports_inference_pin_evidence(self):
        fixed = uuid.UUID("00000000-0000-0000-0000-000000000221")
        pin = (
            "tower1-pin", "Qwen3.6-27B-UD-Q4_K_XL",
            "http://127.0.0.1:18101/v1",
        )
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            source = state / "openclaw.json"
            source.write_text(json.dumps({
                "agents": {
                    "defaults": {},
                    "list": [{"id": "pixel", "model": "tower/dream-fleet-agent"}],
                },
                "models": {"providers": {"tower": {
                    "baseUrl": "http://127.0.0.1:18080/v1",
                    "apiKey": "fixture-secret-never-evidence",
                    "api": "openai-completions",
                    "models": [{
                        "id": "dream-fleet-agent", "contextWindow": 262144,
                        "maxTokens": 16384, "reasoning": False,
                    }],
                }}},
                "tools": {"profile": "coding"},
            }) + "\n", encoding="utf-8")
            source.chmod(0o600)
            workspace = Path(home) / "ws"
            workspace.mkdir()
            ws_stat = workspace.lstat()
            ws_identity = (
                ws_stat.st_dev, ws_stat.st_ino,
                stat.S_IMODE(ws_stat.st_mode), ws_stat.st_uid,
            )
            git_entry = workspace / ".git"
            git_entry.write_text("gitdir: /tmp/common\n", encoding="utf-8")
            git_stat = git_entry.lstat()
            git_identity = (
                git_stat.st_dev, git_stat.st_ino,
                stat.S_IMODE(git_stat.st_mode), git_stat.st_uid,
                git_stat.st_nlink,
            )
            observed_configs = []

            def complete_mailbox(_argv, env, _timeout, **kwargs):
                config_path = Path(env["OPENCLAW_CONFIG_PATH"])
                observed_configs.append(config_path)
                config = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    config["agents"]["list"][0]["model"],
                    "tower1-pin/Qwen3.6-27B-UD-Q4_K_XL",
                )
                transcript = kwargs["session_path"]
                transcript.write_text(json.dumps({
                    "message": {"role": "assistant", "content": [{
                        "type": "toolCall", "name": "pixel_gmail_search",
                        "arguments": {"query": "is:unread"},
                    }]},
                }) + "\n", encoding="utf-8")
                transcript.chmod(0o600)
                return subprocess.CompletedProcess([], 0, json.dumps({
                    "payloads": [{"text": "mailbox report complete"}],
                    "meta": {
                        "agentMeta": {"sessionFile": str(transcript)},
                        "livenessState": "working", "stopReason": "stop", "error": None,
                    },
                }).encode("utf-8"), b"")

            class ContainerObserver:
                def observe_once(self):
                    return None

                def raise_if_faulted(self):
                    return None

                def finalize(self, _tool_call_count, *, authority_config_sha256):
                    self.authority_config_sha256 = authority_config_sha256
                    return {"state": "verified", "networkDisabled": True}

            class Output:
                def __init__(self):
                    self.buffer = io.BytesIO()

            observer = ContainerObserver()
            output, errors = Output(), Output()
            environment = {
                peer.HOME_ENV: home,
                peer.OPENCLAW_BIN_ENV: "/tmp/openclaw-test",
            }
            with patch.dict(os.environ, environment, clear=False), patch.object(
                peer.uuid, "uuid4", return_value=fixed,
            ), patch.object(
                peer, "run_agent_process", side_effect=complete_mailbox,
            ), patch.object(
                peer, "_remove_and_verify_sandbox_runtime", return_value=None,
            ), patch.object(
                peer, "SandboxContainerPolicyObserver", return_value=observer,
            ), patch.object(peer.sys, "stdout", output), patch.object(
                peer.sys, "stderr", errors,
            ):
                self.assertEqual(peer.local_agent(
                    b"mail task",
                    sandboxed=True,
                    sandboxed_workspace=workspace,
                    sandboxed_workspace_identity=ws_identity,
                    sandboxed_git_identity=git_identity,
                    sandboxed_allowed_tools=peer.SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS,
                    sandboxed_model_pin=pin,
                    sandboxed_mailbox_readonly=True,
                ), 0)
            self.assertEqual(len(observed_configs), 1)
            self.assertFalse(observed_configs[0].exists())
            authority = json.loads(output.buffer.getvalue())["result"]["meta"]["meshAuthority"]
            self.assertEqual(authority["profile"], "sandboxed-mailbox-readonly-workspace")
            self.assertTrue(authority["inferencePinned"])
            self.assertEqual(authority["inferencePinSelected"], {
                "provider": pin[0], "model": pin[1], "baseUrl": pin[2],
            })
            self.assertTrue(authority["mailboxReadAuthority"])
            self.assertFalse(authority["mailboxMutationAuthority"])
            self.assertFalse(authority["externalEffectAuthority"])
            self.assertNotIn("brokerMediatedAuthority", authority)
            self.assertEqual(
                observer.authority_config_sha256, authority["configSha256"],
            )

    @unittest.skipUnless(os.name == "posix", "authority evidence is POSIX-only")
    def test_mailbox_authority_evidence_accepts_exact_tools_and_rejects_others(self):
        with tempfile.TemporaryDirectory() as home:
            state = Path(home) / ".openclaw-mesh"
            sessions = state / "agents" / "pixel" / "sessions"
            sessions.mkdir(parents=True)
            state.chmod(0o700)
            transcript = sessions / "00000000-0000-0000-0000-000000000341.jsonl"

            def write_tools(names):
                transcript.write_text("".join(
                    json.dumps({"message": {"role": "assistant", "content": [{
                        "type": "toolCall", "name": name, "arguments": {},
                    }]}}) + "\n" for name in names
                ), encoding="utf-8")
                transcript.chmod(0o600)

            write_tools(peer.SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS)
            evidence = peer.read_only_authority_evidence(
                transcript, state, "a" * 64,
                profile="sandboxed-mailbox-readonly-workspace",
                allowed_tools=peer.SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS,
                extra_evidence={
                    "workspaceIdentitySha256": "b" * 64,
                    "freshSession": True,
                    "networkEnabled": False,
                    "hostFilesystemAuthority": False,
                    "hostGitMetadataReadAuthority": False,
                    "openClawExternalBindSourceOverride": False,
                    "sandboxRuntimeRemovedBeforeSuccess": True,
                    "sandboxWorkspaceControlPathRestoredBeforeSuccess": True,
                    "acceptanceAuthority": False,
                },
                workspace_identity_revalidator=lambda: None,
            )
            self.assertEqual(evidence["profile"], "sandboxed-mailbox-readonly-workspace")
            self.assertEqual(
                evidence["allowedTools"],
                list(peer.SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS),
            )
            self.assertEqual(evidence["observedToolCallRecords"], 5)
            self.assertEqual(
                evidence["observedToolsInOrder"],
                list(peer.SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS),
            )
            self.assertTrue(evidence["workspaceReadAuthority"])
            self.assertTrue(evidence["workspaceWriteAuthority"])
            self.assertTrue(evidence["mailboxReadAuthority"])
            self.assertFalse(evidence["mailboxMutationAuthority"])
            self.assertFalse(evidence["networkEnabled"])
            self.assertFalse(evidence["hostFilesystemAuthority"])
            self.assertFalse(evidence["externalEffectAuthority"])
            self.assertFalse(evidence["acceptanceAuthority"])
            self.assertTrue(evidence["sandboxRuntimeRemovedBeforeSuccess"])
            self.assertTrue(evidence["sandboxWorkspaceControlPathRestoredBeforeSuccess"])

            with self.assertRaisesRegex(ValueError, "overlapped a core evidence key"):
                peer.read_only_authority_evidence(
                    transcript, state, "a" * 64,
                    profile="sandboxed-mailbox-readonly-workspace",
                    allowed_tools=peer.SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS,
                    extra_evidence={
                        "workspaceIdentitySha256": "b" * 64,
                        "freshSession": True,
                        "networkEnabled": False,
                        "hostFilesystemAuthority": False,
                        "hostGitMetadataReadAuthority": False,
                        "openClawExternalBindSourceOverride": False,
                        "sandboxRuntimeRemovedBeforeSuccess": True,
                        "sandboxWorkspaceControlPathRestoredBeforeSuccess": True,
                        "acceptanceAuthority": False,
                        "mailboxReadAuthority": False,
                    },
                    workspace_identity_revalidator=lambda: None,
                )

            write_tools(["pixel_ops_shell_propose"])
            with self.assertRaisesRegex(ValueError, "outside its authority"):
                peer.read_only_authority_evidence(
                    transcript, state, "a" * 64,
                    profile="sandboxed-mailbox-readonly-workspace",
                    allowed_tools=peer.SANDBOXED_MAILBOX_READONLY_ALLOWED_TOOLS,
                    workspace_identity_revalidator=lambda: None,
                )


if __name__ == "__main__":
    unittest.main()
