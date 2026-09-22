import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_install',
    ROOT / 'installers/macos/lib/pixel-native-install.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize('fault', [None, 'license', 'root', 'intel', 'linux', 'existing', 'partial', 'relative'])
def test_preflight_never_mutates_existing_installations(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(module.sys, 'platform', 'linux' if fault == 'linux' else 'darwin')
    monkeypatch.setattr(module.platform, 'machine', lambda: 'x86_64' if fault == 'intel' else 'arm64')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0 if fault == 'root' else 501)
    checked = []
    def exists(path):
        checked.append(str(path))
        return ((fault == 'existing' and str(path).endswith('pixel-access.json')) or
                (fault == 'partial' and str(path).endswith('pixel-native')))
    monkeypatch.setattr(module.os.path, 'lexists', exists)
    args = dict(install_dir='relative' if fault == 'relative' else tmp_path / 'ods',
        license_authorized=fault != 'license')
    if fault:
        with pytest.raises(ValueError): module.preflight(**args)
    else:
        assert module.preflight(**args) == tmp_path / 'ods'
        assert len(checked) == 3
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('fault', [None, 'ref', 'compose', 'remote', 'project', 'services', 'image', 'probe', 'prepare', 'activate'])
def test_initial_installer_connects_resolved_stack_and_native_activation(tmp_path, monkeypatch, fault):
    install_dir = tmp_path / 'ODS with spaces'
    (install_dir / 'data').mkdir(parents=True)
    files = [install_dir / relative for relative in ('docker-compose.base.yml', *module.FRAGMENTS)]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
    binary = tmp_path / 'docker'
    binary.touch()
    socket = tmp_path / 'socket'
    socket.touch()
    monkeypatch.delenv('DOCKER_HOST', raising=False)
    monkeypatch.setenv('DOCKER_CONTEXT', 'inherited-context')
    monkeypatch.setenv('DOCKER_TLS_VERIFY', '1')
    monkeypatch.setenv('DOCKER_CERT_PATH', '/unused-certificates')
    monkeypatch.setattr(module, 'preflight', lambda path, **kw: Path(path))
    monkeypatch.setattr(module.shutil, 'which', lambda name: str(binary))
    monkeypatch.setattr(module, 'node_tools', lambda: ('/node', '/npm'))
    events = []
    def command(argv, **kwargs):
        argv = list(map(str, argv))
        if 'context' in argv:
            events.append('context')
            return json.dumps([{'Endpoints': {'docker': {'Host': 'tcp://remote:2375' if fault == 'remote' else 'unix://' + str(socket)}}}])
        assert kwargs['env']['DOCKER_HOST'] == 'unix://' + str(socket)
        assert not {'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'} & kwargs['env'].keys()
        if 'compose' in argv:
            events.append('compose')
            assert argv[argv.index('--project-directory') + 1] == str(install_dir)
            return json.dumps({'name': 'INVALID!' if fault == 'project' else 'ods-fixture',
                'services': {} if fault == 'services' else dict.fromkeys(('dashboard-api', 'model-router', 'open-webui'), {})})
        if 'pull' in argv:
            events.append('pull')
            return ''
        if 'inspect' in argv:
            events.append('image')
            return json.dumps([{'Id': 'sha256:' + 'a' * 64, 'Architecture': 'amd64' if fault == 'image' else 'arm64', 'Os': 'linux'}])
        assert 'run' in argv and '--read-only' in argv and '--network' in argv
        events.append('probe')
        if fault == 'probe': raise ValueError('probe-failed')
        return ''
    monkeypatch.setattr(module, 'command', command)
    def prepare(**kwargs):
        events.append('prepare')
        assert kwargs['compose_project'] == 'ods-fixture'
        assert kwargs['license_authorized'] is True
        assert kwargs['ingress_image'] == 'sha256:' + 'a' * 64
        assert kwargs['native_home'] == install_dir / 'data/pixel-native/home'
        if fault == 'prepare': raise ValueError('prepare-failed')
    def activate(**kwargs):
        events.append('activate')
        assert kwargs['compose_files'] == files
        assert kwargs['configure_stack'] is True
        if fault == 'activate': raise ValueError('activate-failed')
    monkeypatch.setattr(module, 'helper', lambda name: SimpleNamespace(prepare=prepare, activate=activate))
    def run():
        return module.install(install_dir=install_dir, ods_source=install_dir,
            compose_files=[] if fault == 'compose' else files[:1],
            ref='invalid' if fault == 'ref' else module.DEFAULT_REF, license_authorized=True)
    if fault:
        with pytest.raises(ValueError): run()
        if fault not in ('prepare', 'activate'):
            assert not (install_dir / 'data/pixel-native').exists()
    else:
        assert run() == install_dir / 'data/pixel-native/preparation'
        assert events == ['context', 'compose', 'pull', 'image', 'probe', 'prepare', 'activate']


def test_main_shell_routes_pixel_only_after_base_launch_and_before_flag_persistence():
    script = (ROOT / 'installers/macos/install-macos.sh').read_text()
    assert '--pixel)' in script and '--no-pixel)' in script
    assert script.index('--preflight-only') < script.index('# PHASE 1')
    launch = script.index('"$LIB_DIR/pixel-native-install.py" "${_pixel_install_args[@]}"')
    assert script.index('compose_exit="${PIPESTATUS[0]}"') < launch
    assert launch < script.index('echo "${COMPOSE_FLAGS[*]}" > "${INSTALL_DIR}/.compose-flags"')
    shared = (ROOT / 'installers/phases/06-directories.sh').read_text()
    assert module.DEFAULT_REF in shared


@pytest.mark.parametrize('pixel', ['true', 'false'])
def test_core_feature_selection_keeps_pixel_dependencies_without_heavy_services(pixel):
    script = (ROOT / 'installers/macos/install-macos.sh').read_text()
    start = script.index('if ! $NON_INTERACTIVE && ! $ALL_FEATURES && ! $DRY_RUN; then')
    stop = script.index('ai "Features:"', start)
    shell = '''set -eu
NON_INTERACTIVE=true; ALL_FEATURES=false; DRY_RUN=false
CLOUD_MODE=false; ENABLE_RECOMMENDED=false
ENABLE_HERMES=false; ENABLE_OPENCLAW=false; ENABLE_APE=false
ENABLE_PERPLEXICA=false; ENABLE_VOICE=false; ENABLE_RAG=false; ENABLE_WORKFLOWS=false
''' + 'ENABLE_PIXEL=' + pixel + '\n' + script[start:stop] + '''
printf '%s %s %s %s %s %s %s' "$ENABLE_RECOMMENDED" "$ENABLE_SEARXNG" "$ENABLE_HERMES" "$ENABLE_OPENCLAW" "$ENABLE_VOICE" "$ENABLE_RAG" "$ENABLE_WORKFLOWS"
'''
    result = subprocess.run(['bash'], input=shell, capture_output=True, text=True, check=True)
    assert result.stdout == ' '.join([pixel, pixel, 'false', 'false', 'false', 'false', 'false'])


@pytest.mark.parametrize('mode', ['direct', 'volta', 'brew', 'missing-brew', 'bad-brew'])
def test_node_selection_uses_native_executable_and_qualified_npm(tmp_path, monkeypatch, mode):
    node, npm, shim = [tmp_path / name for name in ('node', 'npm', 'volta-shim')]
    for path in (node, npm, shim): path.touch()
    npm_link = tmp_path / 'npm-link'
    npm_link.symlink_to(shim)
    tools = {'node': str(node), 'npm': str(npm_link if mode == 'volta' else npm),
        'volta': '/volta', 'brew': None if mode == 'missing-brew' else '/brew'}
    monkeypatch.setattr(module.shutil, 'which', tools.get)
    events = []
    installed = []
    def command(argv, **kwargs):
        argv = list(map(str, argv))
        events.append(argv)
        if argv[0] == '/brew':
            if argv[1] == 'install':
                installed.append(True)
                return ''
            return str(tmp_path)
        if argv[0] == '/volta':
            assert argv == ['/volta', 'which', 'npm']
            return str(npm)
        if '-p' in argv:
            valid = mode in ('direct', 'volta') or (bool(installed) and mode != 'bad-brew')
            return json.dumps({'platform': 'darwin', 'arch': 'arm64' if valid else 'x64',
                'major': 24, 'execPath': str(node)})
        if argv[-1] == '--version':
            assert argv[0] == str(npm)
            assert kwargs['env']['PATH'].startswith(str(node.parent) + ':')
            return '11.0.0'
        raise AssertionError(argv)
    # The brew prefix exposes npm under bin, as a real Homebrew installation does.
    (tmp_path / 'bin').mkdir()
    (tmp_path / 'bin/npm').symlink_to(npm)
    monkeypatch.setattr(module, 'command', command)
    if mode in ('missing-brew', 'bad-brew'):
        with pytest.raises(ValueError): module.node_tools()
    else:
        assert module.node_tools() == (node, npm)
    assert bool(installed) == (mode in ('brew', 'bad-brew'))


@pytest.mark.parametrize('state', ['absent', 'existing', 'broken-link'])
def test_base_reinstall_stops_before_changing_a_native_installation(tmp_path, state):
    native = tmp_path / 'data/pixel-native'
    native.parent.mkdir()
    if state == 'existing':
        native.mkdir()
        (native / 'owner-data').write_text('keep exactly')
    elif state == 'broken-link':
        native.symlink_to(tmp_path / 'missing')
    script = (ROOT / 'installers/macos/install-macos.sh').read_text()
    start = script.index('if ! $ENABLE_PIXEL && [[ -e "${INSTALL_DIR}/data/pixel-native"')
    stop = script.index('\nif $ENABLE_PIXEL; then', start)
    assert stop < script.index('# Initialize log file')
    shell = 'set -euo pipefail\nai_err() { echo "$*" >&2; }\nai() { echo "$*"; }\n' + script[start:stop] + '\nprintf reached-base-install\n'
    result = subprocess.run(['bash'], input=shell, text=True, capture_output=True,
        env={**os.environ, 'ENABLE_PIXEL': 'false', 'INSTALL_DIR': str(tmp_path)})
    if state == 'absent':
        assert result.returncode == 0 and result.stdout == 'reached-base-install'
    else:
        assert result.returncode != 0 and 'reached-base-install' not in result.stdout
        assert 'Existing native Pixel installation detected' in result.stderr
        if state == 'existing': assert (native / 'owner-data').read_text() == 'keep exactly'
        else: assert native.is_symlink()
