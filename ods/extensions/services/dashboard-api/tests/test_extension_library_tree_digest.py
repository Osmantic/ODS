"""Complete library-payload provenance, not the legacy timestamp cache."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[4] / "bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

import extension_library_tree_digest as tree  # noqa: E402


@pytest.fixture(autouse=True)
def private_tree_fixture_umask():
    """Tree fixtures must model host-owned files under any runner umask."""
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def test_bytes_paths_and_empty_directories_are_bound(tmp_path):
    root = tmp_path / "extension"
    root.mkdir()
    (root / "manifest.yaml").write_bytes(b"service: one\n")
    (root / "assets").mkdir()
    first = tree.digest_extension_tree(root)
    assert first.startswith("sha256:") and len(first) == 71
    assert tree.digest_extension_tree(root) == first

    (root / "assets" / "hook.sh").write_bytes(b"exit 0\n")
    second = tree.digest_extension_tree(root)
    assert second != first
    (root / "assets" / "hook.sh").write_bytes(b"exit 1\n")
    assert tree.digest_extension_tree(root) != second

    (root / "assets" / "hook.sh").rename(root / "assets" / "other.sh")
    assert tree.digest_extension_tree(root) != second


def test_added_install_receipt_is_excluded_but_other_files_are_not(tmp_path):
    root = tmp_path / "extension"
    root.mkdir()
    (root / "compose.yaml").write_bytes(b"services: {}\n")
    baseline = tree.digest_extension_tree(root)
    (root / ".ods-library-receipt.json").write_bytes(b"receipt")
    assert tree.digest_extension_tree(root) == baseline
    (root / "README.md").write_bytes(b"new supporting payload")
    assert tree.digest_extension_tree(root) != baseline


def test_crlf_bytes_are_hashed_without_platform_text_translation(tmp_path):
    root = tmp_path / "extension"
    root.mkdir()
    source = root / "README.md"
    source.write_bytes(b"first\r\nsecond\r\n")
    crlf = tree.digest_extension_tree(root)
    source.write_bytes(b"first\nsecond\n")
    assert tree.digest_extension_tree(root) != crlf


def test_indexed_payload_is_stable_and_refuses_unstaged_or_untracked_edits(
    tmp_path, monkeypatch
):
    repository = tmp_path / "repository"
    repository.mkdir()
    root = repository / "library" / "example"
    root.mkdir(parents=True)
    (repository / ".gitattributes").write_bytes(b"*.md text eol=lf\n")
    source = root / "README.md"
    source.write_bytes(b"first\r\nsecond\r\n")

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repository), *args], capture_output=True, check=True
        )

    git("init", "-q")
    git("add", ".gitattributes", "library/example/README.md")
    original = tree.digest_indexed_extension_tree(repository, root)
    assert tree.digest_indexed_extension_tree(repository, root) == original
    source.write_bytes(b"first\nsecond\n")
    assert tree.digest_extension_tree(root) == original

    source.write_bytes(b"changed\n")
    with pytest.raises(
        tree.LibraryTreeDigestError, match="library-tree-unstaged-drift"
    ):
        tree.digest_indexed_extension_tree(repository, root)
    git("add", "library/example/README.md")
    assert tree.digest_indexed_extension_tree(repository, root) != original

    original_git = tree._git

    def size_only(repository_path, *argv):
        if argv[:2] == ("cat-file", "blob"):
            raise AssertionError("oversized blob content was read")
        return original_git(repository_path, *argv)

    monkeypatch.setattr(tree, "MAX_TREE_BYTES", 2)
    monkeypatch.setattr(tree, "_git", size_only)
    with pytest.raises(
        tree.LibraryTreeDigestError, match="library-tree-git-blob-invalid"
    ):
        tree.digest_indexed_extension_tree(repository, root)
    monkeypatch.setattr(tree, "MAX_TREE_BYTES", 50 * 1024 * 1024)
    monkeypatch.setattr(tree, "_git", original_git)

    (root / "untracked.sh").write_bytes(b"exit 0\n")
    with pytest.raises(
        tree.LibraryTreeDigestError, match="library-tree-untracked-entry"
    ):
        tree.digest_indexed_extension_tree(repository, root)


def test_symlink_and_oversized_payload_fail_closed(tmp_path, monkeypatch):
    root = tmp_path / "extension"
    root.mkdir()
    (root / "manifest.yaml").write_bytes(b"service: one\n")
    monkeypatch.setattr(tree, "MAX_TREE_BYTES", 2)
    with pytest.raises(tree.LibraryTreeDigestError, match="library-tree-file-invalid"):
        tree.digest_extension_tree(root)
    monkeypatch.setattr(tree, "MAX_TREE_BYTES", 50 * 1024 * 1024)
    link = root / "linked"
    try:
        link.symlink_to(root / "manifest.yaml")
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(tree.LibraryTreeDigestError, match="library-tree-entry-invalid"):
        tree.digest_extension_tree(root)


def test_group_writable_runtime_payload_is_refused(tmp_path):
    if os.name != "posix":
        pytest.skip("Linux host custody only")
    root = tmp_path / "extension"
    root.mkdir()
    source = root / "README.md"
    source.write_bytes(b"approved\n")
    expected = tree.digest_extension_tree(root)
    source.chmod(0o660)
    with pytest.raises(tree.LibraryTreeDigestError, match="library-tree-custody-invalid"):
        tree.digest_extension_tree(root)
    source.chmod(0o600)
    root.chmod(0o770)
    with pytest.raises(tree.LibraryTreeDigestError, match="library-tree-custody-invalid"):
        tree.digest_extension_tree(root)
    root.chmod(0o700)
    assert tree.digest_extension_tree(root) == expected


def test_entry_limit_stops_enumeration_before_file_content(tmp_path, monkeypatch):
    root = tmp_path / "extension"
    root.mkdir()
    for name in ("one", "two", "three"):
        (root / name).write_bytes(b"payload")
    monkeypatch.setattr(tree, "MAX_TREE_ENTRIES", 2)

    def unexpected_read(*_args):
        raise AssertionError("entry content was read after limit")

    monkeypatch.setattr(tree, "_read_file", unexpected_read)
    with pytest.raises(tree.LibraryTreeDigestError, match="library-tree-entry-limit"):
        tree.digest_extension_tree(root)


def test_root_and_special_entries_are_refused(tmp_path):
    with pytest.raises(tree.LibraryTreeDigestError, match="library-tree-root-invalid"):
        tree.digest_extension_tree(Path("relative"))
    root = tmp_path / "extension"
    root.mkdir()
    (root / "special").mkdir()
    assert tree.digest_extension_tree(root).startswith("sha256:")


def test_generated_catalog_pins_every_library_definition():
    ods = Path(__file__).resolve().parents[4]
    catalog = json.loads((ods / "config" / "extensions-catalog.json").read_text())
    library = [
        item
        for item in catalog["extensions"]
        if item["planning"].get("definitionSource") == "library"
    ]
    assert library
    assert all(
        item["planning"].get("sourceTreeSha256", "").startswith("sha256:")
        for item in library
    )
