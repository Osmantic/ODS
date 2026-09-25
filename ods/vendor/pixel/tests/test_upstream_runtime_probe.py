import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("upstream_runtime_probe", ROOT / "scripts" / "upstream-runtime-probe.py")
PROBE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(PROBE)
FIXTURE_VERSION = ".".join(("2026", "6", "34"))


class RuntimeProbeTests(unittest.TestCase):
    def artifact(self, path: Path, contents: bytes) -> dict[str, str]:
        path.write_bytes(contents)
        return {
            "url": f"https://registry.npmjs.org/test/-/{path.name}",
            "sha256": hashlib.sha256(contents).hexdigest(),
            "integrity": "sha512-" + base64.b64encode(hashlib.sha512(contents).digest()).decode(),
        }

    def manifest(self, directory: Path) -> dict[str, object]:
        return {
            "openclaw": FIXTURE_VERSION,
            "openclawPackage": self.artifact(directory / "openclaw.tgz", b"openclaw"),
            "openclawPlugins": {
                "@openclaw/discord": FIXTURE_VERSION,
                "@openclaw/searxng-plugin": FIXTURE_VERSION,
                "@openclaw/llama-cpp-provider": FIXTURE_VERSION,
            },
            "openclawPluginPackages": {
                "discord": self.artifact(directory / "discord.tgz", b"discord"),
                "searxng": self.artifact(directory / "searxng.tgz", b"searxng"),
                "llamaCpp": self.artifact(directory / "llama.tgz", b"llama"),
            },
        }

    def test_archives_require_sha256_and_npm_integrity(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.manifest(directory)
            located = PROBE.locate_and_verify_archives(manifest, directory)
            self.assertEqual(set(located), {"openclaw", "discord", "searxng", "llamaCpp"})
            (directory / "discord.tgz").write_bytes(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "exactly one verified archive"):
                PROBE.locate_and_verify_archives(manifest, directory)

    def test_work_and_evidence_cannot_be_inside_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "repo"
            source.mkdir()
            with self.assertRaisesRegex(RuntimeError, "outside the source"):
                PROBE.require_safe_external(source / "evidence", source, "evidence")
            external = Path(temporary) / "evidence"
            self.assertEqual(PROBE.require_safe_external(external, source, "evidence"), external.resolve())

    def test_probe_contains_real_runtime_and_systemd_gates(self):
        source = (ROOT / "scripts" / "upstream-runtime-probe.py").read_text(encoding="utf-8")
        for expected in (
            '"plugins", "inspect", "pixel-source-broker", "--runtime", "--json"',
            '"gateway", "health"',
            '"name": "pixel_gmail_inbox"',
            '"name": "pixel_social_feed"',
            '"name": "sessions_list"',
            '"name": "write"',
            '"candidate-agent-gateway-token-denial"',
            'invalid_command[invalid_command.index("--token") + 1] = invalid_token',
            'turn_env["OPENCLAW_STATE_DIR"] = str(isolated_state)',
            '"PIXEL_MODEL_CONTEXT_WINDOW": "65536"',
            "candidate-observation-window",
            '"operation": "pixel-upstream-canary"',
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "RestrictNamespaces=true",
            "ReadonlyRootfs",
            "rollback-version",
        ):
            self.assertIn(expected, source)
        self.assertNotIn("approveDevicePairing", source)
        self.assertNotIn("pairing required", source)

    def test_node_qualification_runtime_is_exact_and_pinned(self):
        manifest = json.loads((ROOT / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
        runtime = manifest["nodeRuntime"]
        self.assertRegex(runtime["version"], r"^22\.\d+\.\d+$")
        self.assertEqual(
            runtime["url"],
            f"https://nodejs.org/dist/v{runtime['version']}/node-v{runtime['version']}-linux-x64.tar.xz",
        )
        self.assertRegex(runtime["sha256"], r"^[0-9a-f]{64}$")

    def test_recorder_redacts_every_runtime_credential_and_can_suppress_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            binding = {"qualificationSourceCommit": "a" * 40, "candidateManifestSha256": "b" * 64}
            recorder = PROBE.Recorder(evidence, "gateway-secret-value", binding)
            recorder.add_secret("device-secret-value")
            recorder.run(
                "redaction",
                [sys.executable, "-c", 'print("gateway-secret-value device-secret-value")'],
            )
            recorder.run(
                "suppressed",
                [sys.executable, "-c", 'print("device-secret-value")'],
                record_output=False,
            )
            retained = b"\n".join(path.read_bytes() for path in evidence.iterdir())
            self.assertNotIn(b"gateway-secret-value", retained)
            self.assertNotIn(b"device-secret-value", retained)
            self.assertIn(b"[REDACTED-RUNTIME-CREDENTIAL]", retained)
            self.assertIn(b"[SUPPRESSED]", retained)
            self.assertIn(b'"qualificationSourceCommit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"', retained)
            self.assertTrue(all(item["evidenceBindingSha256"] == recorder.binding_sha256 for item in recorder.records))

    def test_deterministic_model_exercises_write_tool_protocol(self):
        server = PROBE.QualificationModelServer()
        server.start()
        try:
            first = {
                "messages": [{"role": "user", "content": "qualify"}],
                "tools": [{
                    "type": "function",
                    "function": {
                        "name": "write",
                        "parameters": {"type": "object", "properties": {"path": {}, "content": {}}},
                    },
                }],
            }
            request = urllib.request.Request(
                "http://127.0.0.1:19999/v1/chat/completions",
                data=json.dumps(first).encode(),
                headers={"Content-Type": "application/json"},
            )
            response = urllib.request.urlopen(request, timeout=5).read().decode()
            self.assertIn('"name":"write"', response)

            second = dict(first)
            second["messages"] = [
                *first["messages"],
                {"role": "tool", "tool_call_id": "call_pixel_qualification_write", "content": "ok"},
            ]
            request = urllib.request.Request(
                "http://127.0.0.1:19999/v1/chat/completions",
                data=json.dumps(second).encode(),
                headers={"Content-Type": "application/json"},
            )
            response = urllib.request.urlopen(request, timeout=5).read().decode()
            self.assertIn("PIXEL_QUALIFICATION_COMPLETE", response)
            self.assertTrue(server.write_schema_seen)
            self.assertTrue(server.tool_result_seen)
        finally:
            server.stop()

    @unittest.skipUnless(os.name == "posix", "POSIX execute bits are required")
    def test_service_code_tree_is_readable_but_not_writable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            binary = root / "bin" / "openclaw"
            data = root / "package.json"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            data.write_text("{}\n", encoding="utf-8")
            binary.chmod(0o700)
            data.chmod(0o600)
            PROBE.make_service_readable(root)
            self.assertEqual(binary.stat().st_mode & 0o777, 0o755)
            self.assertEqual(data.stat().st_mode & 0o777, 0o644)
            self.assertEqual(root.stat().st_mode & 0o777, 0o755)


if __name__ == "__main__":
    unittest.main()
