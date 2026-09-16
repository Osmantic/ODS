"""Actual file/mode/link/hash checks with explicit non-root custody simulation."""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires POSIX host ownership, file locks, or Unix sockets; run under Linux/WSL")

import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
from pixel_access_bridge import AccessError, atomic_json
from pixel_provider import runtime_custody as r
from pixel_provider.managed_deployment import required_policy


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(r, 'ROOT_UID', os.getuid())
    def parents(path):
        if path.resolve() != path or not path.is_absolute(): raise AccessError('unsafe-provider-service-path')
        for parent in path.parents:
            if parent == tmp_path.parent: break
            st = parent.lstat()
            if not parent.is_dir() or parent.is_symlink() or st.st_uid != os.getuid() or st.st_mode & 0o022:
                raise AccessError('unsafe-provider-service-directory')
    monkeypatch.setattr(r, '_parents', parents)
    original = r._read
    monkeypatch.setattr(r, '_read', lambda path, uid, maximum: original(path, os.getuid(), maximum))
    runtime, source = tmp_path / 'runtime', tmp_path / 'source'
    tmp_path.chmod(0o700)
    runtime.mkdir(mode=0o700)
    source.mkdir(mode=0o700)
    runtime_entry = runtime / 'openclaw.mjs'
    runtime_entry.write_bytes(b'export const version="fixture";\n')
    runtime_entry.chmod(0o600)
    source_entry = source / 'helper.py'
    source_entry.write_bytes(b'# fixture only\n')
    source_entry.chmod(0o600)
    node, python, launcher = tmp_path / 'node', tmp_path / 'python', tmp_path / 'launcher'
    for path in (node, python): path.write_bytes(b'not executable fixture\n'); path.chmod(0o755)
    launcher.write_text('#!/bin/sh\nexec /usr/bin/env -u NODE_OPTIONS -u NODE_PATH '+str(node)+' '+str(runtime)+'/openclaw.mjs "$@"\n')
    launcher.chmod(0o755)
    manifest = {'runtime': r.tree_manifest(runtime), 'source': r.tree_manifest(source),
                'node': r._regular(node), 'launcher': r._regular(launcher), 'hostPython': r._regular(python)}
    mpath, descriptor = tmp_path / 'manifest.json', tmp_path / 'descriptor.json'
    atomic_json(mpath, manifest)
    doc = {'schemaVersion': 1, 'runtimeRoot': str(runtime), 'sourceRoot': str(source), 'node': str(node),
           'launcher': str(launcher), 'hostPython': str(python), 'manifest': str(mpath),
           'manifestSha256': hashlib.sha256(mpath.read_bytes()).hexdigest(), 'policy': required_policy()}
    atomic_json(descriptor, doc)
    monkeypatch.setattr(r, 'DESCRIPTOR', descriptor)
    return SimpleNamespace(root=tmp_path, runtime=runtime, source=source, node=node, launcher=launcher,
        manifest=mpath, descriptor=descriptor, document=doc, custody=r.RuntimeCustody(SimpleNamespace(binary=str(launcher))))


def test_second_qualification_reuses_hashes_but_checks_actual_file_set(artifact, monkeypatch):
    a = artifact
    original = os.open
    opened = []
    def counted(path, *args, **kwargs):
        if str(path) == str(a.runtime / 'openclaw.mjs'): opened.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(r.os, 'open', counted)
    first = a.custody.qualify()
    assert a.custody.qualify() == first and len(opened) == 1
    unexpected = a.runtime / 'unexpected.mjs'
    unexpected.write_text('new code')
    unexpected.chmod(0o600)
    with pytest.raises(AccessError, match='custody-unqualified'): a.custody.qualify()


def test_same_length_rewrite_with_restored_mtime_invalidates_cached_hash(artifact):
    a = artifact
    a.custody.qualify()
    path = a.runtime / 'openclaw.mjs'
    original = path.stat()
    path.write_bytes(b'x' * original.st_size)
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    with pytest.raises(AccessError, match='custody-unqualified'): a.custody.qualify()


@pytest.mark.parametrize('kind', ['mode', 'hardlink', 'external-link', 'deleted', 'fifo', 'replaced'])
def test_metadata_or_entry_drift_is_never_accepted_from_cache(artifact, kind):
    a = artifact
    a.custody.qualify()
    path = a.runtime / 'openclaw.mjs'
    if kind == 'mode': path.chmod(0o666)
    elif kind == 'hardlink': os.link(path, a.root / 'other-link')
    elif kind == 'external-link': (a.runtime / 'escape').symlink_to(a.root / 'node')
    elif kind == 'fifo': os.mkfifo(a.runtime / 'pipe', mode=0o600)
    elif kind == 'deleted': path.unlink()
    elif kind == 'replaced':
        path.unlink()
        path.write_bytes(b'wrong replacement')
        path.chmod(0o600)
    with pytest.raises(AccessError): a.custody.qualify()


def test_package_self_link_is_internal_but_transitive_escape_is_rejected(artifact):
    a = artifact
    (a.runtime / 'self').symlink_to('.', target_is_directory=True)
    assert r.tree_manifest(a.runtime)['self'] == ['link', '.']
    (a.runtime / 'outside').symlink_to('../node')
    (a.runtime / 'indirect').symlink_to('outside')
    with pytest.raises(AccessError, match='link-unqualified'): r.tree_manifest(a.runtime)


def test_modified_descriptor_or_manifest_never_inherits_cached_authority(artifact):
    a = artifact
    a.custody.qualify()
    body = json.loads(a.manifest.read_text())
    body['runtime']['openclaw.mjs'][-1] = '0' * 64
    atomic_json(a.manifest, body)
    with pytest.raises(AccessError, match='manifest-changed'): a.custody.qualify()


def test_another_operation_has_an_independent_hash_cache(artifact):
    a = artifact
    a.custody.qualify()
    other = r.RuntimeCustody(SimpleNamespace(binary=str(a.launcher)))
    assert not other._cache
    assert other.qualify() == a.custody.qualify()


def test_worker_probe_uses_qualified_source_and_private_receipt(artifact):
    a = artifact
    a.custody.bridge.owner = SimpleNamespace(pw_uid=os.getuid())
    directory = a.root / 'providers'
    directory.mkdir(mode=0o700)
    receipt = {'schemaVersion': 1, 'revision': 0, 'runtime': None}
    atomic_json(directory / 'advice-runtime.json', receipt)
    calls = []
    def worker(operation, **kwargs):
        calls.append((operation, kwargs))
        return {'ready': True}
    a.custody.bridge.worker = worker
    a.custody.require_worker(directory, a.custody.qualify())
    assert calls == [('provider-worker-status', {'provider_probe': {
        'python': a.document['hostPython'], 'launcher': str(a.source / 'bin/ods-pixel-route-lease'),
        'providerDirectory': str(directory), 'receipt': receipt}})]
    a.custody.bridge.worker = lambda *args, **kwargs: {'ready': False}
    with pytest.raises(AccessError, match='worker-runtime-not-ready'):
        a.custody.require_worker(directory, a.custody.qualify())


def test_missing_worker_receipt_does_not_start_owner_probe(artifact):
    a = artifact
    a.custody.bridge.owner = SimpleNamespace(pw_uid=os.getuid())
    a.custody.bridge.worker = lambda *args, **kwargs: pytest.fail('missing receipt executed probe')
    with pytest.raises(AccessError, match='worker-runtime-not-ready'):
        a.custody.require_worker(a.root, a.custody.qualify())
