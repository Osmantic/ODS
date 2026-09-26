import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('native_update',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-update.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.fixture
def docker_endpoint():
    # Darwin's default pytest temp path can exceed sockaddr_un.sun_path.
    with tempfile.TemporaryDirectory(prefix='ods-update-', dir='/tmp') as directory:
        endpoint = Path(directory).resolve() / 'docker.sock'
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(endpoint))
            yield endpoint


def test_cli_preserves_update_options_without_license_flag():
    script = (Path(__file__).resolve().parents[1] / 'installers/macos/ods-macos.sh').read_text()
    function = script[script.index('cmd_update_pixel() {'):script.index('\ncmd_update() {')]
    function = function.replace('/usr/bin/python3', 'python_fixture')
    shell = '''set -eu
test_install() { :; }
ai_err() { echo "$*" >&2; }
python_fixture() { printf '%s\\n' "$@"; }
''' + function + '\ncmd_update_pixel --prepare-only\n'
    result = subprocess.run(['/bin/bash', '-c', shell], capture_output=True, text=True,
        env={**os.environ, 'INSTALL_DIR': '/owner/ODS with spaces'})
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        '/owner/ODS with spaces/installers/macos/lib/pixel-native-update.py',
        '--install-dir', '/owner/ODS with spaces', '--ods-source', '/owner/ODS with spaces',
        '--prepare-only']
    assert 'update-pixel) cmd_update_pixel "$@" ;;' in script


@pytest.mark.parametrize('failure', [None, 'prepare-only', 'acquire', 'prepare', 'activate', 'finalize'])
def test_update_orders_existing_helpers_and_restores_docker_environment(tmp_path, monkeypatch, failure, docker_endpoint):
    installed, source = tmp_path / 'ods', tmp_path / 'source'
    (installed / 'data/pixel-native').mkdir(parents=True)
    source.mkdir()
    endpoint = docker_endpoint
    stages = []
    old = 'a' * 64
    def step(name):
        assert os.environ['DOCKER_HOST'] == 'unix://' + str(endpoint)
        assert 'DOCKER_CONTEXT' not in os.environ
        stages.append(name)
        if failure == name:
            raise ValueError('fixture failure')
    def acquire(**kwargs):
        step('acquire')
        return kwargs['destination']
    def prepare(**kwargs):
        step('prepare')
        destination = kwargs['destination']
        destination.mkdir()
        (destination / 'preparation.json').write_text(json.dumps(dict(status='prepared',
            installDir=str(installed), currentDigest=old, runtimeDigest='b' * 64,
            serviceDigest='c' * 64, pixelSourceRef='d' * 40, gatewayPort=18789, accessPort=18790)))
    def activate(command, **kwargs):
        step('activate')
        assert command[:2] == ['/usr/bin/sudo', '/usr/bin/python3']
        assert command[3] == 'migrate-native'
        assert command[command.index('--current-bundle-digest') + 1] == old
        assert '--activate' in command
        assert command[command.index('--ingress-image') + 1] == 'sha256:' + 'e' * 64
    def finalize(preparation):
        step('finalize')
        assert (preparation / 'preparation.json').is_file()
        return {'status': 'selection-ready'}
    env = dict(DOCKER_HOST='unix://' + str(endpoint), PIXEL_HISTORY_DOCKER='/fixture/docker',
        PIXEL_HISTORY_PROJECT='ods', PIXEL_HISTORY_IMAGE='sha256:' + 'e' * 64, PIXEL_HISTORY_USER='501:20')
    modules = {
        'pixel-native-stack': SimpleNamespace(resolve_files=lambda *args: [],
            read_selection=lambda *args: ({'runtimeDigest': old}, {})),
        'pixel-macos-access-install': SimpleNamespace(_launchd=SimpleNamespace(GATEWAY_PLIST='/fixture/plist'),
            _source_gateway=lambda *args: ({}, env, None, None, Path('/runtime') / old / 'node', None),
            _native_transport_environment=lambda *args: {}),
        'pixel-native-install': SimpleNamespace(DEFAULT_REF='d' * 40, node_tools=lambda: ('node', 'npm')),
        'pixel-native-config': SimpleNamespace(bootstrap=SimpleNamespace(acquire_source=acquire,
            stage=lambda **kwargs: step('stage')), private_json=lambda path: json.loads(path.read_text())),
        'pixel-native-prepare': SimpleNamespace(prepare_migration=prepare),
        'pixel-native-finalize': SimpleNamespace(finalize_update=finalize),
    }
    monkeypatch.setattr(module, 'helper', modules.__getitem__)
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 501)
    monkeypatch.setattr(module.shutil, 'which', lambda value: value)
    monkeypatch.setattr(module.subprocess, 'run', activate)
    monkeypatch.setenv('DOCKER_CONTEXT', 'unrelated-remote-context')
    monkeypatch.setenv('DOCKER_HOST', 'tcp://unrelated.invalid:2375')
    before = dict(os.environ)
    try:
        arguments = dict(install_dir=installed, ods_source=source,
            prepare_only=failure == 'prepare-only')
        if failure not in (None, 'prepare-only'):
            with pytest.raises(ValueError): module.update(**arguments)
            assert stages[-1] == failure
        else:
            result = module.update(**arguments)
            expected = ['acquire', 'stage', 'prepare']
            if failure is None:
                expected += ['activate', 'finalize']
                assert result['status'] == 'selection-ready'
                assert result['portalIdentityMigration']['status'] == 'manual-review-required'
                assert result['retiredWorkspaceText']['status'] == 'manual-review-required'
            else:
                assert result['status'] == 'prepared'
            assert stages == expected
    finally:
        assert dict(os.environ) == before


def test_update_reaches_installation_validation_without_license_flag(monkeypatch):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 501)
    with pytest.raises(FileNotFoundError):
        module.update(install_dir='/missing', ods_source='/missing')


def test_native_update_checks_owner_profile_after_activation(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    script = source / 'scripts/migrate-portal-identity.mjs'
    script.parent.mkdir(parents=True)
    script.write_text('// tested separately by the bundled source suite\n')
    preparation = tmp_path / 'preparation'
    generated = preparation / 'candidate/workspace'
    generated.mkdir(parents=True)
    workspace = tmp_path / 'owner-workspace'
    workspace.mkdir()
    document = {'agents': {'list': [{'id': 'pixel', 'workspace': str(workspace)}]}}
    monkeypatch.setattr(module, 'helper', lambda _name: SimpleNamespace(private_json=lambda _path: document))
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='Portal profile: SOUL.md current; IDENTITY.md current\n')
    monkeypatch.setattr(module.subprocess, 'run', run)
    result = module.migrate_public_identity(source=source, preparation=preparation, node=Path('/node'))
    assert result == {'status': 'checked', 'detail':
        'Portal profile: SOUL.md current; IDENTITY.md current'}
    assert calls == [(['/node', str(script), str(workspace), str(generated)],
        {'capture_output': True, 'text': True, 'timeout': 30, 'check': False})]


def test_native_update_does_not_claim_profile_migration_without_candidate(tmp_path):
    assert module.migrate_public_identity(source=tmp_path, preparation=tmp_path, node=Path('/node')) == {
        'status': 'manual-review-required'}


def _retired_text_fixture(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    script = source / 'scripts/migrate-retired-workspace-text.mjs'
    script.parent.mkdir(parents=True)
    script.write_text('// tested separately by the bundled source suite\n')
    preparation = tmp_path / 'preparation'
    (preparation / 'candidate').mkdir(parents=True)
    state = tmp_path / 'owner-home/.openclaw'
    workspace = state / 'workspace-pixel'
    workspace.mkdir(parents=True)
    document = {'agents': {'list': [{'id': 'pixel', 'workspace': str(workspace)}]}}
    monkeypatch.setattr(module, 'helper', lambda _name: SimpleNamespace(private_json=lambda _path: document))
    return source, script, preparation, workspace, state


def test_native_update_removes_retired_workspace_text_after_activation(tmp_path, monkeypatch, capsys):
    source, script, preparation, workspace, state = _retired_text_fixture(tmp_path, monkeypatch)
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0,
            stdout='Retired workspace text: AGENTS.md current\nRetired workspace text: MEMORY.md current\n')
    monkeypatch.setattr(module.subprocess, 'run', run)
    result = module.remove_retired_workspace_text(source=source, preparation=preparation, node=Path('/node'),
        state_dir=str(state))
    assert result == {'status': 'checked', 'detail':
        'Retired workspace text: AGENTS.md current\nRetired workspace text: MEMORY.md current'}
    # Backups hold the removed text, so they go to the OpenClaw state directory, not the workspace.
    assert calls == [(['/node', str(script), str(workspace), str(state / 'backups/retired-workspace-text')],
        {'capture_output': True, 'text': True, 'timeout': 30, 'check': False})]
    assert capsys.readouterr().err == ''


def test_native_update_warns_when_retired_text_needs_review(tmp_path, monkeypatch, capsys):
    source, script, preparation, _workspace, state = _retired_text_fixture(tmp_path, monkeypatch)
    review = ('Retired workspace text: AGENTS.md modified retired section present; review\n'
        'Retired workspace text: MEMORY.md skipped-backup-conflict (backup /b/MEMORY.md.bak); review')
    monkeypatch.setattr(module.subprocess, 'run',
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=review + '\n'))
    assert module.remove_retired_workspace_text(source=source, preparation=preparation, node=Path('/node'),
        state_dir=str(state)) == {'status': 'manual-review-required', 'detail': review}
    assert review in capsys.readouterr().err
    for state_dir in (None, 'relative/state', str(tmp_path / 'missing-state')):
        assert module.remove_retired_workspace_text(source=source, preparation=preparation, node=Path('/node'),
            state_dir=state_dir)['status'] == 'manual-review-required'
    script.unlink()
    assert module.remove_retired_workspace_text(source=source, preparation=preparation, node=Path('/node'),
        state_dir=str(state)) == {'status': 'manual-review-required',
        'detail': 'Retired workspace text: not checked (ValueError)'}
    assert 'not checked (ValueError)' in capsys.readouterr().err
