"""Exercise native installer downloads with harmless archives and no services."""
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "installers/macos/lib/native-runtime-download.sh"
BASH = os.environ.get("ODS_TEST_BASH") or shutil.which("bash")


def shell_path(path):
    value = str(path).replace("\\", "/")
    if os.name == "nt" and len(value) > 2 and value[1] == ":":
        return "/" + value[0].lower() + value[2:]
    return value


@unittest.skipUnless(BASH, "bash is required")
class RuntimeDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ods-runtime-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "shared").mkdir()
        self.archive = self.root / "fixture.tar.gz"
        self.binary = self.root / "install/bin/llama-server"
        self.make_archive()

    def make_archive(self, include_binary=True):
        with tarfile.open(self.archive, "w:gz") as archive:
            members = {"package/libfixture.dylib": b"fixture library", "package/fixture.metal": b"fixture metal"}
            if include_binary:
                members["package/llama-server"] = b"#!/bin/sh\nprintf verified-fixture\\n\n"
            for name, data in members.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                entry.mode = 0o755 if name.endswith("llama-server") else 0o644
                archive.addfile(entry, io.BytesIO(data))

    def run_install(self, extra="", release="b8210", real_pin=False, invocation=None):
        script = r'''
set -euo pipefail
source "$LIBRARY"
ai() { :; }
ai_ok() { :; }
ai_warn() { :; }
ai_err() { printf '%s\n' "$*" >&2; }
# Only relocate the outer /tmp template; production creates the private dirs.
mktemp() {
    if [[ "$*" == '-d /tmp/ods-llama.XXXXXXXXXX' ]]; then
        builtin command mktemp -d "$TEST_ROOT/shared/ods-llama.XXXXXXXXXX"
    else
        builtin command mktemp "$@"
    fi
}
download_with_progress() {
    printf 'download:%s\n' "$2" >> "$TEST_ROOT/events"
    if [[ "$(uname -s)" == Darwin ]]; then
        stat -f '%Lp' "$(dirname "$2")" > "$TEST_ROOT/private-mode"
    else
        stat -c '%a' "$(dirname "$2")" > "$TEST_ROOT/private-mode"
    fi
    printf partial > "$2.part"
    cp "$FIXTURE" "$2"
}
tar() { printf 'extract\n' >> "$TEST_ROOT/events"; builtin command tar "$@"; }
xattr() { printf 'quarantine\n' >> "$TEST_ROOT/events"; }
brew() { printf 'brew\n' >> "$TEST_ROOT/events"; return 0; }
command() {
    if [[ "$1" == -v && "$2" == llama-server ]]; then
        printf '%s\n' "$TEST_ROOT/brew-llama-server"
        return 0
    fi
    builtin command "$@"
}
if [[ "$REAL_PIN" != true ]]; then
    macos_llama_asset_sha256() { printf '%s\n' "$FIXTURE_HASH"; }
fi
'''
        script += extra + '\n' + (invocation or 'macos_install_native_llama "$TEST_BINARY" "$RELEASE" "llama-${RELEASE}-bin-macos-arm64.tar.gz" https://invalid.example/no-network') + '\n'
        env = dict(os.environ, LIBRARY=shell_path(LIBRARY), TEST_ROOT=shell_path(self.root),
                   TEST_BINARY=shell_path(self.binary), FIXTURE=shell_path(self.archive),
                   TEST_PYTHON=shell_path(sys.executable),
                   FIXTURE_HASH=hashlib.sha256(self.archive.read_bytes()).hexdigest(),
                   REAL_PIN=str(real_pin).lower(), RELEASE=release)
        result = subprocess.run([BASH, "-c", script], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(list((self.root / "shared").glob("ods-llama.*")), [], "private staging leaked")
        return result

    def events(self):
        path = self.root / "events"
        return path.read_text() if path.exists() else ""

    def assert_rejected_before_use(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.binary.exists())
        for event in ("extract\n", "quarantine\n", "brew\n"):
            self.assertNotIn(event, self.events())

    def test_fresh_install_ignores_shared_archive_partial_and_extraction_contents(self):
        archive = self.root / "shared/llama-b8210-bin-macos-arm64.tar.gz"
        archive.write_bytes(b"untrusted cached archive")
        partial = Path(str(archive) + ".part")
        partial.write_bytes(b"untrusted resume data")
        extra = r'''
mkdir -p "$TEST_ROOT/shared/llama-extract-$$/000-seed"
printf untrusted > "$TEST_ROOT/shared/llama-extract-$$/000-seed/llama-server"
'''
        result = self.run_install(extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"verified-fixture", self.binary.read_bytes())
        self.assertEqual(archive.read_bytes(), b"untrusted cached archive")
        self.assertEqual(partial.read_bytes(), b"untrusted resume data")
        self.assertEqual((self.binary.parent / "libfixture.dylib").read_bytes(), b"fixture library")
        self.assertEqual((self.binary.parent / "fixture.metal").read_bytes(), b"fixture metal")
        self.assertIn("quarantine\n", self.events())
        if os.name != "nt":
            self.assertEqual((self.root / "private-mode").read_text().strip(), "700")

    def test_alternate_release_uses_private_verified_install(self):
        result = self.run_install(release="b9014")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("llama-b9014-bin-macos-arm64.tar.gz", self.events())

    @unittest.skipIf(os.name == "nt", "requires Unix symlink semantics")
    def test_shared_symlinks_cannot_redirect_download_or_extraction(self):
        victim = self.root / "victim"
        victim.write_bytes(b"keep this file")
        archive = self.root / "shared/llama-b8210-bin-macos-arm64.tar.gz"
        archive.symlink_to(victim)
        Path(str(archive) + ".part").symlink_to(victim)
        directory = self.root / "victim-dir"
        directory.mkdir()
        result = self.run_install('ln -s "$TEST_ROOT/victim-dir" "$TEST_ROOT/shared/llama-extract-$$"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(victim.read_bytes(), b"keep this file")
        self.assertEqual(list(directory.iterdir()), [])

    def test_temporary_directory_creation_failure_stops_before_download(self):
        self.assert_rejected_before_use(self.run_install("mktemp() { return 1; }"))
        self.assertEqual(self.events(), "")

    @unittest.skipIf(os.name == "nt", "requires Unix signal semantics")
    def test_interrupt_cleans_partial_download(self):
        extra = r'''
download_with_progress() {
    printf partial > "$2.part"
    "$TEST_PYTHON" -c 'import os, signal; os.kill(os.getppid(), signal.SIGTERM)'
    return 1
}
'''
        result = self.run_install(extra)
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assert_rejected_before_use(result)

    def test_concurrent_downloads_have_independent_staging_and_cleanup(self):
        calls = r'''
macos_install_native_llama "$TEST_ROOT/one/llama-server" "$RELEASE" "llama-${RELEASE}-bin-macos-arm64.tar.gz" https://invalid.example/no-network &
first=$!
macos_install_native_llama "$TEST_ROOT/two/llama-server" "$RELEASE" "llama-${RELEASE}-bin-macos-arm64.tar.gz" https://invalid.example/no-network &
second=$!
wait "$first"
wait "$second"
'''
        result = self.run_install(invocation=calls)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "one/llama-server").read_bytes(), (self.root / "two/llama-server").read_bytes())
        downloads = [line for line in self.events().splitlines() if line.startswith("download:")]
        self.assertEqual(len(downloads), 2)
        self.assertEqual(len(set(downloads)), 2)

    def test_digest_mismatch_never_extracts_or_falls_back(self):
        self.assert_rejected_before_use(self.run_install('FIXTURE_HASH=' + '0' * 64))

    def test_missing_digest_fails_closed(self):
        self.assert_rejected_before_use(self.run_install("FIXTURE_HASH=''"))

    def test_missing_hash_tools_fails_closed(self):
        extra = r'''
command() {
    if [[ "$1" == -v && ( "$2" == shasum || "$2" == sha256sum ) ]]; then return 1; fi
    builtin command "$@"
}
'''
        self.assert_rejected_before_use(self.run_install(extra))

    def test_hash_command_failure_fails_closed(self):
        self.assert_rejected_before_use(self.run_install("shasum() { return 1; }"))

    def test_sha256sum_fallback_verifies_bytes(self):
        extra = r'''
command() {
    if [[ "$1" == -v && "$2" == shasum ]]; then return 1; fi
    builtin command "$@"
}
sha256sum() {
    if [[ -x /usr/bin/shasum ]]; then /usr/bin/shasum -a 256 "$1";
    else builtin command sha256sum "$1"; fi
}
'''
        result = self.run_install(extra)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_release_rejected_before_downloading(self):
        self.assert_rejected_before_use(self.run_install(release="unapproved", real_pin=True))
        self.assertEqual(self.events(), "")

    def test_existing_runtime_is_unchanged_even_for_custom_release(self):
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b"#!/bin/sh\necho existing\n")
        self.binary.chmod(0o755)
        result = self.run_install(release="custom", real_pin=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events(), "")
        self.assertEqual(self.binary.read_bytes(), b"#!/bin/sh\necho existing\n")

    def test_verified_archive_without_binary_fails_before_publication(self):
        self.make_archive(include_binary=False)
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.binary.exists())
        self.assertNotIn("quarantine", self.events())

    def test_tar_failure_prevents_publication(self):
        result = self.run_install("tar() { return 1; }")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.binary.exists())
        self.assertNotIn("quarantine", self.events())

    def test_transport_failure_preserves_homebrew_route(self):
        (self.root / "brew-llama-server").write_bytes(b"brew fixture")
        result = self.run_install("download_with_progress() { return 1; }")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.binary.read_bytes(), b"brew fixture")
        self.assertIn("brew\n", self.events())
        self.assertNotIn("extract\n", self.events())

    def test_failed_homebrew_cannot_publish_or_clear_quarantine(self):
        result = self.run_install("download_with_progress() { return 1; }; brew() { return 1; }")
        self.assert_rejected_before_use(result)

    def test_transport_failure_without_homebrew_fails_cleanly(self):
        extra = r'''
download_with_progress() { printf partial > "$2.part"; return 1; }
command() {
    if [[ "$1" == -v && "$2" == brew ]]; then return 1; fi
    builtin command "$@"
}
'''
        self.assert_rejected_before_use(self.run_install(extra))

    def test_source_call_stays_inside_noncloud_boundary(self):
        source = (ROOT / "installers/macos/install-macos.sh").read_text(encoding="utf-8")
        start = source.index('    # ── Download and start native llama-server')
        block = source[start:source.index('        # Start native llama-server with Metal', start)]
        self.assertIn('if ! $CLOUD_MODE; then', block)
        self.assertIn('macos_install_native_llama "$LLAMA_SERVER_BIN" "$LLAMA_CPP_RELEASE_TAG"', block)
        self.assertNotIn('LLAMA_ZIP=', block)


if __name__ == "__main__":
    unittest.main()
