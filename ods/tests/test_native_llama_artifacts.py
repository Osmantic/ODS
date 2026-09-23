"""Offline resolver and macOS installer-boundary fixtures; never run an artifact."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
HELPER = ROOT / "installers/lib/native-llama-artifact.py"
SPEC = importlib.util.spec_from_file_location("native_llama_artifact", HELPER)
ARTIFACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ARTIFACT)
MANIFEST = ROOT / "installers/native-llama-artifacts.json"


class ArtifactFixture(unittest.TestCase):
    def setUp(self):
        output = REPO / "output/native-llama-fixtures"
        output.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="case-", dir=output)
        self.addCleanup(self.temporary.cleanup)
        self.stage = Path(self.temporary.name)
        self.document = json.loads(MANIFEST.read_text())
        self.manifest = self.stage / "manifest.json"
        self.write_manifest()

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.document), encoding="utf-8")


class NativeLlamaArtifactTests(ArtifactFixture):
    def test_all_reviewed_default_and_override_selections(self):
        expected = {("windows-vulkan-x64", "b8248"), ("windows-vulkan-x64", "b9014"),
                    ("macos-arm64", "b8210"), ("macos-arm64", "b9014")}
        self.assertEqual({(a["platform"], a["tag"]) for a in self.document["artifacts"]}, expected)
        for platform, tag in expected:
            with self.subTest(platform=platform, tag=tag):
                item = ARTIFACT.resolve(self.manifest, platform, tag)
                self.assertIn("/" + tag + "/" + item["asset"], item["url"])
                self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(item["metadata_url"].endswith(str(item["asset_id"])))

    def test_unknown_or_malformed_contracts_cannot_resolve(self):
        for platform, tag in (("linux-x64", "b9014"), ("macos-arm64", "latest"), ("macos-arm64", "b999999")):
            with self.subTest(platform=platform, tag=tag), self.assertRaises(ValueError):
                ARTIFACT.resolve(self.manifest, platform, tag)
        for changes in ({"sha256": ""}, {"sha256": "0" * 63}, {"sha256": "a" * 64 + "\n"},
                        {"url": "https://example.invalid/runtime.tar.gz"}, {"asset": "../archive"}):
            with self.subTest(changes=changes):
                self.document = json.loads(MANIFEST.read_text())
                self.document["artifacts"][0].update(changes)
                self.write_manifest()
                with self.assertRaises(ValueError):
                    ARTIFACT.resolve(self.manifest, "macos-arm64", "b9014")
        self.document = json.loads(MANIFEST.read_text())
        self.document["artifacts"].append(self.document["artifacts"][0])
        self.write_manifest()
        with self.assertRaises(ValueError):
            ARTIFACT.resolve(self.manifest, "macos-arm64", "b9014")

    def test_local_bytes_empty_directory_missing_symlink_and_substitution(self):
        fixture = self.stage / "archive"
        body = b"reviewed inert bytes"
        fixture.write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        ARTIFACT.verify(fixture, digest)
        fixture.write_bytes(b"substituted bytes")
        with self.assertRaises(ValueError):
            ARTIFACT.verify(fixture, digest)
        fixture.write_bytes(b"")
        with self.assertRaises(ValueError):
            ARTIFACT.verify(fixture, hashlib.sha256(b"").hexdigest())
        for path in (self.stage, self.stage / "missing"):
            with self.subTest(path=path.name), self.assertRaises((ValueError, OSError)):
                ARTIFACT.verify(path, digest)
        if os.name != "nt":
            fixture.write_bytes(body)
            link = self.stage / "link"
            link.symlink_to(fixture)
            with self.assertRaises(ValueError):
                ARTIFACT.verify(link, digest)
            link.unlink()
            os.link(fixture, link)
            with self.assertRaises(ValueError):
                ARTIFACT.verify(fixture, digest)


@unittest.skipIf(os.name == "nt", "POSIX fixture; Windows uses the PowerShell boundary test")
class MacDownloadBoundaryTests(ArtifactFixture):
    def setUp(self):
        super().setUp()
        self.source = self.stage / "source"
        (self.source / "installers/lib").mkdir(parents=True)
        shutil.copyfile(HELPER, self.source / "installers/lib/native-llama-artifact.py")
        self.manifest = self.source / "installers/native-llama-artifacts.json"
        self.archive = self.stage / "inert.tar.gz"
        with tarfile.open(self.archive, "w:gz") as bundle:
            for name, content in (("bin/llama-server", b"inert; never executed"), ("bin/libfixture.dylib", b"inert companion")):
                entry = tarfile.TarInfo(name)
                entry.size = len(content)
                bundle.addfile(entry, io.BytesIO(content))
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        for entry in self.document["artifacts"]:
            if entry["platform"] == "macos-arm64":
                entry["sha256"] = digest
        self.write_manifest()
        self.downloads = self.stage / "downloads"
        self.downloads.mkdir()
        self.binary = self.stage / "installed/llama-server"
        self.owner_bin = self.stage / "owner-bin"
        self.owner_bin.mkdir()
        self.trace = self.stage / "trace"
        installer = (ROOT / "installers/macos/install-macos.sh").read_text()
        branch = installer.split("        # New archives must match the reviewed manifest before extraction.\n", 1)[1]
        branch = branch.split("        # Start native llama-server with Metal\n", 1)[0]
        self.branch = self.stage / "real-install-branch.sh"
        self.branch.write_text(branch)

    def run_boundary(self, mode="valid", tag="b8210"):
        environment = dict(os.environ, TMPDIR=str(self.downloads), TEST_SOURCE=str(self.source),
                           TEST_ARCHIVE=str(self.archive), TEST_BINARY=str(self.binary), TEST_TAG=tag,
                           TEST_MODE=mode, TEST_TRACE=str(self.trace), TEST_OWNER_BIN=str(self.owner_bin),
                           TEST_BRANCH=str(self.branch), ODS_PYTHON_CMD=shutil.which("python3"),
                           PATH=str(self.owner_bin) + os.pathsep + os.environ["PATH"])
        script = r'''
set -euo pipefail
source "$1"
curl() {
    local output=''
    while [[ $# -gt 0 ]]; do
        if [[ "$1" == --output ]]; then output="$2"; shift 2; else shift; fi
    done
    printf 'download:%s\n' "$output" >> "$TEST_TRACE"
    case "$TEST_MODE" in
        valid|extraction-error) command cp "$TEST_ARCHIVE" "$output" ;;
        changed) printf substituted > "$output" ;;
        empty) : > "$output" ;;
        symlink) ln -s "$TEST_ARCHIVE" "$output" ;;
        timeout) return 28 ;;
        tls) return 60 ;;
        *) return 23 ;;
    esac
}
tar() {
    printf 'extract\n' >> "$TEST_TRACE"
    if [[ "$TEST_MODE" == extraction-error ]]; then return 2; fi
    command tar "$@"
}
chmod() { printf 'chmod:%s\n' "$*" >> "$TEST_TRACE"; command chmod "$@"; }
xattr() { printf 'quarantine\n' >> "$TEST_TRACE"; }
brew() {
    printf 'brew\n' >> "$TEST_TRACE"
    printf 'owner package fixture; never executed' > "$TEST_OWNER_BIN/llama-server"
    command chmod +x "$TEST_OWNER_BIN/llama-server"
}
ai() { :; }; ai_ok() { :; }; ai_warn() { :; }; ai_err() { :; }
SOURCE_ROOT="$TEST_SOURCE"
LLAMA_CPP_RELEASE_TAG="$TEST_TAG"
LLAMA_SERVER_BIN="$TEST_BINARY"
LLAMA_SERVER_DIR="$(dirname "$LLAMA_SERVER_BIN")"
source "$TEST_BRANCH"
'''
        result = subprocess.run(["bash", "-c", script, "fixture", str(ROOT / "installers/macos/lib/native-llama-artifact.sh")],
                                env=environment, text=True, capture_output=True, timeout=15)
        self.events = self.trace.read_text().splitlines() if self.trace.exists() else []
        return result

    def assert_untrusted_was_not_used(self):
        self.assertNotIn("extract", self.events)
        self.assertNotIn("brew", self.events)
        self.assertNotIn("quarantine", self.events)
        self.assertFalse(any(line.startswith("chmod:+x") for line in self.events))
        self.assertFalse(self.binary.exists())
        self.assertEqual(list(self.downloads.iterdir()), [])

    def test_both_versions_verify_and_ignore_the_old_predictable_cache(self):
        # The old filename exists with bad bytes; the installer must neither
        # reuse nor remove it. Each accepted attempt uses a new private stage.
        old_cache = self.downloads / "llama-b8210-bin-macos-arm64.tar.gz"
        old_cache.write_bytes(b"untrusted old cache")
        paths = []
        for tag in ("b8210", "b9014"):
            result = self.run_boundary(tag=tag)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.binary.read_bytes(), b"inert; never executed")
            paths.append([line for line in self.events if line.startswith("download:")][-1])
            self.assertIn("extract", self.events)
            self.assertNotIn("brew", self.events)
            self.binary.unlink()
            self.trace.unlink()
        self.assertNotEqual(paths[0], paths[1])
        self.assertEqual(list(self.downloads.iterdir()), [old_cache])
        self.assertEqual(old_cache.read_bytes(), b"untrusted old cache")

    def test_changed_empty_symlink_and_tls_fail_without_extraction_or_brew(self):
        for mode in ("changed", "empty", "symlink", "tls", "write-error"):
            with self.subTest(mode=mode):
                result = self.run_boundary(mode=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assert_untrusted_was_not_used()
                self.trace.unlink()

    def test_missing_hash_and_unknown_tag_stop_before_download(self):
        result = self.run_boundary(tag="b999999")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.events, [])
        self.document["artifacts"][2]["sha256"] = ""
        self.write_manifest()
        result = self.run_boundary()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.events, [])

    def test_matching_digest_with_invalid_archive_never_falls_back_to_brew(self):
        self.archive.write_bytes(b"Reviewed inert bytes that are not an archive")
        for entry in self.document["artifacts"]:
            if entry["platform"] == "macos-arm64":
                entry["sha256"] = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.write_manifest()
        result = self.run_boundary()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("extract", self.events)
        self.assertNotIn("brew", self.events)
        self.assertNotIn("quarantine", self.events)
        self.assertFalse(self.binary.exists())
        self.assertEqual(list(self.downloads.iterdir()), [], result.stdout + result.stderr)

    def test_failed_extraction_cleans_the_literal_staging_path(self):
        self.downloads = self.stage / "downloads [literal] 'quoted'"
        self.downloads.mkdir()
        result = self.run_boundary(mode="extraction-error")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("extract", self.events)
        self.assertNotIn("brew", self.events)
        self.assertNotIn("quarantine", self.events)
        self.assertFalse(self.binary.exists())
        self.assertEqual(list(self.downloads.iterdir()), [], result.stdout + result.stderr)

    def test_network_failure_preserves_only_the_explicit_package_manager_fallback(self):
        result = self.run_boundary(mode="timeout")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("brew", self.events)
        self.assertNotIn("extract", self.events)
        self.assertNotIn("quarantine", self.events)
        self.assertIn(b"owner package fixture", self.binary.read_bytes())
        self.assertEqual(list(self.downloads.iterdir()), [])

    def test_existing_owner_binary_is_reused_without_download_or_attestation(self):
        self.binary.parent.mkdir()
        self.binary.write_bytes(b"owner-installed fixture")
        self.binary.chmod(0o700)
        # Even an unknown download tag cannot turn reuse into a silent download.
        result = self.run_boundary(tag="b999999")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events, [])
        self.assertEqual(self.binary.read_bytes(), b"owner-installed fixture")


if __name__ == "__main__":
    unittest.main()
