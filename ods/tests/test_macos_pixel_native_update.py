import importlib.util
import json
import os
import shutil
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
        '--prompt-for-sudo', '--prepare-only']
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
                # The example step prints nothing; its outcome is only in the
                # update's private folder. The fixture preparation has no
                # candidate profile to name a workspace.
                assert 'exampleProjects' not in result and 'example' not in json.dumps(result).lower()
                logs = list((installed / 'data/pixel-native').glob('update-*/example-projects.log'))
                assert len(logs) == 1 and (logs[0].stat().st_mode & 0o777) == 0o600
                logged = json.loads(logs[0].read_text())
                # The cause is kept for diagnosis, privately.
                assert logged['status'] == 'manual-review-required' and logged['cause'].startswith('FileNotFoundError')
            else:
                assert result['status'] == 'prepared'
            assert stages == expected
    finally:
        assert dict(os.environ) == before


@pytest.mark.parametrize('code', sorted(module.SANDBOX_ERRORS))
def test_update_reports_sandbox_image_guidance(monkeypatch, capsys, code):
    # An update rebuilds the sandbox image before activation. Its failure gives
    # the cause, an update-only recovery and the help link; the trailer that
    # follows forbids a reinstall, so the guidance must never suggest one.
    def fail(**kwargs):
        raise ValueError(code)
    monkeypatch.setattr(module, 'update', fail)
    monkeypatch.setattr(module.sys, 'argv', ['pixel-native-update.py',
        '--install-dir', '/unused', '--ods-source', '/unused'])
    assert module.main() == 1
    err = capsys.readouterr().err
    assert module.SANDBOX_GUIDANCE[code] in err
    assert 'The installed Pixel keeps running unchanged.' in err
    assert 'https://discord.gg/4ntNp9MAwC' in err
    assert 'installer' not in err.lower() and 'reinstall' not in err.split('Native update stopped')[0]
    assert 'Do not reinstall or delete state.' in err
    assert err.index(module.SANDBOX_GUIDANCE[code]) < err.index('Native update stopped (ValueError)')
    if code != 'invalid-sandbox-node-runtime':
        assert 'run the update again' in module.SANDBOX_GUIDANCE[code]
    if code == 'native-sandbox-execution-proof-failed':
        assert 'holds no Pixel state' in err


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
    guidance_calls = []
    guidance_result = {'AGENTS.md': 'migrated', 'MEMORY.md': 'migrated'}
    def guidance(path):
        guidance_calls.append(path)
        assert calls  # The protected activation's identity stage already ran.
        return guidance_result
    monkeypatch.setattr(module, 'helper', lambda _name: SimpleNamespace(
        private_json=lambda _path: document, migrate_workspace_guidance=guidance))
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='Portal profile: SOUL.md current; IDENTITY.md current\n')
    monkeypatch.setattr(module.subprocess, 'run', run)
    result = module.migrate_public_identity(source=source, preparation=preparation, node=Path('/node'))
    assert result == {'status': 'checked', 'detail':
        'Portal profile: SOUL.md current; IDENTITY.md current', 'workspaceGuidance': guidance_result}
    assert guidance_calls == [workspace]
    assert calls == [(['/node', str(script), str(workspace), str(generated)],
        {'capture_output': True, 'text': True, 'timeout': 30, 'check': False})]
    guidance_result['AGENTS.md'] = 'manual-review-required'
    assert module.migrate_public_identity(source=source, preparation=preparation, node=Path('/node'))['status'] == 'manual-review-required'


def test_native_update_does_not_claim_profile_migration_without_candidate(tmp_path):
    assert module.migrate_public_identity(source=tmp_path, preparation=tmp_path, node=Path('/node')) == {
        'status': 'manual-review-required'}


@pytest.mark.skipif(os.name != 'posix' or os.geteuid() == 0, reason='the copy runs as a non-root POSIX workspace owner')
def test_native_update_adds_example_projects_to_the_named_owner_workspace(tmp_path, monkeypatch):
    state = tmp_path.resolve() / 'state'
    workspace = state / 'workspace-pixel'
    workspace.mkdir(parents=True, mode=0o700)
    for folder in (state, workspace):
        folder.chmod(0o700)
    preparation = tmp_path.resolve() / 'preparation'
    document = {'agents': {'list': [{'id': 'pixel', 'workspace': str(workspace)}]}}
    paths = []
    def private_json(path):
        paths.append(path)
        return document
    monkeypatch.setattr(module, 'helper', lambda _name: SimpleNamespace(private_json=private_json))
    assert module.add_example_projects(preparation=preparation, state_dir=state) == {'elf-of-doom': 'added'}
    assert paths == [preparation / 'candidate/openclaw.json']
    assert (workspace / 'Playground/elf-of-doom/index.html').is_file()
    # The record is in the state folder, never in the workspace.
    assert [path.name for path in workspace.iterdir()] == ['Playground']
    assert len(list((state / '.ods-example-projects').iterdir())) == 1
    # The workspace has received it: a later update never adds it again, even
    # after the owner deleted the folder to remove it.
    assert module.add_example_projects(preparation=preparation, state_dir=state) == {
        'elf-of-doom': 'previously-received'}
    shutil.rmtree(workspace / 'Playground/elf-of-doom')
    assert module.add_example_projects(preparation=preparation, state_dir=state) == {
        'elf-of-doom': 'previously-received'}
    assert not (workspace / 'Playground/elf-of-doom').exists()
    document['agents']['list'].append({'id': 'other', 'workspace': str(workspace)})
    outcome = module.add_example_projects(preparation=preparation, state_dir=state)
    assert outcome == {'status': 'manual-review-required', 'cause': 'ValueError: native-portal-profile-agent-required'}


@pytest.mark.skipif(os.name != 'posix' or os.geteuid() == 0, reason='the copy runs as a non-root POSIX workspace owner')
def test_native_update_keeps_the_helper_notes_off_the_terminal(tmp_path, monkeypatch, capfd):
    # The helper writes notes to stderr (a leftover it could not remove, a
    # record it could not write). The update captures them for its private
    # log; `ods-macos.sh update-pixel` shows that terminal to the owner.
    state = tmp_path.resolve() / 'state'
    workspace = state / 'workspace-pixel'
    workspace.mkdir(parents=True, mode=0o700)
    for folder in (state, workspace):
        folder.chmod(0o700)
    document = {'agents': {'list': [{'id': 'pixel', 'workspace': str(workspace)}]}}
    monkeypatch.setattr(module, 'helper', lambda _name: SimpleNamespace(private_json=lambda _path: document))
    # A state folder this owner cannot write: the copy succeeds, its record
    # cannot, and the helper says so on stderr.
    state.chmod(0o500)
    try:
        outcome = module.add_example_projects(preparation=tmp_path, state_dir=state)
    finally:
        state.chmod(0o700)
    assert capfd.readouterr() == ('', '')
    assert outcome['elf-of-doom'] == 'added'
    assert len(outcome['notes']) == 1
    assert outcome['notes'][0].startswith('could not record the added example projects: PermissionError')
    module.log_example_projects(tmp_path, outcome)
    assert json.loads((tmp_path / 'example-projects.log').read_text()) == outcome
    assert capfd.readouterr() == ('', '')


def test_native_update_does_not_claim_example_projects_without_candidate(tmp_path):
    outcome = module.add_example_projects(preparation=tmp_path, state_dir=tmp_path)
    assert outcome['status'] == 'manual-review-required' and outcome['cause'].startswith('FileNotFoundError')


@pytest.mark.parametrize('document', [
    {'agents': {'list': ['pixel']}}, {'agents': {'list': [None]}}, {'agents': {'list': 'pixel'}},
    {'agents': ['pixel']}, ['agents'], {'agents': {'list': [{'id': 'pixel', 'workspace': 7}]}},
])
def test_a_malformed_candidate_profile_is_manual_review_not_a_crash(monkeypatch, tmp_path, document):
    # A non-object agent entry once raised AttributeError after activation had
    # already succeeded, losing the update outcome.
    monkeypatch.setattr(module, 'helper', lambda _name: SimpleNamespace(private_json=lambda _path: document))
    assert module.add_example_projects(preparation=tmp_path, state_dir=tmp_path)['status'] == 'manual-review-required'
    assert module.migrate_public_identity(source=tmp_path, preparation=tmp_path, node=Path('/node')) == {
        'status': 'manual-review-required'}
