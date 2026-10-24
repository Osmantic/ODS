#!/usr/bin/env python3
"""Regression test: preset commands must reject path traversal."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
CLI = ROOT_DIR / "ods-cli"


class TestCLIPresetNameValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = Path(self.tmp.name)
        for d in ("lib", "presets"):
            (self.fixture / d).mkdir(parents=True)
        (self.fixture / "docker-compose.base.yml").write_text("services: {}\n")
        (self.fixture / ".env").write_text("GPU_BACKEND=cpu\nODS_MODE=local\n")
        shutil.copy(CLI, self.fixture / "ods-cli")
        for sh in (ROOT_DIR / "lib").glob("*.sh"):
            shutil.copy(sh, self.fixture / "lib")
        self.env = dict(os.environ)
        self.env["ODS_HOME"] = str(self.fixture)
        self.env["INSTALL_DIR"] = str(self.fixture)

    def tearDown(self):
        self.tmp.cleanup()

    def _run_cli(self, *args, stdin_text=""):
        cmd = ["bash", str(self.fixture / "ods-cli"), *args]
        return subprocess.run(
            cmd, input=stdin_text, capture_output=True, text=True,
            check=False, env=self.env,
        )

    def test_save_rejects_path_traversal(self):
        res = self._run_cli("preset", "save", "../outside")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Invalid preset name", res.stderr)
        self.assertFalse((self.fixture / "outside").exists())

    def test_delete_rejects_path_traversal(self):
        target = self.fixture / "data"
        target.mkdir()
        (target / "sentinel").write_text("keep", encoding="utf-8")
        res = self._run_cli("preset", "delete", "../data", stdin_text="y\n")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Invalid preset name", res.stderr)
        self.assertTrue((target / "sentinel").exists())

    def test_export_rejects_path_traversal(self):
        outside = self.fixture / "outside-preset"
        outside.mkdir()
        (outside / "meta.txt").write_text("name=outside\n", encoding="utf-8")
        out_tar = str(self.fixture / "export.tar.gz")
        res = self._run_cli("preset", "export", "../outside-preset", out_tar)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Invalid preset name", res.stderr)
        self.assertFalse(Path(out_tar).exists())

    def test_save_rejects_dot_and_backslash(self):
        for invalid in (".", r"group\preset"):
            res = self._run_cli("preset", "save", invalid)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("Invalid preset name", res.stderr)

    def test_valid_preset_name_with_spaces_accepted(self):
        res = self._run_cli("preset", "save", "team demo")
        self.assertEqual(res.returncode, 0, res.stderr)
        preset_dir = self.fixture / "presets" / "team demo"
        self.assertTrue((preset_dir / "meta.txt").exists())


    def test_aliases_reject_traversal(self):
        target = self.fixture / "data"
        target.mkdir()
        sentinel = target / "sentinel"
        sentinel.write_text("keep", encoding="utf-8")
        for action in ("s", "l", "rm", "e"):
            with self.subTest(action=action):
                res = self._run_cli("preset", action, "../data", stdin_text="y\n")
                self.assertNotEqual(res.returncode, 0)
                self.assertIn("Invalid preset name", res.stderr)
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_load_and_both_diff_arguments_reject_traversal(self):
        commands = [("load", "../outside")]
        for action in ("diff", "d"):
            commands.extend(((action, "../outside", "good"),
                             (action, "good", "../outside")))
        for args in commands:
            with self.subTest(args=args):
                res = self._run_cli("preset", *args)
                self.assertNotEqual(res.returncode, 0)
                self.assertIn("Invalid preset name", res.stderr)

    def test_save_rejects_dotdot_leading_dash_and_controls(self):
        for invalid in ("..", "-option", "bad\nname", "bad\x01name", "bad\x7fname"):
            with self.subTest(name=invalid):
                res = self._run_cli("preset", "save", invalid)
                self.assertNotEqual(res.returncode, 0)
                self.assertIn("Invalid preset name", res.stderr)

if __name__ == "__main__":
    unittest.main()
