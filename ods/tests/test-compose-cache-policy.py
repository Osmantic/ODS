"""Saved Compose arguments are never a substitute for recipe authorization."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import pytest

ODS = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('cache_policy', ODS / 'scripts/compose-cache-policy.py')
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


def test_argument_cli_preserves_spaces_and_unicode(installed):
    args = flags() + ['--env-file', 'owner café 文.env', '-p', 'owner-project']
    result = subprocess.run(
        [sys.executable, str(ODS / 'scripts/compose-cache-policy.py'),
         '--install-dir', str(installed), '--arguments', *args],
        capture_output=True, encoding='utf-8', check=True)
    assert json.loads(result.stdout) == args


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX legacy model helper')
@pytest.mark.parametrize('action', ['start_llm new-model', 'stop_llm', 'cmd_rollback'])
@pytest.mark.parametrize('confined', [False, True])
def test_legacy_model_helper_validates_before_docker_or_env_write(installed, action, confined):
    source_recipe(installed, confined=confined)
    shutil.copyfile(ODS / 'scripts/compose-cache-policy.py', installed / 'scripts/compose-cache-policy.py')
    (installed / '.compose-flags').write_text('-f base.yml -f data/user-extensions/example/compose.yaml')
    (installed / '.env').write_text('original')
    source = (ODS / 'scripts/upgrade-model.sh').read_text()
    names = ['detect_compose_file', 'detect_inference_service', 'resolve_inference_runtime',
             'validate_model_compose', 'run_model_compose', 'start_llm', 'stop_llm', 'cmd_rollback']
    functions = '\n'.join(re.search(r'^' + name + r'\(\) \{.*?^}', source,
                                    re.MULTILINE | re.DOTALL).group() for name in names)
    prelude = '''
set -u
ODS_DIR="$1"
ODS_PYTHON_CMD="$2"
OLLAMA_PORT=11434
LLAMA_SERVER_CONTAINER=ods-llama-server
BACKUP_FILE="$ODS_DIR/absent-backup"
MODELS_DIR="$ODS_DIR/models"
docker() { printf '%s\\n' "$*" >> "$ODS_DIR/docker-called"; }
log() { :; }
success() { :; }
get_previous_model() { echo previous; }
get_current_model() { echo current; }
wait_for_llm() { return 0; }
test_inference() { return 0; }
HEALTH_CHECK_TIMEOUT=1
save_state() { touch "$ODS_DIR/saved-state"; }
update_env_value() { printf changed > "$1"; }
'''
    result = subprocess.run(['bash', '-s', '--', str(installed), sys.executable],
                            input=prelude + functions + '\n' + action, capture_output=True, text=True)
    assert (result.returncode == 0) == confined, result.stderr
    assert (installed / 'docker-called').exists() == confined
    assert (installed / '.env').read_text() == ('changed' if confined and action != 'stop_llm' else 'original')
    if action == 'cmd_rollback':
        assert (installed / 'saved-state').exists() == confined
    if not confined:
        assert 'requires review' in result.stderr


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX bootstrap integration')
@pytest.mark.parametrize('action', ['llama', 'llama-retry', 'hermes', 'windows-openclaw',
                                   'windows-cached', 'windows-recovered', 'lemonade', 'openclaw'])
@pytest.mark.parametrize('confined', [False, True])
def test_bootstrap_revalidates_every_compose_entry(installed, action, confined):
    source_recipe(installed, confined=confined)
    shutil.copyfile(ODS / 'scripts/compose-cache-policy.py',
                    installed / 'scripts/compose-cache-policy.py')
    (installed / 'base.yml').write_text('services: {}')
    arguments = ['-f', 'base.yml', '-f', 'data/user-extensions/example/compose.yaml']
    saved = ' '.join(arguments)
    (installed / '.compose-flags').write_text(saved)
    (installed / 'logs').mkdir()
    (installed / 'logs/compose-launch.txt').write_text('compose_flags=' + saved + '\n')
    marker = installed / 'docker-called'
    compose = installed / 'compose-probe'
    # Record dispatch only; never start Docker or affect a running installation.
    compose.write_text('#!/bin/sh\nprintf called >> ' + shlex.quote(str(marker)) + '\nexit 42\n')
    if action == 'llama-retry':
        mutation = ('import json, pathlib; p=pathlib.Path(' + repr(str(fragment(installed))) + '); '
                    'd=json.loads(p.read_text()); d["services"]["example"]["privileged"]=True; '
                    'p.write_text(json.dumps(d))')
        compose.write_text('#!/bin/sh\nprintf called >> ' + shlex.quote(str(marker)) + '\n' +
                           shlex.quote(sys.executable) + ' -c ' + shlex.quote(mutation) +
                           '\necho "dependency failed to start"\nexit 1\n')
    compose.chmod(0o700)
    source = (ODS / 'scripts/bootstrap-upgrade.sh').read_text()
    names = ['validate_bootstrap_compose_args', 'compose_recreate_llama_server_with_retry',
             'compose_recreate_hermes', 'load_windows_lemonade_compose_args',
             'recreate_windows_lemonade_openclaw', 'refresh_lemonade_after_bootstrap_cleanup']
    functions = '\n'.join(re.search(r'^' + name + r'\(\) \{.*?^}', source,
                                     re.MULTILINE | re.DOTALL).group() for name in names)
    prelude = '''
INSTALL_DIR="$1"
ODS_PYTHON_CMD="$2"
DOCKER_COMPOSE_CMD="$INSTALL_DIR/compose-probe"
DOCKER_CMD=true
ODS_BOOTSTRAP_COMPOSE_RETRY_DELAY=0
COMPOSE_ARGS=(-f base.yml -f data/user-extensions/example/compose.yaml)
WINDOWS_LEMONADE_COMPOSE_ARGS=()
WINDOWS_LEMONADE_OPENCLAW_PRESENT=true
FULL_GGUF_FILE=full.gguf
BOOTSTRAP_GGUF=small.gguf
is_windows_bash() { return 1; }
log() { echo "$*" >&2; }
read_env_value() { echo amd; }
cd "$INSTALL_DIR" || exit 1
'''
    calls = {
        'llama': 'compose_recreate_llama_server_with_retry "${COMPOSE_ARGS[@]}"',
        'llama-retry': 'compose_recreate_llama_server_with_retry "${COMPOSE_ARGS[@]}"',
        'hermes': 'compose_recreate_hermes',
        'windows-openclaw': 'recreate_windows_lemonade_openclaw',
        'windows-cached': 'WINDOWS_LEMONADE_COMPOSE_ARGS=("${COMPOSE_ARGS[@]}"); recreate_windows_lemonade_openclaw',
        'windows-recovered': 'rm .compose-flags; recreate_windows_lemonade_openclaw',
        'lemonade': 'refresh_lemonade_after_bootstrap_cleanup',
    }
    if action == 'openclaw':
        block = source.split('log "Recreating OpenClaw to pick up model change..."', 1)[1]
        calls[action] = block.split('\n            fi', 1)[0] + '\n            fi\n'
    env = dict(os.environ, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    result = subprocess.run(['bash', '-s', '--', str(installed), sys.executable],
                            input=prelude + functions + '\n' + calls[action],
                            capture_output=True, text=True, env=env)
    assert marker.exists() == confined, result.stderr
    if action == 'llama-retry' and confined:
        assert marker.read_text() == 'called'
        assert result.returncode != 0
        assert 'requires review' in result.stderr
    if not confined:
        assert 'requires review' in result.stderr
        if action == 'windows-recovered':
            assert not (installed / '.compose-flags').exists()


@pytest.fixture
def installed(tmp_path):
    root = tmp_path / 'ods'
    (root / 'scripts').mkdir(parents=True)
    shutil.copyfile(ODS / 'scripts/resolve-compose-stack.sh', root / 'scripts/resolve-compose-stack.sh')
    folder = root / 'data/user-extensions/example'
    folder.mkdir(parents=True)
    (folder / 'manifest.yaml').write_text(json.dumps({'schema_version': 'ods.services.v1', 'service': {'id': 'example'}}))
    (folder / 'upstream.json').write_text(json.dumps({'origin': 'github-proposal'}))
    (folder / 'compose.yaml').write_text(json.dumps({'services': {'example': {
        'image': 'example/app:1', 'ports': ['127.0.0.1:9099:8080'],
    }}}))
    return root


def fragment(root):
    return root / 'data/user-extensions/example/compose.yaml'


def flags():
    return ['--env-file', 'owner.env', '-p', 'owner-project', '-f', 'base.yml',
            '--file=data/user-extensions/example/compose.yaml', '--profile', 'owner']


def change(root, updates):
    path = fragment(root)
    doc = json.loads(path.read_text())
    doc['services']['example'].update(updates)
    path.write_text(json.dumps(doc))


def source_recipe(root, confined=True):
    """An immutable recipe with a valid receipt, including pre-upgrade recipes."""
    path = fragment(root)
    revision = 'a' * 40
    service = {'build': {'context': 'https://github.com/example/app.git#' + revision},
               'image': 'ods-source-example:' + revision, 'pull_policy': 'never'}
    compose = {'services': {'example': service}}
    if confined:
        service.update(user='65532:65532', cap_drop=['ALL'], read_only=True,
                       security_opt=['no-new-privileges:true'], cpus=2, pids_limit=256,
                       mem_limit='2g', networks=['example-sandbox'])
        compose['networks'] = {'example-sandbox': {'internal': True}}
    manifest = {'schema_version': 'ods.services.v1', 'service': {'id': 'example'}}
    candidate = {'repository': 'https://github.com/example/app', 'commit': revision,
                 'manifest': manifest, 'compose': compose}
    digest = hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    receipt = {'origin': 'github-proposal', 'repository': candidate['repository'], 'commit': revision,
               'recipeDigest': digest, 'sourceFiles': [{'service': 'example', 'path': 'Dockerfile', 'blob': 'b' * 40}]}
    path.write_text(json.dumps(compose))
    (path.parent / 'manifest.yaml').write_text(json.dumps(manifest))
    (path.parent / 'upstream.json').write_text(json.dumps(receipt))
    return compose


def test_preserves_approved_arguments_and_recipe_bytes(installed):
    before = fragment(installed).read_bytes()
    assert POLICY.validate_flags(installed, flags()) == flags()
    assert fragment(installed).read_bytes() == before


@pytest.mark.parametrize('ports', [
    ['${BIND_ADDRESS:-127.0.0.1}:9099:8080'], ['0.0.0.0:9099:8080'], ['9099:8080'],
    [{'host_ip': '${BIND_ADDRESS:-127.0.0.1}', 'published': 9099, 'target': 8080}],
])
def test_rejects_saved_lan_binds_without_rewriting_approval(installed, ports):
    change(installed, {'ports': ports})
    before = fragment(installed).read_bytes()
    with pytest.raises(ValueError, match='127.0.0.1'):
        POLICY.validate_flags(installed, flags())
    assert fragment(installed).read_bytes() == before


@pytest.mark.parametrize('updates', [
    {'privileged': True}, {'user': '0'}, {'network_mode': 'host'},
    {'volumes': ['/var/run/docker.sock:/var/run/docker.sock']},
    {'cap_add': ['SYS_ADMIN']}, {'pid': 'host'},
])
def test_edited_recipe_is_rechecked_every_time(installed, updates):
    POLICY.validate_flags(installed, flags())
    change(installed, updates)
    with pytest.raises(ValueError, match='requires review'):
        POLICY.validate_flags(installed, flags())


def test_rejects_shadowing_core_service(installed):
    fragment(installed).write_text(json.dumps({'services': {'dashboard-api': {'image': 'example/app:1'}}}))
    with pytest.raises(ValueError, match='collides'):
        POLICY.validate_flags(installed, flags())


def test_old_source_receipt_does_not_authorize_unconfined_runtime(installed):
    source_recipe(installed, confined=False)
    with pytest.raises(ValueError, match='non-root'):
        POLICY.validate_flags(installed, flags())


def test_confined_source_and_matching_context_projection_are_accepted(installed):
    doc = source_recipe(installed)
    projection = fragment(installed).with_name('.ods-build-context-compose.yaml.json')
    projection.write_text(json.dumps({'services': {'example': {'build': {
        'context': doc['services']['example']['build']['context']}}}}))
    original = flags() + ['-f', str(projection)]
    assert POLICY.validate_flags(installed, original) == original
    projection.write_text(json.dumps({'services': {'example': {'privileged': True}}}))
    with pytest.raises(ValueError, match='projection changed'):
        POLICY.validate_flags(installed, original)


def test_source_receipt_is_bound_to_recipe_bytes(installed):
    source_recipe(installed)
    change(installed, {'command': ['unexpected', 'command']})
    with pytest.raises(ValueError, match='recipe changed'):
        POLICY.validate_flags(installed, flags())


@pytest.mark.parametrize('document', [
    {'services': {'example': {'read_only': False}}},
    {'networks': {'example-sandbox': {'internal': False}}},
    {'services': {'another-service': {'image': 'example/app:1', 'networks': ['example-sandbox']}}},
])
def test_compose_merge_cannot_weaken_or_join_source_sandbox(installed, document):
    source_recipe(installed)
    overlay = fragment(installed).with_name('compose.cpu.yaml')
    overlay.write_text(json.dumps(document))
    with pytest.raises(ValueError, match='source.*(overridden|joined)'):
        POLICY.validate_flags(installed, flags() + ['-f', str(overlay)])


def test_another_extension_cannot_attach_to_a_source_sandbox(installed):
    source_recipe(installed)
    other = installed / 'data/user-extensions/other/compose.yaml'
    other.parent.mkdir()
    other.write_text(json.dumps({'services': {'other': {'image': 'example/app:1',
                                                      'networks': ['example-sandbox']}}}))
    with pytest.raises(ValueError, match='sandbox.*joined'):
        POLICY.validate_flags(installed, flags() + ['-f', str(other)])


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX dynamic resolver integration')
def test_dynamic_resolution_also_rejects_source_overlay(installed):
    import os
    source_recipe(installed)
    (installed / 'docker-compose.base.yml').write_text('services: {}')
    (installed / 'docker-compose.cpu.yml').write_text('services: {}')
    env = dict(os.environ, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    command = ['bash', str(installed / 'scripts/resolve-compose-stack.sh'),
               '--script-dir', str(installed), '--gpu-backend', 'cpu']
    result = subprocess.run(command, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert 'data/user-extensions/example/compose.yaml' in result.stdout
    fragment(installed).with_name('compose.cpu.yaml').write_text(json.dumps({
        'services': {'example': {'read_only': False}}}))
    result = subprocess.run(command, capture_output=True, text=True, env=env)
    assert result.returncode != 0
    assert not result.stdout
    assert 'source service' in result.stderr and 'overridden' in result.stderr


def test_projection_without_original_recipe_is_rejected(installed):
    projection = fragment(installed).with_name('.ods-build-context-compose.yaml.json')
    projection.write_text('{}')
    with pytest.raises(ValueError, match='no validated source recipe'):
        POLICY.validate_flags(installed, ['-f', str(projection)])


def test_disabled_base_does_not_leave_cached_accelerator_enabled(installed):
    overlay = fragment(installed).with_name('compose.nvidia.yaml')
    overlay.write_text('{"services": {"example": {"environment": {"X": "1"}}}}')
    fragment(installed).rename(fragment(installed).with_suffix('.yaml.disabled'))
    with pytest.raises(ValueError, match='disabled or omitted'):
        POLICY.validate_flags(installed, ['-f', str(overlay)])


def test_missing_policy_fails_closed_only_for_extensions(installed):
    (installed / 'scripts/resolve-compose-stack.sh').unlink()
    assert POLICY.validate_flags(installed, ['-f', 'base.yml']) == ['-f', 'base.yml']
    with pytest.raises(ValueError, match='security policy is missing'):
        POLICY.validate_flags(installed, flags())


def link_or_skip(path, target, directory=False):
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        pytest.skip(f'Symlink creation unavailable: {error}')


def test_alias_outside_extensions_cannot_disguise_recipe(installed):
    alias = installed / 'innocent-core.yml'
    link_or_skip(alias, fragment(installed))
    with pytest.raises(ValueError, match='aliases'):
        POLICY.validate_flags(installed, ['-f', str(alias)])


def test_recipe_symlink_is_rejected(installed):
    path = fragment(installed)
    saved = path.with_suffix('.original')
    path.rename(saved)
    link_or_skip(path, saved)
    with pytest.raises(ValueError, match='owned regular'):
        POLICY.validate_flags(installed, flags())


def test_installation_data_can_live_on_another_disk(installed):
    data = installed / 'data'
    moved = installed.parent / 'another-disk-data'
    data.rename(moved)
    link_or_skip(data, moved, directory=True)
    assert POLICY.validate_flags(installed, flags()) == flags()


@pytest.mark.parametrize('arguments', ['-f base.yml', ['-f'], ['-f', 'bad\n.yml'], [42]])
def test_malformed_arguments_are_rejected(installed, arguments):
    with pytest.raises(ValueError):
        POLICY.validate_flags(installed, arguments)


def test_json_cli_preserves_spaces_and_fails_without_emitting_flags(installed):
    command = [sys.executable, str(ODS / 'scripts/compose-cache-policy.py'), '--install-dir', str(installed)]
    original = flags() + ['--env-file', 'folder with spaces/owner.env']
    result = subprocess.run(command, input=json.dumps(original), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == original
    change(installed, {'privileged': True})
    result = subprocess.run(command, input=json.dumps(original), capture_output=True, text=True)
    assert result.returncode == 2
    assert not result.stdout
    assert 'requires review' in result.stderr


def test_model_store_helper_cannot_bypass_recipe_validation(installed):
    command = [sys.executable, str(ODS / 'scripts/model-store-compose-flags.py'),
               '--install-dir', str(installed), '--json-stdin']
    change(installed, {'privileged': True})
    result = subprocess.run(command, input=json.dumps(flags()), capture_output=True, text=True)
    assert result.returncode == 2
    assert not result.stdout
    assert 'requires review' in result.stderr


@pytest.mark.parametrize('helper', ['compose-cache-policy.py', 'model-store-compose-flags.py'])
@pytest.mark.parametrize('prefix', [b'', b'\xef\xbb\xbf'])
def test_native_json_pipeline_preserves_unicode_with_optional_bom(installed, helper, prefix):
    command = [sys.executable, str(ODS / 'scripts' / helper), '--install-dir', str(installed)]
    if helper == 'model-store-compose-flags.py':
        command.append('--json-stdin')
    original = flags() + ['--env-file', 'folder with spaces/owner-\u00e9\u6a21.env']
    wire = prefix + json.dumps(original, ensure_ascii=False).encode('utf-8')
    result = subprocess.run(command, input=wire, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == original
    change(installed, {'privileged': True})
    result = subprocess.run(command, input=wire, capture_output=True)
    assert result.returncode == 2
    assert not result.stdout
    assert b'requires review' in result.stderr


@pytest.mark.parametrize('helper', ['compose-cache-policy.py', 'model-store-compose-flags.py'])
@pytest.mark.parametrize('wire', [b'\xef\xbb\xbf\xef\xbb\xbf[]', b'\xff[]', b'[]garbage', b' ' * 131073],
                         ids=['repeated-bom', 'invalid-utf8', 'trailing-data', 'oversized'])
def test_native_json_pipeline_rejects_invalid_or_oversized_input(installed, helper, wire):
    command = [sys.executable, str(ODS / 'scripts' / helper), '--install-dir', str(installed)]
    if helper == 'model-store-compose-flags.py':
        command.append('--json-stdin')
    result = subprocess.run(command, input=wire, capture_output=True)
    assert result.returncode == 2
    assert not result.stdout


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX CLI integration')
@pytest.mark.parametrize('platform', ['linux', 'macos'])
def test_native_cli_rejects_cached_recipe_before_docker(installed, platform):
    # Extract the actual function, leaving process start and user configuration
    # outside this test. Exercise the real Python validator, not a stub.
    if platform == 'linux':
        script = (ODS / 'ods-cli').read_text(encoding='utf-8')
        function = 'get_compose_flags() {' + script.split('get_compose_flags() {', 1)[1].split('\n}\n', 1)[0] + '\n}\n'
        prelude = '_ensure_hermes_dashboard_session_token() { :; }\nwarn() { echo "$*" >&2; }\n'
    else:
        script = (ODS / 'installers/macos/lib/native-model.sh').read_text(encoding='utf-8')
        function = script.split('macos_model_store_compose_flags() {', 1)[1].split('\n}\n', 1)[0]
        function = 'macos_model_store_compose_flags() {' + function + '\n}\n'
        function += 'get_compose_flags() { macos_model_store_compose_flags "$(cat "$INSTALL_DIR/.compose-flags")"; }\n'
        prelude = ''
    for name in ['compose-cache-policy.py', 'model-store-compose-flags.py']:
        (installed / 'scripts' / name).symlink_to(ODS / 'scripts' / name)
    (installed / 'base.yml').write_text('services: {}')
    saved = '-f base.yml -f data/user-extensions/example/compose.yaml'
    (installed / '.compose-flags').write_text(saved)
    script = 'set -e\nINSTALL_DIR="$1"\n' + prelude + function + '\nget_compose_flags\n'
    command = ['bash', '-s', '--', str(installed)]
    if platform == 'linux':
        # Linux CLI requires Bash 4+. Keep the native macOS case on its system
        # Bash, while selecting a supported shell for the Linux-only function.
        for candidate in ['bash', '/opt/homebrew/bin/bash', '/usr/local/bin/bash']:
            if not shutil.which(candidate):
                continue
            version = subprocess.run([candidate, '-c', 'echo "${BASH_VERSINFO[0]}"'],
                                     capture_output=True, text=True, check=True)
            if int(version.stdout.strip()) >= 4:
                command[0] = candidate
                break
        else:
            pytest.skip('Linux CLI needs Bash 4+; native macOS case runs separately')
    # Use the same configured interpreter for the shell helper and pytest.
    import os
    env = dict(os.environ, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    result = subprocess.run(command, input=script, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == saved
    change(installed, {'privileged': True})
    result = subprocess.run(command, input=script, capture_output=True, text=True, env=env)
    assert result.returncode != 0
    assert not result.stdout
    assert 'requires review' in result.stderr
