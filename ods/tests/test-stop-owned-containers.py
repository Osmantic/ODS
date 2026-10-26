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
    return {'Id': ident * 64, 'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped'}},
            'Config': {'Labels': {
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


def test_preset_stop_preserves_restart_policy_and_owner_data(fixture):
    module, root, _, calls = fixture
    assert module.stop_owned_containers(root, ['example'], preserve_restart_policy=True) == ['a' * 64]
    assert calls[-1] == ['stop', 'a' * 64]
    assert not any(call[0] == 'update' for call in calls)
    assert (root / 'data.txt').read_text() == 'preserve owner data'


def test_preset_stop_refuses_always_policy_before_any_stop(fixture):
    module, root, rows, calls = fixture
    rows[0]['HostConfig']['RestartPolicy']['Name'] = 'always'
    with pytest.raises(ValueError, match='Unsupported restart policy'):
        module.stop_owned_containers(root, ['example'], preserve_restart_policy=True)
    assert not any(call[0] in ('update', 'stop') for call in calls)


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


# Remove deletes a removed extension's stopped containers: nothing manages
# them once its definition is gone (ods-excalidraw stayed behind on the RTX
# Spark laptop, 2026-10-25). Its data stays unless the owner chose otherwise.

@pytest.fixture
def removal(tmp_path, monkeypatch):
    root = tmp_path / 'ODS café 模'
    root.mkdir()
    module = load_helper()
    rows = []
    for ident, service, status, mounts in (
        ('a', 'example', 'exited', [{'Type': 'volume', 'Name': 'ods_example-data'},
                                    {'Type': 'volume', 'Name': 'f' * 64},
                                    {'Type': 'bind', 'Source': str(root / 'data/example')}]),
        ('b', 'example-db', 'exited', [{'Type': 'volume', 'Name': 'ods_shared-cache'},
                                       {'Type': 'volume', 'Name': 'elsewhere_data'}]),
        ('d', 'dashboard-api', 'running', []),
    ):
        row = container(root, ident, service)
        row.update(Name='/ods-' + service, State={'Status': status}, Mounts=mounts)
        rows.append(row)
    other = container(tmp_path / 'other-install', 'c', 'example')
    other.update(Name='/other-example', State={'Status': 'exited'}, Mounts=[])
    rows.append(other)
    volumes = {
        'ods_example-data': {'com.docker.compose.project': 'ods', 'com.docker.compose.volume': 'example-data'},
        'ods_shared-cache': {'com.docker.compose.project': 'ods', 'com.docker.compose.volume': 'shared-cache'},
        'elsewhere_data': {'com.docker.compose.project': 'elsewhere', 'com.docker.compose.volume': 'data'},
        'f' * 64: {'com.docker.volume.anonymous': ''},
    }
    users = {'ods_shared-cache': 'e' * 64}
    calls = []

    def docker(args, timeout=30):
        calls.append(args)
        if args[:2] == ['ps', '--all'] and '--filter' in args and args[-1].startswith('volume='):
            return users.get(args[-1].split('=', 1)[1], '')
        if args[0] == 'ps':
            return '\n'.join(row['Id'] for row in rows)
        if args[0] == 'inspect':
            return json.dumps(rows)
        if args[:2] == ['volume', 'inspect']:
            return json.dumps([{'Name': args[2], 'Labels': volumes[args[2]]}])
        assert args[0] == 'rm' or args[:2] == ['volume', 'rm'], args
        return ''

    monkeypatch.setattr(module, '_docker', docker)
    return module, root, rows, calls


def deletions(calls):
    return [call for call in calls if call[0] == 'rm' or call[:2] == ['volume', 'rm']]


def test_removal_deletes_stopped_containers_and_keeps_every_volume(removal):
    module, root, _, calls = removal
    names, removed, kept = module.remove_owned_containers(root, ['example', 'example-db'])
    assert names == ['ods-example', 'ods-example-db']
    assert removed == []
    # Compose's named volumes of this project; the anonymous one and another
    # project's volume are not this extension's data.
    assert kept == ['ods_example-data', 'ods_shared-cache']
    assert deletions(calls) == [['rm', 'a' * 64, 'b' * 64]]


def test_removal_with_data_deletes_only_unshared_project_volumes(removal):
    module, root, _, calls = removal
    names, removed, kept = module.remove_owned_containers(
        root, ['example', 'example-db'], remove_volumes=True)
    assert names == ['ods-example', 'ods-example-db']
    assert removed == ['ods_example-data']
    assert kept == ['ods_shared-cache']
    assert deletions(calls) == [['rm', '--volumes', 'a' * 64, 'b' * 64],
                                ['volume', 'rm', 'ods_example-data']]


def test_removal_refuses_a_running_container_before_deleting_anything(removal):
    module, root, rows, calls = removal
    rows[0]['State']['Status'] = 'running'
    with pytest.raises(ValueError, match='example container is running'):
        module.remove_owned_containers(root, ['example'], remove_volumes=True)
    assert deletions(calls) == []


@pytest.mark.parametrize('state', [None, {}, {'Status': 'restarting'}, {'Status': 'paused'}])
def test_removal_refuses_unproven_stopped_state(removal, state):
    module, root, rows, calls = removal
    rows[0]['State'] = state
    with pytest.raises(ValueError, match='stop it before removing it'):
        module.remove_owned_containers(root, ['example'])
    assert deletions(calls) == []


def test_removal_never_becomes_an_installation_wide_delete(removal):
    module, root, _, calls = removal
    with pytest.raises(ValueError, match='requires the services'):
        module.remove_owned_containers(root, [])
    assert calls == []


def test_removal_without_owned_containers_deletes_nothing(removal):
    module, root, _, calls = removal
    assert module.remove_owned_containers(root, ['missing']) == ([], [], [])
    assert deletions(calls) == []


@pytest.mark.parametrize('service', ['--all', '../dashboard-api', 'example; rm -rf /'])
def test_removal_rejects_invalid_services_before_docker(removal, service):
    module, root, _, calls = removal
    with pytest.raises(ValueError):
        module.remove_owned_containers(root, [service])
    assert calls == []


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
    shutil.copyfile(ODS / 'scripts/extension-selection.py', scripts / 'extension-selection.py')
    recipe = tmp_path / 'data/user-extensions/example/compose.yaml'
    recipe.parent.mkdir(parents=True)
    recipe.write_text('services: {}\n')
    cache = tmp_path / '.compose-flags'
    cache.write_text('stale')
    common = ('set -eu\nINSTALL_DIR="$1"\nODS_PYTHON_CMD="$2"\n'
              'check_install() { :; }; load_env() { :; }; sr_load() { :; }\n'
              'get_compose_flags() { return 2; }; sr_resolve() { echo "$1"; }\n'
              'declare -A SERVICE_CATEGORIES=([example]=optional); SERVICE_IDS=(example)\n'
              'declare -A SERVICE_TYPES=([example]=docker)\n'
              'docker() { echo UNEXPECTED_COMPOSE; return 99; }\n'
              'warn() { :; }; success() { :; }; error() { echo "$*" >&2; exit 1; }\n'
              '_regenerate_compose_flags() { echo regenerated > "$INSTALL_DIR/flags"; }\n')
    common += shell_function(ODS / 'ods-cli', '_stop_owned_containers_for_recovery')
    common += shell_function(ODS / 'ods-cli', '_ods_cli_reject_host_service')
    common += shell_function(ODS / 'ods-cli', 'cmd_disable')
    result = subprocess.run([bash, '-s', '--', str(tmp_path), sys.executable],
                            input=common + '\ncmd_disable example\n', text=True, capture_output=True, timeout=15)
    assert (result.returncode == 0) == recovery_succeeds, result.stderr
    assert 'UNEXPECTED_COMPOSE' not in result.stdout
    assert recipe.exists() is not recovery_succeeds
    assert cache.exists() is not recovery_succeeds
    surviving = recipe.with_suffix('.yaml.disabled') if recovery_succeeds else recipe
    assert surviving.read_text() == 'services: {}\n'


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX Linux CLI execution')
def test_disable_preserves_selection_when_compose_stop_fails(tmp_path):
    version = subprocess.run(['bash', '-c', 'echo "${BASH_VERSINFO[0]}"'], capture_output=True, text=True)
    if version.returncode != 0 or int(version.stdout.strip()) < 4:
        pytest.skip('Linux CLI requires Bash 4+')
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    shutil.copyfile(ODS / 'scripts/extension-selection.py', scripts / 'extension-selection.py')
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    fake_docker = fake_bin / 'docker'
    fake_docker.write_text('#!/bin/sh\n: > "$ODS_TEST_DOCKER_ATTEMPT"\nexit 71\n')
    fake_docker.chmod(0o755)
    recipe = tmp_path / 'data/user-extensions/example/compose.yaml'
    recipe.parent.mkdir(parents=True)
    recipe.write_text('services: {}\n')
    cache = tmp_path / '.compose-flags'
    cache.write_text('unchanged')
    common = ('set -eu\nINSTALL_DIR="$1"\nODS_PYTHON_CMD="$2"\n'
              'PATH="$INSTALL_DIR/bin:$PATH"; export PATH\n'
              'ODS_TEST_DOCKER_ATTEMPT="$INSTALL_DIR/docker-attempt"; export ODS_TEST_DOCKER_ATTEMPT\n'
              'check_install() { :; }; load_env() { :; }; sr_load() { :; }\n'
              'get_compose_flags() { echo "-f docker-compose.base.yml"; }; sr_resolve() { echo "$1"; }\n'
              'declare -A SERVICE_CATEGORIES=([example]=optional); SERVICE_IDS=(example)\n'
              'declare -A SERVICE_TYPES=([example]=docker)\n'
              'warn() { echo "$*" >&2; }; success() { :; }; error() { echo "$*" >&2; exit 1; }\n')
    common += shell_function(ODS / 'ods-cli', '_ods_cli_reject_host_service')
    common += shell_function(ODS / 'ods-cli', 'cmd_disable')
    result = subprocess.run(['bash', '-s', '--', str(tmp_path), sys.executable],
                            input=common + '\ncmd_disable example\n', text=True,
                            capture_output=True, timeout=15, check=False)
    assert result.returncode != 0
    assert 'selection unchanged' in result.stderr
    assert (tmp_path / 'docker-attempt').exists()
    assert recipe.read_text() == 'services: {}\n'
    assert not recipe.with_suffix('.yaml.disabled').exists()
    assert cache.read_text() == 'unchanged'


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
