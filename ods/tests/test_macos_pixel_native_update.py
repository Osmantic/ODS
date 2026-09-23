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
        arguments = dict(install_dir=installed, ods_source=source, license_authorized=True,
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
