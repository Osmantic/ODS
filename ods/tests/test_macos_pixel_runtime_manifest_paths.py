"""Runtime manifests retain path, custody and live-file checks."""
import hashlib
import os
from pathlib import Path
import stat
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from pixel_access_bridge import AccessError  # noqa: E402
from pixel_provider import runtime_custody as runtime  # noqa: E402


@pytest.fixture
def protected_tree(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    root.mkdir(mode=0o755)
    (root / 'nested').mkdir(mode=0o755)
    file = root / 'nested/file'
    file.write_bytes(b'original')
    file.chmod(0o644)
    (root / 'link').symlink_to('nested/file')
    checks = []
    monkeypatch.setattr(runtime, 'ROOT_UID', os.getuid())
    monkeypatch.setattr(runtime, '_parents', lambda _path: None)
    monkeypatch.setattr(runtime, '_acl', lambda fd: checks.append(os.fstat(fd).st_ino))
    monkeypatch.setattr(runtime.platform, 'system', lambda: 'Darwin')
    return root, file, checks


def test_manifest_rechecks_acl_and_changed_file(protected_tree):
    root, file, checks = protected_tree
    cache = {}
    expected = {'nested': ['directory', stat.S_IMODE((root / 'nested').stat().st_mode)],
        'nested/file': ['file', 0o644, 8, hashlib.sha256(b'original').hexdigest()],
        'link': ['link', 'nested/file']}
    assert runtime.tree_manifest(root, cache) == expected
    count = len(checks)
    assert runtime.tree_manifest(root, cache) == expected
    assert len(checks) > count  # A hash cache never exempts current ACLs.
    file.write_bytes(b'changed')
    changed = runtime.tree_manifest(root, cache)
    assert changed['nested/file'] == ['file', 0o644, 7, hashlib.sha256(b'changed').hexdigest()]


def test_manifest_rejects_escaping_link(protected_tree, tmp_path):
    root, _file, _checks = protected_tree
    (root / 'link').unlink()
    outside = tmp_path / 'outside'
    outside.write_bytes(b'outside')
    (root / 'link').symlink_to(outside)
    with pytest.raises(AccessError, match='provider-runtime-link-unqualified'):
        runtime.tree_manifest(root, {})


def test_manifest_rejects_hardlinked_file(protected_tree):
    root, file, _checks = protected_tree
    os.link(file, root / 'second-link')
    with pytest.raises(AccessError, match='provider-runtime-custody-unqualified'):
        runtime.tree_manifest(root, {})
