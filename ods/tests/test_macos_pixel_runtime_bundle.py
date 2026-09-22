import importlib.util
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('pixel_runtime_bundle',
    ROOT / 'installers/macos/lib/pixel-runtime-bundle.py')
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)
sys.path.insert(0, str(ROOT / 'bin'))
import pixel_macos_custody as custody


@pytest.fixture
def artifacts(tmp_path):
    tmp_path = tmp_path.resolve()
    node = tmp_path / 'node'
    node.write_bytes(b'fixture executable, never executed')
    node.chmod(0o755)
    runtime = tmp_path / 'source'
    runtime.mkdir()
    (runtime / 'package.json').write_text(json.dumps({'name': 'openclaw', 'version': '2026.6.33'}))
    (runtime / 'openclaw.mjs').write_text('fixture entrypoint')
    (runtime / 'node_modules').mkdir()
    (runtime / 'node_modules/dependency.js').write_text('fixture dependency')
    plugin = tmp_path / 'plugin'
    plugin.mkdir()
    (plugin / 'index.mjs').write_text('fixture plugin')
    return dict(node=node, runtime=runtime, destination=tmp_path / 'bundle', plugins=[plugin])


def test_service_only_change_has_distinct_deterministic_deployment_identity(artifacts):
    first = bundle.build(**artifacts, services_digest='a' * 64)
    bundle.verify_service_binding(artifacts['destination'], 'a' * 64)
    second_args = dict(artifacts, destination=artifacts['destination'].with_name('second'))
    assert bundle.build(**second_args, services_digest='a' * 64) == first
    third_args = dict(artifacts, destination=artifacts['destination'].with_name('third'))
    third = bundle.build(**third_args, services_digest='b' * 64)
    assert third != first
    bundle.verify(third_args['destination'], expected_digest=third)
    with pytest.raises(bundle.BundleError, match='binding-mismatch'):
        bundle.verify_service_binding(third_args['destination'], 'a' * 64)


def test_exec_wrapper_is_content_bound_and_versions_are_preserved(artifacts):
    wrapper = artifacts['node'].with_name('wrapper.sh')
    wrapper.write_bytes(b'#!/bin/sh\nprintf old')
    wrapper.chmod(0o644)
    old = bundle.build(**artifacts, exec_wrapper=wrapper)
    assert (artifacts['destination'] / 'cancellable-exec.sh').read_bytes() == wrapper.read_bytes()
    assert (artifacts['destination'] / 'cancellable-exec.sh').stat().st_mode & 0o777 == 0o755
    same = dict(artifacts, destination=artifacts['destination'].with_name('same'))
    wrapper.chmod(0o755)
    assert bundle.build(**same, exec_wrapper=wrapper) == old
    wrapper.write_bytes(b'#!/bin/sh\nprintf new')
    newer = dict(artifacts, destination=artifacts['destination'].with_name('newer'))
    assert bundle.build(**newer, exec_wrapper=wrapper) != old
    assert (artifacts['destination'] / 'cancellable-exec.sh').read_bytes().endswith(b'old')
    bundle.verify(artifacts['destination'], expected_digest=old)
    (artifacts['destination'] / 'cancellable-exec.sh').write_bytes(b'changed')
    with pytest.raises(bundle.BundleError):
        bundle.verify(artifacts['destination'], expected_digest=old)


@pytest.mark.parametrize('fault', ['privileged-mode', 'link'])
def test_exec_wrapper_requires_regular_unprivileged_source_before_staging(artifacts, fault):
    wrapper = artifacts['node'].with_name('wrapper.sh')
    if fault == 'link':
        wrapper.symlink_to(artifacts['node'])
    else:
        wrapper.write_bytes(b'#!/bin/sh\nexit 0')
        wrapper.chmod(0o4755)
    with pytest.raises((bundle.BundleError, OSError)):
        bundle.build(**artifacts, exec_wrapper=wrapper)
    assert not artifacts['destination'].exists()


@pytest.mark.parametrize('value', ['', 'a' * 63, 'G' * 64, [], 1])
def test_invalid_service_binding_is_rejected_before_staging(artifacts, value):
    with pytest.raises(bundle.BundleError, match='invalid-service'):
        bundle.build(**artifacts, services_digest=value)
    assert not artifacts['destination'].exists()


def test_bundle_is_deterministic_and_contains_dependencies_and_plugins(artifacts):
    first = bundle.build(**artifacts)
    bundle.verify_service_binding(artifacts['destination'], 'a' * 64)
    other = dict(artifacts, destination=artifacts['destination'].with_name('second'))
    second = bundle.build(**other)
    assert first == second
    value, checksum = bundle.verify(artifacts['destination'], expected_digest=first)
    assert checksum == first
    assert 'runtime/node_modules/dependency.js' in value['entries']
    assert 'plugins/0/index.mjs' in value['entries']
    assert str(artifacts['runtime']) not in (artifacts['destination'] / bundle.MANIFEST).read_text()


@pytest.mark.parametrize('mutation', [None, 'extra-code', 'node', 'mode'])
def test_workspace_upgrade_accepts_only_exact_inheritance_fix(artifacts, mutation):
    entry = artifacts['plugins'][0] / 'index.js'
    original = '      const workspaceRoot = api.config?.agents?.list?.find(agent => agent.id === AGENT_ID)?.workspace;'
    entry.write_text(original)
    old_digest = bundle.build(**artifacts)
    entry.write_text(original[:-1] + '\n        ?? api.config?.agents?.defaults?.workspace;')
    if mutation == 'extra-code':
        entry.write_text(entry.read_text() + '\nthrow new Error();')
    if mutation == 'node':
        artifacts['node'].write_bytes(b'other executable')
    if mutation == 'mode':
        entry.chmod(0o755)
    candidate = artifacts['destination'].with_name('workspace-candidate')
    new_digest = bundle.build(**dict(artifacts, destination=candidate))
    def qualify():
        return bundle.qualify_workspace_root_upgrade(artifacts['destination'], candidate,
            current_digest=old_digest, candidate_digest=new_digest)
    if mutation:
        with pytest.raises(bundle.BundleError, match='workspace-upgrade-'):
            qualify()
    else:
        assert qualify()['changedEntries'] == ['plugins/0/index.js']


def test_stream_progress_patch_rejects_unknown_runtime_without_publication(artifacts):
    path = artifacts['runtime'] / bundle.STREAM_PROGRESS_FILE
    path.parent.mkdir()
    path.write_text('unreviewed runtime')
    with pytest.raises(bundle.BundleError, match='stream-progress-unqualified-source'):
        bundle.build(**artifacts, stream_progress_fix=True)
    assert not artifacts['destination'].exists()
    assert path.read_text() == 'unreviewed runtime'


def test_stream_progress_patch_precedes_buffers_and_is_manifest_bound(artifacts, monkeypatch):
    original = ('\t\t\tconst innerStreamFn = activeSession.agent.streamFn;\n'
                '\t\t\tactiveSession.agent.streamFn = buffer(innerStreamFn);\n'
                '\t\t\tconst configuredRunTimeoutMs = resolveAgentTimeoutMs({ cfg: params.config });\n'
                '\t\t\tactiveSession.agent.streamFn = observe(activeSession.agent.streamFn);\n'
                '\t\t\ttry {\n\t\t\t\tif (isRawModelRun) {\n')
    monkeypatch.setattr(bundle, 'STREAM_PROGRESS_SOURCE_SHA256', hashlib.sha256(original.encode()).hexdigest())
    path = artifacts['runtime'] / bundle.STREAM_PROGRESS_FILE
    path.parent.mkdir()
    path.write_text(original)
    checksum = bundle.build(**artifacts, stream_progress_fix=True)
    manifest, _ = bundle.verify(artifacts['destination'], expected_digest=checksum)
    patched = (artifacts['destination'] / 'runtime' / bundle.STREAM_PROGRESS_FILE).read_text()
    assert patched.index('observe(') < patched.index('const innerStreamFn') < patched.index('buffer(')
    assert path.read_text() == original
    receipt = json.loads((artifacts['destination'] / 'ods-runtime-patches.json').read_text())[0]
    assert receipt['patchedSha256'] == hashlib.sha256(patched.encode()).hexdigest()
    assert 'ods-runtime-patches.json' in manifest['entries']
    (artifacts['destination'] / 'ods-runtime-patches.json').write_text('[]')
    with pytest.raises(bundle.BundleError, match='content-changed'):
        bundle.verify(artifacts['destination'], expected_digest=checksum)


@pytest.mark.parametrize('mutation', [None, 'node', 'plugin', 'injected-code', 'receipt'])
def test_upgrade_checks_actual_patch_not_only_candidate_manifest(artifacts, monkeypatch, mutation):
    original = ('\t\t\tconst innerStreamFn = activeSession.agent.streamFn;\n'
                '\t\t\tconst configuredRunTimeoutMs = resolveAgentTimeoutMs({ cfg: params.config });\n'
                '\t\t\ttry {\n\t\t\t\tif (isRawModelRun) {\n')
    monkeypatch.setattr(bundle, 'STREAM_PROGRESS_SOURCE_SHA256', hashlib.sha256(original.encode()).hexdigest())
    source = artifacts['runtime'] / bundle.STREAM_PROGRESS_FILE
    source.parent.mkdir()
    source.write_text(original)
    old_digest = bundle.build(**artifacts)
    candidate = artifacts['destination'].with_name('upgrade')
    new_digest = bundle.build(**dict(artifacts, destination=candidate), stream_progress_fix=True)
    if mutation:
        paths = {'node': 'node', 'plugin': 'plugins/0/index.mjs',
                 'injected-code': 'runtime/' + bundle.STREAM_PROGRESS_FILE,
                 'receipt': 'ods-runtime-patches.json'}
        path = candidate / paths[mutation]
        path.write_bytes(path.read_bytes() + b'\nextra')
        manifest = json.loads((candidate / bundle.MANIFEST).read_bytes())
        manifest['entries'] = bundle.inventory(candidate, exclude_manifest=True)
        body = bundle._encode(manifest)
        (candidate / bundle.MANIFEST).write_bytes(body)
        new_digest = hashlib.sha256(body).hexdigest()
        # A freshly computed, internally valid manifest is insufficient.
        bundle.verify(candidate, expected_digest=new_digest)
        with pytest.raises(bundle.BundleError, match='runtime-upgrade-'):
            bundle.qualify_stream_progress_upgrade(artifacts['destination'], candidate,
                current_digest=old_digest, candidate_digest=new_digest)
    else:
        result = bundle.qualify_stream_progress_upgrade(artifacts['destination'], candidate,
            current_digest=old_digest, candidate_digest=new_digest)
        assert result['candidateDigest'] == new_digest
        assert len(result['changedEntries']) == 2


@pytest.mark.parametrize('change', ['content', 'added', 'missing', 'mode', 'dependency', 'plugin'])
def test_any_entry_drift_is_rejected(artifacts, change):
    checksum = bundle.build(**artifacts)
    root = artifacts['destination']
    if change == 'content': (root / 'node').write_bytes(b'changed')
    elif change == 'added': (root / 'unexpected.js').write_text('new')
    elif change == 'missing': (root / 'runtime/openclaw.mjs').unlink()
    elif change == 'mode': (root / 'runtime/openclaw.mjs').chmod(0o666)
    elif change == 'dependency': (root / 'runtime/node_modules/dependency.js').write_text('changed')
    else: (root / 'plugins/0/index.mjs').write_text('changed')
    with pytest.raises(bundle.BundleError, match='content-changed'):
        bundle.verify(root, expected_digest=checksum)


def test_source_absolute_or_escaping_link_is_rejected(artifacts):
    link = artifacts['runtime'] / 'outside'
    for target in (str(artifacts['node']), '../node'):
        link.symlink_to(target)
        with pytest.raises(bundle.BundleError, match='link-escapes'):
            bundle.build(**artifacts)
        link.unlink()
    assert not artifacts['destination'].exists()


def test_internal_relative_links_survive_copy(artifacts):
    (artifacts['runtime'] / 'node_modules/alias.js').symlink_to('dependency.js')
    checksum = bundle.build(**artifacts)
    root = artifacts['destination']
    assert (root / 'runtime/node_modules/alias.js').is_symlink()
    bundle.verify(root, expected_digest=checksum)


def test_symlink_loop_is_rejected(artifacts):
    (artifacts['runtime'] / 'loop').symlink_to('loop')
    with pytest.raises(bundle.BundleError, match='link-unavailable'):
        bundle.build(**artifacts)


@pytest.mark.parametrize('kind', ['fifo', 'setuid'])
def test_special_files_and_privileged_modes_are_rejected(artifacts, kind):
    path = artifacts['runtime'] / 'special'
    if kind == 'fifo': os.mkfifo(path)
    else:
        path.write_text('fixture')
        path.chmod(0o4755)
    with pytest.raises(bundle.BundleError, match='unsupported-bundle-file'):
        bundle.build(**artifacts)


def test_existing_destination_never_overwritten(artifacts):
    artifacts['destination'].mkdir()
    keep = artifacts['destination'] / 'user-file'
    keep.write_text('preserve')
    with pytest.raises(bundle.BundleError, match='new-absolute'):
        bundle.build(**artifacts)
    assert keep.read_text() == 'preserve'


def test_changed_source_during_copy_leaves_no_completed_bundle(artifacts, monkeypatch):
    original = bundle._copy_tree
    def mutate(source, target, entries):
        (source / 'openclaw.mjs').write_text('changed after inventory')
        return original(source, target, entries)
    monkeypatch.setattr(bundle, '_copy_tree', mutate)
    with pytest.raises(bundle.BundleError, match='source-changed'):
        bundle.build(**artifacts)
    assert not artifacts['destination'].exists()
    assert not list(artifacts['destination'].parent.glob('.ods-pixel-bundle-*'))


def test_rejects_unknown_runtime_version(artifacts):
    with pytest.raises(bundle.BundleError, match='unqualified-openclaw-version'):
        bundle.build(**artifacts, expected_version='other')


def test_private_umask_does_not_change_bundle_modes(artifacts):
    previous = os.umask(0o077)
    try:
        bundle.build(**artifacts)
    finally:
        os.umask(previous)
    root = artifacts['destination']
    assert stat.S_IMODE((root / 'runtime/node_modules').stat().st_mode) == 0o755
    bundle.verify(root)


def test_manifest_must_match_previously_selected_digest(artifacts):
    bundle.build(**artifacts)
    with pytest.raises(bundle.BundleError, match='manifest-changed'):
        bundle.verify(artifacts['destination'], expected_digest='0' * 64)


def test_manifest_duplicate_keys_are_not_accepted(artifacts):
    bundle.build(**artifacts)
    path = artifacts['destination'] / bundle.MANIFEST
    raw = path.read_bytes().replace(b'{', b'{"schemaVersion":1,', 1)
    path.write_bytes(raw)
    with pytest.raises(bundle.BundleError, match='invalid-bundle-manifest'):
        bundle.verify(artifacts['destination'])


@pytest.fixture
def publisher(artifacts, monkeypatch):
    digest = bundle.build(**artifacts)
    # Real filesystem/copy/locking; simulated root custody for unprivileged
    # macOS testing. No test of this fixture establishes a root deployment.
    monkeypatch.setattr(bundle.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(custody, '_verify_fd', lambda fd, **_: os.fstat(fd))
    return artifacts['destination'], digest, artifacts['destination'].parent / 'protected'


def test_publish_verifies_and_reuses_exact_version(publisher):
    source, digest, parent = publisher
    target = bundle.publish(source, expected_digest=digest, install_root=parent)
    assert target == parent / digest
    assert target != source
    assert bundle.verify(target, expected_digest=digest)[1] == digest
    inode = target.stat().st_ino
    assert bundle.publish(source, expected_digest=digest, install_root=parent) == target
    assert target.stat().st_ino == inode
    assert not list(parent.glob('.publishing-*'))


def test_publish_never_repairs_existing_drift(publisher):
    source, digest, parent = publisher
    target = bundle.publish(source, expected_digest=digest, install_root=parent)
    (target / 'node').write_bytes(b'changed')
    with pytest.raises(bundle.BundleError, match='content-changed'):
        bundle.publish(source, expected_digest=digest, install_root=parent)
    assert (target / 'node').read_bytes() == b'changed'


def test_publish_rejects_source_drift_before_creating_destination(publisher):
    source, digest, parent = publisher
    (source / 'node').write_bytes(b'changed')
    with pytest.raises(bundle.BundleError, match='content-changed'):
        bundle.publish(source, expected_digest=digest, install_root=parent)
    assert not parent.exists()


def test_publish_requires_selected_digest(publisher):
    source, _, parent = publisher
    for digest in (None, '', 'z' * 64, '0' * 64):
        with pytest.raises(bundle.BundleError, match='digest-required|manifest-changed'):
            bundle.publish(source, expected_digest=digest, install_root=parent)
    assert not parent.exists()


def test_publish_refuses_symlinked_parent_or_version(publisher):
    source, digest, parent = publisher
    parent.symlink_to(source, target_is_directory=True)
    with pytest.raises(OSError):
        bundle.publish(source, expected_digest=digest, install_root=parent)
    parent.unlink()
    parent.mkdir()
    (parent / digest).symlink_to(source, target_is_directory=True)
    with pytest.raises(OSError):
        bundle.publish(source, expected_digest=digest, install_root=parent)


def test_publish_failure_cleans_only_its_stage(publisher, monkeypatch):
    source, digest, parent = publisher
    parent.mkdir()
    unrelated = parent / 'previous-version'
    unrelated.mkdir()
    (unrelated / 'keep').write_text('untouched')
    def fail(*_):
        raise OSError('simulated write failure')
    monkeypatch.setattr(bundle, '_copy_tree', fail)
    with pytest.raises(OSError, match='write failure'):
        bundle.publish(source, expected_digest=digest, install_root=parent)
    assert not (parent / digest).exists()
    assert (unrelated / 'keep').read_text() == 'untouched'
    assert not list(parent.glob('.publishing-*'))


def test_publish_requires_root(artifacts, monkeypatch):
    monkeypatch.setattr(bundle.os, 'geteuid', lambda: 501)
    with pytest.raises(bundle.BundleError, match='root-bundle-publisher-required'):
        bundle.publish(artifacts['destination'], expected_digest='0' * 64)


def test_publish_checks_custody_before_publishing(publisher, monkeypatch):
    source, digest, parent = publisher
    def fail(_):
        raise custody.CustodyError('simulated unsafe ACL')
    monkeypatch.setattr(custody, 'protected_tree_metadata', fail)
    with pytest.raises(custody.CustodyError, match='unsafe ACL'):
        bundle.publish(source, expected_digest=digest, install_root=parent)
    assert not (parent / digest).exists()
    assert not list(parent.glob('.publishing-*'))


def test_publish_rechecks_content_after_copy(publisher, monkeypatch):
    source, digest, parent = publisher
    original = bundle._copy_tree
    def changed(source, target, entries):
        original(source, target, entries)
        (target / 'node').write_bytes(b'post-copy mutation')
    monkeypatch.setattr(bundle, '_copy_tree', changed)
    with pytest.raises(bundle.BundleError, match='content-changed'):
        bundle.publish(source, expected_digest=digest, install_root=parent)
    assert not (parent / digest).exists()


@pytest.mark.skipif(os.geteuid() != 0 or sys.platform != 'linux', reason='isolated Linux root publication fixture')
def test_root_publication_checks_real_ownership_and_internal_links(artifacts, monkeypatch):
    # Linux has no Darwin ACL API. Only that API is replaced; real uid/mode,
    # descriptor walking, copy, links, version reuse and content checks remain.
    monkeypatch.setattr(custody, '_require_no_acl', lambda _: None)
    (artifacts['runtime'] / 'node_modules/alias.js').symlink_to('dependency.js')
    digest = bundle.build(**artifacts)
    with tempfile.TemporaryDirectory(dir='/root', prefix='pixel-publish-') as directory:
        parent = Path(directory) / 'versions'
        target = bundle.publish(artifacts['destination'], expected_digest=digest, install_root=parent)
        assert target.stat().st_uid == 0
        assert (target / 'runtime/node_modules/alias.js').read_text() == 'fixture dependency'
        assert bundle.publish(artifacts['destination'], expected_digest=digest, install_root=parent) == target
        (target / 'node').chmod(0o777)
        with pytest.raises(custody.CustodyError, match='root-custody-required'):
            bundle.publish(artifacts['destination'], expected_digest=digest, install_root=parent)
        assert stat.S_IMODE((target / 'node').stat().st_mode) == 0o777
