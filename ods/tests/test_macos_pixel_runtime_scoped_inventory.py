"""Exercise descriptor-relative inventory; lease and ACLs are simulated here."""
import importlib.util
import os
from pathlib import Path
import sys

import pytest
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
from pixel_provider import runtime_custody  # noqa: E402
from pixel_connections import native_mcp  # noqa: E402
import pixel_macos_custody as mac  # noqa: E402
from test_connection_deployment_paths import metadata_tree_driver  # noqa: E402

spec = importlib.util.spec_from_file_location('scoped_bundle_test',
    ROOT / 'installers/macos/lib/pixel-runtime-bundle.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


def test_relative_path_character_checks_match_original_policy():
    for code in (*range(256), 0xD7FF, 0xD800, 0xDFFF, 0xE000, 0x1F600):
        value = 'safe/' + chr(code) + 'name'
        invalid = code < 32 or code in (47, 127) or 0xD800 <= code <= 0xDFFF
        if invalid:
            with pytest.raises(bundle.BundleError, match='invalid-bundle-path'):
                bundle._relative(value)
        else:
            assert bundle._relative(value) == value
    for value in ('', '/absolute', '.', '..', 'safe//file', 'safe/../file'):
        with pytest.raises(bundle.BundleError, match='invalid-bundle-path'):
            bundle._relative(value)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    root.mkdir()
    (root / 'nested').mkdir()
    (root / 'nested/target').write_bytes(b'qualified runtime bytes')
    (root / 'other').write_bytes(b'another file')
    (root / 'link').symlink_to('nested/target')
    cache, acl_calls = {}, []
    monkeypatch.setattr(bundle, '_operation_cache', lambda operation, *_args, **_kwargs: cache if operation else None)
    monkeypatch.setattr(runtime_custody, 'ROOT_UID', os.getuid())
    monkeypatch.setattr(runtime_custody, '_parents', lambda path: native_mcp._parents(path, os.getuid()))
    monkeypatch.setattr(runtime_custody, '_acl', lambda fd: acl_calls.append(os.fstat(fd).st_ino))
    driver = metadata_tree_driver(tmp_path, os.getuid())
    monkeypatch.setattr(mac, 'protected_tree_metadata', driver.check)
    return root, acl_calls, driver


def test_scoped_inventory_matches_ordinary_inventory_and_rechecks_acl(tree):
    root, acl_calls, _driver = tree
    ordinary = bundle.inventory(root, normalize_modes=False)
    assert bundle.inventory(root, normalize_modes=False, _operation=object()) == ordinary
    first = Counter(acl_calls)
    checked = [p.stat().st_ino for p in (root, root/'nested', root/'other', root/'nested/target')]
    assert all(first[inode] > 0 for inode in checked)
    assert bundle.inventory(root, normalize_modes=False, _operation=object()) == ordinary
    second = Counter(acl_calls)
    assert all(second[inode] > first[inode] for inode in checked)


def test_scoped_inventory_refuses_directory_moved_during_read(tree, monkeypatch):
    root, _acl_calls, _driver = tree
    original = bundle._file_descriptor
    def moved(fd, root_path, relative, *args, **kwargs):
        result = original(fd, root_path, relative, *args, **kwargs)
        if relative == 'nested/target':
            os.rename(root / 'nested', root / 'moved')
        return result
    monkeypatch.setattr(bundle, '_file_descriptor', moved)
    with pytest.raises((bundle.BundleError, OSError)):
        bundle.inventory(root, normalize_modes=False, _operation=object())


def test_scoped_inventory_refuses_root_replaced_during_read(tree, monkeypatch):
    root, _acl_calls, _driver = tree
    original = bundle._file_descriptor
    def replaced(fd, root_path, relative, *args, **kwargs):
        result = original(fd, root_path, relative, *args, **kwargs)
        if relative == 'nested/target':
            os.rename(root, root.with_name('moved-runtime'))
            root.mkdir()
        return result
    monkeypatch.setattr(bundle, '_file_descriptor', replaced)
    with pytest.raises((bundle.BundleError, OSError)):
        bundle.inventory(root, normalize_modes=False, _operation=object())


def test_scoped_inventory_refuses_replaced_file_between_stat_and_open(tree, monkeypatch):
    root, _acl_calls, driver = tree
    (root / 'nested/replacement').write_bytes(b'replacement')
    original = driver.module.os.open
    changed = []
    def opened(path, flags, *args, **kwargs):
        if path == 'target' and kwargs.get('dir_fd') is not None and not changed:
            parent = kwargs['dir_fd']
            os.replace('replacement', 'target', src_dir_fd=parent, dst_dir_fd=parent)
            changed.append(True)
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(driver.module.os, 'open', opened)
    with pytest.raises(bundle.BundleError, match='bundle-source-changed'):
        bundle.inventory(root, normalize_modes=False, _operation=object())
    assert changed


def test_scoped_inventory_refuses_hardlinked_files(tree):
    root, _acl_calls, _driver = tree
    os.link(root / 'other', root / 'second-link')
    with pytest.raises(bundle.BundleError, match='bundle-operation-unqualified'):
        bundle.inventory(root, _operation=object())


def test_scoped_inventory_refuses_escaping_symlink(tree, tmp_path):
    root, _acl_calls, _driver = tree
    outside = tmp_path / 'outside'
    outside.write_bytes(b'outside')
    (root / 'link').unlink()
    (root / 'link').symlink_to('../outside')
    with pytest.raises(bundle.BundleError, match='bundle-link-escapes'):
        bundle.inventory(root, _operation=object())
