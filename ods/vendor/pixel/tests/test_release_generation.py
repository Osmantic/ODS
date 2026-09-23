import json
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest


class ReleaseGenerationTests(unittest.TestCase):
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
            ignore=shutil.ignore_patterns(".git", ".generated", ".runtime", "dist", "node_modules", "__pycache__", "*.pyc"),
        )
        return copy

    def run_node(self, root: Path, script: str, *arguments: str, success: bool = True):
        result = subprocess.run(
            [self.node, script, *arguments],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if success and result.returncode != 0:
            self.fail(f"{script} failed:\n{result.stdout}\n{result.stderr}")
        if not success and result.returncode == 0:
            self.fail(f"{script} unexpectedly passed")
        return result

    def test_checked_in_generation_is_current(self):
        self.run_node(self.root, "scripts/generate-release-files.mjs", "--check")

    def test_drift_is_rejected_and_regeneration_repairs_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(Path(temporary))
            env_path = root / ".env.example"
            version = json.loads((root / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))["openclaw"]
            env_path.write_text(env_path.read_text(encoding="utf-8").replace(f"PIXEL_OPENCLAW_VERSION='{version}'", "PIXEL_OPENCLAW_VERSION='0.0.0'"), encoding="utf-8")
            failure = self.run_node(root, "scripts/generate-release-files.mjs", "--check", success=False)
            self.assertIn("generated release files are stale", failure.stderr)
            self.run_node(root, "scripts/generate-release-files.mjs", "--write")
            self.run_node(root, "scripts/generate-release-files.mjs", "--check")

    def test_manifest_generated_base_image_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(Path(temporary))
            constants = root / "scripts" / "generated" / "release-constants.json"
            data = json.loads(constants.read_text(encoding="utf-8"))
            manifest = json.loads((root / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
            self.assertEqual(data["baseImage"], manifest["baseImage"])
            data["baseImage"] = "debian:bookworm-slim@sha256:" + "ab" * 32
            constants.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            failure = self.run_node(root, "scripts/generate-release-files.mjs", "--check", success=False)
            self.assertIn("generated release files are stale", failure.stderr)

    def test_unqualified_combination_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(Path(temporary))
            path = root / "RELEASE-MANIFEST.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["openclaw"] = "2099.1.1"
            manifest["openclawPackage"]["url"] = "https://registry.npmjs.org/openclaw/-/openclaw-2099.1.1.tgz"
            path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            failure = self.run_node(root, "scripts/generate-release-files.mjs", "--write", success=False)
            self.assertIn("absent from the compatibility matrix", failure.stderr)

    def test_release_contract_requires_shared_primitive_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(Path(temporary))
            path = root / "scripts" / "lib" / "release-build.sh"
            source = path.read_text(encoding="utf-8")
            path.write_text(source.replace("install-manifest.sha256", "install-manifest-missing.sha256"), encoding="utf-8")
            failure = self.run_node(root, "scripts/check-release-contract.mjs", success=False)
            self.assertIn("release contract value: install-manifest.sha256", failure.stderr)

    def test_release_contract_rejects_apply_bypassing_shared_primitive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(Path(temporary))
            path = root / "scripts" / "apply.sh"
            source = path.read_text(encoding="utf-8")
            path.write_text(source.replace('pixel_build_release_stage "$ROOT" "$stage"', 'true  # bypassed shared primitive'), encoding="utf-8")
            failure = self.run_node(root, "scripts/check-release-contract.mjs", success=False)
            self.assertIn("must build the release through the shared release-build primitive", failure.stderr)

    def test_release_contract_rejects_migrate_prepare_bypassing_shared_primitive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(Path(temporary))
            path = root / "scripts" / "migrate-prepare.sh"
            source = path.read_text(encoding="utf-8")
            path.write_text(source.replace('pixel_build_release_stage "$ROOT" "$hidden"', 'true  # bypassed shared primitive'), encoding="utf-8")
            failure = self.run_node(root, "scripts/check-release-contract.mjs", success=False)
            self.assertIn("must build the release through the shared release-build primitive", failure.stderr)

    def test_duplicate_authored_pin_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(Path(temporary))
            deployment = root / "DEPLOYMENT.md"
            version = json.loads((root / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))["openclaw"]
            deployment.write_text(deployment.read_text(encoding="utf-8") + f"\nOpenClaw {version}\n", encoding="utf-8")
            failure = self.run_node(root, "scripts/check-release-contract.mjs", success=False)
            self.assertIn("duplicates an authored release pin", failure.stderr)

    def test_release_update_generator_rejects_ambiguous_or_invalid_qualification_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(Path(temporary))
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Pixel test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "pixel-test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=root, check=True)
            version = (root / "VERSION").read_text(encoding="ascii").strip()
            paths = [
                "--artifact", str(root / f"pixel-{version}.tar.gz"),
                "--sbom", str(root / f"pixel-{version}.cdx.json"),
                "--provenance", str(root / f"pixel-{version}.intoto.jsonl"),
                "--output", str(root / f"pixel-{version}.update.json"),
            ]
            compatibility_path = root / "OPENCLAW-COMPATIBILITY.json"
            original = json.loads(compatibility_path.read_text(encoding="utf-8"))
            current = next(item for item in original["combinations"] if item["pixel"] == version)
            original["combinations"].append(json.loads(json.dumps(current)))
            compatibility_path.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
            ambiguous = self.run_node(
                root, "scripts/generate-release-update.mjs", *paths, success=False,
            )
            self.assertIn("no unique matching compatibility qualification record", ambiguous.stderr)

            original["combinations"].pop()
            current["evidence"]["sourceCommit"] = "invalid"
            compatibility_path.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
            invalid = self.run_node(
                root, "scripts/generate-release-update.mjs", *paths, success=False,
            )
            self.assertIn("qualification source commit is invalid", invalid.stderr)

    def test_legacy_manifest_migration_is_explicit_and_no_overwrite(self):
        manifest = json.loads((self.root / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
        integrity = manifest["openclawPackage"].pop("integrity")
        manifest.pop("$schema")
        manifest.pop("schemaVersion")
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            source = temporary_path / "legacy.json"
            output = temporary_path / "migrated.json"
            source.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            self.run_node(
                self.root,
                "scripts/migrate-release-manifest.mjs",
                str(source.resolve()),
                str(output.resolve()),
                "--openclaw-integrity",
                integrity,
                "--confirm",
            )
            migrated = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(migrated["schemaVersion"], 1)
            self.assertEqual(migrated["openclawPackage"]["integrity"], integrity)
            failure = self.run_node(
                self.root,
                "scripts/migrate-release-manifest.mjs",
                str(source.resolve()),
                str(output.resolve()),
                "--openclaw-integrity",
                integrity,
                "--confirm",
                success=False,
            )
            self.assertNotEqual(failure.returncode, 0)

    def test_installer_mirrors_carried_into_generated_files(self):
        manifest = json.loads((self.root / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
        mirrors = manifest["openclawInstaller"]["mirrors"]
        self.assertEqual(len(mirrors), 1)
        self.assertTrue(mirrors[0].startswith("https://"))
        constants = json.loads((self.root / "scripts/generated/release-constants.json").read_text(encoding="utf-8"))
        self.assertEqual(constants["openclawInstaller"]["mirrors"], mirrors)
        env = (self.root / "scripts/generated/release.env").read_text(encoding="utf-8")
        self.assertIn(f"PIXEL_GENERATED_OPENCLAW_INSTALLER_MIRRORS=('{mirrors[0]}')", env)
        self.run_node(self.root, "scripts/generate-release-files.mjs", "--check")

    def test_mirror_drift_is_rejected_and_regeneration_repairs_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(Path(temporary))
            manifest_path = root / "RELEASE-MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["openclawInstaller"]["mirrors"] = [
                "https://cdn.jsdelivr.net/gh/openclaw/openclaw@deadbeef/scripts/install.sh",
            ]
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            failure = self.run_node(root, "scripts/generate-release-files.mjs", "--check", success=False)
            self.assertIn("generated release files are stale", failure.stderr)
            self.run_node(root, "scripts/generate-release-files.mjs", "--write")
            constants = json.loads((root / "scripts/generated/release-constants.json").read_text(encoding="utf-8"))
            self.assertEqual(constants["openclawInstaller"]["mirrors"], manifest["openclawInstaller"]["mirrors"])
            self.run_node(root, "scripts/generate-release-files.mjs", "--check")

    def test_non_https_mirror_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(Path(temporary))
            manifest_path = root / "RELEASE-MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["openclawInstaller"]["mirrors"] = ["http://insecure.example/install.sh"]
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            failure = self.run_node(root, "scripts/generate-release-files.mjs", "--write", success=False)
            self.assertIn("failed schema validation", failure.stderr)

    PROVIDER_INGRESS_PRODUCTION = {
        "deploy/work-provider/install-owner-test-key.sh",
        "deploy/work-provider/provider-credential-ingress.mjs",
        "deploy/work-provider/provider-credential-ingress-core.mjs",
        "deploy/work-provider/provider-credential-ingress-internal.mjs",
        "deploy/work-provider/renameat2_noreplace.py",
        "deploy/work-provider/provider-registry.mjs",
        "deploy/work-provider/adapter-contract.mjs",
        "deploy/work-provider/adapters/anthropic-messages.mjs",
        "deploy/work-provider/adapters/local-openai.mjs",
        "deploy/work-provider/adapters/openai-chat.mjs",
        "deploy/work-provider/adapters/openai-responses.mjs",
        "scripts/lib/secure-files.mjs",
        "scripts/lib/work-contract.mjs",
        "scripts/lib/json-schema.mjs",
    }
    PROVIDER_INGRESS_TEST_ONLY = {
        "deploy/work-provider/provider-credential-ingress-test.mjs",
        "deploy/work-provider/provider-smoke-cli-test.mjs",
    }

    def test_provider_ingress_clean_install_release_closure(self):
        # Build a real release tree through the shared release-build primitive (the ONLY
        # generator seam that writes install-manifest.sha256 for clean installs) and prove
        # it carries the complete production provider-ingress dependency closure while
        # never shipping credential bytes or test-only seams. npm/venv and the network are
        # disabled; the closure itself is copied verbatim by the primitive.
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_repository(Path(temporary))
            dist = root / "dist"
            dist.mkdir()
            for name in ("openclaw.json", "openclaw.sha256", "release-identity.json", "deployment.sha256", "source-runtime.sha256"):
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
            result = subprocess.run(["bash", "-c", script], cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

            manifest = (target / "install-manifest.sha256").read_text(encoding="utf-8")
            for rel in self.PROVIDER_INGRESS_PRODUCTION:
                self.assertTrue((target / rel).is_file(), f"release tree missing production ingress file: {rel}")
                self.assertIn(rel, manifest, f"release install-manifest missing production ingress file: {rel}")
            for rel in self.PROVIDER_INGRESS_TEST_ONLY:
                self.assertFalse((target / rel).exists(), f"release tree must not ship test-only seam: {rel}")
                self.assertNotIn(rel, manifest, f"release install-manifest must not list test-only seam: {rel}")

            # No credential bytes may be shipped by the installed production ingress closure.
            credential_patterns = [
                re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
                re.compile(r"\bsk-[A-Za-z0-9]{8,}\b"),
                re.compile(r'\"client_secret\"\s*:\s*\"[^\"]{8,}\"', re.IGNORECASE),
                re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
            ]
            for rel in self.PROVIDER_INGRESS_PRODUCTION:
                text = (target / rel).read_text(encoding="utf-8", errors="replace")
                for pattern in credential_patterns:
                    self.assertIsNone(pattern.search(text), f"release file ships credential bytes: {rel}")


if __name__ == "__main__":
    unittest.main()
