"""Focused unit tests for the shared release-tree verifier (scripts/lib/release-tree-sha.py).

Covers the release-blocking verifier gaps directly on the canonical helper:
  * install-manifest.sha256 must be an owner-owned safe regular file (a symlinked manifest
    is rejected and never followed);
  * duplicate install-manifest lines and malformed/unsafe relative paths are rejected;
  * an unsafe (world-writable) release-tree root is rejected;
  * extended attributes on the root, a directory, or a regular file are rejected;
  * a file replaced between lstat and open (race/substitution) is rejected because the
    opened descriptor no longer matches the observed object.
"""
import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pixel_test_release_tree_sha", ROOT / "scripts/lib/release-tree-sha.py",
)
rts = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(rts)


def _build(release, files):
    """Create a minimal valid release tree with the given {relpath: bytes} files."""
    release.mkdir()
    os.chmod(release, 0o700)
    manifest_lines = []
    for rel, data in sorted(files.items()):
        p = release / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        os.chmod(p, 0o600)
        manifest_lines.append(f"{hashlib.sha256(data).hexdigest()}  {rel}\n")
    manifest = (release / "install-manifest.sha256")
    manifest.write_text("".join(manifest_lines))
    os.chmod(manifest, 0o600)


class ReleaseTreeShaTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_exact_tree_is_accepted(self):
        release = self.directory / "rel"
        _build(release, {"VERSION": b"4.2.0\n", "plugin/index.js": b"x\n"})
        digest = rts.release_tree_sha(str(release))
        self.assertIsInstance(digest, str)
        self.assertEqual(len(digest), 64)
        self.assertEqual(rts.release_tree_sha(str(release)), digest)

    @unittest.skipUnless(hasattr(os, "setxattr"), "extended attributes are unavailable")
    def test_extended_attributes_on_opened_tree_objects_are_rejected(self):
        for relative in (".", "plugin", "VERSION"):
            with self.subTest(relative=relative):
                release = self.directory / f"rel-{relative.replace('.', 'root').replace('/', '-')}"
                _build(release, {"VERSION": b"4.2.0\n", "plugin/index.js": b"x\n"})
                target = release if relative == "." else release / relative
                try:
                    os.setxattr(target, b"user.pixel-release-test", b"present")
                except OSError as exc:
                    self.skipTest(f"test filesystem does not support user xattrs: {exc}")
                with self.assertRaisesRegex(SystemExit, "contains extended attributes"):
                    rts.release_tree_sha(str(release))

    def test_manifest_symlink_is_rejected_and_never_followed(self):
        # install-manifest.sha256 replaced with a symlink to a foreign file: the verifier
        # must open it descriptor-relatively with O_NOFOLLOW and reject (never follow it).
        release = self.directory / "rel"
        _build(release, {"VERSION": b"4.2.0\n"})
        target = self.directory / "foreign"
        target.write_bytes(b"not-a-manifest\n")
        os.unlink(release / "install-manifest.sha256")
        os.symlink(str(target), release / "install-manifest.sha256")
        with self.assertRaises(SystemExit):
            rts.release_tree_sha(str(release))

    def test_duplicate_manifest_line_is_rejected(self):
        release = self.directory / "rel"
        _build(release, {"VERSION": b"4.2.0\n"})
        manifest = release / "install-manifest.sha256"
        line = manifest.read_text().strip() + "\n"
        manifest.write_text(line + line)
        with self.assertRaises(SystemExit):
            rts.release_tree_sha(str(release))

    def test_unsafe_manifest_relative_path_is_rejected(self):
        release = self.directory / "rel"
        _build(release, {"VERSION": b"4.2.0\n"})
        manifest = release / "install-manifest.sha256"
        digest = hashlib.sha256((release / "VERSION").read_bytes()).hexdigest()
        manifest.write_text(f"{digest}  ../VERSION\n")
        with self.assertRaises(SystemExit):
            rts.release_tree_sha(str(release))

    def test_unsafe_release_root_mode_is_rejected(self):
        release = self.directory / "rel"
        _build(release, {"VERSION": b"4.2.0\n"})
        # Deterministically model the root as world-writable via the first root
        # os.fstat result rather than actually chmodding the temp directory.
        real_fstat = os.fstat
        first = {"seen": False}

        def first_root_fstat(fd):
            st = real_fstat(fd)
            if not first["seen"]:
                first["seen"] = True
                st = os.stat_result(
                    (st.st_mode | 0o002, st.st_ino, st.st_dev, st.st_nlink,
                     st.st_uid, st.st_gid, st.st_size, st.st_atime,
                     st.st_mtime, st.st_ctime),
                )
            return st

        with mock.patch("os.fstat", side_effect=first_root_fstat):
            with self.assertRaises(SystemExit):
                rts.release_tree_sha(str(release))

    def test_file_replacement_between_lstat_and_open_is_rejected(self):
        # Simulate a race/substitution: between the verifier's lstat of "good" and its open,
        # the path is swapped to the "evil" file. The opened descriptor must be validated to
        # still match the observed object; the inode mismatch must be rejected.
        release = self.directory / "rel"
        _build(release, {"VERSION": b"4.2.0\n", "good": b"good-content\n", "evil": b"evil-content\n"})
        real_open = os.open

        def swapped_open(path, flags, mode=0o600, *, dir_fd=None):
            if path == "good" and dir_fd is not None:
                return real_open("evil", flags, mode, dir_fd=dir_fd)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        with mock.patch("os.open", side_effect=swapped_open):
            with self.assertRaises(SystemExit):
                rts.release_tree_sha(str(release))

    def test_file_mode_mismatch_between_lstat_and_open_is_rejected(self):
        # A chmod between lstat and open must be rejected even when both modes are
        # independently safe: the digest must record the mode actually hashed, not a
        # stale one. Change the opened file's mode (0o600 -> 0o640) so fstat differs.
        release = self.directory / "rel"
        _build(release, {"VERSION": b"4.2.0\n"})
        real_open = os.open

        def chmod_on_open(path, flags, mode=0o600, *, dir_fd=None):
            fd = real_open(path, flags, mode, dir_fd=dir_fd)
            if path == "VERSION":
                os.fchmod(fd, 0o700)
            return fd

        with mock.patch("os.open", side_effect=chmod_on_open):
            with self.assertRaises(SystemExit):
                rts.release_tree_sha(str(release))

    def test_dir_mode_mismatch_between_lstat_and_open_is_rejected(self):
        # Same for directories: fstat mode must equal the observed lstat mode, so a
        # chmod race cannot make the digest record a stale directory mode.
        release = self.directory / "rel"
        _build(release, {"VERSION": b"4.2.0\n", "plugin/index.js": b"x\n"})
        os.chmod(release / "plugin", 0o700)
        real_open = os.open

        def chmod_on_open(path, flags, mode=0o600, *, dir_fd=None):
            fd = real_open(path, flags, mode, dir_fd=dir_fd)
            if path == "plugin":
                os.fchmod(fd, 0o500)
            return fd

        with mock.patch("os.open", side_effect=chmod_on_open):
            with self.assertRaises(SystemExit):
                rts.release_tree_sha(str(release))

    def test_manifest_replacement_between_traversal_and_parse_is_rejected(self):
        # install-manifest.sha256 is hashed during the walk, then reopened for parsing.
        # A replacement (different inode/content) swapped in before the parse open must be
        # rejected: the parsing fd must be the exact observed entry with matching bytes.
        release = self.directory / "rel"
        _build(release, {"VERSION": b"4.2.0\n"})
        replacement = self.directory / "replacement-manifest"
        replacement.write_text("tampered manifest content\n")
        real_open = os.open
        state = {"walk_opened": False}

        def swapped_manifest_open(path, flags, mode=0o600, *, dir_fd=None):
            if path == "install-manifest.sha256" and dir_fd is not None:
                if not state["walk_opened"]:
                    state["walk_opened"] = True
                    return real_open(path, flags, mode, dir_fd=dir_fd)
                return real_open(str(replacement), flags, mode)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        with mock.patch("os.open", side_effect=swapped_manifest_open):
            with self.assertRaises(SystemExit):
                rts.release_tree_sha(str(release))

    def test_legitimate_absolute_and_dotdot_symlink_targets_accepted(self):
        # Symlink targets are bound by text and never followed: absolute and relative-with-..
        # targets are legitimate and must not be rejected.
        release = self.directory / "rel"
        _build(release, {"VERSION": b"4.2.0\n"})
        os.symlink("/usr/bin/python3", release / "python3")
        os.symlink("../lib/python3.11", release / "python")
        digest = rts.release_tree_sha(str(release))
        self.assertIsInstance(digest, str)
        self.assertEqual(len(digest), 64)


if __name__ == "__main__":
    unittest.main()
