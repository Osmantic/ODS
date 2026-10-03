import base64
import csv
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
NORMALIZER = ROOT / "scripts/lib/normalize-release-venv.py"


def digest(payload: bytes) -> str:
    value = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode("ascii")
    return f"sha256={value}"


def tree(root: Path) -> dict[str, tuple[str, int, str]]:
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode & 0o777
        if path.is_symlink():
            result[relative] = ("symlink", mode, os.readlink(path))
        elif path.is_dir():
            result[relative] = ("dir", mode, "")
        else:
            result[relative] = ("file", mode, hashlib.sha256(path.read_bytes()).hexdigest())
    return result


@unittest.skipUnless(sys.platform == "linux", "release venv normalization is Linux-only")
class ReleaseVenvNormalizationTests(unittest.TestCase):
    def make_venv(self, stage: Path, final_venv: Path) -> Path:
        venv = stage / "web-courier/.venv"
        bin_dir = venv / "bin"
        dist = venv / "lib/python3.12/site-packages/fixture-1.0.dist-info"
        cache = venv / "lib/python3.12/site-packages/fixture/__pycache__"
        bin_dir.mkdir(parents=True)
        dist.mkdir(parents=True)
        cache.mkdir(parents=True)
        transient = str(venv)
        files = {
            venv / "pyvenv.cfg": f"home = /usr/bin\ncommand = /usr/bin/python3 -m venv {transient}\n",
            bin_dir / "activate": f"VIRTUAL_ENV={transient}\n",
            bin_dir / "fixture": f"#!{transient}/bin/python\nprint('fixture')\n",
        }
        for path, content in files.items():
            path.write_text(content, encoding="utf-8")
            path.chmod(0o755 if path.parent == bin_dir else 0o600)
        bytecode = cache / "fixture.cpython-312.pyc"
        bytecode.write_bytes(b"synthetic-pyc:" + os.fsencode(transient))
        bytecode.chmod(0o600)
        record = dist / "RECORD"
        fixture_payload = (bin_dir / "fixture").read_bytes()
        with record.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["../../../bin/fixture", digest(fixture_payload), str(len(fixture_payload))])
            writer.writerow(["fixture-1.0.dist-info/RECORD", "", ""])
        record.chmod(0o600)
        result = subprocess.run(
            [sys.executable, str(NORMALIZER), str(venv), str(final_venv)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rewritten=3", result.stdout)
        self.assertIn("bytecode_removed=1", result.stdout)
        return venv

    def test_different_stage_paths_normalize_to_identical_tamper_evident_trees(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            final_venv = root / "install/releases/4.3.12/web-courier/.venv"
            first = self.make_venv(root / "releases/.4.3.12.stage.111", final_venv)
            second = self.make_venv(root / "releases/.4.3.12.stage.999999", final_venv)
            self.assertEqual(tree(first), tree(second))
            self.assertFalse(list(first.rglob("*.pyc")))
            self.assertFalse(list(first.rglob("__pycache__")))
            for path in first.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    self.assertNotIn(b".stage.", path.read_bytes())
            tool = first / "bin/fixture"
            self.assertEqual(tool.read_text(encoding="utf-8").splitlines()[0], f"#!{final_venv}/bin/python")
            with (first / "lib/python3.12/site-packages/fixture-1.0.dist-info/RECORD").open(
                "r", encoding="utf-8", newline="",
            ) as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows[0][1], digest(tool.read_bytes()))
            self.assertEqual(rows[0][2], str(tool.stat().st_size))
            before = tree(first)
            tool.write_bytes(tool.read_bytes() + b"# tamper\n")
            self.assertNotEqual(before, tree(first))

    def test_unexpected_transient_path_surface_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            final_venv = root / "install/releases/4.3.12/web-courier/.venv"
            stage = root / "releases/.4.3.12.stage.123"
            venv = stage / "web-courier/.venv"
            (venv / "bin").mkdir(parents=True)
            (venv / "pyvenv.cfg").write_text(f"command = {venv}\n", encoding="utf-8")
            unexpected = venv / "lib/unexpected.bin"
            unexpected.parent.mkdir(parents=True)
            unexpected.write_bytes(b"opaque:" + os.fsencode(venv))
            for path in (venv / "pyvenv.cfg", unexpected):
                path.chmod(0o600)
            result = subprocess.run(
                [sys.executable, str(NORMALIZER), str(venv), str(final_venv)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("transient staging path remains", result.stderr)

    def test_transient_symlink_target_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            final_venv = root / "install/releases/4.3.12/web-courier/.venv"
            venv = root / "releases/.4.3.12.stage.123/web-courier/.venv"
            (venv / "bin").mkdir(parents=True)
            (venv / "pyvenv.cfg").write_text(f"command = {venv}\n", encoding="utf-8")
            (venv / "pyvenv.cfg").chmod(0o600)
            (venv / "bin/transient-link").symlink_to(venv / "lib/python3.12")
            result = subprocess.run(
                [sys.executable, str(NORMALIZER), str(venv), str(final_venv)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("transient staging path remains in symlink", result.stderr)


if __name__ == "__main__":
    unittest.main()
