import importlib.util
import json
import tarfile
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "pixel_test_portal_outcome_livesystem", ROOT / "scripts/portal_outcome_livesystem.py",
)
livesystem = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(livesystem)


class PortalOutcomeLiveSystemTests(unittest.TestCase):
    def _pixel_config_fixture(self, parent: Path, *, startup_timeout_seconds: int = 900):
        runtime_root = parent / "runtime"
        runtime_root.mkdir(mode=0o700)
        environment_template = parent / "environment.json"
        environment_template.write_text('{"researchRuntime":{"enabled":true}}\n', encoding="utf-8")
        model_backend_template = parent / "model-backend.json"
        model_backend_template.write_text(json.dumps({
            "$schema": "https://osmantic.com/pixel/schemas/work-model-backend-config-v1.schema.json",
            "schemaVersion": 1,
            "readiness": {
                "startupTimeoutSeconds": startup_timeout_seconds,
                "probeIntervalMilliseconds": 1000,
            },
        }), encoding="utf-8")
        pixel_config = parent / "pixel-system.json"
        pixel_config.write_text(json.dumps({
            "runtimeRoot": str(runtime_root),
            "environmentTemplatePath": str(environment_template),
            "modelBackendTemplatePath": str(model_backend_template),
        }), encoding="utf-8")
        if livesystem.os.name != "nt":
            for path in (environment_template, model_backend_template, pixel_config):
                path.chmod(0o600)
        return pixel_config, model_backend_template

    def test_health_deadline_derives_from_exact_pixel_model_backend_template(self):
        with tempfile.TemporaryDirectory() as temporary:
            pixel_config, model_backend_template = self._pixel_config_fixture(
                Path(temporary).resolve(), startup_timeout_seconds=900,
            )
            system = livesystem.DockerSystem(
                root=ROOT, codex_image="fixture-codex:latest", pixel_system_config_path=pixel_config,
            )
            self.assertEqual(system.health_deadline_seconds, 900)
            backend, _raw = livesystem.evaluation.read_json(
                model_backend_template, "private Pixel model backend template", private=True,
            )
            self.assertEqual(
                system.pixel_model_backend_template_sha256,
                livesystem.evaluation.sha256(livesystem.evaluation.canonical(backend)),
            )

    def test_explicit_health_deadline_is_only_allowed_without_pixel_configuration(self):
        self.assertEqual(
            livesystem.DockerSystem(root=ROOT, codex_image="fixture-codex:latest").health_deadline_seconds,
            180,
        )
        self.assertEqual(
            livesystem.DockerSystem(
                root=ROOT, codex_image="fixture-codex:latest", health_deadline_seconds=1200,
            ).health_deadline_seconds,
            1200,
        )
        with self.assertRaisesRegex(livesystem.evaluation.OutcomeError, "health deadline"):
            livesystem.DockerSystem(
                root=ROOT, codex_image="fixture-codex:latest", health_deadline_seconds=5,
            )
        with tempfile.TemporaryDirectory() as temporary:
            pixel_config, _model_backend = self._pixel_config_fixture(Path(temporary).resolve())
            for explicit in (180, 1200):
                with self.subTest(explicit=explicit), self.assertRaisesRegex(
                    livesystem.evaluation.OutcomeError, "derived from the Pixel model backend",
                ):
                    livesystem.DockerSystem(
                        root=ROOT, codex_image="fixture-codex:latest",
                        pixel_system_config_path=pixel_config, health_deadline_seconds=explicit,
                    )

    def test_pixel_model_backend_deadline_rejects_path_contract_and_bound_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            pixel_config, model_backend = self._pixel_config_fixture(parent)
            base = json.loads(pixel_config.read_text(encoding="utf-8"))

            relative_config = parent / "pixel-relative.json"
            relative_config.write_text(json.dumps({
                **base, "modelBackendTemplatePath": "model-backend.json",
            }), encoding="utf-8")
            if livesystem.os.name != "nt":
                relative_config.chmod(0o600)
            with self.assertRaisesRegex(livesystem.evaluation.OutcomeError, "model backend template"):
                livesystem.DockerSystem(
                    root=ROOT, codex_image="fixture-codex:latest",
                    pixel_system_config_path=relative_config,
                )

            mutations = [
                {"$schema": "invalid", "schemaVersion": 1, "readiness": {
                    "startupTimeoutSeconds": 900, "probeIntervalMilliseconds": 1000,
                }},
                {"$schema": "https://osmantic.com/pixel/schemas/work-model-backend-config-v1.schema.json",
                 "schemaVersion": 1, "readiness": {
                    "startupTimeoutSeconds": 3601, "probeIntervalMilliseconds": 1000,
                 }},
                {"$schema": "https://osmantic.com/pixel/schemas/work-model-backend-config-v1.schema.json",
                 "schemaVersion": 1, "readiness": {
                    "startupTimeoutSeconds": 900, "probeIntervalMilliseconds": 99,
                 }},
                {"$schema": "https://osmantic.com/pixel/schemas/work-model-backend-config-v1.schema.json",
                 "schemaVersion": 1, "readiness": {
                    "startupTimeoutSeconds": 900, "probeIntervalMilliseconds": 1000, "extra": True,
                 }},
            ]
            for index, mutation in enumerate(mutations):
                with self.subTest(index=index):
                    model_backend.write_text(json.dumps(mutation), encoding="utf-8")
                    if livesystem.os.name != "nt":
                        model_backend.chmod(0o600)
                    with self.assertRaises(livesystem.evaluation.OutcomeError):
                        livesystem.DockerSystem(
                            root=ROOT, codex_image="fixture-codex:latest",
                            pixel_system_config_path=pixel_config,
                        )

    def test_private_tmpfs_and_container_identity_preserve_private_host_ownership(self):
        value = livesystem.private_tmpfs("/work", size="1g")
        self.assertTrue(value.startswith("/work:rw,nosuid,nodev,size=1g,mode=0700"))
        if livesystem.os.name == "nt":
            self.assertEqual(livesystem.container_user_args(), [])
        else:
            self.assertEqual(
                livesystem.container_user_args(),
                ["--user", f"{livesystem.os.getuid()}:{livesystem.os.getgid()}"],
            )
            pass
        uid, gid = livesystem.comparison_container_identity()
        self.assertIn(f"uid={uid}", value)
        self.assertIn(f"gid={gid}", value)
        with self.assertRaises(livesystem.evaluation.OutcomeError):
            livesystem.private_tmpfs("relative", size="1g")

    def test_codex_container_resources_equal_the_admitted_backend_neutral_limits(self):
        environment = {"limits": {
            "maxIterations": 40, "maxToolCalls": 4000, "maxConcurrentSubagents": 1,
            "maxCpuCores": 4, "maxMemoryMiB": 8192, "maxDiskBytes": 10737418240,
            "maxNetworkBytes": 104857600, "maxFailures": 8, "noProgressLimit": 4,
            "maxPids": 1024,
        }}
        args = livesystem.codex_container_resource_args(environment)
        self.assertEqual(args[args.index("--pids-limit") + 1], "96")
        self.assertEqual(args[args.index("--memory") + 1], "8192m")
        self.assertEqual(args[args.index("--memory-swap") + 1], "8192m")
        self.assertEqual(args[args.index("--cpus") + 1], "4")
        self.assertIn("size=5368709120", args[args.index("--tmpfs") + 1])
        widened = json.loads(json.dumps(environment))
        widened["limits"]["unreviewed"] = 1
        with self.assertRaisesRegex(livesystem.evaluation.OutcomeError, "environment limits"):
            livesystem.codex_container_resource_args(widened)
        narrowed = json.loads(json.dumps(environment))
        narrowed["limits"]["maxPids"] = 64
        with self.assertRaisesRegex(livesystem.evaluation.OutcomeError, "below Pixel"):
            livesystem.codex_container_resource_args(narrowed)

    def test_codex_workspace_uses_half_the_disk_envelope_in_an_exact_tmpfs_volume(self):
        environment = {"limits": {
            "maxIterations": 40, "maxToolCalls": 4000, "maxConcurrentSubagents": 1,
            "maxCpuCores": 4, "maxMemoryMiB": 8192, "maxDiskBytes": 10737418240,
            "maxNetworkBytes": 104857600, "maxFailures": 8, "noProgressLimit": 4, "maxPids": 1024,
        }}
        system = livesystem.DockerSystem(root=ROOT, codex_image="fixture-codex:latest")
        calls = []
        system._docker = lambda args, **_kwargs: calls.append(args) or args[-1]
        volume, maximum = system.codex_workspace_volume_create("pixel-outcome-aaaaaaaaaaaa", environment)
        self.assertEqual(volume, "pixel-outcome-aaaaaaaaaaaa-codex-workspace")
        self.assertEqual(maximum, 5368709120)
        uid, gid = livesystem.comparison_container_identity()
        option = calls[-1][calls[-1].index("--opt", calls[-1].index("device=tmpfs") + 1) + 1]
        self.assertEqual(option, f"o=size={maximum},uid={uid},gid={gid},mode=0700,nosuid,nodev")
        system._docker = lambda args, **_kwargs: calls.append(args) or "a" * 64
        keeper = system.codex_workspace_keeper_start("pixel-outcome-aaaaaaaaaaaa", volume)
        self.assertEqual(keeper, "pixel-outcome-aaaaaaaaaaaa-codex-workspace-keeper")
        self.assertIn("none", calls[-1])
        self.assertIn(f"type=volume,source={volume},target=/workspace", calls[-1])
        with tempfile.TemporaryDirectory() as temporary:
            host = Path(temporary).resolve()
            system._docker = lambda args, **_kwargs: calls.append(args) or json.dumps({
                "operation": "codex-comparison-workspace-init", "entries": 0, "bytes": 0,
                "sha256": livesystem.hashlib.sha256(b"").hexdigest(),
            })
            receipt = system.codex_workspace_copy("init", volume, host, maximum)
            self.assertEqual(receipt["bytes"], 0)
            self.assertTrue(any(f"type=volume,source={volume},target=/workspace" == item for item in calls[-1]))

    def test_executable_digest_inspection_is_offline_read_only_and_capability_free(self):
        system = livesystem.DockerSystem(root=ROOT, codex_image="fixture-codex:latest")
        observed = {}

        def capture(args, *, timeout, input_bytes=None):
            observed["args"] = args
            observed["timeout"] = timeout
            observed["input"] = input_bytes
            return "a" * 64 + "  /usr/local/bin/codex\n"

        system._docker = capture
        self.assertEqual(system.executable_sha256("fixture-codex:latest", "/usr/local/bin/codex"), "a" * 64)
        self.assertEqual(observed["timeout"], 120)
        self.assertIsNone(observed["input"])
        self.assertEqual(
            observed["args"],
            [
                "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "--pids-limit", "32", "--memory", "128m", "--memory-swap", "128m",
                "--entrypoint", "/bin/sh", "fixture-codex:latest", "-c",
                "sha256sum /usr/local/bin/codex",
            ],
        )
        with self.assertRaisesRegex(livesystem.evaluation.OutcomeError, "path is invalid"):
            system.executable_sha256("fixture-codex:latest", "/tmp/bad path")

    def test_codex_harness_contract_binds_resolved_image_binary_config_and_source(self):
        self.assertTrue({
            "scripts/portal_outcome_evaluation.py",
            "scripts/portal_outcome_task.py",
            "scripts/portal_outcome_verifier.py",
            "scripts/portal_outcome_runner.py",
            "scripts/portal_outcome_pair.py",
            "scripts/portal_outcome_pair_preflight.py",
            "scripts/portal_outcome_battery_campaign.py",
        }.issubset(set(livesystem.CODEX_HARNESS_FILES)))
        system = livesystem.DockerSystem(root=ROOT, codex_image="fixture-codex:latest")
        system.image_id = lambda _reference: "sha256:" + "a" * 64
        system.codex_comparison_toolchain_sha256 = lambda: "e" * 64
        boundary_sha256 = livesystem.evaluation.sha256(
            (ROOT / "deploy/agent-comparison/inference-boundary.mjs").read_bytes(),
        )
        policy_sha256 = livesystem.evaluation.sha256(
            (ROOT / "deploy/work-model-proxy/inference-policy.mjs").read_bytes(),
        )
        research_mcp_sha256 = livesystem.evaluation.sha256(
            (ROOT / "deploy/agent-comparison/research-mcp-server.mjs").read_bytes(),
        )
        research_tool_sha256 = livesystem.evaluation.sha256(
            (ROOT / "deploy/work-runner/research-tool.mjs").read_bytes(),
        )
        workspace_copier_sha256 = livesystem.evaluation.sha256(
            (ROOT / "scripts/codex_comparison_workspace.py").read_bytes(),
        )
        embedded = {
            "inference-boundary.mjs": boundary_sha256, "inference-policy.mjs": policy_sha256,
            "research-mcp-server.mjs": research_mcp_sha256, "research-tool.mjs": research_tool_sha256,
            "codex_comparison_workspace.py": workspace_copier_sha256,
        }
        system.executable_sha256 = lambda _reference, path: next(
            (digest for suffix, digest in embedded.items() if path.endswith(suffix)), "b" * 64,
        )
        first = system.codex_harness_contract_sha256()
        second = system.codex_harness_contract_sha256()
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[a-f0-9]{64}$")
        system.health_deadline_seconds = 181
        self.assertNotEqual(first, system.codex_harness_contract_sha256())
        system.health_deadline_seconds = 180
        system.pixel_model_backend_template_sha256 = "f" * 64
        self.assertNotEqual(first, system.codex_harness_contract_sha256())
        system.pixel_model_backend_template_sha256 = None
        system.executable_sha256 = lambda _reference, path: next(
            (digest for suffix, digest in embedded.items() if path.endswith(suffix)), "c" * 64,
        )
        self.assertNotEqual(first, system.codex_harness_contract_sha256())
        system.executable_sha256 = lambda _reference, _path: "d" * 64
        with self.assertRaisesRegex(livesystem.evaluation.OutcomeError, "image code differs"):
            system.codex_harness_contract_sha256()

    def test_codex_comparison_toolchain_probe_is_offline_exact_and_content_free(self):
        system = livesystem.DockerSystem(root=ROOT, codex_image="fixture-codex:latest")
        observed = {}

        def capture(args, *, timeout, input_bytes=None):
            observed.update(args=args, timeout=timeout, input=input_bytes)
            return json.dumps(livesystem.CODEX_COMPARISON_TOOLCHAIN, sort_keys=True, separators=(",", ":"))

        system._docker = capture
        digest = system.codex_comparison_toolchain_sha256()
        self.assertEqual(digest, livesystem.evaluation.sha256(
            livesystem.evaluation.canonical(livesystem.CODEX_COMPARISON_TOOLCHAIN),
        ))
        self.assertEqual(observed["timeout"], 120)
        self.assertIsNone(observed["input"])
        self.assertIn("--network", observed["args"])
        self.assertIn("none", observed["args"])
        self.assertIn("--read-only", observed["args"])
        self.assertIn("--cap-drop", observed["args"])
        self.assertEqual(observed["args"][observed["args"].index("--pids-limit") + 1], "96")
        self.assertIn("fixture-codex:latest", observed["args"])
        widened = dict(livesystem.CODEX_COMPARISON_TOOLCHAIN)
        widened["python"] = "3.12.0"
        system._docker = lambda *_args, **_kwargs: json.dumps(widened)
        with self.assertRaisesRegex(livesystem.evaluation.OutcomeError, "exact backend-neutral toolchain"):
            system.codex_comparison_toolchain_sha256()

    def test_created_at_normalizes_docker_nanoseconds(self):
        self.assertEqual(
            livesystem.normalize_created_at("2026-08-12T21:11:03.123456789Z"),
            "2026-08-12T21:11:03.123456Z",
        )
        self.assertEqual(
            livesystem.normalize_created_at("2026-08-12T21:11:03Z"),
            "2026-08-12T21:11:03Z",
        )
        with self.assertRaises(livesystem.evaluation.OutcomeError):
            livesystem.normalize_created_at("2026-08-12 21:11:03")
        with self.assertRaises(livesystem.evaluation.OutcomeError):
            livesystem.normalize_created_at("2026-08-12T21:11:03+02:00")

    def test_request_count_parses_only_known_counters(self):
        vllm = (
            "# TYPE vllm:request_success_total counter\n"
            'vllm:request_success_total{finished_reason="stop"} 3.0\n'
            'vllm:request_success_total{finished_reason="length"} 1.0\n'
        )
        self.assertEqual(livesystem.parse_request_count(vllm), 4)
        llama = (
            "llamacpp:prompt_tokens_total 30\n"
            "llamacpp:requests_processing 0\n"
        )
        self.assertIsNone(livesystem.parse_request_count(llama))
        self.assertIsNone(livesystem.parse_request_count("vllm:request_success_total oops\n"))
        self.assertIsNone(livesystem.parse_request_count(None))

    def test_codex_script_binds_hardened_flags_and_quotes_safely(self):
        hardened = livesystem.load_hardened_codex_config(ROOT)
        self.assertIn("-c", hardened)
        self.assertIn("features.shell_tool=false", hardened)
        script = livesystem.build_codex_script(
            hardened, model_id="DeepSeek-V4-Flash-0731", model_context_window=1048576,
            wire_api="responses", env_key="PIXEL_OUTCOME_KEY",
            profile="builder", capabilities=["filesystem-read", "process-execution", "reasoning"],
            source_media_type="application/x-tar", tool_policy={
                "workspace": "disposable-read-write", "tools": ["read", "search", "shell"],
            },
        )
        self.assertTrue(script.startswith("mkdir -p /work/.codex"))
        self.assertIn("cd /work/source && exec /usr/local/bin/codex exec", script)
        self.assertIn("--sandbox workspace-write", script)
        self.assertIn("features.shell_tool=true", script)
        self.assertIn("features.multi_agent=false", script)
        self.assertNotIn("features.shell_tool=false", script)
        self.assertIn("model_providers.pixel_outcome.wire_api=\"responses\"", script.replace("'", ""))
        self.assertIn('model_catalog_json="/input/model-catalog.json"', script.replace("'", ""))
        self.assertIn("model_context_window=1048576", script)
        self.assertIn("model_auto_compact_token_limit=943718", script)
        self.assertIn("requires_openai_auth=false", script)
        self.assertTrue(script.rstrip().endswith(" -"))
        delegated = livesystem.build_codex_script(
            hardened, model_id="DeepSeek-V4-Flash-0731", model_context_window=1048576,
            wire_api="responses", env_key="PIXEL_OUTCOME_KEY",
            profile="builder", capabilities=["filesystem-read", "process-execution", "reasoning"],
            source_media_type="application/x-tar", tool_policy={
                "workspace": "disposable-read-write", "tools": ["read", "search", "shell", "task"],
                "maximumSubagents": 1,
            },
        )
        self.assertIn("features.multi_agent=true", delegated)
        self.assertNotIn("features.multi_agent=false", delegated)
        with self.assertRaisesRegex(livesystem.evaluation.OutcomeError, "single-subagent"):
            livesystem.build_codex_script(
                hardened, model_id="DeepSeek-V4-Flash-0731", model_context_window=1048576,
                wire_api="responses", env_key="PIXEL_OUTCOME_KEY", profile="builder",
                capabilities=["filesystem-read", "process-execution", "reasoning"],
                source_media_type="application/x-tar", tool_policy={
                    "workspace": "disposable-read-write", "tools": ["read", "search", "shell", "task"],
                    "maximumSubagents": 2,
                },
            )
        with self.assertRaises(livesystem.evaluation.OutcomeError):
            livesystem.build_codex_script(
                hardened, model_id="m", model_context_window=32768, wire_api="websocket", env_key="K", profile="scout",
                capabilities=["reasoning"], source_media_type="text/plain",
                tool_policy={"workspace": "read-only", "tools": ["read", "search"]},
            )

        read_only = livesystem.build_codex_script(
            hardened, model_id="DeepSeek-V4-Flash-0731", model_context_window=1048576,
            wire_api="responses", env_key="PIXEL_OUTCOME_KEY",
            profile="scout", capabilities=["filesystem-read", "reasoning"], source_media_type="text/plain",
            tool_policy={"workspace": "read-only", "tools": ["read", "search"]},
        )
        self.assertIn("--sandbox read-only", read_only)
        self.assertIn("features.shell_tool=false", read_only)
        self.assertNotIn("cp -R", read_only)

        researcher = livesystem.build_codex_script(
            hardened, model_id="DeepSeek-V4-Flash-0731", model_context_window=1048576,
            wire_api="responses", env_key="PIXEL_OUTCOME_KEY", profile="researcher",
            capabilities=["filesystem-read", "public-research", "reasoning"],
            source_media_type="application/x-tar", tool_policy={
                "workspace": "read-only", "tools": ["public-research"],
                "brokeredServices": ["public-research"],
            },
        )
        self.assertIn("--output-schema /input/research-output-schema.json", researcher)
        self.assertIn('mcp_servers.pixel_research.url="http://research-mcp:8081/mcp"', researcher.replace("'", ""))
        self.assertIn("features.shell_tool=false", researcher)
        with self.assertRaisesRegex(livesystem.evaluation.OutcomeError, "public-research"):
            livesystem.build_codex_script(
                hardened, model_id="DeepSeek-V4-Flash-0731", model_context_window=1048576,
                wire_api="responses", env_key="PIXEL_OUTCOME_KEY", profile="researcher",
                capabilities=["filesystem-read", "reasoning"], source_media_type="application/x-tar",
                tool_policy={"workspace": "read-only", "tools": ["read"], "brokeredServices": []},
            )

    def test_codex_model_catalog_preserves_pinned_instructions_and_full_dsv4_context(self):
        payload = livesystem.build_codex_model_catalog(
            ROOT, model_id="DeepSeek-V4-Flash-0731", context_window=1048576,
        )
        catalog = json.loads(payload)
        self.assertEqual(list(catalog), ["models"])
        self.assertEqual(len(catalog["models"]), 1)
        model = catalog["models"][0]
        self.assertEqual(model["slug"], "DeepSeek-V4-Flash-0731")
        self.assertEqual(model["context_window"], 1048576)
        self.assertEqual(model["max_context_window"], 1048576)
        self.assertEqual(model["auto_compact_token_limit"], 943718)
        self.assertTrue(model["supports_reasoning_summary_parameter"])
        self.assertEqual(model["default_reasoning_summary"], "auto")
        self.assertFalse(model["include_apps_usage_instructions"])
        self.assertEqual(model["apply_patch_tool_type"], "freeform")
        self.assertEqual(model["multi_agent_version"], "v1")
        self.assertNotIn(None, model.values())
        self.assertEqual(
            livesystem.evaluation.sha256(model["base_instructions"].encode("utf-8")),
            livesystem.CODEX_PINNED_PROMPT_SHA256,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prompt = root / livesystem.CODEX_PINNED_PROMPT_RELATIVE
            prompt.parent.mkdir(parents=True)
            prompt.write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(livesystem.evaluation.OutcomeError, "differ"):
                livesystem.build_codex_model_catalog(
                    root, model_id="DeepSeek-V4-Flash-0731", context_window=1048576,
                )

    def test_workspace_delta_is_independent_bounded_and_rejects_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            baseline = parent / "baseline"
            workspace = parent / "workspace"
            baseline.mkdir()
            workspace.mkdir()
            (baseline / "kept.txt").write_text("same\n", encoding="utf-8")
            (workspace / "kept.txt").write_text("same\n", encoding="utf-8")
            (baseline / "changed.txt").write_text("old\n", encoding="utf-8")
            (workspace / "changed.txt").write_text("new\n", encoding="utf-8")
            (workspace / "added.txt").write_text("added\n", encoding="utf-8")
            artifacts = livesystem.collect_workspace_delta(baseline, workspace, 65536)
            self.assertEqual([item["kind"] for item in artifacts], ["test-evidence", "patch"])
            manifest = json.loads(artifacts[0]["payload"])
            self.assertEqual([item["path"] for item in manifest["changedFiles"]], ["added.txt", "changed.txt"])
            patch = artifacts[1]["payload"].decode("utf-8")
            self.assertIn("+++ b/added.txt", patch)
            self.assertIn("-old", patch)
            self.assertIn("+new", patch)
            with self.assertRaisesRegex(livesystem.evaluation.OutcomeError, "artifact ceiling"):
                livesystem.collect_workspace_delta(baseline, workspace, 4)
            linked = workspace / "linked.txt"
            try:
                linked.symlink_to(workspace / "added.txt")
            except OSError:
                return
            with self.assertRaisesRegex(livesystem.evaluation.OutcomeError, "symbolic link"):
                livesystem.collect_workspace_delta(baseline, workspace, 65536)

    def test_independent_workspace_verification_is_fresh_scored_and_immutable(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            baseline = parent / "baseline"
            workspace = parent / "workspace"
            run_dir = parent / "run"
            baseline.mkdir()
            workspace.mkdir()
            run_dir.mkdir()
            (baseline / "app.py").write_text("value = 1\n", encoding="utf-8")
            (workspace / "app.py").write_text("value = 2\n", encoding="utf-8")
            environment = {
                "limits": {"maxPids": 128, "maxMemoryMiB": 2048, "maxCpuCores": 2},
                "verifier": {"imageDigest": "sha256:" + "e" * 64},
            }
            definition = {
                "acceptanceCriteria": ["Candidate is safe", "Candidate is correct"],
                "workspaceVerification": {
                    "checks": [
                        {"id": "patch-boundary", "kind": "patch-integrity", "criterionIndexes": [0]},
                        {"id": "semantic", "kind": "command", "criterionIndexes": [1], "workingDirectory": "source", "argv": ["/usr/bin/python3", "-c", "print('OK')"], "timeoutSeconds": 30, "maxOutputBytes": 65536},
                    ],
                    "immutablePathPrefixes": ["source/__pixel_inert__/"],
                    "maxRuntimeSeconds": 60, "maxOutputBytes": 65536,
                },
            }
            system = livesystem.DockerSystem(root=ROOT, codex_image="fixture-codex:latest")
            system.image_id = lambda digest: digest
            system._run_verifier_check = lambda **kwargs: {
                "id": kwargs["check"]["id"], "kind": "command", "criterionIndexes": kwargs["check"]["criterionIndexes"],
                "status": "pass", "runtimeMilliseconds": 12, "exitCode": 0, "signal": None,
                "timedOut": False, "outputLimitExceeded": False, "spawnFailed": False,
                "stdoutBytes": 3, "stdoutSha256": "1" * 64, "stderrBytes": 0,
                "stderrSha256": livesystem.evaluation.sha256(b""), "cleanupVerified": True,
            }
            receipt = system.verify_workspace(
                baseline=baseline, workspace=workspace, definition=definition, environment=environment,
                admission={"taskId": "outcometask-1786550400001-aaaaaaaaaaaa", "budgets": {"artifactBytes": 65536}, "bindings": {"sourceSnapshotSha256": "3" * 64}},
                run_dir=run_dir, run_id="outcomerun-1786550400001-bbbbbbbbbbbb",
            )
            self.assertEqual(receipt["status"], "pass")
            self.assertTrue(receipt["cleanupVerified"])
            self.assertEqual([item["status"] for item in receipt["criteria"]], ["pass", "pass"])
            protected = json.loads(json.dumps(definition))
            protected["workspaceVerification"]["immutablePathPrefixes"] = ["source/app.py"]
            blocked = system.verify_workspace(
                baseline=baseline, workspace=workspace, definition=protected, environment=environment,
                admission={"taskId": "outcometask-1786550400001-aaaaaaaaaaaa", "budgets": {"artifactBytes": 65536}, "bindings": {"sourceSnapshotSha256": "3" * 64}},
                run_dir=run_dir, run_id="outcomerun-1786550400002-cccccccccccc",
            )
            self.assertEqual(blocked["status"], "fail")
            self.assertTrue(blocked["immutablePathViolation"])

    def test_last_agent_message_takes_the_final_structured_message(self):
        transcript = "\n".join([
            "noise line",
            '{"type":"item.completed","item":{"type":"agent_message","text":"first"}}',
            '{"type":"item.completed","item":{"type":"error","message":"warning"}}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"final findings"}}',
        ])
        self.assertEqual(livesystem.last_agent_message(transcript), "final findings")
        self.assertIsNone(livesystem.last_agent_message("no structured lines"))

    def test_researcher_composition_requires_broker_transport_and_returns_only_finalized_evidence(self):
        class FinishedProcess:
            returncode = 0
            def communicate(self, timeout=None):
                self.timeout = timeout
                return b"", b""
            def poll(self): return self.returncode

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            pixel_config, _model_backend = self._pixel_config_fixture(parent)
            runtime_root = parent / "runtime"
            system = livesystem.DockerSystem(
                root=ROOT, codex_image="fixture-codex:latest", pixel_system_config_path=pixel_config,
            )
            run_id = "outcomerun-1786550400003-aaaaaaaaaaac"
            run_dir = parent / "run"
            run_dir.mkdir(mode=0o700)
            source_tar = parent / "source.tar"
            source_file = parent / "source.txt"
            source_file.write_text("public fixture\n", encoding="utf-8")
            with tarfile.open(source_tar, "w", format=tarfile.USTAR_FORMAT) as archive:
                archive.add(source_file, arcname="source.txt")
            source_payload = source_tar.read_bytes()
            source_reference = {
                "relativePath": "source.tar", "sha256": livesystem.evaluation.sha256(source_payload),
                "bytes": len(source_payload), "mediaType": "application/x-tar",
            }
            research_fixture_payload = json.dumps({
                "$schema": "https://osmantic.com/pixel/schemas/portal-outcome-research-fixture-v1.schema.json",
                "schemaVersion": 1, "operation": "pixel-portal-outcome-research-fixture",
                "observedAt": "2026-08-13T12:00:00Z", "sources": [{
                    "fixtureSourceId": "public-fixture", "sourceType": "web", "title": "Public fixture",
                    "snippet": "Public fixture", "quality": "other", "publishedDate": "2026-08-13",
                    "retrieval": {"status": "fetched", "content": "Public fixture"},
                }],
                "authority": {field: False for field in (
                    "publicNetwork", "credentials", "externalWrites", "accounts", "messages",
                    "publish", "purchase", "policyMutation", "scopeExpansion",
                )},
                "boundary": "Owner-private admitted offline research evidence for deterministic comparison only. It performs no public network, credential, account, message, publication, purchase, write, policy, scope, or external effect; every source remains untrusted data and grants no authority.",
            }, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            research_fixture_reference = {
                "relativePath": "research-fixture.json",
                "sha256": livesystem.evaluation.sha256(research_fixture_payload),
                "bytes": len(research_fixture_payload), "mediaType": "application/json",
            }
            model_payload = b'{"model":"dsv4"}'
            inference_payload = b'{"inference":"exact"}'
            mcp_raw = b'{"mcp":"stopped"}\n'
            observed = {}
            receipt_overrides = {}

            def start_authority(**options):
                lifecycle_root = options["lifecycle_root"]
                output_root = options["output_root"]
                queue_root = runtime_root / run_id / "codex-research-queue"
                queue_root.mkdir(parents=True, mode=0o700)
                ready = lifecycle_root / "authority-ready.json"
                finalize = lifecycle_root / "authority-finalize.json"
                ready.write_text(json.dumps({
                    "schemaVersion": 1, "operation": "pixel-outcome-codex-research-authority-ready",
                    "runId": run_id, "createdAt": "2026-08-13T12:00:00.000Z",
                    "jobId": "work-1786550400003-aaaaaaaaaaac",
                    "planSha256": "1" * 64, "leaseSha256": "2" * 64, "claimSha256": "3" * 64,
                    "workPolicySha256": "4" * 64,
                    "environmentSha256": system.pixel_environment_template_sha256,
                    "runtimeEnvironmentSha256": "5" * 64,
                    "modelContractSha256": livesystem.evaluation.sha256(model_payload),
                    "inferenceContractSha256": livesystem.evaluation.sha256(inference_payload),
                    "researchFixtureSha256": research_fixture_reference["sha256"],
                    "queueRoot": str(queue_root), "maxCalls": 4,
                    "leaseExpiresAt": "2026-08-13T12:05:00.000Z",
                    "authority": dict(livesystem.CODEX_RESEARCH_AUTHORITY),
                    "boundary": livesystem.CODEX_RESEARCH_READY_BOUNDARY,
                }), encoding="utf-8")
                if livesystem.os.name != "nt":
                    ready.chmod(0o600)
                report = {
                    "titleBase64": "UmVzZWFyY2ggdGl0bGU=", "limitationsBase64": "TGltaXRhdGlvbnM=",
                    "findings": [{"statementBase64": "VmVyaWZpZWQgZmluZGluZw==", "citations": [{}]}],
                }
                verification = {"status": "evidence-pass"}
                provenance = {"format": "codex-research-provenance-v1"}
                payloads = {
                    "codex-research-report.json": (json.dumps(report) + "\n").encode(),
                    "codex-research-verification.json": (json.dumps(verification) + "\n").encode(),
                    "codex-research-provenance.json": (json.dumps(provenance) + "\n").encode(),
                }
                for name, payload in payloads.items():
                    (output_root / name).write_bytes(payload)
                receipt = {
                    "schemaVersion": 1, "operation": "pixel-outcome-codex-research-authority-complete",
                    "runId": run_id, "completedAt": "2026-08-13T12:00:01.000Z",
                    "jobId": "work-1786550400003-aaaaaaaaaaac",
                    "planSha256": "1" * 64, "leaseSha256": "2" * 64, "claimSha256": "3" * 64,
                    "workPolicySha256": "4" * 64,
                    "environmentSha256": system.pixel_environment_template_sha256,
                    "runtimeEnvironmentSha256": "5" * 64,
                    "modelContractSha256": livesystem.evaluation.sha256(model_payload),
                    "inferenceContractSha256": livesystem.evaluation.sha256(inference_payload),
                    "researchFixtureSha256": research_fixture_reference["sha256"],
                    "mcpReceiptSha256": livesystem.evaluation.sha256(mcp_raw),
                    "broker": {"completed": 1, "rejected": 0, "errors": 0, "invalid": 0,
                        "contentStoredBeyondJob": False, "credentialsExposed": False,
                        "directNetworkGrantedToWorker": False, "externalWritesPerformed": False},
                    "batches": 1,
                    "reportSha256": livesystem.evaluation.sha256(payloads["codex-research-report.json"]),
                    "verificationSha256": livesystem.evaluation.sha256(payloads["codex-research-verification.json"]),
                    "provenanceSha256": livesystem.evaluation.sha256(payloads["codex-research-provenance.json"]),
                    "contentStoredBeyondJob": False, "credentialsExposed": False,
                    "directPublicNetworkGrantedToModel": False, "externalWritesPerformed": False,
                    "authority": dict(livesystem.CODEX_RESEARCH_AUTHORITY),
                    "boundary": livesystem.CODEX_RESEARCH_RECEIPT_BOUNDARY,
                }
                receipt.update(receipt_overrides)
                (output_root / "authority-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
                return FinishedProcess(), ready, finalize

            system._start_research_authority = start_authority
            system.research_mcp_start = lambda *args: ("fixture-mcp", parent / "mcp-receipt.json")
            system.research_mcp_stop = lambda *args: ({"calls": 1, "completed": 1}, mcp_raw)
            def staged(*args, **kwargs):
                observed["schema"] = kwargs.get("research_schema_path")
                return {
                    "transcript": '{"type":"item.completed","item":{"type":"agent_message","text":"proposal"}}',
                    "exitCode": 0, "latencyMs": 50,
                    "artifacts": [{"kind": "finding-report", "relativePath": "artifact.md", "payload": b"proposal"}],
                    "finalMessage": '{"proposal":true}',
                }
            system._run_codex_staged = staged
            def invoke():
                return system.run_codex(
                    "pixel-outcome-aaaaaaaaaaaa",
                    {"modelId": "DeepSeek-V4-Flash-0731", "runtime": {"contextWindow": 1048576}},
                    {"profile": "researcher", "capabilities": ["public-research"], "budgets": {
                        "wallTimeSeconds": 60, "artifactBytes": 1048576,
                    }}, b"Research this",
                    source_payload=source_payload, source_reference=source_reference,
                    research_fixture_payload=research_fixture_payload,
                    research_fixture_reference=research_fixture_reference,
                    environment_payload=b"{}", environment={}, tool_policy_payload=b"{}",
                    tool_policy={"workspace": "read-only", "tools": ["public-research"],
                        "brokeredServices": ["public-research"]},
                    verifier_definition={}, run_dir=run_dir, run_id=run_id,
                    task={"profile": "researcher"}, model_contract_payload=model_payload,
                    inference_contract_payload=inference_payload,
                )

            outcome = invoke()
            self.assertEqual(outcome["researchEvidence"]["format"], "codex-research-provenance-v1")
            self.assertEqual(outcome["independentVerification"]["status"], "evidence-pass")
            self.assertIn("# Research title", outcome["finalMessage"])
            self.assertIsNotNone(observed["schema"])
            paths = {item["relativePath"] for item in outcome["artifacts"]}
            self.assertNotIn("artifact.md", paths)
            self.assertIn("codex-research-authority-receipt.json", paths)
            self.assertIn("codex-research-mcp-receipt.json", paths)

            livesystem.shutil.rmtree(runtime_root / run_id)
            receipt_overrides["planSha256"] = "f" * 64
            with self.assertRaisesRegex(
                livesystem.evaluation.OutcomeError, "authority receipt differs from its exact safe run",
            ):
                invoke()

    def test_model_mount_target_is_bound_to_artifact_and_runtime_kind(self):
        system = livesystem.DockerSystem(root=ROOT, codex_image="fixture-codex:latest")
        calls = []
        system._docker = lambda args, **_kwargs: calls.append(args) or ""
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            model_file = parent / "model.gguf"
            model_file.write_bytes(b"fixture")
            single = {
                "artifact": {"kind": "single-file"},
                "runtime": {
                    "implementation": "llama.cpp", "imageDigest": "sha256:" + "1" * 64,
                    "resources": {
                        "acceleratorClass": "cpu", "acceleratorCount": 0, "cpuCores": 4,
                        "memoryMiB": 4096, "sharedMemoryMiB": 64, "tmpfsMiB": 32,
                        "cacheMiB": 64, "pidsLimit": 256,
                    },
                },
            }
            system.model_container_start("pixel-outcome-aaaaaaaaaaaa", single, ["--port", "8080"], model_file)
            self.assertIn(f"type=bind,source={model_file},target=/models/model.gguf,readonly", calls[-1])
            self.assertIn("4096m", calls[-1])
            self.assertNotIn("--gpus", calls[-1])

            model_dir = parent / "model"
            model_dir.mkdir()
            directory = json.loads(json.dumps(single))
            directory["artifact"]["kind"] = "directory-manifest"
            directory["runtime"]["implementation"] = "vllm"
            directory["runtime"]["resources"].update({
                "acceleratorClass": "nvidia", "acceleratorCount": 2,
                "memoryMiB": 32768, "sharedMemoryMiB": 32768, "cacheMiB": 8192,
            })
            system.model_container_start("pixel-outcome-bbbbbbbbbbbb", directory, ["/models/model"], model_dir)
            model_command = calls[-1]
            self.assertIn(f"type=bind,source={model_dir},target=/models/model,readonly", model_command)
            self.assertIn("driver=nvidia,count=2", model_command)
            self.assertIn("32768m", model_command)
            self.assertIn(livesystem.BACKEND_ALIAS, model_command)
            self.assertIn("pixel-outcome-bbbbbbbbbbbb-backend", model_command)
            self.assertNotIn(livesystem.MODEL_ALIAS, model_command)

            incompatible = json.loads(json.dumps(directory))
            incompatible["runtime"]["implementation"] = "llama.cpp"
            with self.assertRaisesRegex(livesystem.evaluation.OutcomeError, "incompatible"):
                system.model_container_start("pixel-outcome-cccccccccccc", incompatible, [], model_dir)

    def test_exact_inference_boundary_is_private_policy_bound_and_receipted(self):
        system = livesystem.DockerSystem(
            root=ROOT, codex_image="fixture-codex:latest", boundary_image="sha256:" + "b" * 64,
        )
        calls = []
        system._docker = lambda args, **_kwargs: calls.append(args) or ""
        inference = {
            "sampling": {
                "source": "request-boundary-enforced", "temperaturePermille": 700, "topPPermille": 950,
                "topK": 40, "minPPermille": 50, "repeatPenaltyPermille": 1100, "seed": 42,
                "reasoningEffort": "backend-default", "reasoningVisibility": "hidden",
            },
            "request": {
                "wireApi": "openai-chat-completions", "stream": True, "maxOutputTokens": 4096,
                "toolEncoding": "function", "requestFieldPolicySha256": "0" * 64,
                "promptCachePolicy": "empty-at-run-start",
            },
        }
        inference["request"]["requestFieldPolicySha256"] = livesystem.outcome_task.inference_policy_sha256(inference)
        model = {"modelId": "DeepSeek-V4-Flash-0731", "runtime": {"implementation": "vllm"}}
        admission = {"budgets": {
            "artifactBytes": 1048576, "wallTimeSeconds": 900, "modelRequests": 20,
            "inputTokens": 100000, "outputTokens": 20000,
        }}
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary).resolve() / "run"
            run_dir.mkdir(mode=0o700)
            run_id = "outcomerun-1786550400003-aaaaaaaaaaac"
            name = system.inference_boundary_start(
                "pixel-outcome-aaaaaaaaaaaa", run_id, model, inference, admission, run_dir,
            )
            command = calls[-2]
            connect = calls[-1]
            self.assertIn(livesystem.MODEL_ALIAS, command)
            self.assertIn("--pull", command)
            self.assertIn("never", command)
            self.assertIn("/opt/pixel/deploy/agent-comparison/inference-boundary.mjs", command)
            if livesystem.os.name != "nt":
                self.assertIn("--user", command)
                self.assertIn(f"{livesystem.os.getuid()}:{livesystem.os.getgid()}", command)
            self.assertEqual(connect, ["network", "connect", "pixel-outcome-aaaaaaaaaaaa-backend", name])
            config = json.loads((run_dir / "inference-boundary-private/config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["backendOrigin"], f"http://{livesystem.BACKEND_ALIAS}:8080")
            self.assertEqual(config["ingressWireApi"], "openai-responses")
            self.assertEqual(config["inference"]["temperaturePermille"], 700)
            receipt_path = system._boundary_receipts[name]
            receipt_path.write_text(json.dumps({
                "schemaVersion": 1, "runId": run_id,
                "inferencePolicySha256": inference["request"]["requestFieldPolicySha256"],
                "requests": 3, "inputTokens": 1200, "outputTokens": 300,
                "deniedRequests": 0, "backendFailures": 0, "responseBytes": 4096,
                "backendResponseBytes": 2048, "ingressWireApi": "openai-responses",
                "backendWireApi": "openai-chat-completions", "adapter": "responses-to-chat-v2",
                "active": False, "lastFailureCode": None, "contentStored": False,
                "credentialsForwarded": False, "arbitraryNetwork": False, "externalEffects": False,
            }), encoding="utf-8")
            if livesystem.os.name != "nt":
                receipt_path.chmod(0o600)
            receipt = system.inference_boundary_receipt(name, inference["request"]["requestFieldPolicySha256"])
            self.assertEqual(receipt["requests"], 3)
            self.assertEqual(config["maxModelRequests"], 20)
            self.assertEqual(config["maxInputTokens"], 100000)
            self.assertEqual(config["maxOutputTokens"], 20000)
            tampered = dict(receipt)
            tampered["inferencePolicySha256"] = "f" * 64
            receipt_path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(livesystem.evaluation.OutcomeError, "exact safe policy"):
                system.inference_boundary_receipt(name, inference["request"]["requestFieldPolicySha256"])


if __name__ == "__main__":
    unittest.main()
