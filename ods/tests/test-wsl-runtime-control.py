"""Owned Windows control contract with mocks; no installed runtime is touched."""

import copy
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
from model_switchboard import wsl_runtime as bridge


ENV = {"ODS_HOST_LLM_TRANSPORT": "model-router"}
ROOT = Path(tempfile.gettempdir()) / "ods-wsl-control-fixture"
WINDOWS = bridge._WindowsTools('/drives/d/Windows/System32/WindowsPowerShell/v1.0/powershell.exe',
                              '/drives/d/Windows/System32/whoami.exe',
                              r'D:\Windows\System32\WindowsPowerShell\v1.0\Modules')
CONTEXT = bridge._Context("Ubuntu", "/home/test/ods", r"\\wsl.localhost\Ubuntu\home\test\ods\installers\windows\portal-model-control.ps1", WINDOWS)
SOCKET = ("/run/WSL/123_interop", (1, 2))
DIGEST = "a" * 64
PLAN = {"ExecutablePath": r"C:\Users\Test\AppData\Local\ODS\llama.cpp\b9014-win-vulkan-x64\llama-server.exe",
        "Port": 13305,
        "ModelsDir": r"C:\Users\Test\AppData\Local\ODS\lemonade\models",
        "ContextSize": 65536, "GgufFile": "Qwen-9B.gguf",
        "WslDistro": CONTEXT.distro, "WslInstallDir": CONTEXT.install_dir}


def response(*, running=True, plan=None, digest=DIGEST):
    plan = copy.deepcopy(plan or PLAN)
    return {"ok": True, "managed": True, "running": running,
            "modelStoreWindowsPath": plan["ModelsDir"], "planDigest": digest, "plan": plan,
            "planPathWindows": str(bridge.PureWindowsPath(plan["ModelsDir"]).parent / "portal-runtime" / "runtime.json"),
            "observation": {"status": "verified", "modelId": plan["GgufFile"],
                            "contextLength": plan["ContextSize"]} if running else None}


def completed(value, code=0):
    return subprocess.CompletedProcess([], code, json.dumps(value).encode(), b"")


class ControlTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("candidate", True), ("_context", CONTEXT),
                            ("_sockets", [SOCKET]), ("_socket_identity", SOCKET[1]), ("_probe", True)):
            patcher = patch.object(bridge, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_status_sends_only_fixed_controller_and_json_stdin(self):
        with patch.object(bridge, "_run", return_value=completed(response())) as run:
            value = bridge.status(ROOT, ENV)
        self.assertTrue(value["managed"])
        command = run.call_args.args[0]
        self.assertEqual(command[0], WINDOWS.shell)
        self.assertEqual(command[-2:], ["-File", CONTEXT.controller])
        self.assertNotIn("-Command", command)
        self.assertEqual(json.loads(run.call_args.kwargs["data"]),
                         {"action": "status", "distro": "Ubuntu", "installDir": "/home/test/ods"})
        self.assertEqual(run.call_args.kwargs["environ"]["WSL_INTEROP"], SOCKET[0])
        self.assertEqual(run.call_args.kwargs["environ"]["PSModulePath"],
                         WINDOWS.module_path)
        self.assertIn("PSModulePath/w", run.call_args.kwargs["environ"]["WSLENV"].split(":"))
        self.assertEqual(run.call_args.kwargs["timeout"], 15)

    def test_module_path_override_is_child_only_and_preserves_other_exports(self):
        with patch.dict(os.environ, {"PSModulePath": "PS7", "WSLENV": "KEEP/p:PSMODULEPATH/l:ANOTHER"}), \
                patch.object(bridge, "_run", return_value=completed(response())) as run:
            bridge.status(ROOT, ENV)
            self.assertEqual(os.environ["PSModulePath"], "PS7")
            self.assertEqual(run.call_args.kwargs["environ"]["WSLENV"], "KEEP/p:ANOTHER:PSModulePath/w")

    def test_configured_endpoints_must_match_owned_task_before_mutation(self):
        good = {**ENV, "NATIVE_LLM_BASE_URL": "http://localhost:13305",
                "NATIVE_LLM_CONTAINER_BASE_URL": "http://host.docker.internal:13305/v1", "AMD_INFERENCE_PORT": "13305"}
        with patch.object(bridge, "_run", return_value=completed(response())):
            self.assertTrue(bridge.status(ROOT, good)["managed"])
        for key, value in (("NATIVE_LLM_BASE_URL", "http://localhost:8080"),
                           ("NATIVE_LLM_BASE_URL", "http://someone:secret@localhost:13305"),
                           ("NATIVE_LLM_BASE_URL", "http://remote.example:13305"),
                           ("NATIVE_LLM_BASE_URL", "http://localhost:13305/other"),
                           # Lemonade's API path is not a llama-server origin.
                           ("NATIVE_LLM_BASE_URL", "http://localhost:13305/api/v1"),
                           ("NATIVE_LLM_CONTAINER_BASE_URL", "http://host.docker.internal:13306"),
                           ("NATIVE_LLM_CONTAINER_BASE_URL", "http://localhost:13305"),
                           ("AMD_INFERENCE_PORT", "8080")):
            with self.subTest(key=key, value=value), patch.object(bridge, "_run", return_value=completed(response())) as run:
                with self.assertRaises(bridge.BridgeError) as caught:
                    bridge.stop(ROOT, {**good, key: value}, DIGEST)
                self.assertEqual(caught.exception.code, "endpoint_mismatch")
                self.assertEqual(run.call_count, 1)

    def test_unmigrated_endpoint_keys_are_read_for_one_release(self):
        legacy = {"LEMONADE_HOST_TRANSPORT": "model-router",
                  "LEMONADE_BASE_URL": "http://localhost:13305/api/v1",
                  "LEMONADE_CONTAINER_BASE_URL": "http://host.docker.internal:13305/api/v1",
                  "AMD_INFERENCE_PORT": "13305"}
        with patch.object(bridge, "_run", return_value=completed(response())):
            self.assertTrue(bridge.status(ROOT, legacy)["managed"])
        with patch.object(bridge, "_run", return_value=completed(response())) as run:
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge.stop(ROOT, {**legacy, "LEMONADE_BASE_URL": "http://localhost:8080/api/v1"}, DIGEST)
            self.assertEqual(caught.exception.code, "endpoint_mismatch")
            self.assertEqual(run.call_count, 1)
        # The migrated key wins over a stale legacy line.
        mixed = {**legacy, "NATIVE_LLM_BASE_URL": "http://localhost:8080"}
        with patch.object(bridge, "_run", return_value=completed(response())):
            with self.assertRaises(bridge.BridgeError):
                bridge.stop(ROOT, mixed, DIGEST)

    def test_transport_key_and_its_legacy_name(self):
        self.assertEqual(bridge.env_value(ENV, bridge.TRANSPORT_KEY), ("ODS_HOST_LLM_TRANSPORT", "model-router"))
        self.assertEqual(bridge.env_value({"LEMONADE_HOST_TRANSPORT": "model-router"}, bridge.TRANSPORT_KEY),
                         ("LEMONADE_HOST_TRANSPORT", "model-router"))
        self.assertEqual(bridge.env_value({"ODS_HOST_LLM_TRANSPORT": "direct",
                                           "LEMONADE_HOST_TRANSPORT": "model-router"}, bridge.TRANSPORT_KEY),
                         ("ODS_HOST_LLM_TRANSPORT", "direct"))
        self.assertEqual(bridge.env_value({}, bridge.TRANSPORT_KEY), ("ODS_HOST_LLM_TRANSPORT", None))

    def test_legacy_module_name_is_the_same_bridge(self):
        # Scripts outside the agent import the old name for one release.
        from model_switchboard import wsl_lemonade
        self.assertIs(wsl_lemonade, bridge)

    def test_activate_preflights_and_uses_same_socket_with_cas(self):
        target = {**PLAN, "GgufFile": "hf-Qwen-0.6B.gguf", "ContextSize": 16384}
        with patch.object(bridge, "_run", side_effect=[completed(response()),
                completed(response(plan=target, digest="b" * 64))]) as run:
            result = bridge.activate(ROOT, ENV, target["GgufFile"], 16384, DIGEST)
        self.assertEqual(result["plan"], target)
        calls = run.call_args_list
        request = json.loads(calls[1].kwargs["data"])
        self.assertEqual(request, {"action": "activate", "distro": "Ubuntu", "installDir": "/home/test/ods",
                                  "expectedPlanDigest": DIGEST, "gguf": target["GgufFile"], "contextSize": 16384})
        self.assertEqual(calls[0].kwargs["environ"]["WSL_INTEROP"], calls[1].kwargs["environ"]["WSL_INTEROP"])

    def test_stop_then_start_preserves_plan(self):
        for function, initially_running, expected_running in ((bridge.stop, True, False), (bridge.start, False, True)):
            with self.subTest(function=function.__name__), patch.object(bridge, "_run", side_effect=[
                    completed(response(running=initially_running)), completed(response(running=expected_running))]) as run:
                result = function(ROOT, ENV, DIGEST)
                self.assertEqual(result["running"], expected_running)
                self.assertEqual(result["plan"], PLAN)
                self.assertEqual(json.loads(run.call_args.kwargs["data"])["action"], function.__name__)

    def test_restore_only_model_and_context(self):
        previous = {**PLAN, "GgufFile": "Previous.gguf", "ContextSize": 32768}
        with patch.object(bridge, "_run", side_effect=[completed(response()), completed(response(plan=previous))]) as run:
            self.assertEqual(bridge.restore(ROOT, ENV, previous, DIGEST)["plan"], previous)
            self.assertEqual(json.loads(run.call_args.kwargs["data"])["plan"], previous)
        for key, value in (("Port", 8000), ("ExecutablePath", r"C:\other.exe"),
                           ("ModelsDir", r"C:\other"), ("WslDistro", "Other")):
            with self.subTest(key=key), patch.object(bridge, "_run", return_value=completed(response())) as run:
                with self.assertRaises(ValueError):
                    bridge.restore(ROOT, ENV, {**previous, key: value}, DIGEST)
                self.assertEqual(run.call_count, 1)

    def test_vision_projector_is_planned_and_proven_with_its_model(self):
        target = {**PLAN, "GgufFile": "gemma-4-E4B-it-Q4_K_M.gguf", "ContextSize": 16384,
                  "MmprojFile": "mmproj-F16.gguf"}
        capable = {**response(), "planFeatures": ["MmprojFile"]}
        self.assertTrue(bridge.supports_projector(capable))
        self.assertFalse(bridge.supports_projector(response()))
        self.assertFalse(bridge.supports_projector({"ok": True, "managed": False, "running": False}))
        with patch.object(bridge, "_run", side_effect=[completed(capable),
                completed({**response(plan=target, digest="b" * 64), "planFeatures": ["MmprojFile"]})]) as run:
            result = bridge.activate(ROOT, ENV, target["GgufFile"], 16384, DIGEST, mmproj="mmproj-F16.gguf")
        self.assertEqual(result["plan"], target)
        self.assertEqual(json.loads(run.call_args.kwargs["data"])["mmproj"], "mmproj-F16.gguf")
        text = {**PLAN, "GgufFile": "next.gguf", "ContextSize": 16384}
        with patch.object(bridge, "_run", side_effect=[completed(response(plan=target)),
                completed(response(plan=text, digest="b" * 64))]) as run:
            self.assertEqual(bridge.activate(ROOT, ENV, "next.gguf", 16384, DIGEST)["plan"], text)
        self.assertNotIn("mmproj", json.loads(run.call_args.kwargs["data"]))
        # A switch that dropped the requested projector, or kept the previous one, is unproven.
        for returned, requested in ((text, "mmproj-F16.gguf"), ({**text, "MmprojFile": "mmproj-F16.gguf"}, None)):
            with self.subTest(returned=returned, requested=requested), patch.object(bridge, "_run", side_effect=[
                    completed(capable), completed(response(plan=returned, digest="b" * 64))]):
                with self.assertRaisesRegex(bridge.BridgeError, "did not persist"):
                    bridge.activate(ROOT, ENV, "next.gguf", 16384, DIGEST, mmproj=requested)
        for projector in ("../mmproj.gguf", "mmproj.bin", "NEXT.gguf", "bad:stream.gguf", ""):
            with self.subTest(projector=projector), patch.object(bridge, "_run") as run:
                with self.assertRaises(ValueError):
                    bridge.activate(ROOT, ENV, "next.gguf", 16384, DIGEST, mmproj=projector)
                run.assert_not_called()

    def test_restore_returns_the_previous_projector_with_its_model(self):
        previous = {**PLAN, "GgufFile": "Previous.gguf", "ContextSize": 32768, "MmprojFile": "mmproj-F16.gguf"}
        with patch.object(bridge, "_run", side_effect=[completed(response()), completed(response(plan=previous))]) as run:
            self.assertEqual(bridge.restore(ROOT, ENV, previous, DIGEST)["plan"], previous)
            self.assertEqual(json.loads(run.call_args.kwargs["data"])["plan"]["MmprojFile"], "mmproj-F16.gguf")
        with patch.object(bridge, "_run", side_effect=[completed(response()), completed(response(plan={
                **previous, "MmprojFile": "other-mmproj.gguf"}))]):
            with self.assertRaisesRegex(bridge.BridgeError, "did not persist"):
                bridge.restore(ROOT, ENV, previous, DIGEST)

    def test_projector_plan_and_features_are_validated(self):
        with patch.object(bridge, "_run", return_value=completed(response(plan={**PLAN, "MmprojFile": "mmproj-F16.gguf"}))):
            self.assertEqual(bridge.status(ROOT, ENV)["plan"]["MmprojFile"], "mmproj-F16.gguf")
        for projector in ("../mmproj.gguf", "QWEN-9B.GGUF", None, 7):
            with self.subTest(projector=projector), patch.object(bridge, "_run",
                    return_value=completed(response(plan={**PLAN, "MmprojFile": projector}))):
                with self.assertRaises(ValueError):
                    bridge.status(ROOT, ENV)
        for features in ("MmprojFile", [7], ["x" * 65], ["MmprojFile"] * 17):
            with self.subTest(features=features), self.assertRaisesRegex(bridge.BridgeError, "plan features"):
                bridge._response({**response(), "planFeatures": features}, CONTEXT)

    def test_unmanaged_or_changed_plan_never_dispatches_mutation(self):
        for info in ({"ok": True, "managed": False, "running": False}, response(digest="b" * 64)):
            with self.subTest(info=info), patch.object(bridge, "_run", return_value=completed(info)) as run:
                with self.assertRaises(bridge.BridgeError):
                    bridge.stop(ROOT, ENV, DIGEST)
                self.assertEqual(run.call_count, 1)

    def test_wrong_binding_or_plan_schema_rejected(self):
        for changes in ({"WslDistro": "Other"}, {"WslInstallDir": "/other"}, {"extra": "no"},
                        {"ExecutablePath": r"\\server\share\evil.exe"}, {"Port": True},
                        {"GgufFile": "../escape.gguf"}, {"ContextSize": float("inf")},
                        {"ModelsDir": r"C:\models\..\other"}):
            with self.subTest(changes=changes), patch.object(bridge, "_run",
                    return_value=completed(response(plan={**PLAN, **changes}))):
                with self.assertRaises(ValueError):
                    bridge.status(ROOT, ENV)

    def test_distribution_case_is_equivalent_but_install_directory_case_is_not(self):
        with patch.object(bridge, '_run', return_value=completed(response(plan={**PLAN, 'WslDistro': 'ubuntu'}))):
            self.assertTrue(bridge.status(ROOT, ENV)['managed'])
        with patch.object(bridge, '_run', return_value=completed(response(plan={**PLAN, 'WslInstallDir': '/home/Test/ods'}))):
            with self.assertRaises(ValueError):
                bridge.status(ROOT, ENV)

    def test_restore_accepts_only_case_equivalent_distribution(self):
        plan = {**PLAN, 'WslDistro': 'ubuntu'}
        with patch.object(bridge, '_run', side_effect=[completed(response()), completed(response(plan=plan))]):
            self.assertEqual(bridge.restore(ROOT, ENV, plan, DIGEST)['plan'], plan)

    def test_no_mutation_retry_on_timeout_or_controller_error(self):
        error = completed({"ok": False, "code": "load_failed", "error": "Model load failed",
                           "newPlanDigest": "b" * 64, "private": "must not be projected"}, 1)
        for failure in (subprocess.TimeoutExpired("controller", 1), error):
            with self.subTest(failure=failure), patch.object(bridge, "_run", side_effect=[completed(response()), failure]) as run:
                with self.assertRaises((subprocess.TimeoutExpired, bridge.BridgeError)) as caught:
                    bridge.activate(ROOT, ENV, "next.gguf", 16384, DIGEST)
                self.assertEqual(run.call_count, 2)
                if isinstance(caught.exception, bridge.BridgeError):
                    self.assertEqual(caught.exception.new_plan_digest, "b" * 64)
                    self.assertEqual(caught.exception.code, "load_failed")
                    self.assertNotIn("private", caught.exception.response)

    def test_socket_replacement_stops_before_mutation(self):
        with patch.object(bridge, "_socket_identity", side_effect=[SOCKET[1], SOCKET[1], (3, 4)]), \
                patch.object(bridge, "_run", return_value=completed(response())) as run:
            with self.assertRaisesRegex(bridge.BridgeError, "changed before dispatch"):
                bridge.stop(ROOT, ENV, DIGEST)
            self.assertEqual(run.call_count, 1)

    def test_missing_trusted_socket_cannot_call_controller(self):
        with patch.object(bridge, "_sockets", return_value=[]), patch.object(bridge, "_run") as run:
            with self.assertRaises(bridge.BridgeError):
                bridge.status(ROOT, ENV)
            run.assert_not_called()

    def test_stale_session_discovery_retries_only_native_probe(self):
        second = ("/run/WSL/456_interop", SOCKET[1])
        with patch.object(bridge, "_sockets", return_value=[SOCKET, second]), \
                patch.object(bridge, "_probe", side_effect=[False, True]) as probe, \
                patch.object(bridge, "_run", side_effect=[completed(response()), completed(response(running=False))]) as run:
            bridge.stop(ROOT, ENV, DIGEST)
            self.assertEqual([json.loads(call.kwargs["data"])["action"] for call in run.call_args_list],
                             ["status", "stop"])
            self.assertEqual(probe.call_count, 2)
            self.assertEqual(run.call_args_list[1].kwargs["environ"]["WSL_INTEROP"], second[0])

    def test_full_ownership_timeout_is_not_replayed_on_another_peer(self):
        with patch.object(bridge, "_sockets", return_value=[SOCKET, ("/run/WSL/456_interop", SOCKET[1])]), \
                patch.object(bridge, "_run", side_effect=subprocess.TimeoutExpired("controller", 15)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                bridge.stop(ROOT, ENV, DIGEST)
            self.assertEqual(run.call_count, 1)

    def test_status_reselects_only_after_proven_socket_replacement(self):
        second = ("/run/WSL/456_interop", (3, 4))
        with patch.object(bridge, "_sockets", return_value=[SOCKET, second]), \
                patch.object(bridge, "_socket_identity", side_effect=[SOCKET[1], None, second[1], second[1]]), \
                patch.object(bridge, "_run", side_effect=[subprocess.CompletedProcess([], 1, b"", b""),
                                                        completed(response())]) as run:
            self.assertTrue(bridge.status(ROOT, ENV)["managed"])
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args.kwargs["environ"]["WSL_INTEROP"], second[0])

    def test_invalid_json_from_unchanged_socket_is_not_retried(self):
        with patch.object(bridge, "_run", return_value=subprocess.CompletedProcess([], 1, b"bad" * 500, b"failure")) as run, \
                self.assertLogs(bridge.__name__, level="WARNING") as logs:
            with self.assertRaisesRegex(bridge.BridgeError, "invalid JSON"):
                bridge.status(ROOT, ENV)
            self.assertEqual(run.call_count, 1)
            self.assertIn("exit=1", logs.output[0])
            self.assertLess(len(logs.output[0]), 750)

    def test_socket_replacement_after_mutation_does_not_replay(self):
        with patch.object(bridge, "_socket_identity", side_effect=[SOCKET[1], SOCKET[1], SOCKET[1], None]), \
                patch.object(bridge, "_run", side_effect=[completed(response()), subprocess.CompletedProcess([], 1, b"", b"")]) as run:
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge.stop(ROOT, ENV, DIGEST)
            self.assertEqual(caught.exception.code, "interop_changed")
            self.assertEqual(run.call_count, 2)

    def test_ownership_denial_does_not_try_another_peer(self):
        with patch.object(bridge, "_sockets", return_value=[SOCKET, ("/run/WSL/456_interop", SOCKET[1])]), \
                patch.object(bridge, "_run", return_value=completed({"ok": False, "code": "ownership", "error": "Wrong owner"}, 1)) as run:
            with self.assertRaises(bridge.BridgeError):
                bridge.status(ROOT, ENV)
            self.assertEqual(run.call_count, 1)

    def test_invalid_requests_fail_before_preflight(self):
        for gguf, size, digest in (("../bad.gguf", 16384, DIGEST), ("ok.gguf", True, DIGEST),
                                   ("ok.gguf", 10000000, DIGEST), ("ok.gguf", 16384, "bad"),
                                   ("bad\n.gguf", 16384, DIGEST), ("bad:stream.gguf", 16384, DIGEST)):
            with self.subTest(gguf=gguf, size=size), patch.object(bridge, "_run") as run:
                with self.assertRaises(ValueError):
                    bridge.activate(ROOT, ENV, gguf, size, digest)
                run.assert_not_called()

    def test_changed_returned_target_or_unproven_runtime_rejected(self):
        invalid = [response(), response(running=False), response(plan={**PLAN, "GgufFile": "next.gguf", "ContextSize": 16384})]
        invalid[-1]["observation"] = None
        for value in invalid:
            with self.subTest(value=value), patch.object(bridge, "_run", side_effect=[completed(response()), completed(value)]):
                with self.assertRaises(bridge.BridgeError):
                    bridge.activate(ROOT, ENV, "next.gguf", 16384, DIGEST)

    def test_response_contract_rejects_untyped_and_nonfinite_values(self):
        invalid = [None, [], {"ok": True, "managed": "true", "running": True}]
        for key, value in (("planDigest", "x"), ("running", 1), ("modelStoreWindowsPath", r"C:\other"),
                           ("observation", {"status": "verified", "modelId": "x", "contextLength": True})):
            invalid.append({**response(), key: value})
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(bridge.BridgeError):
                bridge._response(value, CONTEXT)

    def test_model_store_requires_roundtrip_and_canonical_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            drive = Path(directory).resolve()
            root = drive.joinpath(*bridge.PureWindowsPath(PLAN["ModelsDir"]).parts[1:])
            root.mkdir(parents=True)
            with patch.object(bridge, "_path", side_effect=[str(drive), PLAN["ModelsDir"]]) as translate:
                self.assertEqual(bridge.model_store(ROOT, ENV, response()), root)
                self.assertEqual(translate.call_args_list[0].args, ("C:\\", "-u"))
            with patch.object(bridge, "_path", side_effect=[str(drive), r"C:\other"]):
                with self.assertRaises(bridge.BridgeError):
                    bridge.model_store(ROOT, ENV, response())

    def test_full_directory_docker_alias_is_never_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            drive = Path(directory).resolve() / "custom-drive-mount"
            root = drive.joinpath(*bridge.PureWindowsPath(PLAN["ModelsDir"]).parts[1:])
            root.mkdir(parents=True)

            def translate(path, direction):
                if (path, direction) == ("C:\\", "-u"):
                    return str(drive)
                if (path, direction) == (str(root), "-w"):
                    return PLAN["ModelsDir"]
                if (path, direction) == (PLAN["ModelsDir"], "-u"):
                    return "/mnt/wsl/docker-desktop-bind-mounts/Ubuntu/temporary-alias"
                raise AssertionError((path, direction))

            with patch.object(bridge, "_path", side_effect=translate):
                self.assertEqual(bridge.model_store(ROOT, ENV, response()), root)

    def test_plan_path_requires_the_expected_owned_file(self):
        with tempfile.TemporaryDirectory() as directory:
            drive = Path(directory).resolve()
            windows = response()["planPathWindows"]
            path = drive.joinpath(*bridge.PureWindowsPath(windows).parts[1:])
            path.parent.mkdir(parents=True)
            path.write_text("{}")
            with patch.object(bridge, "_path", side_effect=[str(drive), windows]):
                self.assertEqual(bridge.plan_path(ROOT, ENV, response()), path)
        for value in (r"C:\other\runtime.json", r"\\server\runtime.json", None):
            with self.subTest(value=value), self.assertRaises(bridge.BridgeError):
                bridge._response({**response(), "planPathWindows": value}, CONTEXT)


class PlatformAndSocketTests(unittest.TestCase):
    def test_other_platforms_and_transports_do_not_invoke_windows(self):
        for system, release, env in (("Windows", "10", ENV), ("Darwin", "24", ENV),
                                     ("Linux", "6.8.0-generic", ENV),
                                     ("Linux", "6.6.87-microsoft-standard-WSL2", {})):
            with self.subTest(system=system), patch.object(bridge.platform, "system", return_value=system), \
                    patch.object(bridge.platform, "release", return_value=release), patch.object(bridge, "_run") as run:
                self.assertFalse(bridge.status(ROOT, env)["managed"])
                with self.assertRaises(bridge.BridgeError):
                    bridge.stop(ROOT, env, DIGEST)
                run.assert_not_called()

    def test_socket_requires_protected_parents_root_owner_and_no_symlink(self):
        directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        socket = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o777, st_uid=0, st_dev=1, st_ino=2)
        with patch.object(bridge.Path, "lstat", side_effect=[directory, directory, socket]):
            self.assertEqual(bridge._socket_identity(SOCKET[0]), (1, 2))
        for sequence in ([SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_uid=0)],
                         [directory, SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=1000)],
                         [directory, directory, SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0)],
                         [directory, directory, SimpleNamespace(st_mode=stat.S_IFSOCK | 0o777, st_uid=1000)]):
            with self.subTest(sequence=sequence), patch.object(bridge.Path, "lstat", side_effect=sequence):
                self.assertIsNone(bridge._socket_identity(SOCKET[0]))
        for value in ("/tmp/123_interop", "/run/WSL/1_interop/../x", "/run/WSL/0_interop", ""):
            self.assertIsNone(bridge._socket_identity(value))

    def test_discovery_is_bounded_and_ignores_untrusted_sockets(self):
        entries = [SimpleNamespace(path=f"/run/WSL/{index}_interop",
                                   stat=lambda follow_symlinks, index=index: SimpleNamespace(st_mtime_ns=index))
                   for index in range(1, 101)]
        with patch.dict(os.environ, {"WSL_INTEROP": "/run/WSL/9_interop"}), \
                patch.object(bridge.os, "scandir") as scan, \
                patch.object(bridge, "_socket_identity", side_effect=lambda value: (1, 2) if value.endswith("9_interop") else None):
            scan.return_value.__enter__.return_value = iter(entries)
            self.assertEqual([value[0] for value in bridge._sockets()],
                             ["/run/WSL/9_interop", "/run/WSL/59_interop", "/run/WSL/49_interop",
                              "/run/WSL/39_interop", "/run/WSL/29_interop", "/run/WSL/19_interop"])

    def test_probe_is_fixed_readonly_and_checks_socket_identity(self):
        with patch.object(bridge, "_socket_identity", return_value=SOCKET[1]), \
                patch.object(bridge, "_run", return_value=subprocess.CompletedProcess([], 0, b"user,sid", b"")) as run:
            self.assertTrue(bridge._probe(SOCKET, 2, WINDOWS))
            self.assertEqual(run.call_args.args[0], [WINDOWS.probe, "/user", "/fo", "csv", "/nh"])
            self.assertNotIn("data", run.call_args.kwargs)
        with patch.object(bridge, "_socket_identity", side_effect=[SOCKET[1], (3, 4)]), \
                patch.object(bridge, "_run", return_value=subprocess.CompletedProcess([], 0, b"user,sid", b"")):
            self.assertFalse(bridge._probe(SOCKET, 2, WINDOWS))

    def test_discovery_probes_beyond_three_and_has_a_global_deadline(self):
        sockets = [(f"/run/WSL/{index}_interop", (1, index)) for index in range(1, 7)]
        with patch.object(bridge, "_sockets", return_value=sockets), \
                patch.object(bridge, "_probe", side_effect=[False, False, False, True]) as probe:
            self.assertEqual(bridge._select_socket(WINDOWS), sockets[3])
            self.assertEqual(probe.call_count, 4)
        with patch.object(bridge, "_sockets", return_value=sockets), \
                patch.object(bridge.time, "monotonic", side_effect=[0, 0, 1, 2, 3]), \
                patch.object(bridge, "_probe", side_effect=subprocess.TimeoutExpired("probe", 0.5)) as probe:
            with self.assertRaisesRegex(bridge.BridgeError, "No responsive"):
                bridge._select_socket(WINDOWS)
            self.assertEqual(probe.call_count, 3)
            self.assertTrue(all(call.args[1] <= 2 for call in probe.call_args_list))

    def test_probe_allows_cold_start_without_extending_global_deadline(self):
        def cold_probe(socket, timeout, windows):
            self.assertEqual(socket, SOCKET)
            self.assertEqual(windows, WINDOWS)
            self.assertGreaterEqual(timeout, 1.5)
            return True
        with patch.object(bridge, '_sockets', return_value=[SOCKET]), \
                patch.object(bridge, '_probe', side_effect=cold_probe):
            self.assertEqual(bridge._select_socket(WINDOWS), SOCKET)


class WindowsLocationTests(unittest.TestCase):
    def test_authoritative_directory_supports_non_c_drive_and_custom_mount_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            drive = Path(temporary).resolve() / 'custom-drive'
            system = drive / 'Operating System' / 'System32'
            shell = system / 'WindowsPowerShell/v1.0/powershell.exe'
            shell.parent.mkdir(parents=True)
            shell.write_bytes(b'fixture; never executed')
            probe = system / 'whoami.exe'
            probe.write_bytes(b'fixture; never executed')
            raw = r'D:\Operating System\System32'
            with patch.object(bridge, '_path', side_effect=[str(drive), raw]) as translate, \
                    patch.object(bridge, '_run', side_effect=AssertionError('must not execute Windows')):
                tools = bridge._windows_tools({'ODS_WINDOWS_SYSTEM_DIRECTORY': raw})
            self.assertEqual(tools.shell, str(shell))
            self.assertEqual(tools.probe, str(probe))
            self.assertEqual(tools.module_path, raw + r'\WindowsPowerShell\v1.0\Modules')
            self.assertEqual(translate.call_args_list[0].args, ('D:\\', '-u'))

    def test_invalid_directory_rejected_before_translation_or_execution(self):
        for raw in (r'\\peer\share\System32', r'C:\Windows\..\System32', '/tmp/System32',
                    r'C:\Other', r'C:\Windows\System32:stream', 'C:\\Windows\\System32\n'):
            with self.subTest(raw=raw), patch.object(bridge, '_path') as translate:
                with self.assertRaises(ValueError):
                    bridge._windows_tools({'ODS_WINDOWS_SYSTEM_DIRECTORY': raw})
                translate.assert_not_called()

    def test_missing_legacy_windows_path_has_actionable_error_without_guessing_drive(self):
        with patch.object(bridge.shutil, 'which', return_value=None), patch.object(bridge, '_run') as run:
            with self.assertRaisesRegex(bridge.BridgeError, 'rerun the Windows installer'):
                bridge._windows_tools({})
            run.assert_not_called()

    def test_legacy_path_cannot_supply_a_linux_or_non_system_executable(self):
        for raw in (r'\\wsl.localhost\Ubuntu\tmp\powershell.exe', r'D:\user\powershell.exe'):
            with self.subTest(raw=raw), patch.object(bridge.shutil, 'which', return_value='/tmp/powershell.exe'), \
                    patch.object(bridge, '_path', return_value=raw), patch.object(bridge, '_run') as run:
                with self.assertRaises(bridge.BridgeError):
                    bridge._windows_tools({})
                run.assert_not_called()

    def test_legacy_system_path_is_translated_and_roundtrip_is_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            drive = Path(temporary).resolve()
            system = drive / 'Windows/System32'
            shell = system / 'WindowsPowerShell/v1.0/powershell.exe'
            shell.parent.mkdir(parents=True)
            shell.write_bytes(b'fixture')
            (system / 'whoami.exe').write_bytes(b'fixture')
            with patch.object(bridge.shutil, 'which', return_value=str(shell)), \
                    patch.object(bridge, '_path', side_effect=[r'E:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe',
                                                             str(drive), r'E:\Windows\System32']):
                self.assertEqual(bridge._windows_tools({}).shell, str(shell))
            with patch.object(bridge, '_path', side_effect=[str(drive), r'C:\unrelated\System32']):
                with self.assertRaisesRegex(bridge.BridgeError, 'canonical WSL translation'):
                    bridge._windows_tools({'ODS_WINDOWS_SYSTEM_DIRECTORY': r'E:\Windows\System32'})


class StartupRetirementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        program = self.root / 'installers/wsl-lifecycle.ps1'
        program.parent.mkdir()
        program.write_text('# fixture only; never executed')
        source = patch.object(bridge, '_SOURCE', self.root)
        source.start()
        self.addCleanup(source.stop)
        for name, value in (('_context', CONTEXT), ('_path', r'\\wsl.localhost\Ubuntu\startup.ps1'),
                            ('_select_socket', SOCKET), ('_socket_identity', SOCKET[1])):
            patcher = patch.object(bridge, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def result(self, state):
        return {'scope': 'wsl-startup', 'state': state,
                'identity': {'distro': 'ubuntu', 'installRoot': CONTEXT.install_dir}}

    def test_candidate_lifecycle_does_not_need_script_in_the_older_target(self):
        old = self.root / 'old-install-without-controllers'
        old.mkdir()
        with patch.object(bridge, '_run', return_value=completed(self.result('validated'))) as run:
            bridge.disable_startup(old, ENV, validate_only=True)
        self.assertFalse((old / 'installers').exists())
        bridge._context.assert_called_once_with(old, ENV)
        bridge._path.assert_called_once_with(str(self.root / 'installers/wsl-lifecycle.ps1'), '-w')
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('-InstallRoot') + 1], CONTEXT.install_dir)

    def test_fixed_retirement_command_and_readonly_precheck(self):
        for readonly, state in ((True, 'validated'), (False, 'disabled'), (False, 'unmanaged')):
            with self.subTest(readonly=readonly, state=state), patch.object(bridge, '_run',
                    return_value=completed(self.result(state))) as run:
                self.assertEqual(bridge.disable_startup(self.root, ENV, validate_only=readonly)['state'], state)
                command = run.call_args.args[0]
                self.assertEqual(command[0], WINDOWS.shell)
                self.assertNotIn('-Command', command)
                self.assertEqual(command[command.index('-Action') + 1], 'disable-startup')
                self.assertEqual(command[command.index('-Distro') + 1], CONTEXT.distro)
                self.assertEqual(command[command.index('-InstallRoot') + 1], CONTEXT.install_dir)
                self.assertNotIn('-StateRoot', command)
                self.assertEqual('-ValidateOnly' in command, readonly)
                self.assertEqual(run.call_args.kwargs['timeout'], 45)
                self.assertNotIn('-RetireRelay', command)

    def test_uninstall_explicitly_retires_relay_with_a_verified_receipt(self):
        for readonly, state, relay in ((True, 'validated', 'validated'),
                                       (False, 'disabled', 'stopped'),
                                       (False, 'unmanaged', 'unmanaged')):
            response = {**self.result(state), 'relayRetirement': relay}
            with self.subTest(state=state), patch.object(bridge, '_run', return_value=completed(response)) as run:
                bridge.disable_startup(self.root, ENV, validate_only=readonly, retire_relay=True)
                self.assertIn('-RetireRelay', run.call_args.args[0])
                self.assertEqual('-ValidateOnly' in run.call_args.args[0], readonly)
                self.assertEqual(run.call_args.kwargs['timeout'], 45 if readonly else 90)
                self.assertEqual(run.call_count, 1)

    def test_uninstall_rejects_missing_or_wrong_relay_receipt_without_replay(self):
        for response in (self.result('disabled'), {**self.result('disabled'), 'relayRetirement': 'validated'}):
            with self.subTest(response=response), patch.object(bridge, '_run', return_value=completed(response)) as run:
                with self.assertRaisesRegex(bridge.BridgeError, 'owned relay retirement'):
                    bridge.disable_startup(self.root, ENV, retire_relay=True)
                self.assertEqual(run.call_count, 1)

    def test_custom_state_root_is_forwarded_literally_for_precheck_and_retirement(self):
        for root in (r"D:\Owner's state $literal", r'\\server\private share\ODS state', 'E:/ODS/state/'):
            for readonly, state in ((True, 'validated'), (False, 'disabled')):
                with self.subTest(root=root, readonly=readonly), patch.object(bridge, '_run',
                        return_value=completed(self.result(state))) as run:
                    bridge.disable_startup(self.root, {**ENV, 'ODS_WSL_STATE_ROOT': root}, validate_only=readonly)
                    command = run.call_args.args[0]
                    self.assertEqual(command[command.index('-StateRoot') + 1], root)
                    self.assertEqual(command[command.index('-InstallRoot') + 1], CONTEXT.install_dir)
                    self.assertNotIn('-Command', command)
                    self.assertEqual(run.call_count, 1)

    def test_invalid_state_root_is_rejected_before_interop_or_dispatch(self):
        for root in ('', None, True, 'relative', 'C:relative', 'C:\\', r'\\server\share',
                     r'\\?\C:\state', r'\\.\state\directory', r'C:\state\..\other',
                     r'C:\state\.\child', r'C:\state:stream', 'C:\\state"injected',
                     'C:\\state\ninjected', '/mnt/c/state', 'x' * 4097):
            with self.subTest(root=root), patch.object(bridge, '_run') as run:
                with self.assertRaises(ValueError):
                    bridge.disable_startup(self.root, {**ENV, 'ODS_WSL_STATE_ROOT': root})
                run.assert_not_called()
        bridge._context.assert_not_called()
        bridge._select_socket.assert_not_called()

    def test_custom_state_root_does_not_relax_installation_binding(self):
        response = self.result('disabled')
        response['identity']['installRoot'] = '/home/other/ods'
        with patch.object(bridge, '_run', return_value=completed(response)) as run:
            with self.assertRaises(bridge.BridgeError):
                bridge.disable_startup(self.root, {**ENV, 'ODS_WSL_STATE_ROOT': r'D:\ODS state'})
            self.assertEqual(run.call_count, 1)

    def test_unproved_retirement_and_replaced_socket_are_never_replayed(self):
        bad = self.result('disabled')
        bad['identity']['installRoot'] = '/another/install'
        for result in (completed(bad), completed(self.result('validated')),
                       subprocess.CompletedProcess([], 1, b'', b'foreign task'),
                       subprocess.CompletedProcess([], 0, b'not json', b''),
                       subprocess.TimeoutExpired('fixture', 1)):
            with self.subTest(result=result), patch.object(bridge, '_run', side_effect=[result]) as run:
                with self.assertRaises((bridge.BridgeError, subprocess.TimeoutExpired)):
                    bridge.disable_startup(self.root, ENV)
                self.assertEqual(run.call_count, 1)
        with patch.object(bridge, '_run', return_value=completed(self.result('disabled'))) as run, \
                patch.object(bridge, '_socket_identity', side_effect=[SOCKET[1], (1, 99)]):
            with self.assertRaises(bridge.BridgeError):
                bridge.disable_startup(self.root, ENV)
            self.assertEqual(run.call_count, 1)


class ControllerSourceTests(unittest.TestCase):
    def test_source_controller_keeps_the_target_installation_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory).resolve() / 'candidate'
            old = Path(directory).resolve() / 'old-install'
            old.mkdir()
            controller = current / bridge._CONTROLLER
            controller.parent.mkdir(parents=True)
            controller.write_text('# fixture only; never executed')
            with patch.object(bridge, '_SOURCE', current), patch.object(bridge, '_windows_tools', return_value=WINDOWS), \
                    patch.object(Path, 'as_posix', return_value='/home/owner/old-install'), \
                    patch.object(bridge, '_path', side_effect=[r'\\wsl.localhost\Ubuntu', r'\\wsl.localhost\Ubuntu\candidate\controller.ps1']) as translate:
                context = bridge._context(old, ENV)
            self.assertEqual(context.install_dir, '/home/owner/old-install')
            self.assertEqual(context.controller, r'\\wsl.localhost\Ubuntu\candidate\controller.ps1')
            self.assertEqual(translate.call_args.args, (str(controller), '-w'))
            self.assertFalse((old / bridge._CONTROLLER).exists())


class BoundedProcessTests(unittest.TestCase):
    def test_real_worker_roundtrip(self):
        result = bridge._run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
                             data=b'{"action":"status"}', timeout=5)
        self.assertEqual(result.stdout, b'{"action":"status"}')
        self.assertEqual(result.returncode, 0)

    def test_real_worker_oversized_stdout_and_stderr(self):
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream), self.assertRaises(bridge.BridgeError):
                bridge._run([sys.executable, "-c", f"import sys; sys.{stream}.buffer.write(b'x' * 1000000)"], timeout=5)

    def test_real_worker_timeout_is_bounded(self):
        before = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            bridge._run([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.1)
        self.assertLess(time.monotonic() - before, 4)

    def test_oversized_input_and_invalid_deadline_never_spawn(self):
        for data, timeout in ((b"x" * 65537, 5), (None, float("nan")), (None, 1201)):
            with self.subTest(timeout=timeout), patch.object(bridge.subprocess, "Popen") as popen:
                with self.assertRaises(ValueError):
                    bridge._run(["unused"], data=data, timeout=timeout)
                popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
