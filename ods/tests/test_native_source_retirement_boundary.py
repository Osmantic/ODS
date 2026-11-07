"""Legacy retirement cannot silently adopt or report absent Source private data."""
import importlib.util
import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('source_retirement_boundary',
    ROOT / 'installers/macos/lib/pixel-native-uninstall.py')
retirement = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retirement)


def owner_fixture(tmp_path, monkeypatch):
    state, broker, settings, install = [tmp_path / name for name in ('metadata', 'ops-home', 'settings.json', 'ods')]
    state.mkdir()
    owner = SimpleNamespace(pw_name='fixture-owner', pw_uid=501, pw_gid=20, pw_dir=str(tmp_path / 'home'))
    monkeypatch.setattr(retirement.sys, 'platform', 'darwin')
    monkeypatch.setattr(retirement.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(retirement.pwd, 'getpwnam', lambda _name: owner)
    for name, value in (('STATE', state), ('BROKER', broker), ('SETTINGS', settings)):
        monkeypatch.setattr(retirement, name, value)
    monkeypatch.setattr(retirement, 'DIRECTORIES', ())
    monkeypatch.setattr(retirement, 'INTEGRATION_DIRECTORIES', ())
    monkeypatch.setattr(retirement, 'CONFIG_NAMES', ())
    monkeypatch.setattr(retirement, 'command', lambda _a: pytest.fail('no job or process commands before Source admission'))
    return state, broker, settings, install, owner


@pytest.mark.parametrize('access_present', [False, True])
@pytest.mark.parametrize('kind', ['state', 'environment', 'logs', 'kit', 'descriptor'])
def test_unbound_or_unqualified_source_is_preserved_before_legacy_stops(tmp_path, monkeypatch, access_present, kind):
    state, _broker, settings, install, owner = owner_fixture(tmp_path, monkeypatch)
    residue = tmp_path / ('source-' + kind)
    if kind in ('environment', 'descriptor'): residue.write_bytes(b'INVENTED_UNBOUND_PRIVATE_IMAGE')
    else:
        residue.mkdir()
        (residue / 'private-child').write_bytes(b'INVENTED_PRIVATE_IMAGE')
    stamp = residue.stat()
    monkeypatch.setattr(retirement, 'SOURCE_RESIDUES', (str(residue),))
    if access_present: settings.write_bytes(b'not read before Source refusal')
    monkeypatch.setattr(retirement, 'helper', lambda _n: pytest.fail('no account adoption before residue custody'))
    with pytest.raises(ValueError, match='source-retirement-not-qualified|unbound-residue'):
        retirement.retire(str(install), owner.pw_name)
    assert (residue.stat().st_dev, residue.stat().st_ino) == (stamp.st_dev, stamp.st_ino)
    assert residue.exists()
    assert not (state / 'retirement.json').exists()


@pytest.mark.parametrize('source_retained', [False, True])
@pytest.mark.parametrize('ops_home', [False, True])
def test_already_retired_source_identity_requires_exact_qualified_union(tmp_path, monkeypatch, source_retained, ops_home):
    state, broker, _settings, install, owner = owner_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(retirement, 'SOURCE_RESIDUES', ())
    for name in ('ops-identity.json', 'ops-identity.lock'):
        (state / name).write_bytes(b'fixture retained Ops identity')
    if source_retained: (state / 'source-identity.json').write_bytes(b'fixture retained Source identity')
    if ops_home: broker.mkdir()
    calls = []
    def identity(**kwargs): calls.append(('identity', kwargs))
    def home(**kwargs): calls.append(('empty-home', kwargs))
    monkeypatch.setattr(retirement, 'helper', lambda _name:
        SimpleNamespace(verify_identity_only=identity, verify_empty_home_only=home))
    assert retirement.retire(str(install), owner.pw_name) == {'status': 'absent'}
    expected = dict(source_owner=owner.pw_name, source_install_dir=str(install)) if source_retained else {}
    assert calls == [('empty-home' if ops_home else 'identity', expected)]
    assert {p.name for p in state.iterdir()} == ({'ops-identity.json', 'ops-identity.lock'}
        | ({'source-identity.json'} if source_retained else set()))


def typed_inventory():
    from pixel_connections import custody as c
    from test_native_source_installer_recovery import source_images, withdrawal_authority
    intent = withdrawal_authority(source_images())[0]['proof']
    identity = dict(schema=1, name=c.NATIVE_BROKER, id=62000,
        userGuid=intent['binding']['brokerGuid'], groupGuid='BBBBBBBB-CCCC-4DDD-8EEE-FFFFFFFFFFFF')
    runtime = copy.deepcopy(intent['runtime'])
    runtime = dict(deploymentSha256=intent['binding']['deploymentSha256'], **runtime)
    image = lambda index: {'inode': [1, index], 'sha256': '6' * 64}
    inventory = dict(schemaVersion=1, kind='ods-native-source-retirement-inventory',
        binding=copy.deepcopy(intent['binding']), ownerGuid='CCCCCCCC-DDDD-4EEE-8FFF-AAAAAAAAAAAA',
        identity=identity, generation=intent['generation'], generationBoot=intent['boot'], boot=intent['boot'],
        portalRecord=None, gate=copy.deepcopy(intent['gate']),
        children={name: [1, 20 + i] for i, name in enumerate(('private', 'private/oauth', 'projection', 'results', 'proposals'))},
        environment=copy.deepcopy(intent['environment']),
        kit={'inode': [1, 30], 'files': {name: image(31 + i)
            for i, name in enumerate(('source-kit.json', 'broker.py', 'source-broker.env', *c.NATIVE_PROFILES))}},
        logs={'directory': [1, 50], 'files': {name: [1, 51 + i] for i, name in enumerate(c._native_log_names())}},
        metadata={name: image(70 + i) for i, name in enumerate(('source-identity.json', 'source-custody-prepare.json',
            'source-environment.json', 'source-logs-prepare.json', 'source-publication.json', 'source-jobs-prepare.json'))},
        definitions={c.NATIVE_LABEL + 'sync': {'role': 'sync', 'identifier': None, 'active': False, **image(80)}},
        jobs={'directory': [1, 90], 'sha256': '7' * 64}, token=None)
    inventory['kit']['files']['source-kit.json']['sha256'] = intent['binding']['kitSha256']
    return inventory, runtime


@pytest.mark.parametrize('fault', [None, 'foreign-owner', 'runtime', 'nonce', 'inventory-extra',
    'token-metadata', 'context-extra', 'new-boot', 'duplicate-json', 'fake-upgrade'])
def test_source_retirement_schema_is_distinct_and_keeps_exact_context_identity(tmp_path, monkeypatch, fault):
    import sys
    sys.path.insert(0, str(ROOT / 'bin'))
    inventory, runtime = typed_inventory()
    owner = SimpleNamespace(pw_name='fixture-owner', pw_uid=501, pw_gid=20, pw_dir='/Users/fixture-owner')
    monkeypatch.setattr(retirement.pwd, 'getpwuid', lambda _uid: owner)
    source = dict(schemaVersion=1, kind='ods-native-source-retirement-intent', nonce='a' * 32,
        owner=dict(uid=501, gid=20, name=owner.pw_name, installDir=owner.pw_dir + '/ods'),
        runtime=runtime, inventory=inventory)
    core = dict(schema=1, owner=501, boot=inventory['boot'].lower(),
        hashes={'/fixed/protected-core.json': '8' * 64},
        trees={'system/com.ods.pixel-native-gateway': [[42, 10, 20]]}, sandboxes=[])
    if fault == 'foreign-owner': source['owner']['gid'] = 21
    if fault == 'runtime': source['runtime']['bundleSha256'] = '9' * 64
    if fault == 'nonce': source['nonce'] = 'not-frozen'
    if fault == 'inventory-extra': source['inventory']['allowArchive'] = True
    if fault == 'token-metadata': source['inventory']['token'] = [1, 11, 501, 20, 0o600, 50, 1, 1, 1]
    if fault == 'new-boot': core['boot'] = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
    if fault in ('foreign-owner', 'runtime', 'nonce', 'inventory-extra', 'token-metadata', 'new-boot'):
        with pytest.raises(ValueError): retirement.source_retirement_witness(core, source)
        return
    witness = retirement.source_retirement_witness(core, source)
    assert core['schema'] == 1 and 'source' not in core
    assert witness['schema'] == 3
    if fault == 'context-extra': witness['candidateDigest'] = 'f' * 64
    if fault == 'fake-upgrade': witness['kind'] = 'native-installer-upgrade'
    raw = (json.dumps(witness, sort_keys=True) + '\n').encode()
    if fault == 'duplicate-json': raw = raw.replace(b'"schema": 3', b'"schema": 3, "schema": 3', 1)
    if fault:
        with pytest.raises(ValueError): retirement.source_retirement_transaction(raw)
    else:
        transaction = retirement.source_retirement_transaction(raw)
        assert transaction == dict(kind='native-installer-retirement', transaction=hashlib.sha256(raw).hexdigest(),
            currentDigest=runtime['bundleSha256'])
        # Exact protected bytes matter; parsing/re-encoding does not silently
        # substitute a transaction or fabricate a candidate deployment.
        assert retirement.source_retirement_transaction(raw + b' ')['transaction'] != transaction['transaction']
        assert 'candidateDigest' not in transaction


def test_retirement_constructs_only_actual_old_controller_with_fixed_owner_key(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(ROOT / 'bin'))
    import pixel_access_bridge
    from test_native_source_installer_recovery import access
    owner = SimpleNamespace(pw_name='fixture-owner', pw_uid=os.geteuid(), pw_gid=os.getegid())
    key = tmp_path / 'owner-controller.key'
    key.write_bytes(b'fixture-existing-owner-api-key')
    key.chmod(0o600)
    token = tmp_path / 'unrelated-source-refresh-token'
    token.write_bytes(b'INVENTED_TOKEN_NOT_ADDRESSED')
    token_stamp = token.stat()
    monkeypatch.setitem(access.ACCESS_FILES, 'key', key)
    monkeypatch.setattr(retirement, 'helper', lambda name: access if name == 'pixel-macos-access-install'
        else pytest.fail('only existing controller construction helper is used'))
    opened = []
    original_open = access.os.open
    def open_only_key(path, *args, **kwargs):
        assert Path(path) == key
        opened.append(Path(path))
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(access.os, 'open', open_only_key)
    calls = []
    def bridge(root, api_key, **kwargs):
        calls.append((root, api_key, kwargs))
        return 'constructed-actual-controller'
    monkeypatch.setattr(pixel_access_bridge, 'LaunchdAccessBridge', bridge)
    settings = dict(owner=owner.pw_name, install_dir='/Users/fixture-owner/ods', state_dir=str(access._launchd.ACCESS_STATE),
        gateway_target=access._launchd.GATEWAY_TARGET, gateway_plist=str(access._launchd.GATEWAY_PLIST),
        openclaw_bin=str(access.GATEWAY_LAUNCHER), gateway_process={'actual': 'old-process-binding'},
        gateway_binding={'actual': 'old-definition-binding'}, gateway_port=18789, gateway_policy={'actual': 'old-policy'})
    assert retirement._source_retirement_bridge(settings, owner) == 'constructed-actual-controller'
    assert opened == [key]
    assert calls[0][1] == 'fixture-existing-owner-api-key'
    assert calls[0][2]['gateway_process'] == settings['gateway_process']
    assert calls[0][2]['gateway_binding'] == settings['gateway_binding']
    assert (token.stat().st_dev, token.stat().st_ino, token.stat().st_size,
        token.stat().st_mtime_ns) == (token_stamp.st_dev, token_stamp.st_ino,
            token_stamp.st_size, token_stamp.st_mtime_ns)
