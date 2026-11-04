"""Actual protected Source copies, fixed installer journal and rename recovery.

Linux Root/Apple metadata driver only; no native service/provider execution.
The restored-runtime route exercises the complete original runtime custody;
candidate activation qualification is separately owned by the installer suite.
"""
# ruff: noqa: E402,F401,F811
import json
import base64
import os
from pathlib import Path
import plistlib
import shutil
import sys

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs metadata harness, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_executor import executor, qualification_operation
from test_native_source_generation import generation, SPECS
from test_native_source_custody import native
from test_connection_deployment_paths import deployment
from test_native_source_runtime_publication import built, source, package, runtime_custody, bundle, services
from test_native_source_publication import material, publication, helper
from test_native_source_installer_withdrawal import make_prepared, files, digest
from test_native_source_backend import backend, broker_file
from pixel_connections import custody as c, coordinator, state
from pixel_access_bridge import AccessError, LaunchdAccessBridge
import pixel_macos_custody as mac


def source_before(n, monkeypatch):
    # The relocated public kit parent models /usr/local/libexec (Root755);
    # the access, state, jobs and retained private directories remain700.
    n.world.chmod(0o755)
    descriptor = json.loads(runtime_custody.DESCRIPTOR.read_bytes())
    monkeypatch.setattr(c, 'NATIVE_RUNTIME_DESCRIPTOR', runtime_custody.DESCRIPTOR)
    monkeypatch.setattr(c, 'NATIVE_RUNTIME_MANIFEST', Path(descriptor['manifest']))
    monkeypatch.setattr(c, 'NATIVE_RUNTIMES', n.f.root.parent)
    program = n.world.parent / 'native-program'
    program.mkdir(mode=0o755)
    monkeypatch.setattr(c, 'NATIVE_PROGRAM', program)
    for path in c.native_installer_source_family():
        path = Path(path)
        if path.is_relative_to(program):
            source_path = n.f.root / 'ods-source/bin' / path.relative_to(program)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source_path.read_bytes())
            path.chmod(0o644)
    previous = mac.protected_bytes
    def protected(path, *, limit=2 * 1024 * 1024):
        path = Path(path)
        if path.is_relative_to(n.world):
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                body = c._native_root_file(directory, path.name, mode=path.stat().st_mode & 0o777, limit=limit)[0]
            finally:
                os.close(directory)
        else:
            body = previous(path)
        if len(body) > limit:
            raise ValueError('fixture protected read exceeded real bound')
        return body
    monkeypatch.setattr(mac, 'protected_bytes', protected)
    sidefiles = {'gateway.env': n.f.env.read_text(), 'onboarding.json': n.f.answers.read_text()}
    parent_info = n.f.config.parent.stat()
    record = {'schemaVersion': 1, 'kind': 'ods-native-connection-sidefiles',
        'ownerUid': n.f.bridge.owner.pw_uid, 'ownerGid': n.f.bridge.owner.pw_gid,
        'configPath': str(n.f.config), 'bundleSha256': n.f.root.name,
        'files': {name: digest(body.encode()) for name, body in sidefiles.items()},
        'parent': [parent_info.st_dev, parent_info.st_ino],
        'children': {name: {'inode': [path.stat().st_dev, path.stat().st_ino], 'ready': True}
            for name, path in (('gateway.env', n.f.env), ('onboarding.json', n.f.answers))}}
    (n.metadata / 'source-sidefiles.json').write_bytes(c._native_encoded(record))
    (n.metadata / 'source-sidefiles.json').chmod(0o600)
    return sidefiles, descriptor


@pytest.fixture
def restored(executor, monkeypatch):
    n = executor
    sidefiles, _descriptor = source_before(n, monkeypatch)
    n = make_prepared(n, monkeypatch, runtime_bundle={'connection_sidefiles': sidefiles, 'config_path': str(n.f.config)})
    n.receipt = helper.withdraw_installer_generation(n.selected, transaction=n.transaction, intent=n.intent)
    # Simulate the installer's actual typed CAS after producer verification,
    # then the explicit restored phase. No candidate code is substituted.
    helper.verify_installer_withdrawal(n.selected, transaction=n.transaction, intent=n.intent, receipt=n.receipt)
    journal = json.loads(n.upgrade.read_bytes())
    journal['sourceAddition']['receipt'] = n.receipt
    journal['phase'] = 'restored'
    n.upgrade.write_bytes(c._native_encoded(journal))
    return n


@pytest.fixture
def upgraded(executor, monkeypatch):
    return build_upgrade(executor, monkeypatch)


def build_upgrade(n, monkeypatch, *, connected=False, activate=True):
    """Build both real bundles; exercise actual target RuntimeCustody selection."""
    sidefiles, old_descriptor = source_before(n, monkeypatch)
    captured = None
    if connected:
        record = {'schemaVersion': 1, 'kind': state.KIND, 'updatedAt': '2026-11-04T00:00:00Z',
            'ownerTimeZone': 'UTC', 'connectors': {'google': {'status': 'connected', 'account': 'fixture@example.com',
                'calendarId': 'primary', 'gmailWindowDays': 7, 'cloudModels': False,
                'connectedAt': '2026-11-04T00:00:00Z', 'capabilities': {'calendar.read': False,
                    'calendar.write': False, 'calendar.direct': False, 'gmail.read': True, 'gmail.drafts': False}}}}
        state.validate(record)
        path = n.metadata / 'portal-connections.json'
        path.write_bytes(c._native_encoded(record)); path.chmod(0o644)
        monkeypatch.setattr(c, 'NATIVE_CONNECTION_RECORD', path)
        n.connected_token = broker_file(n, 'private/google-token.json', b'SYNTHETIC_TOKEN_NOT_READ')
        live_target = coordinator.gateway_target(record, custody_held=True)
        answers = state.connection_answers(record, google_custody=True)
        env = c.env_text(c.parse_env(n.f.env.read_text()), {'PIXEL_LIMB_EMAIL_ENABLED': '1'})
        for path, body in ((n.f.env, env.encode()),
                (n.f.answers, c._native_encoded(dict(answers, modelChoice='preserved-owner-model')))):
            staged = path.with_name('.admitted-' + path.name)
            staged.write_bytes(body); staged.chmod(0o600)
            os.replace(staged, path)
        def worker(operation, **_kwargs):
            assert operation == 'connection-owner-status'
            return {'pending': False, 'configSha256': digest(n.f.config.read_bytes()),
                'envSha256': digest(n.f.env.read_bytes()), 'live': live_target, 'answers': answers, 'completion': None}
        monkeypatch.setattr(n.f.bridge, 'worker', worker)
        monkeypatch.setattr(n.f.bridge, 'inspect', lambda: {'runtime_verified': True, 'pending': False,
            'configured_mode': 'sandboxed', 'effective_mode': 'sandboxed',
            '_config': {'config_sha256': digest(n.f.config.read_bytes())}, 'revision': 'e' * 64})
        source_env = n.backend.connection_env(gateway=live_target, calendar_id='primary', time_zone='UTC', gmail_window_days=7)
        helper.publish_environment_generation(n.selected, source_env)
        assert dict(c.parse_env(c.NATIVE_ENV.read_text()))['PIXEL_LIMB_EMAIL_ENABLED'] == '1'
        captured = c.native_installer_sidefiles_snapshot(n.selected)
        sidefiles = captured['sidefiles']
        n.connected_before = captured['snapshot']
        assert n.connected_before['files']['gateway.env']['inode'] != json.loads(
            (n.metadata / 'source-sidefiles.json').read_bytes())['children']['gateway.env']['inode']
    runtime = n.world.parent / 'candidate-runtime-input'
    shutil.copytree(n.f.root / 'runtime', runtime)
    with (runtime / 'openclaw.mjs').open('ab') as stream:
        stream.write(b'// separately built candidate runtime, never executed\n')
    service_manifest = services(n.built.source)
    service_manifest['pixelSourceRef'] = n.built.package[1]
    destination = n.f.root.parent / 'candidate-preparing'
    checksum = bundle.build(node=n.f.root / 'node', runtime=runtime, destination=destination,
        plugins=[n.f.root / relative for relative in json.loads((n.f.root / 'bundle.json').read_bytes())['plugins']],
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
    config.write_bytes(n.f.config.read_bytes()); config.chmod(0o600)
    document = plistlib.loads(n.f.plist.read_bytes())
    document['ProgramArguments'] = [item.replace(str(n.f.root), str(target)).replace(str(n.f.config), str(config))
        for item in document['ProgramArguments']]
    candidate_plist = plistlib.dumps(document)
    after = {}
    for path in c.native_installer_source_family():
        value = Path(path)
        if value == c.NATIVE_RUNTIME_DESCRIPTOR:
            after[path] = helper.encoded(descriptor)
        elif value == c.NATIVE_RUNTIME_MANIFEST:
            after[path] = manifest_body
        elif value == n.metadata / 'source-runtime-installation.json':
            after[path] = helper.encoded({'schemaVersion': 1, 'kind': 'ods-native-source-runtime-publication',
                'bundleSha256': checksum, 'sourceRef': n.built.package[1]})
        else:
            after[path] = (target / 'ods-source/bin' / value.relative_to(c.NATIVE_PROGRAM)).read_bytes()
    runtime_bundle = {'connection_sidefiles': sidefiles, 'config_path': str(config)}
    if captured is not None:
        runtime_bundle['source_sidefiles_before'] = captured['snapshot']
    n = make_prepared(n, monkeypatch, runtime_bundle=runtime_bundle,
        candidate_digest=checksum, source_after=after,
        extra_after=[(launcher, launcher_body, 0o755), (n.f.plist, candidate_plist, 0o644)])
    n.receipt = helper.withdraw_installer_generation(n.selected, transaction=n.transaction, intent=n.intent)
    helper.verify_installer_withdrawal(n.selected, transaction=n.transaction, intent=n.intent, receipt=n.receipt)
    journal = json.loads(n.upgrade.read_bytes())
    journal['sourceAddition']['receipt'] = n.receipt
    journal['phase'] = 'replacing-files'
    n.upgrade.write_bytes(c._native_encoded(journal))
    if not activate:
        journal['phase'] = 'restored'
        n.upgrade.write_bytes(c._native_encoded(journal))
        return n
    for row in journal['files']:
        path = Path(row['path'])
        path.write_bytes(base64.b64decode(row['after'])); path.chmod(row['mode'])
    previous_read = runtime_custody._read
    def public_metadata(path, owner, maximum):
        # Darwin publishes these two NONSECRET Root metadata images644;
        # the shared Linux fixture otherwise uses its private600 reader.
        # All actual tree/interpreter/launcher hashes remain production code.
        path = Path(path)
        if path not in (c.NATIVE_RUNTIME_DESCRIPTOR, c.NATIVE_RUNTIME_MANIFEST):
            return previous_read(path, owner, maximum)
        before = runtime_custody._regular(path, maximum=maximum)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                body = stream.read(maximum + 1)
        finally:
            os.close(fd)
        if len(body) > maximum or runtime_custody._regular(path, maximum=maximum) != before or digest(body) != before[-1]:
            raise ValueError('fixture runtime metadata changed')
        return json.loads(body, object_pairs_hook=c._native_pairs), digest(body)
    monkeypatch.setattr(runtime_custody, '_read', public_metadata)
    n.f.bridge._runtime_config_path = config
    n.f.bridge.gateway_process['executable'] = str(target / 'node')
    n.f.root, n.f.config = target, config
    n.f.value = LaunchdAccessBridge.connection_deployment(n.f.bridge)
    n.selected = c.native_source_selection(n.f.bridge, n.f.value)
    previous = helper._read_at
    def read(directory, name, **kwargs):
        location = Path(os.readlink('/proc/self/fd/' + str(directory)))
        if location.is_relative_to(target):
            kwargs.update(uid=os.getuid(), gid=os.getgid())
        return previous(directory, name, **kwargs)
    monkeypatch.setattr(helper, '_read_at', read)
    journal['phase'] = 'active'
    n.upgrade.write_bytes(c._native_encoded(journal))
    return n


@pytest.fixture
def connected_upgraded(backend, monkeypatch):
    return build_upgrade(backend, monkeypatch, connected=True)


@pytest.fixture
def connected_restored(backend, monkeypatch):
    return build_upgrade(backend, monkeypatch, connected=True, activate=False)


def test_restored_runtime_rebind_retains_old_custody_and_mints_only_inactive_child(restored):
    n = restored
    environment = (c.NATIVE_ENV.read_bytes(), c.NATIVE_ENV.stat().st_ino)
    owner_files = {p: (p.read_bytes(), p.stat().st_ino) for p in (n.f.env, n.f.answers)}
    old_kit = n.kit.stat().st_ino
    result = helper.rebind_installer_generation(n.selected, transaction=n.transaction, receipt=n.receipt)
    assert result['status'] == 'inactive'
    child = json.loads((n.metadata / 'source-jobs-prepare.json').read_bytes())
    assert child['generation'] == result['generation'] and child['phase'] == 'committed-inactive'
    assert sorted(item['role'] for item in child['definitions'].values()) == ['direct', 'sync']
    gate = json.loads((c.NATIVE_STATE / 'custody.json').read_bytes())
    assert gate['schemaVersion'] == 2 and gate['phase'] == 'inactive'
    parent = json.loads((n.metadata / 'source-installer-rebind.json').read_bytes())
    assert parent['phase'] == 'committed'
    assert (n.kit.parent / parent['backup']).stat().st_ino == old_kit
    assert (c.NATIVE_ENV.read_bytes(), c.NATIVE_ENV.stat().st_ino) == environment
    assert {p: (p.read_bytes(), p.stat().st_ino) for p in owner_files} == owner_files
    before = files(n)
    assert helper.rebind_installer_generation(n.selected, transaction=n.transaction, receipt=n.receipt) == result
    assert files(n) == before


def test_actual_new_bundle_rebinds_deployment_without_rewriting_private_or_owner_data(upgraded):
    n = upgraded
    old = n.intent['binding']
    environment = (c.NATIVE_ENV.read_bytes(), c.NATIVE_ENV.stat().st_ino)
    owner_files = {p: (p.read_bytes(), p.stat().st_ino) for p in (n.f.env, n.f.answers)}
    result = helper.rebind_installer_generation(n.selected, transaction=n.transaction, receipt=n.receipt)
    assert result['status'] == 'inactive' and n.f.root.name == n.transaction['candidateDigest']
    parent = json.loads((n.metadata / 'source-installer-rebind.json').read_bytes())
    assert parent['oldBinding']['deploymentSha256'] == old['deploymentSha256']
    assert parent['binding']['deploymentSha256'] != old['deploymentSha256']
    assert parent['binding']['deploymentSha256'] == n.selected.deployment.checksum
    assert json.loads((c.NATIVE_STATE / 'custody.json').read_bytes())['phase'] == 'inactive'
    assert (c.NATIVE_ENV.read_bytes(), c.NATIVE_ENV.stat().st_ino) == environment
    assert {p: (p.read_bytes(), p.stat().st_ino) for p in owner_files} == owner_files
    assert helper.verify_installer_rebind_generation(n.selected, transaction=n.transaction, receipt=n.receipt) == result
    before = files(n)
    assert helper.rebind_installer_generation(n.selected, transaction=n.transaction, receipt=n.receipt) == result
    assert files(n) == before


@pytest.mark.parametrize('fixture_name', ['connected_upgraded', 'connected_restored'])
def test_admitted_connected_owner_images_survive_target_and_restored_routes(request, fixture_name):
    n = request.getfixturevalue(fixture_name)
    owner_files = {path: (path.read_bytes(), path.stat().st_ino) for path in (n.f.env, n.f.answers)}
    token = n.connected_token.stat()
    environment = (c.NATIVE_ENV.read_bytes(), c.NATIVE_ENV.stat().st_ino)
    result = helper.rebind_installer_generation(n.selected, transaction=n.transaction, receipt=n.receipt)
    assert helper.verify_installer_rebind_generation(n.selected, transaction=n.transaction, receipt=n.receipt) == result
    parent = json.loads((n.metadata / 'source-installer-rebind.json').read_bytes())
    image = parent['bindings']['source-sidefiles.json']
    assert image['before']['children']['gateway.env']['inode'] != image['after']['children']['gateway.env']['inode']
    assert image['after']['children']['gateway.env']['inode'] == n.connected_before['files']['gateway.env']['inode']
    assert {path: (path.read_bytes(), path.stat().st_ino) for path in owner_files} == owner_files
    assert n.connected_token.stat().st_ino == token.st_ino
    assert (c.NATIVE_ENV.read_bytes(), c.NATIVE_ENV.stat().st_ino) == environment
    assert json.loads(n.f.answers.read_bytes())['modelChoice'] == 'preserved-owner-model'
    before = files(n)
    first = c.native_installer_sidefiles_snapshot(n.selected)
    assert c.native_installer_sidefiles_snapshot(n.selected) == first
    assert first['sidefiles'] == {path.name: body.decode() for path, (body, _inode) in owner_files.items()}
    assert first['snapshot']['runtime']['deploymentSha256'] == n.selected.deployment.checksum
    assert first['snapshot']['bootstrap']['inode'] == image['afterInode']
    assert files(n) == before


@pytest.mark.parametrize('change', ['receipt', 'unknown-kit', 'identity', 'sidefiles', 'actor'])
def test_unknown_target_boundary_refuses_before_staging_and_preserves_all_data(restored, monkeypatch, change):
    n = restored
    receipt = json.loads(json.dumps(n.receipt))
    if change == 'receipt':
        receipt['withdrawal'] = 'f' * 32
    elif change == 'unknown-kit':
        (n.kit / 'unexpected').write_bytes(b'preserve unknown kit entry')
    elif change == 'identity':
        n.records['Users']['GeneratedUID'] = ['00000000-0000-0000-0000-000000000000']
    elif change == 'sidefiles':
        n.f.env.write_bytes(n.f.env.read_bytes() + b'# changed owner file\n')
    else:
        previous = n.f.bridge.command
        def command(argv, timeout=20):
            if argv == ['/bin/ps', '-axo', 'pid=,uid=']:
                return '1 0\n99 ' + str(n.identity['id']) + '\n'
            return previous(argv, timeout=timeout)
        monkeypatch.setattr(n.f.bridge, 'command', command)
    before = files(n)
    with pytest.raises((ValueError, c.CustodyError, AccessError)):
        helper.rebind_installer_generation(n.selected, transaction=n.transaction, receipt=receipt)
    assert files(n) == before and not (n.metadata / 'source-installer-rebind.json').exists()


@pytest.mark.parametrize('where', ['old-kit', 'new-kit', 'binding-old', 'binding-new', 'publication', 'parent', 'child', 'committed'])
def test_rebind_lost_reply_reuses_original_kit_and_child(restored, monkeypatch, where):
    n = restored
    rename, saved = os.rename, c._native_save_at
    hit = []
    def renamed(first, second, **kwargs):
        result = rename(first, second, **kwargs)
        chosen = (where == 'old-kit' and str(second).startswith('.source-kit-before-')
            or where == 'new-kit' and first != n.kit.name and second == n.kit.name
            or where == 'binding-old' and str(second).startswith('.source-binding-before-')
            or where == 'binding-new' and str(first).startswith('.source-binding-') and not str(first).startswith('.source-binding-before-')
            or where == 'publication' and second == 'source-publication.json'
            or where == 'parent' and str(second).startswith('source-environment-generation-')
            or where == 'child' and first == '.gate-next')
        if chosen and not hit:
            hit.append(True)
            raise OSError('invented target rebind lost acknowledgement')
        return result
    def save(selection, directory, name, value, **kwargs):
        result = saved(selection, directory, name, value, **kwargs)
        if not hit and where == 'committed' and name == 'source-installer-rebind.json' and value['phase'] == 'committed':
            hit.append(True)
            raise OSError('invented target rebind lost acknowledgement')
        return result
    monkeypatch.setattr(os, 'rename', renamed)
    monkeypatch.setattr(c, '_native_save_at', save)
    with pytest.raises(OSError, match='target rebind lost acknowledgement'):
        helper.rebind_installer_generation(n.selected, transaction=n.transaction, receipt=n.receipt)
    frozen = json.loads((n.metadata / 'source-installer-rebind.json').read_bytes())
    monkeypatch.setattr(os, 'rename', rename)
    monkeypatch.setattr(c, '_native_save_at', saved)
    result = helper.rebind_installer_generation(n.selected, transaction=n.transaction, receipt=n.receipt)
    assert hit and result['generation'] == frozen['generation']
    parent = json.loads((n.metadata / 'source-installer-rebind.json').read_bytes())
    assert parent['nonce'] == frozen['nonce'] and parent['oldKit'] == frozen['oldKit']
