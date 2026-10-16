"""The owner builds; only sealed-source inspection and planning use root."""
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
from types import SimpleNamespace

import pytest


LIB = Path(__file__).resolve().parents[1] / 'installers/macos/lib'


def load(name):
    spec = importlib.util.spec_from_file_location('proof_' + name, LIB / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load('pixel-macos-access-install')
prepare = load('pixel-native-prepare')


@pytest.mark.parametrize('fault', [None, 'owner', 'digest', 'selected-node', 'config-path',
    'custody', 'inventory', 'config-drift', 'plist-drift'])
def test_root_proof_checks_full_selected_tree_and_returns_only_hashes(tmp_path, monkeypatch, fault):
    monkeypatch.syspath_prepend(str(LIB.parents[2] / 'bin'))
    import pixel_macos_custody as custody
    monkeypatch.setattr(installer.sys, 'platform', 'darwin')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 501 if fault == 'owner' else 0)
    digest = 'a' * 64
    bundle = installer._bundle.INSTALL_ROOT / digest
    config = str(installer.RUNTIME_CONFIG_ROOT / '501/openclaw.json')
    document = {'UserName': 'owner'}
    definition = plistlib.dumps(document)
    private = b'{"credential":"never-disclose-this-secret"}'
    monkeypatch.setattr(installer.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=501))
    monkeypatch.setattr(installer, '_source_gateway', lambda *args: (document,
        {'OPENCLAW_CONFIG_PATH': '/foreign/config' if fault == 'config-path' else config}, None, None,
        bundle / ('foreign' if fault == 'selected-node' else 'node'), bundle / 'runtime/openclaw.mjs'))
    reads = {'plist': 0, 'config': 0}
    def read(kind, value):
        reads[kind] += 1
        return value + b' ' if fault == kind + '-drift' and reads[kind] > 1 else value
    monkeypatch.setattr(custody, 'protected_bytes', lambda path: read('plist', definition))
    monkeypatch.setattr(installer, '_configuration_bytes', lambda path, uid: read('config', private))
    events = []
    def tree(path):
        assert path == bundle
        events.append('custody')
        if fault == 'custody':
            raise custody.CustodyError('unsafe-root-tree')
    def verify(path, *, expected_digest):
        assert path == bundle and expected_digest == digest
        events.append('full-inventory')
        if fault == 'inventory':
            raise ValueError('changed-link-or-content')
    monkeypatch.setattr(custody, 'protected_tree_metadata', tree)
    monkeypatch.setattr(installer._bundle, 'verify', verify)
    arguments = dict(owner_name='owner', current_digest='invalid' if fault == 'digest' else digest,
        gateway_port=18789)
    if fault:
        with pytest.raises(ValueError):
            installer.migration_source_proof(**arguments)
    else:
        result = installer.migration_source_proof(**arguments)
        assert result == {'currentBundleDigest': digest,
            'sourceDefinitionSha256': hashlib.sha256(definition).hexdigest(),
            'sourceConfigSha256': hashlib.sha256(private).hexdigest()}
        assert events == ['custody', 'full-inventory']
        assert 'never-disclose' not in json.dumps(result)


@pytest.mark.parametrize('prompt,tty', [(False, False), (False, True), (True, False), (True, True)])
def test_owner_uses_explicit_readonly_sudo_and_controlling_tty(monkeypatch, prompt, tty):
    expected = {'currentBundleDigest': 'a' * 64}
    monkeypatch.setattr(prepare, 'helper', lambda name: SimpleNamespace(controlling_terminal=lambda: tty))
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(expected).encode())
    monkeypatch.setattr(prepare.subprocess, 'run', run)
    assert prepare.protected_migration_check(['verify-migration-source'], expected=expected,
        prompt_for_sudo=prompt) == expected
    argv, options = calls[0]
    assert ('-n' not in argv) is (prompt and tty)
    assert argv[argv.index('/usr/bin/python3') + 1:][:2] == ['-I', '-B']
    assert '--activate' not in argv
    assert options['stdin'] == prepare.subprocess.DEVNULL
    assert options['timeout'] == 180


@pytest.mark.parametrize('fault', ['failed', 'malformed', 'oversized', 'wrong-hash', 'extra-secret'])
def test_owner_refuses_unproved_or_mismatched_result_without_leaking_output(monkeypatch, capsys, fault):
    expected = {'currentBundleDigest': 'a' * 64}
    value = dict(expected)
    if fault == 'wrong-hash':
        value['currentBundleDigest'] = 'b' * 64
    if fault == 'extra-secret':
        value['secret'] = 'never-disclose'
    body = b'never-disclose' if fault == 'malformed' else b'x' * 4097 if fault == 'oversized' else json.dumps(value).encode()
    monkeypatch.setattr(prepare.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=1 if fault == 'failed' else 0, stdout=body, stderr=b'never-disclose'))
    with pytest.raises(ValueError) as error:
        prepare.protected_migration_check(['verify-migration-source'], expected=expected)
    assert 'never-disclose' not in str(error.value) + capsys.readouterr().err


@pytest.mark.parametrize('arguments', [[], ['other'], ['migrate-native'],
    ['migrate-native', '--verify-preparation', '--activate']])
def test_owner_never_launches_a_mutating_operation(monkeypatch, arguments):
    monkeypatch.setattr(prepare.subprocess, 'run', lambda *a, **kw: pytest.fail('must not execute'))
    with pytest.raises(ValueError, match='read-only-operation'):
        prepare.protected_migration_check(arguments, expected={})


def test_source_cli_redacts_unsafe_os_errors(monkeypatch, capsys):
    def fail(**kwargs):
        raise PermissionError('private-path-or-secret')
    monkeypatch.setattr(installer, 'migration_source_proof', fail)
    assert installer.main(['verify-migration-source', '--owner', 'owner',
        '--current-bundle-digest', 'a' * 64]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert output.err == 'error: native-migration-source-verification-failed\n'


@pytest.mark.parametrize('nonroot', [False, True])
def test_final_proof_runs_existing_complete_planner_without_activation(monkeypatch, capsys, nonroot):
    monkeypatch.setattr(installer.sys, 'platform', 'darwin')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 501 if nonroot else 0)
    events = []
    def plan(**kwargs):
        events.append('complete-planner')
        return {'source_bytes': b'private-plist', 'migration_source_config_bytes': b'private-config'}
    monkeypatch.setattr(installer, 'make_migration_plan', plan)
    monkeypatch.setattr(installer, 'migrate_install', lambda *a: pytest.fail('must not activate'))
    argv = ['migrate-native', '--verify-preparation']
    for key, value in {'source': '/source', 'install-dir': '/ods', 'owner': 'owner', 'candidate': '/candidate',
            'runtime-bundle': '/runtime', 'bundle-digest': 'b' * 64, 'current-bundle-digest': 'a' * 64,
            'services-bundle': '/services', 'services-digest': 'c' * 64, 'pixel-source-ref': 'd' * 40}.items():
        argv.extend(['--' + key, value])
    assert installer.main(argv) == int(nonroot)
    output = capsys.readouterr()
    assert 'private-' not in output.out + output.err
    if nonroot:
        assert not events
    else:
        result = json.loads(output.out)
        assert events == ['complete-planner'] and result['status'] == 'planned'
        assert result['sourceDefinitionSha256'] == hashlib.sha256(b'private-plist').hexdigest()
        assert result['sourceConfigSha256'] == hashlib.sha256(b'private-config').hexdigest()
