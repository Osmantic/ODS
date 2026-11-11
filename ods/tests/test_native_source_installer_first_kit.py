"""Real canonical candidate, actual absent Root Journal4 and first inert kit.

Only the existing Linux/Apple metadata driver is simulated. Source custody,
both immutable runtime inventories, Root context/journal FDs and publication
renames are production code. No service, account, model or provider executes.
"""
# ruff: noqa: E402,F401,F811
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import plistlib
import shutil
import sys
import time
from types import SimpleNamespace

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs metadata harness, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_executor import qualification_operation
from test_native_source_custody import native
from test_connection_deployment_paths import deployment
from test_native_source_runtime_publication import built, source, package, runtime_custody, bundle, services
from test_native_source_publication import material, helper
from test_native_source_installer_rebind import source_before
from test_native_source_installer_withdrawal import digest, files
from pixel_connections import custody as c, coordinator, state
from pixel_connections import native_executor as ex
import pixel_macos_custody as mac
import pixel_access_bridge as bridge


@pytest.fixture
def first_target(material, built, monkeypatch, request):
    n = material
    code_only = getattr(request, 'param', None) in ('code-only', 'code-only-history')
    n.built = built
    # Same explicit OS-observation driver as the executor harness. Deadline
    # identity/ceiling checks themselves remain the production implementation.
    raw_clock = lambda: time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
    monkeypatch.setattr(bridge, '_native_source_clock_ns', raw_clock)
    monkeypatch.setattr(c, '_native_clock_ns', raw_clock)
    monkeypatch.setattr(c, '_native_boot_uuid', lambda: '01234567-89AB-4DEF-8123-0123456789AB')
    # Only the relocated protected-copy import uses the existing metadata
    # driver. Full immutable bundle/component qualification remains actual;
    # no context/absence/publication authority function is substituted.
    def publisher(selection=None, *, with_material=False, _current_bridge=None):
        if _current_bridge is not None:
            selection = c.native_current_source_selection(_current_bridge)
            return selection, helper, helper._material(selection)
        qualified = helper._material(selection)
        return (helper, qualified) if with_material else helper
    monkeypatch.setattr(ex, '_publisher', publisher)
    scope = n.f.bridge.bounded(900 if code_only else 300)
    scope.__enter__()
    request.addfinalizer(lambda: scope.__exit__(None, None, None))
    if code_only:
        onboarding = json.loads(n.f.answers.read_bytes())
        onboarding.update(state.connection_answers(None, google_custody=False))
        onboarding['modelChoice'] = 'preserved-owner-model'
        n.f.answers.write_bytes(c._native_encoded(onboarding))
    sidefiles, old_descriptor = source_before(n, monkeypatch)
    # The fixed file-family proof opens each Root image by its parent FD.
    # Extend only the OS ownership/directory observation driver to those exact
    # declared QA roots; owner config/sidefile FDs keep their real owner.
    root_images = {Path(path) for path in c.native_installer_source_family()}
    root_images.update((Path(old_descriptor['launcher']), n.f.plist))
    root_parents = {path.parent for path in root_images} | {n.metadata, n.kit, n.world}
    old_fstat = os.fstat
    root_reading = []
    actual_root_file = c._native_root_file
    def root_file(directory, name, **kwargs):
        root_reading.append(True)
        try: return actual_root_file(directory, name, **kwargs)
        finally: root_reading.pop()
    monkeypatch.setattr(c, '_native_root_file', root_file)
    def root_fstat(fd):
        info = old_fstat(fd)
        try: path = Path(os.readlink('/proc/self/fd/' + str(fd)))
        except OSError: return info
        # RuntimeCustody's immutable-tree driver already models ROOT_UID with
        # the Linux test user; only the fixed native Root-file FD primitive
        # needs the Apple UID0 observation. Its entire actual body still runs.
        if not root_reading or path not in root_images: return info
        fields = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
        fields.update(st_uid=0, st_gid=0)
        return SimpleNamespace(**fields)
    monkeypatch.setattr(os, 'fstat', root_fstat)
    @contextmanager
    def directory(path, **kwargs):
        assert not kwargs and Path(path) in root_parents
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: yield fd
        finally: os.close(fd)
    monkeypatch.setattr(mac, 'protected_directory', directory)
    # Remove only the fixture's three inert bootstrap kit images and simulated
    # identity, before any private Source state or installer authority exists.
    assert set(p.name for p in n.kit.iterdir()) == {'source-kit.json', 'broker.py', 'source-broker.env'}
    for path in n.kit.iterdir(): path.unlink()
    n.kit.rmdir()
    (n.metadata / 'source-identity.json').unlink()
    n.records['Users'] = n.records['Groups'] = None
    if code_only:
        # Real preconnection publication with no account/private data. Keep
        # the admitted owner sidefiles and their original Root receipt intact.
        helper.publish_kit(n.selected)
    else:
        (n.metadata / 'source-sidefiles.json').unlink()
        n.f.env.unlink()
        n.f.answers.unlink()
    monkeypatch.setattr(n.f.bridge, 'state', n.metadata, raising=False)
    monkeypatch.setattr(n.f.bridge.owner, 'pw_dir', str(n.f.bridge.home), raising=False)
    captured = None
    if code_only:
        monkeypatch.setattr(c, 'NATIVE_CONNECTION_RECORD', n.metadata / 'portal-connections.json')
        live = coordinator.gateway_target(None, custody_held=False)
        answers = state.connection_answers(None, google_custody=False)
        def worker(operation, **_kwargs):
            assert operation == 'connection-owner-status'
            return dict(pending=False, configSha256=digest(n.f.config.read_bytes()),
                envSha256=digest(n.f.env.read_bytes()), live=live, answers=answers, completion=None)
        monkeypatch.setattr(n.f.bridge, 'worker', worker)
        monkeypatch.setattr(n.f.bridge, 'inspect', lambda: dict(runtime_verified=True, pending=False,
            configured_mode='sandboxed', effective_mode='sandboxed',
            _config=dict(config_sha256=digest(n.f.config.read_bytes())), revision='e' * 64))
        captured = c.native_installer_sidefiles_snapshot(n.selected)
        sidefiles = captured['sidefiles']
    runtime = n.world.parent / 'first-kit-candidate-runtime'
    shutil.copytree(n.f.root / 'runtime', runtime)
    with (runtime / 'openclaw.mjs').open('ab') as stream:
        stream.write(b'// separately canonical first-kit candidate; never executed\n')
    service_manifest = services(n.built.source)
    service_manifest['pixelSourceRef'] = n.built.package[1]
    destination = n.f.root.parent / 'first-kit-candidate-preparing'
    checksum = bundle.build(node=n.f.root / 'node', runtime=runtime, destination=destination,
        plugins=[n.f.root / name for name in json.loads((n.f.root / 'bundle.json').read_bytes())['plugins']],
        ods_source=n.built.source.ods, pixel_source_ref=n.built.package[1], ods_plugin_indices=[0],
        service_manifest=service_manifest, services_digest=digest(bundle._encode(service_manifest)),
        source_component={name: (n.f.root / 'native-source' / name).read_bytes() for name in helper.FILES})
    target = n.f.root.parent / checksum
    destination.rename(target)
    launcher = Path(old_descriptor['launcher'])
    launcher_body = ('#!/bin/sh\nexec /usr/bin/env -u NODE_OPTIONS -u NODE_PATH ' + str(target / 'node')
        + ' ' + str(target / 'runtime/openclaw.mjs') + ' "$@"\n').encode()
    manifest = {'runtime': runtime_custody.tree_manifest(target / 'runtime'),
        'source': runtime_custody.tree_manifest(target / 'ods-source'), 'node': runtime_custody._regular(target / 'node'),
        'launcher': ['file', 0o755, len(launcher_body), digest(launcher_body)],
        'hostPython': runtime_custody._regular(Path(old_descriptor['hostPython']))}
    manifest_body = helper.encoded(manifest)
    descriptor = dict(old_descriptor, runtimeRoot=str(target / 'runtime'), sourceRoot=str(target / 'ods-source'),
        node=str(target / 'node'), manifestSha256=digest(manifest_body))
    config = n.f.config.with_name('openclaw-' + checksum + '.json')
    config.write_bytes(n.f.config.read_bytes())
    config.chmod(0o600)
    document = plistlib.loads(n.f.plist.read_bytes())
    document['ProgramArguments'] = [arg.replace(str(n.f.root), str(target)).replace(str(n.f.config), str(config))
        for arg in document['ProgramArguments']]
    rows = []
    for path in sorted(c.native_installer_source_family()):
        value = Path(path)
        if value == c.NATIVE_RUNTIME_DESCRIPTOR: after = helper.encoded(descriptor)
        elif value == c.NATIVE_RUNTIME_MANIFEST: after = manifest_body
        elif value == n.metadata / 'source-runtime-installation.json':
            after = helper.encoded({'schemaVersion': 1, 'kind': 'ods-native-source-runtime-publication',
                'bundleSha256': checksum, 'sourceRef': n.built.package[1]})
        else: after = (target / 'ods-source/bin' / value.relative_to(c.NATIVE_PROGRAM)).read_bytes()
        before = value.read_bytes() if value.is_file() else None
        rows.append(dict(path=path, mode=0o600 if value.name == 'source-runtime-installation.json' else 0o644,
            gid=0, before=None if before is None else base64.b64encode(before).decode(),
            beforeSha256=None if before is None else digest(before), after=base64.b64encode(after).decode(), afterSha256=digest(after)))
    family = digest(json.dumps([[r['path'], r['mode'], r['gid'], r['afterSha256']] for r in rows],
        separators=(',', ':'), ensure_ascii=True).encode())
    for path, after, mode in ((launcher, launcher_body, 0o755), (n.f.plist, plistlib.dumps(document), 0o644)):
        before = path.read_bytes()
        rows.append(dict(path=str(path), mode=mode, gid=0, before=base64.b64encode(before).decode(), beforeSha256=digest(before),
            after=base64.b64encode(after).decode(), afterSha256=digest(after)))
    owner = n.f.bridge.owner
    context = dict(schemaVersion=2, owner=dict(uid=owner.pw_uid, gid=owner.pw_gid, name=owner.pw_name, home=str(n.f.bridge.home)),
        runtime_bundle=dict(digest=checksum, connection_sidefiles=sidefiles, config_path=str(config)),
        upgrade_qualification=dict(currentDigest=n.f.root.name, candidateDigest=checksum),
        source_lifecycle=dict(schemaVersion=1, kind='ods-native-source-installer-intent', mode='absent', nonce='1' * 32,
            currentDigest=n.f.root.name, candidateDigest=checksum, ownerUid=owner.pw_uid, ownerGid=owner.pw_gid, familySha256=family))
    if captured is not None: context['runtime_bundle']['source_sidefiles_before'] = captured['snapshot']
    raw = c._native_encoded(context)
    n.transaction = dict(kind='native-installer-upgrade', transaction=digest(raw), currentDigest=n.f.root.name, candidateDigest=checksum)
    n.context = n.metadata / ('runtime-upgrade-context-' + checksum + '.json')
    n.context.write_bytes(raw)
    n.context.chmod(0o600)
    n.absence = c.native_absent_for_installer(n.f.bridge, transaction=n.transaction)
    assert (n.absence['kit'] is not None) == code_only
    assert n.absence['identity'] == {'kind': 'absent'}
    journal = dict(schemaVersion=4, currentDigest=n.f.root.name, candidateDigest=checksum, phase='replacing-files', files=rows,
        sourceAddition=dict(schemaVersion=1, kind='ods-native-source-installer-addition', contextSha256=n.transaction['transaction'],
            familySha256=family, proof=n.absence, absentPaths=sorted(r['path'] for r in rows if r['before'] is None)))
    n.upgrade = n.metadata / 'runtime-upgrade.json'
    n.upgrade.write_bytes(c._native_encoded(journal))
    n.upgrade.chmod(0o600)
    for row in rows:
        path = Path(row['path'])
        path.write_bytes(base64.b64decode(row['after']))
        path.chmod(row['mode'])
    # Equivalent fixed owner publication, actual files/inodes and Root receipt.
    # This is not an authority callback or a substituted Source result.
    if not code_only:
        for path, text in ((n.f.env, sidefiles['gateway.env']), (n.f.answers, sidefiles['onboarding.json'])):
            path.write_text(text)
            path.chmod(0o600)
    parent = config.parent.stat()
    receipt = dict(schemaVersion=1, kind='ods-native-connection-sidefiles', ownerUid=owner.pw_uid, ownerGid=owner.pw_gid,
        configPath=str(config), bundleSha256=checksum, files={name: digest(body.encode()) for name, body in sidefiles.items()},
        parent=[parent.st_dev, parent.st_ino], children={name: dict(inode=[path.stat().st_dev, path.stat().st_ino], ready=True)
            for name, path in (('gateway.env', n.f.env), ('onboarding.json', n.f.answers))})
    if not code_only:
        (n.metadata / 'source-sidefiles.json').write_bytes(c._native_encoded(receipt))
        (n.metadata / 'source-sidefiles.json').chmod(0o600)
    # RuntimeCustody's Root644 metadata reader, same explicit driver as rebind.
    previous_read = runtime_custody._read
    def public_metadata(path, owner, maximum):
        path = Path(path)
        if path not in (c.NATIVE_RUNTIME_DESCRIPTOR, c.NATIVE_RUNTIME_MANIFEST): return previous_read(path, owner, maximum)
        before = runtime_custody._regular(path, maximum=maximum)
        body = path.read_bytes()
        if len(body) > maximum or runtime_custody._regular(path, maximum=maximum) != before or digest(body) != before[-1]:
            raise ValueError('actual fixture metadata drift')
        return json.loads(body, object_pairs_hook=c._native_pairs), digest(body)
    monkeypatch.setattr(runtime_custody, '_read', public_metadata)
    n.f.bridge._runtime_config_path = config
    n.f.bridge.gateway_process['executable'] = str(target / 'node')
    n.f.root, n.f.config = target, config
    n.f.value = n.f.bridge.connection_deployment()
    n.selected = c.native_source_selection(n.f.bridge, n.f.value)
    original = helper._read_at
    def candidate_read(directory, name, **kwargs):
        if Path(os.readlink('/proc/self/fd/' + str(directory))).is_relative_to(target):
            kwargs.update(uid=os.getuid(), gid=os.getgid())
        return original(directory, name, **kwargs)
    monkeypatch.setattr(helper, '_read_at', candidate_read)
    journal['phase'] = 'active'
    n.upgrade.write_bytes(c._native_encoded(journal))
    return n


def test_canonical_first_kit_is_inert_and_repeated_proof_never_creates_private_state(first_target):
    n = first_target
    owner = {path: (path.read_bytes(), path.stat().st_ino) for path in (n.f.env, n.f.answers)}
    result = helper.publish_installer_absent_kit(n.selected, transaction=n.transaction, proof=n.absence)
    assert result['status'] == 'prepared-no-jobs' and result['transaction'] == n.transaction
    assert helper.verify_installer_absent_kit(n.selected, transaction=n.transaction, proof=n.absence) == result
    assert len(list(n.kit.iterdir())) == 9
    assert not any(os.path.lexists(path) for path in (c.NATIVE_STATE, c.NATIVE_ENV, c.NATIVE_LOGS))
    assert not (n.metadata / 'source-identity.json').exists()
    assert n.records == {'Users': None, 'Groups': None}
    before = files(n)
    assert helper.publish_installer_absent_kit(n.selected, transaction=n.transaction, proof=n.absence) == result
    assert files(n) == before
    assert {path: (path.read_bytes(), path.stat().st_ino) for path in owner} == owner
    assert not any(cmd[1] in ('bootstrap', 'kickstart', 'enable', 'bootout') for cmd in n.commands)


@pytest.mark.parametrize('fault', ['context', 'restored', 'new-private-state', 'owner-file', 'new-identity'])
def test_first_kit_refuses_changed_actual_authority_or_owner_images_before_publication(first_target, fault):
    n = first_target
    if fault == 'context': n.context.write_bytes(n.context.read_bytes() + b' ')
    elif fault == 'restored':
        journal = json.loads(n.upgrade.read_bytes())
        journal['phase'] = 'restored'
        n.upgrade.write_bytes(c._native_encoded(journal))
    elif fault == 'new-private-state': c.NATIVE_STATE.mkdir(mode=0o700)
    elif fault == 'owner-file': n.f.env.write_text('PIXEL_EMAIL_INBOX=0\n')
    else:
        (n.metadata / 'source-identity.json').write_bytes(c._native_encoded(n.identity))
        (n.metadata / 'source-identity.json').chmod(0o600)
    before = files(n)
    with pytest.raises((ValueError, c.CustodyError, bridge.AccessError, OSError)):
        helper.publish_installer_absent_kit(n.selected, transaction=n.transaction, proof=n.absence)
    assert files(n) == before and not n.kit.exists()
    assert not (n.metadata / 'source-publication.json').exists()


def test_first_kit_authority_reuses_its_publisher_proof_and_plan_rechecks_selection(first_target, monkeypatch):
    """One guard qualifies the deployment and both runtime trees once, then rechecks."""
    n = first_target
    calls = []
    publisher, material, selected = ex._publisher, helper._material, c._native_selected
    def counted_publisher(selection=None, **kwargs):
        calls.append(('publisher', kwargs.get('with_material', False)))
        return publisher(selection, **kwargs)
    def counted_material(selection):
        calls.append('material')
        return material(selection)
    def counted_selected(selection, **kwargs):
        calls.append('selected')
        return selected(selection, **kwargs)
    monkeypatch.setattr(ex, '_publisher', counted_publisher)
    monkeypatch.setattr(helper, '_material', counted_material)
    monkeypatch.setattr(c, '_native_selected', counted_selected)
    authority = c.native_installer_absent_kit_authority(n.selected, transaction=n.transaction, proof=n.absence)
    # The driver's publisher stands in for the protected import; its material is
    # the one complete selection and bundle proof this authority consumes.
    assert calls == [('publisher', True), 'material', 'selected']
    assert authority['publication'] is None and authority['kit'] is None
    calls.clear()
    assert c.native_installer_absent_kit_plan(n.selected, transaction=n.transaction, proof=n.absence) == authority
    assert calls == [('publisher', True), 'material', 'selected', 'selected']
    with pytest.raises(c.CustodyError, match='first-kit-target-changed'):
        c.native_installer_absent_kit_authority(n.selected, transaction=n.transaction, proof=n.absence,
            _with_publisher='yes')
    # A deployment change after the authority is still refused by the plan's
    # own fresh selection proof, before any prefix is read.
    authority_done, real_authority = [], c.native_installer_absent_kit_authority
    def tracked_authority(*args, **kwargs):
        result = real_authority(*args, **kwargs)
        authority_done.append(True)
        return result
    def changed_after_authority(selection, **kwargs):
        if authority_done:
            raise c.CustodyError('native-source-deployment-changed')
        return counted_selected(selection, **kwargs)
    calls.clear()
    monkeypatch.setattr(c, 'native_installer_absent_kit_authority', tracked_authority)
    monkeypatch.setattr(c, '_native_selected', changed_after_authority)
    before = files(n)
    with pytest.raises(c.CustodyError, match='native-source-deployment-changed'):
        c.native_installer_absent_kit_plan(n.selected, transaction=n.transaction, proof=n.absence)
    assert calls == [('publisher', True), 'material', 'selected'] and files(n) == before


@pytest.mark.parametrize('where', ['kit-rename', 'committed-receipt'])
def test_first_kit_lost_ack_preserves_active_and_resumes_exact_inodes(first_target, monkeypatch, where):
    n = first_target
    actual_rename, actual_save = os.rename, c._native_save_at
    hit = []
    def rename(first, second, **kwargs):
        result = actual_rename(first, second, **kwargs)
        if where == 'kit-rename' and second == n.kit.name and str(first).startswith('.source-kit-') and not hit:
            hit.append(True)
            raise OSError('owned fixture lost kit rename acknowledgement')
        return result
    def save(selection, directory, name, value, **kwargs):
        result = actual_save(selection, directory, name, value, **kwargs)
        if (where == 'committed-receipt' and name == 'source-publication.json'
                and value.get('phase') == 'prepared-no-jobs' and not hit):
            hit.append(True)
            raise OSError('owned fixture lost committed receipt acknowledgement')
        return result
    monkeypatch.setattr(os, 'rename', rename)
    monkeypatch.setattr(c, '_native_save_at', save)
    with pytest.raises(OSError):
        helper.publish_installer_absent_kit(n.selected, transaction=n.transaction, proof=n.absence)
    assert hit and json.loads(n.upgrade.read_bytes())['phase'] == 'active'
    record = json.loads((n.metadata / 'source-publication.json').read_bytes())
    frozen = record['candidateInode']
    result = helper.publish_installer_absent_kit(n.selected, transaction=n.transaction, proof=n.absence)
    assert helper.verify_installer_absent_kit(n.selected, transaction=n.transaction, proof=n.absence) == result
    assert [n.kit.stat().st_dev, n.kit.stat().st_ino] == frozen
    assert not (n.metadata / 'source-identity.json').exists() and not c.NATIVE_STATE.exists()
