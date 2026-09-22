"""Actual file/mode/link/hash checks with explicit non-root custody simulation."""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires POSIX host ownership, file locks, or Unix sockets; run under Linux/WSL")

import hashlib
import json
import os
import plistlib
import subprocess
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


@pytest.mark.parametrize('field,limit', [('descriptor', 16384), ('manifest', r.MAX_MANIFEST)])
def test_oversized_metadata_is_rejected_before_hashing(artifact, monkeypatch, field, limit):
    path = getattr(artifact, field)
    with path.open('r+b') as handle:
        handle.truncate(limit + 1)
    original = r.os.fdopen
    def guarded(fd, *args, **kwargs):
        assert os.fstat(fd).st_ino != path.stat().st_ino, 'oversized file opened for hashing'
        return original(fd, *args, **kwargs)
    monkeypatch.setattr(r.os, 'fdopen', guarded)
    with pytest.raises(AccessError, match='file-too-large'):
        artifact.custody.qualify()


def test_size_limit_applies_even_to_cached_hash(artifact):
    cache = {}
    size = artifact.node.stat().st_size
    expected = r._regular(artifact.node, cache, maximum=size)
    assert r._regular(artifact.node, cache, maximum=size) == expected
    with pytest.raises(AccessError, match='file-too-large'):
        r._regular(artifact.node, cache, maximum=size - 1)


def test_second_qualification_reuses_hashes_but_checks_actual_file_set(artifact, monkeypatch):
    a = artifact
    original = os.fdopen
    read = []
    inode = (a.runtime / 'openclaw.mjs').stat().st_ino
    def counted(fd, *args, **kwargs):
        if os.fstat(fd).st_ino == inode: read.append(fd)
        return original(fd, *args, **kwargs)
    monkeypatch.setattr(r.os, 'fdopen', counted)
    first = a.custody.qualify()
    assert a.custody.qualify() == first and len(read) == 1
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


@pytest.mark.parametrize('system,expected', [
    ('Darwin', '/private/etc/ods/pixel-provider-runtime.json'),
    ('Linux', '/etc/ods/pixel-provider-runtime.json'),
])
def test_fixed_descriptor_uses_canonical_host_path(monkeypatch, system, expected):
    monkeypatch.setattr(r.platform, 'system', lambda: system)
    monkeypatch.setattr(r, 'DESCRIPTOR', Path('/etc/ods/pixel-provider-runtime.json'))
    assert r._descriptor_path() == Path(expected)
    monkeypatch.setattr(r, 'DESCRIPTOR', Path('/operator/selected.json'))
    assert r._descriptor_path() == Path('/operator/selected.json')


@pytest.mark.skipif(sys.platform != 'darwin', reason='Real Darwin ACL API required')
@pytest.mark.parametrize('target', ['entry', 'directory', 'descriptor', 'manifest', 'node', 'launcher'])
def test_real_acl_revokes_cached_runtime_qualification(artifact, target):
    a = artifact
    nested = a.runtime / 'nested'
    nested.mkdir(mode=0o700)
    manifest = json.loads(a.manifest.read_text())
    manifest['runtime'] = r.tree_manifest(a.runtime)
    atomic_json(a.manifest, manifest)
    a.document['manifestSha256'] = hashlib.sha256(a.manifest.read_bytes()).hexdigest()
    atomic_json(a.descriptor, a.document)
    a.custody.qualify()
    path = {'entry': a.runtime / 'openclaw.mjs', 'directory': nested,
            'descriptor': a.descriptor, 'manifest': a.manifest,
            'node': a.node, 'launcher': a.launcher}[target]
    mode = path.stat().st_mode
    try:
        subprocess.run(['chmod', '+a', 'everyone allow write', str(path)], check=True, capture_output=True)
        assert path.stat().st_mode == mode
        with pytest.raises(AccessError, match='custody-unqualified'):
            a.custody.qualify()
    finally:
        subprocess.run(['chmod', '-N', str(path)], check=True, capture_output=True)
    a.custody.qualify()


def test_tree_scan_error_cannot_be_treated_as_an_empty_subtree(artifact, monkeypatch):
    def failed_walk(_root, *, followlinks, onerror):
        assert followlinks is False
        onerror(PermissionError('unreadable runtime directory'))
        return iter(())
    monkeypatch.setattr(r.os, 'walk', failed_walk)
    with pytest.raises(PermissionError, match='unreadable runtime'):
        artifact.custody.qualify()


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


def test_macos_runtime_process_is_bound_to_launchd_plist_and_libproc_identity(artifact, monkeypatch):
    import grp
    from pixel_settings import coordinator as settings
    plist = artifact.root / 'gateway.plist'
    document = {'Label': 'com.ods.fixture',
                'ProgramArguments': ['/usr/bin/env', '-i', '/usr/bin/sandbox-exec', '-f',
                                     '/etc/ods/pixel-gateway.sb', str(artifact.launcher), 'gateway', 'run'],
                'UserName': 'fixture', 'GroupName': grp.getgrgid(os.getgid()).gr_name}
    plist.write_bytes(plistlib.dumps(document, sort_keys=True))
    plist.chmod(0o600)
    identity = {'pid': 123, 'started': 1700000000000000,
                'boot': '11111111-2222-3333-4444-555555555555'}
    artifact.custody.bridge.gateway_process = {
        'uid': os.getuid(), 'gid': os.getgid(), 'executable': str(artifact.node)}
    artifact.custody.bridge.gateway_service = SimpleNamespace(plist=plist)
    monkeypatch.setattr(settings, '_identity', lambda _bridge: identity)
    monkeypatch.setattr(r.platform, 'system', lambda: 'Darwin')
    if sys.platform != 'darwin':
        # This fixture simulates Darwin process APIs on Linux, not Darwin ACLs.
        monkeypatch.setattr('pixel_macos_custody._require_no_acl', lambda fd: os.fstat(fd))
    monkeypatch.setattr('pixel_macos_custody.protected_bytes', lambda _path: plist.read_bytes())
    monkeypatch.setattr('pixel_macos_process.process_identity',
                        lambda pid, **_spec: (pid, 1700000000, 0, os.getuid(), os.getgid(),
                                              os.getuid(), os.getgid(), os.getuid(), os.getgid(),
                                              str(artifact.node)))
    artifact.custody.verify_process()


def test_macos_runtime_process_rejects_launchd_command_drift(artifact, monkeypatch):
    from pixel_settings import coordinator as settings
    plist = artifact.root / 'gateway.plist'
    document = {'Label': 'com.ods.fixture',
                'ProgramArguments': ['/usr/bin/env', '-i', '/usr/bin/sandbox-exec', '-f',
                                     '/etc/ods/pixel-gateway.sb', '/tmp/unapproved', 'gateway', 'run'],
                'UserName': 'fixture', 'GroupName': 'staff'}
    plist.write_bytes(plistlib.dumps(document, sort_keys=True))
    identity = {'pid': 123, 'started': 1700000000000000,
                'boot': '11111111-2222-3333-4444-555555555555'}
    artifact.custody.bridge.gateway_process = {
        'uid': os.getuid(), 'gid': os.getgid(), 'executable': str(artifact.node)}
    artifact.custody.bridge.gateway_service = SimpleNamespace(plist=plist)
    monkeypatch.setattr(settings, '_identity', lambda _bridge: identity)
    monkeypatch.setattr(r.platform, 'system', lambda: 'Darwin')
    if sys.platform != 'darwin':
        monkeypatch.setattr('pixel_macos_custody._require_no_acl', lambda fd: os.fstat(fd))
    monkeypatch.setattr('pixel_macos_custody.protected_bytes', lambda _path: plist.read_bytes())
    monkeypatch.setattr('pixel_macos_process.process_identity',
                        lambda pid, **_spec: (pid, 1700000000, 0, os.getuid(), os.getgid(),
                                              os.getuid(), os.getgid(), os.getuid(), os.getgid(),
                                              str(artifact.node)))
    with pytest.raises(AccessError, match='provider-runtime-process-unqualified'):
        artifact.custody.verify_process()
