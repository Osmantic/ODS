#!/usr/bin/env python3
"""Successful archive moves must preserve Hugging Face cache resolution."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/llm-cold-storage.sh"


class RelativeArchiveLinks(unittest.TestCase):
    def test_archive_and_restore_resolve_relative_cold_directory(self):
        for relative in ("cold pool", "../cold pool"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory(prefix="ods-cold-links-") as directory:
                root = Path(directory)
                work = root / "working/nested"
                work.mkdir(parents=True)
                cache = root / "cache"
                name = "models--ODSFixture--relative-path-model"
                model = cache / name
                model.mkdir(parents=True)
                (model / "weights.bin").write_bytes(b"fixture weights")
                # Also use a genuinely old access time, so the fixture remains
                # valid with backlog changes that remove the bc dependency.
                os.utime(model / "weights.bin", (1_000_000_000, 1_000_000_000))
                cold = (work / relative).resolve()
                self.assertTrue(cold.is_relative_to(root))
                cold.mkdir()
                bindir = root / "bin"
                bindir.mkdir()
                # Existing age-calculation and process-discovery contracts are
                # outside this path test. No real model/process is consulted.
                for command, body in (("bc", "printf '1728000\\n'"), ("pgrep", "exit 1")):
                    path = bindir / command
                    path.write_text(f"#!/bin/bash\n{body}\n")
                    path.chmod(0o755)
                env = {**os.environ, "HF_CACHE": str(cache), "COLD_DIR": relative,
                       "LOG_FILE": str(root / "logs/cold.log"), "PATH": f"{bindir}:{os.environ['PATH']}"}

                def invoke(*args, working_dir=work, environment=env):
                    return subprocess.run(["bash", str(SCRIPT), *args], cwd=working_dir, env=environment,
                                          capture_output=True, text=True, check=True, timeout=10)

                dry_run = invoke()
                self.assertIn("WOULD ARCHIVE", dry_run.stdout)
                self.assertFalse(model.is_symlink())
                invoke("--execute")
                self.assertTrue(model.is_symlink())
                self.assertEqual(model.resolve(), cold / name)
                self.assertEqual((model / "weights.bin").read_bytes(), b"fixture weights")
                self.assertIn("SYMLINK -> cold", invoke("--status").stdout)
                invoke("--execute")  # Already archived; never nest the model.
                self.assertFalse((cold / name / name).exists())
                invoke("--restore", name)
                self.assertFalse(model.is_symlink())
                self.assertEqual((model / "weights.bin").read_bytes(), b"fixture weights")
                self.assertFalse((cold / name).exists())


if __name__ == "__main__":
    unittest.main()
