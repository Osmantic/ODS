#!/usr/bin/env python3
"""Regression test: verify build-installation-context writes SOUL.md atomically and guards missing templates."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from importlib import import_module
builder = import_module("build-installation-context")


class BuildInstallationContextAtomicTests(unittest.TestCase):
    def test_missing_template_raises_filenotfound(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            missing_template = tmp_path / "missing.template"
            env_path = tmp_path / ".env"
            env_path.write_text("ODS_DEVICE_NAME=test-device\n", encoding="utf-8")
            out_path = tmp_path / "SOUL.md"

            with self.assertRaises(FileNotFoundError) as ctx:
                builder.build_soul(missing_template, env_path, out_path)
            self.assertIn("Template not found", str(ctx.exception))

    def test_atomic_write_and_replace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            template = tmp_path / "SOUL.md.template"
            template.write_text("Hello <!-- INSTALLATION_CONTEXT -->\nBye\n", encoding="utf-8")
            env_path = tmp_path / ".env"
            env_path.write_text("ODS_DEVICE_NAME=my-node\n", encoding="utf-8")
            out_path = tmp_path / "SOUL.md"

            changed = builder.build_soul(template, env_path, out_path)
            self.assertTrue(changed)
            self.assertTrue(out_path.is_file())
            self.assertFalse(out_path.with_suffix(".tmp").exists())
            content = out_path.read_text(encoding="utf-8")
            self.assertIn("Hello", content)
            self.assertIn("Bye", content)


if __name__ == "__main__":
    unittest.main()
