"""Exercise production source copying followed by phase 06 SearXNG generation."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(os.environ.get("ODS_TEST_SOURCE_ROOT", Path(__file__).resolve().parents[2]))
ODS = ROOT / "ods"
STOCK = "CHANGEME-run-the-installer-to-generate-a-secure-key"
OWNER = b'server:\n  secret_key: "owner-secret"\nsearch:\n  safe_search: 2\n# owner engines\n'


@unittest.skipUnless(shutil.which("bash") and shutil.which("rsync"), "requires bash and rsync")
class SearxngSourceCopyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ods-searxng-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.src, self.dest = self.root / "source", self.root / "install"
        self.dest.mkdir(mode=0o700)
        self.leaf = self.dest / "config/searxng/settings.yml"
        source_leaf = self.src / "config/searxng/settings.yml"
        source_leaf.parent.mkdir(parents=True)
        shutil.copyfile(ODS / "config/searxng/settings.yml", source_leaf)
        for name in (".gitignore", ".dockerignore", ".env.example", ".env.schema.json"):
            (self.src / name).write_text("fixture\n")
        text = (ODS / "installers/phases/06-directories.sh").read_text()
        begin = "    # Generate SearXNG config with randomized secret key"
        end = "# Documentation, CLI tools, and compose variants already copied by rsync/cp block above"
        self.assertEqual(text.count(begin), 1)
        self.assertEqual(text.count(end), 1)
        block = text.split(begin, 1)[1].split(end, 1)[0].rstrip()
        self.assertTrue(block.endswith("\nfi"), "phase outer condition boundary changed")
        block = block[:-3]
        self.assertEqual(block.count('cat > "$INSTALL_DIR/config/searxng/settings.yml"'), 1)
        self.generator = self.root / "generator.sh"
        self.generator.write_text("generate() {\n" + block + "\n}\n")
        checked = subprocess.run(["/bin/bash", "-n", str(self.generator)], capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def owner(self, content=OWNER):
        self.leaf.parent.mkdir(parents=True, exist_ok=True)
        self.leaf.write_bytes(content)

    def run_flow(self, *, force=False, copy=True, generate=True, no_rsync=False,
                 read_error=False, transaction=False):
        script = r'''
set -euo pipefail
source "$1/installers/lib/source-copy.sh"
source "$1/installers/lib/searxng-locale.sh"
source "$2"
INSTALL_DIR="$4"; SEARXNG_SECRET=generated-fixture-secret; FORCE="$5"
error() { printf '%s\n' "$*" >&2; }
ai_ok() { :; }; _phase06_step() { :; }
# Permission repair itself is covered by installed validation, not this stub.
_phase06_repair_host_path() { return 1; }
'''
        if no_rsync:
            script += 'command() { if [[ "$1" == -v && "$2" == rsync ]]; then return 1; fi; builtin command "$@"; }\n'
        if transaction:
            script += "ODS_PIXEL_SOURCE_TRANSACTION=fixture-held\n"
        if copy:
            script += 'ods_copy_install_source "$3" "$4" /dev/null || exit $?\n'
        if read_error:
            script += 'grep() { return 2; }\n'
        if generate:
            script += 'generate\n'
        return subprocess.run(["/bin/bash", "-c", script, "fixture", str(ODS),
                               str(self.generator), str(self.src), str(self.dest),
                               "true" if force else "false"], capture_output=True, text=True,
                              timeout=15, env={**os.environ, "LC_ALL": "C"})

    def succeeded(self, **kwargs):
        result = self.run_flow(**kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fresh_then_owner_rerun_then_force(self):
        self.succeeded()
        self.assertIn(b'  secret_key: "generated-fixture-secret"', self.leaf.read_bytes())
        self.assertNotIn(STOCK.encode(), self.leaf.read_bytes())
        self.owner()
        self.succeeded()
        self.assertEqual(self.leaf.read_bytes(), OWNER)
        self.succeeded(force=True)
        self.assertIn(b'  secret_key: "generated-fixture-secret"', self.leaf.read_bytes())
        self.assertNotIn(b"owner engines", self.leaf.read_bytes())

    def test_in_place_stock_then_owner(self):
        self.dest = self.src
        self.leaf = self.dest / "config/searxng/settings.yml"
        self.succeeded()
        self.assertNotIn(STOCK.encode(), self.leaf.read_bytes())
        self.owner()
        self.succeeded()
        self.assertEqual(self.leaf.read_bytes(), OWNER)

    def test_placeholder_comment_preserved(self):
        content = OWNER + ("# secret_key: \"" + STOCK + '"\n').encode()
        self.owner(content)
        self.succeeded()
        self.assertEqual(self.leaf.read_bytes(), content)

    def test_stock_scalar_variants(self):
        for quote in ("", "'", '"'):
            with self.subTest(quote=quote):
                self.owner(f"server:\n  secret_key: {quote}{STOCK}{quote} # factory\n".encode())
                self.succeeded()
                self.assertNotIn(STOCK.encode(), self.leaf.read_bytes())

    def test_read_error_preserves_bytes_and_fails(self):
        self.owner()
        result = self.run_flow(read_error=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.leaf.read_bytes(), OWNER)
        self.assertIn("Failed to read existing SearXNG", result.stderr)

    def test_empty_settings_fail_with_force_recovery(self):
        self.owner(b"")
        result = self.run_flow()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.leaf.read_bytes(), b"")
        self.assertIn("use --force", result.stderr)
        self.succeeded(force=True)
        self.assertIn(b"generated-fixture-secret", self.leaf.read_bytes())

    def test_unsafe_paths_fail_before_copy_or_generation(self):
        for kind in ("config-link", "searxng-link", "leaf-link", "leaf-dir", "leaf-fifo", "parent-file"):
            for mode in ("copy", "generate"):
                with self.subTest(kind=kind, mode=mode):
                    self.dest = self.root / f"{kind}-{mode}"
                    self.dest.mkdir()
                    foreign = self.root / f"foreign-{kind}-{mode}"
                    foreign.mkdir()
                    sentinel = foreign / "settings.yml"
                    sentinel.write_bytes(OWNER)
                    cfg = self.dest / "config"
                    if kind == "config-link":
                        cfg.symlink_to(foreign, target_is_directory=True)
                    else:
                        cfg.mkdir()
                        sx = cfg / "searxng"
                        if kind == "searxng-link":
                            sx.symlink_to(foreign, target_is_directory=True)
                        elif kind == "parent-file":
                            sx.write_bytes(OWNER)
                        else:
                            sx.mkdir()
                            leaf = sx / "settings.yml"
                            if kind == "leaf-link":
                                leaf.symlink_to(sentinel)
                            elif kind == "leaf-dir":
                                leaf.mkdir()
                            else:
                                os.mkfifo(leaf)
                    result = self.run_flow(copy=mode == "copy", generate=mode == "generate", force=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(sentinel.read_bytes(), OWNER)

    def test_missing_rsync_preserves_owner(self):
        self.owner()
        result = self.run_flow(no_rsync=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.leaf.read_bytes(), OWNER)

    def test_fresh_fallback_keeps_dotfiles(self):
        self.succeeded(no_rsync=True)
        self.assertNotIn(STOCK.encode(), self.leaf.read_bytes())
        for name in (".dockerignore", ".env.example", ".env.schema.json"):
            self.assertEqual((self.dest / name).read_bytes(), (self.src / name).read_bytes())

    def test_cloud_and_held_source_exclusions(self):
        self.owner()
        cloud = self.dest / "config/litellm/cloud.yaml"
        cloud.parent.mkdir(mode=0o700)
        cloud.write_text("model_list: []\n")
        cloud.chmod(0o600)
        (self.src / "config/litellm").mkdir()
        (self.src / "config/litellm/cloud.yaml").write_text("replace me\n")
        for directory in ("bin", "lib", "scripts", "installers", "extensions", "vendor"):
            (self.src / directory).mkdir()
            (self.dest / directory).mkdir()
            (self.src / directory / "keep").write_text("source")
            (self.dest / directory / "keep").write_text("held")
        self.succeeded(transaction=True)
        self.assertEqual(cloud.read_text(), "model_list: []\n")
        self.assertEqual(self.leaf.read_bytes(), OWNER)
        for directory in ("bin", "lib", "scripts", "installers", "extensions", "vendor"):
            self.assertEqual((self.dest / directory / "keep").read_text(), "held")


if __name__ == "__main__":
    unittest.main()
