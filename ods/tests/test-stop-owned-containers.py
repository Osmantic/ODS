"""Recovery must stop a rejected runtime without evaluating its Compose file."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ODS = Path(__file__).resolve().parents[1]
HELPER = ODS / 'scripts/stop-owned-containers.py'


def load_helper():
    spec = importlib.util.spec_from_file_location('stop_owned', HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_docker_metadata_uses_utf8_independently_of_windows_codepage(monkeypatch):
    module = load_helper()
    actual_run = subprocess.run
    # Docker emits UTF-8, even when the host process defaults to a legacy Windows
    # code page. Exercise real subprocess decoding, not pre-decoded JSON mocks.
    metadata = json.dumps({'path': 'C:/Users/Jos\u00e9/\u6a21\u578b'}, ensure_ascii=False)
    command = [sys.executable, '-c',
               f'import sys; sys.stdout.buffer.write({metadata!r}.encode("utf-8"))']
    monkeypatch.setattr(subprocess, '_text_encoding', lambda: 'cp1252')
    monkeypatch.setattr(subprocess, 'run', lambda args, **kwargs: actual_run(command, **kwargs))
    assert module._docker(['inspect', 'a' * 64]) == metadata


def container(root, ident='a', service='example', project='ods'):
    return {'Id': ident * 64, 'Config': {'Labels': {
        'com.docker.compose.project': project,
        'com.docker.compose.project.working_dir': str(root),
        'com.docker.compose.project.config_files': str(root / 'docker-compose.base.yml'),
        'com.docker.compose.service': service,
    }}}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    root = tmp_path / 'ODS caf\u00e9 \u6a21'
    root.mkdir()
    canary = root / 'data.txt'
    canary.write_text('preserve owner data')
    module = load_helper()
    rows = [container(root), container(root, 'b', 'example-db'),
            container(tmp_path / 'other-install', 'c'), container(root, 'd', 'dashboard-api')]
    calls = []

    def docker(args, timeout=30):
        calls.append(args)
        if args[0] == 'ps':
            return '\n'.join(row['Id'] for row in rows)
        if args[0] == 'inspect':
            return json.dumps(rows)
        assert args[0] in ('update', 'stop'), args
        return ''

    monkeypatch.setattr(module, '_docker', docker)
    return module, root, rows, calls


def test_service_recovery_preserves_other_installations_core_and_data(fixture):
    module, root, _, calls = fixture
    assert module.stop_owned_containers(root, ['example']) == ['a' * 64]
    assert calls[-2:] == [['update', '--restart=no', 'a' * 64], ['stop', 'a' * 64]]
    assert (root / 'data.txt').read_text() == 'preserve owner data'


def test_extension_companions_need_explicit_service_targets(fixture):
    module, root, _, _ = fixture
    assert module.stop_owned_containers(root, ['example', 'example-db']) == ['a' * 64, 'b' * 64]


def test_whole_install_recovery_does_not_trust_shared_project_name(fixture):
    module, root, _, calls = fixture
    assert module.stop_owned_containers(root) == ['a' * 64, 'b' * 64, 'd' * 64]
    assert all('c' * 64 not in call for call in calls if call[0] in ('update', 'stop'))


@pytest.mark.parametrize('key,value', [
    ('com.docker.compose.project.working_dir', 'relative/root'),
    ('com.docker.compose.project.config_files', '/other/docker-compose.base.yml'),
    ('com.docker.compose.project.config_files', 'docker-compose.base.yml'),
    ('com.docker.compose.project', ''),
    ('com.docker.compose.service', 'example-other'),
    ('com.docker.compose.service', None),
])
def test_unproven_ownership_is_never_stopped(fixture, key, value):
    module, root, rows, calls = fixture
    rows[0]['Config']['Labels'][key] = value
    assert module.stop_owned_containers(root, ['example']) == []
    assert not any(call[0] in ('update', 'stop') for call in calls)


@pytest.mark.parametrize('service', ['--all', '../dashboard-api', 'example; touch canary', 'example\nother'])
def test_invalid_service_never_reaches_docker(fixture, service):
    module, root, _, calls = fixture
    with pytest.raises(ValueError):
        module.stop_owned_containers(root, [service])
    assert calls == []


def test_inspection_error_preserves_every_container(fixture, monkeypatch):
    module, root, _, calls = fixture
    real = module._docker

    def broken(args, timeout=30):
        if args[0] == 'inspect':
            return '[]'
        return real(args, timeout)

    monkeypatch.setattr(module, '_docker', broken)
    with pytest.raises(ValueError, match='inspection'):
        module.stop_owned_containers(root)
    assert not any(call[0] in ('update', 'stop') for call in calls)


def test_bad_recipe_bytes_are_not_read_or_executed(fixture):
    module, root, _, _ = fixture
    recipe = root / 'docker-compose.base.yml'
    recipe.write_text('malformed YAML: [\ninclude: untrusted.yaml\npre_stop: delete-data')
    assert module.stop_owned_containers(root, ['example']) == ['a' * 64]
    assert 'delete-data' in recipe.read_text()


def shell_function(path, name):
    source = path.read_text(encoding='utf-8')
    return name + '() {' + source.split(name + '() {', 1)[1].split('\n}\n', 1)[0] + '\n}\n'


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX CLI execution')
@pytest.mark.parametrize('platform', ['linux', 'macos'])
def test_cli_stop_routes_failed_validation_to_owned_recovery(tmp_path, platform):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    # The recovery policy is tested above; this fixture records the actual CLI
    # routing and fails if it ever executes Compose after validation refused it.
    (scripts / HELPER.name).write_text('import sys; print("RECOVER", repr(sys.argv[1:]))')
    common = ('set -eu\nINSTALL_DIR="$1"\nODS_PYTHON_CMD="$2"\nLLAMA_SERVER_PID_FILE="$1/no-pid"\n'
              'check_install() { :; }; test_install() { :; }; load_env() { :; }\n'
              'get_compose_flags() { return 2; }; resolve_service() { echo "$1"; }\n'
              'docker() { echo UNEXPECTED_COMPOSE; return 99; }\n'
              'warn() { :; }; ai() { :; }; stop_native_llama() { echo NATIVE_STOP; }\n')
    if platform == 'linux':
        common += shell_function(ODS / 'ods-cli', '_stop_owned_containers_for_recovery')
        common += shell_function(ODS / 'ods-cli', 'cmd_stop')
    else:
        common += shell_function(ODS / 'installers/macos/ods-macos.sh', 'cmd_stop')
    result = subprocess.run(['bash', '-s', '--', str(tmp_path), sys.executable],
                            input=common + '\ncmd_stop example\n', text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "'--service', 'example'" in result.stdout
    assert 'UNEXPECTED_COMPOSE' not in result.stdout


def test_host_agent_uses_recovery_only_for_stop(tmp_path):
    source = (ODS / 'bin/ods-host-agent.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'docker_compose_action')
    (tmp_path / 'scripts').mkdir()
    # The real host action imports the installed helper. No mocked success from
    # the normal resolver can hide bypasses on the start path.
    (tmp_path / 'scripts/stop-owned-containers.py').write_text(
        'def stop_owned_containers(root, services):\n'
        '    (root / "recovery.json").write_text(__import__("json").dumps(services))\n')

    def refused():
        raise ValueError('legacy source is unconfined')

    namespace = {'INSTALL_DIR': tmp_path, 'resolve_compose_flags': refused,
                 '_extension_stop_targets': lambda service: [service, service + '-db'],
                 'importlib': __import__('importlib'), 'subprocess': subprocess}
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<host-action>', 'exec'), namespace)
    assert namespace['docker_compose_action']('example', 'start') == (False, 'legacy source is unconfined')
    assert not (tmp_path / 'recovery.json').exists()
    assert namespace['docker_compose_action']('example', 'stop') == (True, '')
    assert json.loads((tmp_path / 'recovery.json').read_text()) == ['example', 'example-db']


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX Linux CLI execution')
@pytest.mark.parametrize('recovery_succeeds', [False, True])
def test_disable_renames_recipe_only_after_safe_recovery(tmp_path, recovery_succeeds):
    bash = 'bash'
    for candidate in ('bash', '/opt/homebrew/bin/bash', '/usr/local/bin/bash'):
        if not shutil.which(candidate):
            continue
        version = subprocess.run([candidate, '-c', 'echo "${BASH_VERSINFO[0]}"'], capture_output=True, text=True)
        if version.returncode == 0 and int(version.stdout.strip()) >= 4:
            bash = candidate
            break
    else:
        pytest.skip('Linux CLI requires Bash 4+')
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    (scripts / HELPER.name).write_text('raise SystemExit(' + ('0' if recovery_succeeds else '1') + ')')
    recipe = tmp_path / 'data/user-extensions/example/compose.yaml'
    recipe.parent.mkdir(parents=True)
    recipe.write_text('legacy unconfined source recipe')
    common = ('set -eu\nINSTALL_DIR="$1"\nODS_PYTHON_CMD="$2"\n'
              'check_install() { :; }; load_env() { :; }; sr_load() { :; }\n'
              'get_compose_flags() { return 2; }; sr_resolve() { echo "$1"; }\n'
              'declare -A SERVICE_CATEGORIES=([example]=optional); SERVICE_IDS=(example)\n'
              'docker() { echo UNEXPECTED_COMPOSE; return 99; }\n'
              'warn() { :; }; success() { :; }; error() { echo "$*" >&2; exit 1; }\n'
              '_regenerate_compose_flags() { echo regenerated > "$INSTALL_DIR/flags"; }\n')
    common += shell_function(ODS / 'ods-cli', '_stop_owned_containers_for_recovery')
    common += shell_function(ODS / 'ods-cli', 'cmd_disable')
    result = subprocess.run([bash, '-s', '--', str(tmp_path), sys.executable],
                            input=common + '\ncmd_disable example\n', text=True, capture_output=True, timeout=15)
    assert (result.returncode == 0) == recovery_succeeds, result.stderr
    assert 'UNEXPECTED_COMPOSE' not in result.stdout
    assert recipe.exists() is not recovery_succeeds
    assert (tmp_path / 'flags').exists() is recovery_succeeds
    surviving = recipe.with_suffix('.yaml.disabled') if recovery_succeeds else recipe
    assert surviving.read_text() == 'legacy unconfined source recipe'


@pytest.mark.skipif(os.environ.get('ODS_RUN_DOCKER_SECURITY_TESTS') != '1',
                    reason='Requires an explicitly selected disposable Docker test host')
def test_real_docker_recovery_stops_only_the_owned_installation(tmp_path):
    module = load_helper()
    image = 'python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e'
    module._docker(['pull', image], timeout=300)
    roots = [tmp_path / 'owner', tmp_path / 'other']
    ids = []
    try:
        for root in roots:
            root.mkdir()
            (root / 'data.txt').write_text('keep')
            labels = container(root)['Config']['Labels']
            arguments = ['run', '-d', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                         '--security-opt', 'no-new-privileges', '--restart', 'always']
            for key, value in labels.items():
                arguments += ['--label', key + '=' + value]
            arguments += [image, 'python', '-c', 'import time; time.sleep(600)']
            ids.append(module._docker(arguments).strip())
        assert module.stop_owned_containers(roots[0], ['example']) == [ids[0]]
        states = json.loads(module._docker(['inspect', *ids]))
        assert states[0]['State']['Running'] is False
        assert states[0]['HostConfig']['RestartPolicy']['Name'] == 'no'
        assert states[1]['State']['Running'] is True
        assert states[1]['HostConfig']['RestartPolicy']['Name'] == 'always'
        assert all((root / 'data.txt').read_text() == 'keep' for root in roots)
    finally:
        if ids:
            module._docker(['rm', '-f', *ids])
