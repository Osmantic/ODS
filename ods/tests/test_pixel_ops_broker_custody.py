"""Exercise the privileged broker-home custody code on a real POSIX filesystem."""

import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


UNINSTALL = Path(__file__).resolve().parents[1] / "lib/pixel-uninstall.sh"


def custody_code() -> str:
    source = UNINSTALL.read_text(encoding="utf-8")
    start = source.index('if ! ops_custody_path="$(sudo python3 -')
    start = source.index("<<'PY'\n", start) + len("<<'PY'\n")
    end = source.index("\nPY\n", start)
    return source[start:end]


def classify(root: Path, mode: str) -> str:
    source = UNINSTALL.read_text(encoding="utf-8")
    start = source.index('state_cleanup_action = "remove"')
    end = source.index('\nprint("present|', start)
    namespace = {
        "os": os,
        "pathlib": __import__("pathlib"),
        "stat": stat,
        "state_dir": root,
        "state_cleanup_mode": mode,
        "broker_uid": os.getuid(),
        "broker_gid": os.getgid(),
        "owner_uid": os.getuid(),
        "root_uid": os.getuid(),
        "root_gid": os.getgid(),
        "exists": lambda path: path.exists() or path.is_symlink(),
    }
    exec(compile(source[start:end], str(UNINSTALL), "exec"), namespace)
    return namespace["state_cleanup_action"]


def invoke(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", custody_code(), str(root), str(os.getuid()),
         str(os.getgid()), str(os.getuid()), str(os.getgid())],
        capture_output=True,
        text=True,
        check=False,
    )


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux mount custody")
class BrokerCustodyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_preserves_nested_data_symlink_and_hardlink(self) -> None:
        root = self.root / "pixel-ops-broker"
        root.mkdir(mode=0o750)
        (root / ".composer").mkdir(mode=0o700)
        (root / ".composer" / "user-data").write_text("keep me", encoding="utf-8")
        outside = self.root / "outside"
        outside.write_text("outside data", encoding="utf-8")
        os.link(outside, root / "shared")
        os.symlink(outside, root / ".ghcup")
        large = root / ".dotnet"
        large.mkdir(mode=0o700)
        (large / "payload").write_bytes(b"x" * (1024 * 1024))

        result = invoke(root)

        self.assertEqual(result.returncode, 0, result.stderr)
        retained = Path(result.stdout.strip())
        self.assertFalse(root.exists())
        self.assertEqual(retained.parent.parent, self.root)
        self.assertEqual(stat.S_IMODE(retained.parent.lstat().st_mode), 0o700)
        self.assertEqual((retained / ".composer/user-data").read_text(), "keep me")
        self.assertEqual((retained / ".dotnet/payload").stat().st_size, 1024 * 1024)
        self.assertTrue((retained / ".ghcup").is_symlink())
        self.assertEqual(os.readlink(retained / ".ghcup"), str(outside))
        self.assertEqual((retained / "shared").stat().st_ino, outside.stat().st_ino)
        self.assertEqual(outside.read_text(), "outside data")

    def test_rejects_unsafe_parent_before_move(self) -> None:
        root = self.root / "pixel-ops-broker"
        root.mkdir(mode=0o750)
        self.root.chmod(0o777)

        result = invoke(root)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe Pixel Operations Broker custody path", result.stderr)
        self.assertTrue(root.is_dir())
        self.assertFalse(list(self.root.glob(".pixel-ops-broker-custody-*")))

    def test_absent_root_does_not_create_custody(self) -> None:
        result = invoke(self.root / "pixel-ops-broker")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "absent")
        self.assertFalse(list(self.root.glob(".pixel-ops-broker-custody-*")))

    def test_clean_source_transition_does_not_need_custody(self) -> None:
        root = self.root / "pixel-ops-broker"
        root.mkdir(mode=0o750)
        (root / "results").mkdir(mode=0o700)
        receipt = root / "results" / "receipt"
        receipt.write_text("safe", encoding="utf-8")
        receipt.chmod(0o600)
        self.assertEqual(classify(root, "source-transition"), "remove")

    def test_legacy_child_is_preserved_only_on_source_transition(self) -> None:
        root = self.root / "pixel-ops-broker"
        root.mkdir(mode=0o750)
        inherited = root / ".composer"
        inherited.mkdir(mode=0o755)
        inherited.chmod(0o755)
        self.assertEqual(classify(root, "source-transition"), "preserve")
        with self.assertRaises(SystemExit):
            classify(root, "strict")


if __name__ == "__main__":
    unittest.main()
