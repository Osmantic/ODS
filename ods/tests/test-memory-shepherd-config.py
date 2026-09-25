#!/usr/bin/env python3
"""Exercise both shipped config consumers with actual unterminated files."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "memory-shepherd"


class ConfigEndOfFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ods-memory-config-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "memory.conf"
        self.baseline = b"# Curated baseline\n" + b"Keep durable instructions.\n" * 30
        (self.root / "baseline.md").write_bytes(self.baseline)
        self.memory = self.root / "MEMORY.md"
        self.archive = self.root / "archives"
        self.env = dict(os.environ, MEMORY_SHEPHERD_CONF=str(self.config))
        self.general = f"[general]\nbaseline_dir={self.root}\narchive_dir={self.archive}\n"

    def run_script(self, name, *args):
        result = subprocess.run(["bash", str(SCRIPTS / name), *args], env=self.env,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_final_baseline_setting_is_used_by_reset(self):
        for ending in ("", "\n", "\r\n"):
            with self.subTest(ending=repr(ending)):
                if self.memory.exists():
                    self.memory.unlink()
                self.config.write_text(self.general + f"[fixture]\nmemory_file={self.memory}\n"
                                       + "baseline=baseline.md" + ending)
                self.run_script("memory-shepherd.sh", "fixture")
                self.assertTrue(self.memory.exists(), "The configured agent was silently skipped")
                self.assertEqual(self.memory.read_bytes(), self.baseline)

    def test_final_archive_subdirectory_is_used_by_installer(self):
        binary = self.root / "bin"
        binary.mkdir()
        systemctl = binary / "systemctl"
        systemctl.write_text("#!/bin/sh\nexit 0\n")
        systemctl.chmod(0o755)
        self.env["PATH"] = f"{binary}:{os.environ['PATH']}"
        for index, ending in enumerate(("", "\n", "\r\n")):
            with self.subTest(ending=repr(ending)):
                subdir = f"custom-{index}"
                self.config.write_text(self.general + "[fixture]\n"
                                       + f"archive_subdir={subdir}" + ending)
                self.run_script("install.sh", "--prefix", str(self.root / "units"))
                self.assertTrue((self.archive / subdir).is_dir(),
                                "Installer ignored the final archive_subdir setting")

    def test_unterminated_comment_does_not_add_an_agent(self):
        for suffix in ("# trailing comment", "   ", ""):
            with self.subTest(suffix=suffix):
                self.config.write_text(self.general + "[fixture]\n# configured agent\n" + suffix)
                result = self.run_script("install.sh", "--dry-run", "--prefix", str(self.root / "units"))
                self.assertIn("Agents:  fixture\n", result.stdout)


if __name__ == "__main__":
    if sys.argv[1:] == ["--private-tmp"]:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
    else:
        if sys.platform != "linux":
            raise SystemExit("This regression requires Linux mount namespaces.")
        namespace = ["unshare", "--mount", "--fork"]
        if os.geteuid() != 0:
            namespace += ["--user", "--map-root-user"]
        raise SystemExit(subprocess.call(namespace + ["bash", "-c",
            'set -euo pipefail; mount --make-rprivate /; mount -t tmpfs tmpfs /tmp; exec "$@"',
            "bash", sys.executable, str(Path(__file__).resolve()), "--private-tmp"]))
