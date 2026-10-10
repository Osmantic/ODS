"""Execute recovery shell entrypoints with a local candidate and inert services."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
BASH = (str(Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/bash.exe')
        if os.name == 'nt' else '/bin/bash')


def shell_path(path):
    value = str(path).replace('\\', '/')
    return '/' + value[0].lower() + value[2:] if os.name == 'nt' else value


def script(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')
    path.chmod(0o755)


@pytest.fixture
def retained(tmp_path):
    home = tmp_path / 'home'
    install = home / 'ODS retained'
    install.mkdir(parents=True)
    (install / '.env').write_text('ORIGINAL_PRIVATE_SETTING=retained\n')
    (install / 'docker-compose.base.yml').write_text('services: {}\n')
    (install / 'owner-data.txt').write_text('preserve me')
    source = tmp_path / 'candidate'
    source.mkdir()
    candidate = source / 'ods'
    candidate.mkdir()
    # If routing regresses into installer or copy, these witnesses expose it.
    script(candidate / 'install.sh', '#!/bin/bash\necho unexpected-install >&2\nexit 91\n')
    script(candidate / 'ods-uninstall.sh', '#!/bin/bash\necho unexpected-uninstall >&2\nexit 92\n')
    (candidate / 'owner-data.txt').write_text('overwritten by candidate')
    helper = candidate / 'installers/macos/lib/pixel-native-resume.py'
    script(helper, 'import json, os, pathlib, sys\n'
        'pathlib.Path(os.environ["RESULT"]).write_text(json.dumps(sys.argv[1:]))\n'
        'raise SystemExit(int(os.environ.get("RECOVERY_EXIT", "0")))\n')
    for args in [['init', '-q', str(source)], ['-C', str(source), 'add', '.'],
                 ['-C', str(source), '-c', 'user.name=Fixture',
                  '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture']]:
        subprocess.run(['git', *args], check=True, capture_output=True)
    stubs = tmp_path / 'bin'
    for name in ('docker', 'nvidia-smi', 'lspci'):
        script(stubs / name, '#!/bin/bash\nexit 1\n')
    script(stubs / 'python3', '#!/bin/bash\nexec ' + shlex.quote(shell_path(sys.executable)) + ' "$@"\n')
    result = tmp_path / 'result.json'
    env = dict(os.environ, HOME=shell_path(home), ODS_INSTALL_DIR=shell_path(install),
        ODS_REPO_URL=shell_path(source), ODS_ALLOW_LEGACY_PARALLEL='1', RESULT=str(result),
        PATH=shell_path(stubs) + ':' + ('/usr/bin:/bin' if os.name == 'nt' else os.environ['PATH']))
    for key in ('ODS_REF', 'ODS_BOOTSTRAP_REF', 'ODS_HOME', 'BASH_ENV', 'ENV'):
        env.pop(key, None)
    return install, candidate, result, env


def bootstrap(retained, *args, ostype='darwin', piped=False):
    _, _, _, env = retained
    if piped:
        command = [BASH, '-s', '--', *args]
        source = 'OSTYPE=' + shlex.quote(ostype) + '\n' + (ROOT / 'get-ods.sh').read_text(encoding='utf-8')
        return subprocess.run(command, input=source, env=env,
            capture_output=True, text=True, encoding='utf-8', timeout=60)
    return subprocess.run([BASH, '-c', 'OSTYPE=' + shlex.quote(ostype)
        + '; source "$1" "${@:2}"', 'fixture', shell_path(ROOT / 'get-ods.sh'), *args],
        stdin=subprocess.DEVNULL, env=env, capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize('extra', [[], ['--non-interactive', '--no-watch', '--no-open',
                                      '--opencode-choice', 'enabled']])
def test_piped_recovery_runs_candidate_without_overwriting_retained_install(retained, extra):
    install, _, result, _ = retained
    before = {p.name: p.read_bytes() for p in install.iterdir()}
    run = bootstrap(retained, '--recover', *extra, piped=True)
    assert run.returncode == 0, run.stdout + run.stderr
    args = json.loads(result.read_text())
    assert args[0] == '--install-dir' and Path(args[1]).resolve() == install.resolve()
    assert args[2] == '--ods-source' and args[3] != str(install)
    assert args[4:] == extra
    assert before == {p.name: p.read_bytes() for p in install.iterdir()}
    assert not Path(args[3]).exists(), 'Bootstrap should clean only its temporary candidate'


def test_recovery_propagates_child_failure_and_preserves_original(retained):
    install, _, _, env = retained
    env['RECOVERY_EXIT'] = '19'
    run = bootstrap(retained, '--recover')
    assert run.returncode == 19, run.stdout + run.stderr
    assert (install / 'owner-data.txt').read_text() == 'preserve me'


@pytest.mark.parametrize('options', [
    ['--force'], ['--keep-models'], ['--tier', '1'], ['--opencode-choice'],
    ['--opencode-choice', 'maybe'], ['--cloud'], ['--install-dir', '/different'],
])
def test_incompatible_recovery_options_are_rejected_before_clone(retained, options):
    _, _, result, _ = retained
    run = bootstrap(retained, '--recover', *options)
    assert run.returncode != 0
    assert 'Cloning ODS' not in run.stdout and not result.exists()


def test_recovery_requires_macos_and_retained_environment(retained):
    install, _, result, _ = retained
    run = bootstrap(retained, '--recover', ostype='linux-gnu')
    assert run.returncode != 0 and 'macOS' in run.stdout
    (install / '.env').unlink()
    run = bootstrap(retained, '--recover')
    assert run.returncode != 0 and 'retained ODS folder' in run.stdout
    assert 'Cloning ODS' not in run.stdout and not result.exists()
    assert (install / 'owner-data.txt').is_file()


def test_default_mac_rerun_gives_an_explicit_recovery_command_without_changes(retained):
    install, _, result, _ = retained
    run = bootstrap(retained)
    assert run.returncode == 0, run.stdout + run.stderr
    assert '--recover' in run.stdout and 'ods-macos.sh' in run.stdout
    assert './ods-cli' not in run.stdout and 'Cloning ODS' not in run.stdout
    assert not result.exists() and (install / '.env').is_file()


def test_initial_pixel_failure_names_the_same_non_destructive_entrypoint(retained, tmp_path):
    install, _, _, env = retained
    source = (ROOT / 'installers/macos/install-macos.sh').read_text(encoding='utf-8')
    start = source.index('        if ! /usr/bin/python3 "$LIB_DIR/pixel-native-install.py"')
    stop = source.index('\n        fi', start) + len('\n        fi')
    block = source[start:stop].replace('/usr/bin/python3', 'fixture_python')
    source = ('set -euo pipefail\n'
        'ai_err() { printf "%s\\n" "$*"; }\n'
        'ai() { printf "%s\\n" "$*"; }\n'
        'fixture_python() { echo fixture-health-failure; return 1; }\n'
        '_pixel_install_args=(--fixture)\n' + block)
    log = tmp_path / 'installation.log'
    run = subprocess.run([BASH], input=source, text=True, capture_output=True,
        env=dict(env, INSTALL_DIR=shell_path(install), LIB_DIR='/fixture', ODS_LOG_FILE=shell_path(log)),
        timeout=30)
    assert run.returncode == 1, run.stdout + run.stderr
    assert 'https://raw.githubusercontent.com/Osmantic/ODS/main/ods/get-ods.sh' in run.stdout
    assert 'bash -s -- --recover' in run.stdout
    assert log.read_text() == 'fixture-health-failure\n'
    assert (install / 'owner-data.txt').read_text() == 'preserve me'


@pytest.mark.parametrize('command,options,helper_name', [
    ('recover', ['--no-open', '--no-watch'], 'pixel-native-resume.py'),
    ('progress', ['--watch'], 'pixel-native-progress.py'),
])
def test_installed_cli_routes_recovery_and_progress_without_docker_precheck(retained, command, options, helper_name):
    install, candidate, result, env = retained
    shutil.copyfile(ROOT / 'installers/macos/ods-macos.sh', install / 'ods-macos.sh')
    for name in ('constants', 'ui', 'bridge-manager', 'native-model', 'detection'):
        script(install / ('lib/' + name + '.sh'), 'echo unexpected-runtime-library >&2\nexit 97\n')
    path_utils = install / 'installers/lib/path-utils.sh'
    path_utils.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / 'installers/lib/path-utils.sh', path_utils)
    target = install / ('installers/macos/lib/' + helper_name)
    target.parent.mkdir(parents=True)
    shutil.copyfile(candidate / 'installers/macos/lib/pixel-native-resume.py', target)
    env['RECOVERY_EXIT'] = '17'
    run = subprocess.run([BASH, shell_path(install / 'ods-macos.sh'), command, *options],
        env=env, capture_output=True, text=True, timeout=30)
    assert run.returncode == 17, run.stdout + run.stderr
    args = json.loads(result.read_text())
    assert args[0] == '--install-dir' and Path(args[1]).resolve() == install.resolve()
    assert args[-len(options):] == options
    assert (install / 'owner-data.txt').read_text() == 'preserve me'
