import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class ReleaseProviderRuntimeTests(unittest.TestCase):
    """Artifact-level regression evidence for the live-exposed provider runtime.

    Builds a real release tree through the shared release-build primitive (the only
    generator seam that writes install-manifest.sha256 for clean installs) and proves,
    from the extracted artifact rather than the source tree, that the required provider
    runtime entrypoints/profiles/schemas exist, pass syntax and import closure, and can
    execute a hermetic offline provider sentinel with no credentials and no provider
    spend. Also asserts that excluded test/example/secret material stays absent.
    """

    # The complete production transitive runtime closure projected by release-build.sh
    # for local-only, moonshot-kimi, openai, and anthropic smoke, qualification,
    # equivalence, egress-proxy, routing, and model-proxy operation. Derived from actual
    # imports and executable entrypoints; test-only seams are excluded.
    RUNTIME_PRODUCTION = {
        # work-provider core / transports / runners / smoke
        "deploy/work-provider/Dockerfile.egress-proxy",
        "deploy/work-provider/Dockerfile.moonshot-worker",
        "deploy/work-provider/adapter-contract.mjs",
        "deploy/work-provider/credential-custody.mjs",
        "deploy/work-provider/dev-proxy-cli.mjs",
        "deploy/work-provider/egress-proxy.mjs",
        "deploy/work-provider/equivalence-runner.mjs",
        "deploy/work-provider/executor.mjs",
        "deploy/work-provider/generic-remote-transport.mjs",
        "deploy/work-provider/grading.mjs",
        "deploy/work-provider/harness.mjs",
        "deploy/work-provider/local-policy.mjs",
        "deploy/work-provider/local-transport.mjs",
        "deploy/work-provider/moonshot-container-entrypoint.mjs",
        "deploy/work-provider/moonshot-smoke-cli.mjs",
        "deploy/work-provider/moonshot-transport.mjs",
        "deploy/work-provider/neutral-corpus.mjs",
        "deploy/work-provider/patch-extraction.mjs",
        "deploy/work-provider/patch-verifier.mjs",
        "deploy/work-provider/private-policy.mjs",
        "deploy/work-provider/provider-registry.mjs",
        "deploy/work-provider/provider-smoke-cli.mjs",
        "deploy/work-provider/provider-smoke-core.mjs",
        "deploy/work-provider/qualification-promotion.mjs",
        "deploy/work-provider/qualification-runner.mjs",
        "deploy/work-provider/run-ledger.mjs",
        "deploy/work-provider/run-store.mjs",
        "deploy/work-provider/transport-registry.mjs",
        # adapters
        "deploy/work-provider/adapters/anthropic-messages.mjs",
        "deploy/work-provider/adapters/local-openai.mjs",
        "deploy/work-provider/adapters/openai-chat.mjs",
        "deploy/work-provider/adapters/openai-responses.mjs",
        # router
        "deploy/work-provider-router/qualification.mjs",
        "deploy/work-provider-router/router-policy.mjs",
        "deploy/work-provider-router/router.mjs",
        # model proxy
        "deploy/work-model-proxy/inference-policy.mjs",
        "deploy/work-model-proxy/proxy.mjs",
        # shared runtime libs
        "scripts/lib/secure-files.mjs",
        "scripts/lib/work-contract.mjs",
        "scripts/lib/json-schema.mjs",
    }

    RUNTIME_EXECUTABLE = {
        "deploy/work-provider/run-moonshot-smoke-container.sh",
    }

    # Eight profiles are eagerly loaded by the registry at module load, so each is an
    # import-time closure requirement (not a broadening of defaults).
    PROFILES = {
        "deploy/work-provider/profiles/anthropic.json",
        "deploy/work-provider/profiles/fireworks.json",
        "deploy/work-provider/profiles/groq.json",
        "deploy/work-provider/profiles/local.json",
        "deploy/work-provider/profiles/moonshot-kimi.json",
        "deploy/work-provider/profiles/openai.json",
        "deploy/work-provider/profiles/openrouter.json",
        "deploy/work-provider/profiles/together.json",
    }

    # Runtime entrypoints used for the import-closure check from the artifact.
    ENTRYPOINTS = [
        "deploy/work-provider/provider-smoke-cli.mjs",
        "deploy/work-provider/moonshot-smoke-cli.mjs",
        "deploy/work-provider/qualification-runner.mjs",
        "deploy/work-provider/equivalence-runner.mjs",
        "deploy/work-provider/egress-proxy.mjs",
        "deploy/work-provider/dev-proxy-cli.mjs",
        "deploy/work-provider/executor.mjs",
        "deploy/work-provider/harness.mjs",
        "deploy/work-provider/moonshot-container-entrypoint.mjs",
        "deploy/work-provider-router/router.mjs",
        "deploy/work-model-proxy/proxy.mjs",
    ]

    # Excluded material that must never ship in the installed release.
    EXCLUDED = {
        "deploy/work-provider/provider-credential-ingress-test.mjs",
        "deploy/work-provider/provider-smoke-cli-test.mjs",
        "deploy/work-provider/private-policy.example.json",
    }

    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.node = shutil.which("node")
        if cls.node is None:
            raise unittest.SkipTest("node is required")

    def copy_repository(self, destination: Path) -> Path:
        copy = destination / "repo"
        shutil.copytree(
            self.root,
            copy,
            ignore=shutil.ignore_patterns(
                ".git", ".generated", ".runtime", "dist", "node_modules",
                "__pycache__", "*.pyc",
            ),
        )
        return copy

    def build_release_stage(self, root: Path) -> Path:
        dist = root / "dist"
        dist.mkdir()
        for name in ("openclaw.json", "openclaw.sha256", "release-identity.json",
                     "deployment.sha256", "source-runtime.sha256"):
            (dist / name).write_text("dummy plan artifact\n", encoding="utf-8")
        target = root / "release-stage"
        env = dict(os.environ)
        env.update({
            "PIXEL_SOURCE_BROKER_ENABLED": "0",
            "PIXEL_OPS_BROKER_ENABLED": "0",
            "PIXEL_FRONTIER_BROKER_ENABLED": "0",
            "PIXEL_WEB_COURIER_ENABLED": "0",
        })
        script = (
            "set -euo pipefail\n"
            "pixel_die(){ echo \"PIXEL_DIE: $*\" >&2; exit 70; }\n"
            f"source \"{root}/scripts/lib/release-build.sh\"\n"
            f"pixel_build_release_stage \"{root}\" \"{target}\"\n"
        )
        result = subprocess.run(
            ["bash", "-c", script], cwd=root, env=env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return target

    def run_node_from(self, cwd: Path, *arguments: str):
        result = subprocess.run(
            [self.node, *map(str, arguments)], cwd=cwd, capture_output=True, text=True,
        )
        if result.returncode:
            self.fail(f"node failed in {cwd}:\n{result.stdout}\n{result.stderr}")
        return result

    def install_manifest_paths(self, target: Path):
        paths = set()
        manifest = (target / "install-manifest.sha256").read_text(encoding="utf-8")
        for line in manifest.splitlines():
            digest, separator, path = line.partition("  ")
            self.assertEqual(separator, "  ", f"malformed install-manifest line: {line}")
            self.assertRegex(digest, r"^[a-f0-9]{64}$")
            self.assertTrue(path.startswith("./"), f"non-relative install-manifest path: {path}")
            relative = path[2:]
            self.assertNotIn(relative, paths, f"duplicate install-manifest path: {relative}")
            paths.add(relative)
        self.assertTrue(paths)
        return paths

    def test_release_provider_runtime_closure_is_projected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(Path(temporary))
            target = self.build_release_stage(root)

            manifest_paths = self.install_manifest_paths(target)
            packaged_runtime = self.RUNTIME_PRODUCTION | self.RUNTIME_EXECUTABLE | self.PROFILES
            for rel in packaged_runtime:
                self.assertTrue((target / rel).is_file(), f"release tree missing runtime file: {rel}")
                self.assertIn(rel, manifest_paths, f"release install-manifest missing runtime file: {rel}")

            # All eight profiles present.
            self.assertEqual(len(list((target / "deploy/work-provider/profiles").glob("*.json"))), 8)

            # Schemas referenced by work-contract.mjs at module load are projected.
            schema_names = (
                (root / "scripts/lib/release-provider-schemas.txt").read_text(encoding="utf-8").split()
            )
            imported_schema_names = set(re.findall(
                r'loadSchema\("([^"]+)"\)',
                (root / "scripts/lib/work-contract.mjs").read_text(encoding="utf-8"),
            ))
            self.assertEqual(schema_names, sorted(set(schema_names)))
            self.assertEqual(set(schema_names), imported_schema_names)
            for name in schema_names:
                self.assertTrue((target / "schemas" / name).is_file(), f"release tree missing schema: {name}")
                self.assertIn(f"schemas/{name}", manifest_paths)

            # Owner-only modes preserved for the provider runtime.
            for rel in self.RUNTIME_PRODUCTION | self.PROFILES:
                self.assertEqual(
                    oct((target / rel).stat().st_mode & 0o777), "0o600",
                    f"runtime file not owner-only: {rel}",
                )

            for rel in self.RUNTIME_EXECUTABLE:
                self.assertEqual(
                    oct((target / rel).stat().st_mode & 0o777), "0o700",
                    f"runtime launcher not owner-executable: {rel}",
                )

    def test_release_provider_runtime_syntax_and_import_closure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(Path(temporary))
            target = self.build_release_stage(root)

            # Syntax check every projected runtime module.
            for rel in sorted(self.RUNTIME_PRODUCTION):
                if rel.endswith(".mjs"):
                    self.run_node_from(target, "--check", rel)

            # Import closure from the artifact: every entrypoint resolves transitively.
            for entry in self.ENTRYPOINTS:
                # Import the module path (relative to the release root).
                self.run_node_from(
                    target, "--input-type=module", "-e",
                    f"import('./{entry}').catch((e) => {{ console.error(e); process.exit(1); }})",
                )

            launcher = target / "deploy/work-provider/run-moonshot-smoke-container.sh"
            result = subprocess.run(
                ["bash", "-n", str(launcher)], cwd=target, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_release_provider_runtime_hermetic_offline_sentinel(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(Path(temporary))
            target = self.build_release_stage(root)

            sentinel = r"""
// Hermetic offline provider-runtime sentinel. No network, no credentials, no spend.
import { resolveWorkProvider, listWorkProviderProfiles, resolveProviderModel } from "./deploy/work-provider/provider-registry.mjs";
const profiles = listWorkProviderProfiles();
const ids = profiles.map((p) => p.id).sort();
const required = ["anthropic", "local", "moonshot-kimi", "openai"];
for (const id of required) if (!ids.includes(id)) { console.error("missing " + id); process.exit(1); }
const local = resolveWorkProvider("local");
for (const id of ["anthropic", "moonshot-kimi", "openai"]) {
  let threw = false;
  try { resolveWorkProvider(id); } catch (e) { threw = true; }
  if (!threw) { console.error("remote " + id + " enabled by default"); process.exit(1); }
}
for (const id of ["anthropic", "moonshot-kimi", "openai"]) {
  const resolved = resolveWorkProvider(id, { enabledRemoteProviders: [id] });
  resolveProviderModel({ resolvedProvider: resolved, model: resolved.profile.defaultModel });
}
console.log("SENTINEL OK " + profiles.length);
"""
            (target / "provider-sentinel.mjs").write_text(sentinel, encoding="utf-8")
            result = self.run_node_from(target, "provider-sentinel.mjs")
            self.assertIn("SENTINEL OK 8", result.stdout)

    def test_release_provider_runtime_excludes_test_example_secret_material(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(Path(temporary))
            target = self.build_release_stage(root)

            manifest_paths = self.install_manifest_paths(target)
            for rel in self.EXCLUDED:
                self.assertFalse((target / rel).exists(), f"release tree must not ship excluded file: {rel}")
                self.assertNotIn(rel, manifest_paths, f"release install-manifest must not list excluded file: {rel}")

            # No credential bytes may ship in the installed production runtime closure.
            credential_patterns = [
                re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
                re.compile(r"\bsk-[A-Za-z0-9]{8,}\b"),
                re.compile(r'"client_secret"\s*:\s*"[^"]{8,}"', re.IGNORECASE),
                re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
            ]
            for rel in self.RUNTIME_PRODUCTION | self.RUNTIME_EXECUTABLE | self.PROFILES:
                text = (target / rel).read_text(encoding="utf-8", errors="replace")
                for pattern in credential_patterns:
                    self.assertIsNone(
                        pattern.search(text),
                        f"release file ships credential bytes: {rel}",
                    )


if __name__ == "__main__":
    unittest.main()
