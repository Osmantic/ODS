#!/usr/bin/env python3
"""Exercise reset CLI archive publication in a private Linux /tmp namespace."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SHEPHERD = Path(__file__).resolve().parents[1] / "memory-shepherd/memory-shepherd.sh"
STAMP = "2026-09-14_1200"


class ArchiveCollisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ods-archive-collision-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.baseline = b"# Curated baseline\n" + b"Keep these instructions.\n" * 30
        (self.root / "baseline.md").write_bytes(self.baseline)
        self.memory = self.root / "MEMORY.md"
        self.archives = self.root / "archives"
        self.binary = self.root / "bin"
        self.binary.mkdir()
        (self.binary / "date").write_text(f"""#!/bin/bash
if [[ "$1" == '+%Y-%m-%d_%H%M' ]]; then
    printf '%s\\n' '{STAMP}'
else
    exec /bin/date "$@"
fi
""")
        (self.binary / "scp").write_text("""#!/bin/bash
set -euo pipefail
[[ "$1" == '-q' ]]
shift
if [[ "$1" == 'fixture@fixture.invalid:/fixture/MEMORY.md' ]]; then
    cp "$ODS_TEST_MEMORY" "$2"
elif [[ "$2" == 'fixture@fixture.invalid:/fixture/MEMORY.md' ]]; then
    cp "$1" "$ODS_TEST_MEMORY"
else
    exit 91
fi
""")
        for path in self.binary.iterdir():
            path.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.binary}:{os.environ['PATH']}",
                        ODS_TEST_MEMORY=str(self.memory),
                        MEMORY_SHEPHERD_CONF=str(self.root / "memory.conf"))

    def configure(self, remote=False, extra=""):
        location = ("remote_host=fixture.invalid\nremote_user=fixture\n"
                    "remote_memory=/fixture/MEMORY.md\n") if remote else f"memory_file={self.memory}\n"
        (self.root / "memory.conf").write_text(
            f"[general]\nbaseline_dir={self.root}\narchive_dir={self.archives}\n"
            f"[fixture]\nbaseline=baseline.md\narchive_subdir=shared\n{location}{extra}")

    def reset(self, target="fixture", success=True):
        result = subprocess.run(["bash", str(SHEPHERD), target], env=self.env,
                                capture_output=True, text=True, timeout=15)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_repeated_resets_preserve_both_scratch_and_full_archives(self):
        for remote in (False, True):
            for full in (False, True):
                with self.subTest(remote=remote, full=full):
                    self.configure(remote)
                    before = set(self.archives.rglob("*.md"))
                    saved = {}
                    for note in ("first unique note", "second unique note"):
                        content = f"# Previous baseline\n{'' if full else '---' + chr(10)}{note}\n".encode()
                        self.memory.write_bytes(content)
                        self.reset()
                        self.assertEqual(self.memory.read_bytes(), self.baseline)
                        created = set(self.archives.rglob("*.md")) - before - saved.keys()
                        self.assertEqual(len(created), 1, "Each reset must create a new archive")
                        file = created.pop()
                        saved[file] = file.read_bytes()
                        self.assertIn(note.encode(), saved[file])
                        if full:
                            self.assertEqual(saved[file], content)
                    for file, content in saved.items():
                        self.assertEqual(file.read_bytes(), content, "A later reset replaced an archive")

    def test_all_agents_can_share_an_archive_subdirectory(self):
        second = self.root / "SECOND.md"
        second.write_text("# Other baseline\n---\nsecond agent note\n")
        self.memory.write_text("# Previous baseline\n---\nfirst agent note\n")
        self.configure(extra=f"\n[second]\nbaseline=baseline.md\nmemory_file={second}\narchive_subdir=shared\n")
        self.reset("all")
        files = list((self.archives / "shared").glob("*.md"))
        self.assertEqual(len(files), 2)
        archived = b"\n".join(file.read_bytes() for file in files)
        self.assertIn(b"first agent note", archived)
        self.assertIn(b"second agent note", archived)

    def test_existing_archive_symlink_is_not_overwritten(self):
        self.configure()
        destination = self.archives / "shared"
        destination.mkdir(parents=True)
        outside = self.root / "existing.md"
        outside.write_bytes(b"Previous archive must survive.")
        (destination / f"{STAMP}.md").symlink_to(outside)
        self.memory.write_text("# Previous baseline\n---\nnew scratch note\n")
        self.reset()
        self.assertEqual(outside.read_bytes(), b"Previous archive must survive.")
        files = [file for file in destination.glob("*.md") if not file.is_symlink()]
        self.assertEqual(len(files), 1)
        self.assertIn(b"new scratch note", files[0].read_bytes())

    def test_archive_creation_failure_leaves_memory_intact(self):
        self.configure()
        self.archives.write_text("This file blocks archive directory creation.")
        content = b"# Previous baseline\n---\nKeep this note on archive failure.\n"
        self.memory.write_bytes(content)
        self.reset(success=False)
        self.assertEqual(self.memory.read_bytes(), content)


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
