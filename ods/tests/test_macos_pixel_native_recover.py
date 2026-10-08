"""Recovery publishes readiness only after protected and consumer readback."""
import importlib.util
import json
import os
import plistlib
from pathlib import Path
import subprocess
import shlex
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace
import urllib.request  # noqa: F401 - initialize before emulating Darwin

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_recover',
    ROOT / 'installers/macos/lib/pixel-native-recover.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
continuation = module.helper('pixel-native-continuation')
readiness = module.helper('pixel-native-readiness')
acceptance = module.helper('pixel-native-acceptance')


@pytest.fixture
def recommended_model():
    bootstrap = dict(bootstrap_file='starter.gguf', bootstrap_model='starter', bootstrap_context=65536)
    saved = dict(ODS_MODE='local', GPU_BACKEND='apple', LLM_BACKEND='llama-server',
        GGUF_FILE='starter.gguf', LLM_MODEL='starter', MAX_CONTEXT='65536', CTX_SIZE='65536',
        MODEL_RECOMMENDED_GGUF='chosen.gguf', MODEL_RECOMMENDED_MODEL='chosen',
        MODEL_RECOMMENDED_CONTEXT='32768')
    record = dict(id='chosen-q4', llm_model_name='chosen', gguf_file='chosen.gguf',
        gguf_url='https://huggingface.co/org/model/resolve/' + 'a' * 40 + '/chosen.gguf',
        gguf_sha256='b' * 64, size_bytes=1000, context_length=32768, max_context_length=65536)
    return saved, dict(models=[record]), bootstrap


def test_continuation_reuses_recommendation_not_hardware(recommended_model):
    saved, catalog, bootstrap = recommended_model
    before = json.dumps([saved, catalog, bootstrap], sort_keys=True)
    plan = continuation.model_upgrade_plan(saved, catalog, **bootstrap)
    assert plan == dict(status='upgrade-required', modelId='chosen-q4',
        arguments=['chosen.gguf', catalog['models'][0]['gguf_url'], 'b' * 64,
                   'chosen', '32768', 'starter.gguf'])
    assert json.dumps([saved, catalog, bootstrap], sort_keys=True) == before
    # No memory/tier input participates; a later hardware policy cannot repick.
    assert continuation.model_upgrade_plan(dict(saved, HOST_RAM_GB='128', TIER='4'),
        catalog, **bootstrap) == plan


@pytest.mark.parametrize('change', [
    {'ODS_MODE': 'hybrid'}, {'GPU_BACKEND': 'nvidia'}, {'LLM_BACKEND': 'external'},
    {'EXTERNAL_LLM_URL': 'http://another-model'}, {'LEMONADE_EXTERNAL': 'true'},
    {'ODS_ACTIVE_MODEL_STORE': 'external-drive'}, {'MODEL_RECOMMENDED_GGUF': '../chosen.gguf'},
    {'MODEL_RECOMMENDED_GGUF': ''}, {'MODEL_RECOMMENDED_MODEL': 'different'},
    {'MODEL_RECOMMENDED_CONTEXT': '0'}, {'MODEL_RECOMMENDED_CONTEXT': '65537'},
    {'MODEL_RECOMMENDED_CONTEXT': '32768\nextra'}, {'MODEL_RECOMMENDED_CONTEXT': 'True'},
    {'GGUF_FILE': 'operator-choice.gguf'}, {'LLM_MODEL': 'operator-choice'},
    {'CTX_SIZE': '8192'}, {'MAX_CONTEXT': '8192'}, {'MODEL_SELECTION_SOURCE': 'dashboard'},
])
def test_continuation_refuses_changed_model_or_route(recommended_model, change):
    saved, catalog, bootstrap = recommended_model
    with pytest.raises(ValueError):
        continuation.model_upgrade_plan(dict(saved, **change), catalog, **bootstrap)


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'parts', 'digest', 'floating-url',
    'host', 'file', 'query', 'context-bool', 'context-missing'])
def test_continuation_requires_unambiguous_pinned_catalog(recommended_model, fault):
    saved, catalog, bootstrap = recommended_model
    record = catalog['models'][0]
    if fault == 'missing': catalog['models'] = []
    if fault == 'duplicate': catalog['models'].append(dict(record))
    if fault == 'parts':
        record['gguf_parts'] = [
            dict(file=name, url=record['gguf_url'], sha256='b' * 64)
            for name in ('chosen.gguf', 'part2.gguf')]
    if fault == 'digest': record['gguf_sha256'] = ''
    if fault == 'floating-url': record['gguf_url'] = record['gguf_url'].replace('a' * 40, 'main')
    if fault == 'host': record['gguf_url'] = record['gguf_url'].replace('huggingface.co', 'huggingface.co.invalid')
    if fault == 'file': record['gguf_url'] = record['gguf_url'].replace('/chosen.gguf', '/different.gguf')
    if fault == 'query': record['gguf_url'] += '?token=do-not-publish'
    if fault == 'context-bool': record['max_context_length'] = True
    if fault == 'context-missing':
        del record['max_context_length']
        del record['context_length']
    with pytest.raises(ValueError):
        continuation.model_upgrade_plan(saved, catalog, **bootstrap)


def test_continuation_does_not_download_for_selected_or_cloud_model(recommended_model):
    saved, catalog, bootstrap = recommended_model
    saved.update(GGUF_FILE='chosen.gguf', LLM_MODEL='chosen', MAX_CONTEXT='32768', CTX_SIZE='32768')
    assert continuation.model_upgrade_plan(saved, catalog, **bootstrap) == dict(
        status='selected-model', modelId='chosen-q4', arguments=[])
    assert continuation.model_upgrade_plan({'ODS_MODE': 'cloud'}, {}, **bootstrap) == dict(
        status='cloud-model', arguments=[])


def test_model_continuation_reads_private_inputs_without_mutation(tmp_path, recommended_model):
    saved, catalog, bootstrap = recommended_model
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/model-library.json').write_text(json.dumps(catalog))
    env = tmp_path / '.env'
    env.write_text('\n'.join(key + '=' + json.dumps(value) for key, value in saved.items())
        + '\nDASHBOARD_API_KEY=do-not-publish\n')
    env.chmod(0o600)
    before = env.read_bytes()
    plan, snapshot = continuation.inspect_model_upgrade(tmp_path, **bootstrap)
    assert snapshot[0] == before == env.read_bytes()
    assert 'do-not-publish' not in json.dumps(plan)
    assert plan['status'] == 'upgrade-required'
    env.write_text(env.read_text() + 'GGUF_FILE=other.gguf\n')
    with pytest.raises(ValueError, match='duplicate-retained-model-setting'):
        continuation.inspect_model_upgrade(tmp_path, **bootstrap)
    env.chmod(0o644)
    with pytest.raises(ValueError, match='private-owner-environment-required'):
        continuation.inspect_model_upgrade(tmp_path, **bootstrap)


@pytest.mark.parametrize('profile', ['qwen', 'gemma4'])
@pytest.mark.parametrize('tier', ['1', '2', '3', '4'])
def test_continuation_accepts_real_mac_tier_recommendations(profile, tier):
    # Exercise installed-format recommendations, including the reporter's high
    # memory tiers, without running hardware detection or starting any service.
    output = subprocess.run(['/bin/bash', '-c',
        'source "$1"; "set_${2}_tier_config" "$3"; '
        'printf "%s\\n" "$BOOTSTRAP_GGUF_FILE" "$BOOTSTRAP_LLM_MODEL" "$BOOTSTRAP_MAX_CONTEXT" '
        '"$GGUF_FILE" "$LLM_MODEL" "$MAX_CONTEXT"',
        'test-tier', str(ROOT / 'installers/macos/lib/tier-map.sh'), profile, tier],
        check=True, capture_output=True, text=True, timeout=10).stdout.splitlines()
    starter_file, starter_model, starter_context, filename, model_name, context = output
    saved = dict(ODS_MODE='local', GPU_BACKEND='apple', LLM_BACKEND='llama-server',
        GGUF_FILE=starter_file, LLM_MODEL=starter_model, MAX_CONTEXT=starter_context, CTX_SIZE=starter_context,
        MODEL_RECOMMENDED_GGUF=filename, MODEL_RECOMMENDED_MODEL=model_name, MODEL_RECOMMENDED_CONTEXT=context)
    catalog = json.loads((ROOT / 'config/model-library.json').read_text())
    plan = continuation.model_upgrade_plan(saved, catalog,
        bootstrap_file=starter_file, bootstrap_model=starter_model, bootstrap_context=starter_context)
    assert plan['status'] == 'upgrade-required'
    assert plan['arguments'][0] == filename
    assert plan['arguments'][3:] == [model_name, context, starter_file]


@pytest.fixture
def retained(tmp_path, monkeypatch):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 501)
    preparation = tmp_path / 'data/pixel-native/preparation'
    preparation.mkdir(mode=0o700, parents=True)
    receipt = dict(schemaVersion=1, status='prepared', phase='awaiting-protected-activation',
        requiresActivation=True, runtimeDigest='a' * 64, serviceDigest='b' * 64, pixelSourceRef='c' * 40,
        home=str(tmp_path / 'data/pixel-native/home'))
    activation = dict(schemaVersion=1, status='error', phase='final-health', requiresRecovery=True,
        runtimeDigest=receipt['runtimeDigest'], serviceDigest=receipt['serviceDigest'])
    for name, value in (('preparation.json', receipt), ('activation.json', activation)):
        path = preparation / name
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    # Use the real bounded owner/private-file reader while exercising real
    # flock, fsync and atomic publication on the platform running this test.
    config = module.helper('pixel-native-config')
    monkeypatch.setattr(module, 'helper', lambda name: config)
    return preparation, receipt, activation


@pytest.mark.parametrize('fault', [None, 'proof', 'reproof', 'health', 'client', 'route',
    'changed', 'foreign-selection', 'legacy-host', 'lock-link'])
def test_recovery_preserves_failed_attempt_and_never_reactivates(retained, monkeypatch, tmp_path, fault):
    preparation, receipt, activation = retained
    originals = {name: (preparation / name).read_bytes() for name in ('preparation.json', 'activation.json')}
    destination = preparation / 'selection-update.json'
    if fault == 'foreign-selection':
        destination.write_text(json.dumps({'another': 'transaction'}))
        destination.chmod(0o600)
    if fault == 'lock-link':
        target = tmp_path / 'unchanged-lock-target'
        target.write_text('private')
        (preparation / '.selection.lock').symlink_to(target)
    events = []
    proof_count = 0
    def prove():
        nonlocal proof_count
        events.append('proof')
        proof_count += 1
        if fault == 'changed':
            (preparation / 'activation.json').write_text(json.dumps(dict(activation, phase='protected-activation')))
        return dict(status='unknown' if fault == 'proof' or fault == 'reproof' and proof_count == 2 else 'active',
                    runtimeDigest=receipt['runtimeDigest'], serviceDigest=receipt['serviceDigest'])
    def health(run):
        events.append('health')
        if fault == 'health': raise ValueError('native-compose-health-timeout')
    def run(*args, **kwargs):
        events.append(args[0])
        if args[0] == 'up':
            assert args == ('up', '-d', '--no-deps', '--wait', '--wait-timeout', '120', 'dashboard-api', 'open-webui')
            return SimpleNamespace(returncode=int(fault == 'client'))
        assert args[:5] == ('exec', '-T', 'dashboard-api', 'python3', '-c')
        assert 'http://pixel-edge:9595/health' in args[5]
        return SimpleNamespace(returncode=int(fault == 'route'))
    services = {'dashboard-api': {}, 'open-webui': {}}
    if fault == 'legacy-host': services['dashboard-api']['extra_hosts'] = {'pixel-edge': 'host-gateway'}
    def finish():
        return module.finish(preparation=preparation, receipt=receipt, run=run, verify=prove,
            compose=SimpleNamespace(wait_ready=health), selected_services=services)
    if fault:
        with pytest.raises((ValueError, OSError)):
            finish()
        if fault == 'foreign-selection':
            assert json.loads(destination.read_text()) == {'another': 'transaction'}
        else:
            assert not destination.exists()
        if fault in ('proof', 'foreign-selection', 'lock-link'):
            assert 'up' not in events
    else:
        assert finish() == destination
        assert events == ['proof', 'health', 'up', 'exec', 'health', 'proof']
        selected = json.loads(destination.read_text())
        assert selected['activation'] == dict(activation, status='ready', phase='services-ready', requiresRecovery=False)
        assert selected['preparation'] == receipt
        assert destination.stat().st_mode & 0o777 == 0o600
        stack_spec = importlib.util.spec_from_file_location('recovered_stack',
            ROOT / 'installers/macos/lib/pixel-native-stack.py')
        stack = importlib.util.module_from_spec(stack_spec)
        stack_spec.loader.exec_module(stack)
        for relative in stack.installer.FRAGMENTS:
            path = tmp_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        assert stack.read_selection(preparation) == (receipt, selected['activation'])
        assert stack.resolve_files(tmp_path, ['docker-compose.yml']) == [
            'docker-compose.yml', *stack.installer.FRAGMENTS]
        # An interrupted caller can explicitly replay; no gateway or protected
        # service start/stop/install operation is available to this coordinator.
        assert finish() == destination
    for name, value in originals.items():
        if fault == 'changed' and name == 'activation.json': continue
        assert (preparation / name).read_bytes() == value


@pytest.mark.parametrize('field,value', [('phase', 'protected-activation'), ('phase', 'infrastructure'),
    ('status', 'activating'), ('requiresRecovery', False), ('runtimeDigest', 'd' * 64), ('schemaVersion', True)])
def test_only_matching_late_initial_failures_can_resume(retained, field, value):
    _, receipt, activation = retained
    with pytest.raises(ValueError):
        module.selection(receipt, dict(activation, **{field: value}))
    with pytest.raises(ValueError):
        module.selection(dict(receipt, kind='legacy-native'), activation)


def test_recovery_rejects_root_before_reading_or_running(monkeypatch):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    with pytest.raises(ValueError, match='owner-required'):
        module.recover('/does-not-exist', '/does-not-exist')


def test_recovery_refuses_concurrent_publication(retained):
    preparation, receipt, _ = retained
    import fcntl
    with (preparation / '.selection.lock').open('w') as lock:
        (preparation / '.selection.lock').chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            module.finish(preparation=preparation, receipt=receipt, run=None,
                verify=None, compose=None, selected_services={})
    assert not (preparation / 'selection-update.json').exists()


@pytest.mark.parametrize('fault', [None, 'proof', 'host', 'reproof', 'changed'])
def test_host_agent_continuation_is_between_proofs_and_before_publication(retained, fault):
    preparation, receipt, activation = retained
    calls = []
    def verify():
        calls.append('proof')
        failed = fault == 'proof' or fault == 'reproof' and calls.count('proof') == 2
        return dict(status='unknown' if failed else 'active',
            runtimeDigest=receipt['runtimeDigest'], serviceDigest=receipt['serviceDigest'])
    def run(*args, **kwargs):
        calls.append(args[0])
        return SimpleNamespace(returncode=0)
    def restore():
        calls.append('host')
        assert calls[:4] == ['proof', 'up', 'exec', 'host']
        assert not (preparation / 'selection-update.json').exists()
        assert json.loads((preparation / 'activation.json').read_text()) == activation
        if fault == 'host': raise ValueError('native-recovery-host-agent-failed')
        if fault == 'changed':
            (preparation / 'activation.json').write_text(json.dumps(dict(activation, phase='changed')))
    def finish():
        return module.finish(preparation=preparation, receipt=receipt, run=run, verify=verify,
            compose=SimpleNamespace(wait_ready=lambda run: None),
            selected_services={'dashboard-api': {}}, restore_host_agent=restore)
    if fault:
        with pytest.raises(ValueError): finish()
        assert not (preparation / 'selection-update.json').exists()
        if fault == 'proof': assert calls == ['proof']
    else:
        assert finish() == preparation / 'selection-update.json'
        assert calls == ['proof', 'up', 'exec', 'host', 'proof']
    if fault != 'changed':
        assert json.loads((preparation / 'activation.json').read_text()) == activation


@pytest.mark.parametrize('fault', [None, 'process', 'symlink', 'hardlink', 'public-log'])
def test_host_setup_uses_bound_transport_and_private_logs(tmp_path, monkeypatch, fault):
    preparation = tmp_path / 'data/pixel-native/preparation'
    preparation.mkdir(parents=True, mode=0o700)
    log = preparation / 'continuation-host-agent.log'
    other = tmp_path / 'untouched'
    other.write_text('untouched')
    other.chmod(0o600)
    if fault == 'symlink': log.symlink_to(other)
    if fault == 'hardlink': os.link(other, log)
    if fault == 'public-log':
        log.write_text('untouched')
        log.chmod(0o644)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert command[:2] == ['/bin/bash', '-c']
        assert 'ods_macos_install_host_agent' in command[2]
        assert kwargs['env']['DOCKER_HOST'] == 'unix:///verified.sock'
        assert set(kwargs['env']) == {'HOME', 'PATH', 'DOCKER_HOST', 'DOCKER_CONFIG', 'ODS_CONTINUATION_LOG_FD', 'ODS_PYTHON_CMD'}
        assert kwargs['env']['ODS_PYTHON_CMD'] == '/retained/python'
        assert kwargs['cwd'] == tmp_path and kwargs['close_fds'] is True
        assert kwargs['stdin'] == subprocess.DEVNULL and kwargs['stderr'] == subprocess.STDOUT
        fd = kwargs['stdout'].fileno()
        assert kwargs['pass_fds'] == (fd,)
        assert os.fstat(fd).st_mode & 0o777 == 0o600
        os.write(fd, b'private diagnostic that must not be printed')
        return SimpleNamespace(returncode=int(fault == 'process'))
    monkeypatch.setattr(continuation.subprocess, 'run', run)
    environment = dict(HOME=str(tmp_path), PATH='/usr/bin:/bin', DOCKER_HOST='unix:///verified.sock',
        DOCKER_CONFIG=str(tmp_path / 'docker'), ODS_PYTHON_CMD='/retained/python',
        BASH_ENV='must-not-run', ODS_AGENT_KEY='must-not-forward')
    if fault:
        with pytest.raises((ValueError, OSError)) as error:
            continuation.restore_host_agent(tmp_path, environment)
        assert 'private diagnostic' not in str(error.value)
        if fault != 'process': assert not calls
    else:
        continuation.restore_host_agent(tmp_path, environment)
        assert len(calls) == 1
    assert other.read_text() == 'untouched'


@pytest.mark.parametrize('requested', [False, True])
def test_cli_host_agent_success_does_not_claim_complete_install(monkeypatch, capsys, requested):
    monkeypatch.setattr(module, 'prepare_python', lambda path: None)
    def recover(install_dir, ods_source, **kwargs):
        assert kwargs == ({'restore_host_agent': True} if requested else {})
        return Path('/fixture/selection-update.json')
    monkeypatch.setattr(module, 'recover', recover)
    monkeypatch.setattr(module.sys, 'argv', ['recover', '--install-dir', '/fixture',
        *(['--restore-host-agent'] if requested else [])])
    assert module.main() == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result['installerComplete'] is False
    assert result.get('hostAgentReady') is (True if requested else None)
    assert 'full-model download' in output.err


@pytest.mark.parametrize('authenticated', [True, False])
@pytest.mark.parametrize('transport', [True, False])
def test_real_host_setup_subprocess_uses_shared_library_in_isolated_home(tmp_path, monkeypatch, authenticated, transport):
    installed = tmp_path / 'retained ods'
    preparation = installed / 'data/pixel-native/preparation'
    preparation.mkdir(parents=True, mode=0o700)
    home, library, binaries = (tmp_path / name for name in ('home', 'lib', 'bin'))
    for path in (home, library, binaries, installed / 'bin', installed / '.venv/host-agent/bin'):
        path.mkdir(parents=True, exist_ok=True)
    (installed / 'bin/ods-host-agent.py').touch()
    runtime = installed / '.venv/host-agent/bin/python'
    runtime.write_text('#!/bin/sh\nif [ "$1" = "-c" ]; then exit 0; fi\nexec '
        + shlex.quote(sys.executable) + ' "$@"\n')
    runtime.chmod(0o700)
    (library / 'host-agent-install.sh').write_bytes((ROOT / 'installers/macos/lib/host-agent-install.sh').read_bytes())
    (library / 'constants.sh').write_text("""
ODS_AGENT_PLIST_LABEL=com.ods.host-agent
ODS_AGENT_PLIST="$HOME/Library/LaunchAgents/$ODS_AGENT_PLIST_LABEL.plist"
HOST_AGENT_BRIDGE_PLIST_LABEL=com.ods.host-agent-bridge
HOST_AGENT_BRIDGE_PLIST="$HOME/bridge.plist"
HOST_AGENT_BRIDGE_LOG="$HOME/bridge.log"
macos_normalize_agent_bind() { printf '%s\\n' "$1"; }
macos_bind_probe_host() { printf '%s\\n' "$1"; }
macos_bind_uses_direct_gateway() { return 1; }
""")
    (library / 'env-generator.sh').write_text("""
read_env_value() {
    case "$2" in
        ODS_AGENT_BIND) printf '127.0.0.1\\n' ;;
        ODS_AGENT_PORT) printf '7710\\n' ;;
        ODS_AGENT_KEY) printf 'fixture-private-key\\n' ;;
        ODS_AGENT_HOST) printf 'host.docker.internal\\n' ;;
        ODS_MACOS_HOST_AGENT_BRIDGE_ENABLED) printf 'false\\n' ;;
        *) printf '\\n' ;;
    esac
}
""")
    (library / 'bridge-manager.sh').write_text('macos_configure_port_bridge() { test "$1" = false; }\n')
    (library / 'host-agent-listener.sh').write_text('macos_retire_owned_host_agent_listener() { return 0; }\n')
    scripts = {
        'launchctl': 'printf "%s\\n" "$*" >> "$HOME/launchctl.calls"\n',
        'curl': 'exit 0\n',
        'sleep': 'exit 0\n',
        'docker': """
case "$1" in
    inspect) printf 'running\\n' ;;
    exec)
        IFS= read -r header
        test "$header" = 'Authorization: Bearer fixture-private-key' || exit 2
        printf '%s\\n' "$*" >> "$HOME/docker.calls"
        """ + "test \"$DOCKER_HOST\" = " + shlex.quote('unix:///verified.sock' if transport else '') + " || exit 3\n" + ('exit 0' if authenticated else 'exit 1') + """
        ;;
    *) exit 4 ;;
esac
""",
    }
    for name, body in scripts.items():
        path = binaries / name
        path.write_text('#!/bin/sh\n' + body)
        path.chmod(0o700)
    monkeypatch.setattr(continuation, 'HERE', library)
    environment = dict(HOME=str(home), PATH=str(binaries) + ':/usr/bin:/bin',
        DOCKER_HOST='unix:///verified.sock' if transport else '', DOCKER_CONFIG=str(home / 'docker-config'))
    if authenticated:
        continuation.restore_host_agent(installed, environment)
    else:
        with pytest.raises(ValueError, match='native-recovery-host-agent-failed'):
            continuation.restore_host_agent(installed, environment)
    plist_path = home / 'Library/LaunchAgents/com.ods.host-agent.plist'
    plist = plistlib.loads(plist_path.read_bytes())
    assert plist['ProgramArguments'][-2:] == ['--install-dir', str(installed)]
    assert plist['ProgramArguments'][0] == str(runtime)
    assert plist['EnvironmentVariables']['ODS_PYTHON_CMD'] == str(runtime)
    assert plist['EnvironmentVariables'].get('DOCKER_HOST') == ('unix:///verified.sock' if transport else None)
    assert plist['EnvironmentVariables'].get('DOCKER_CONFIG') == (str(home / 'docker-config') if transport else None)
    assert plist['EnvironmentVariables'].get('DOCKER_CONTEXT') == ('' if transport else None)
    calls = (home / 'docker.calls').read_text()
    assert 'fixture-private-key' not in calls
    assert len(calls.splitlines()) == (1 if authenticated else 20)
    diagnostic = (preparation / 'continuation-host-agent.log').read_text()
    assert 'fixture-private-key' not in diagnostic
    assert ('Dashboard container reached the authenticated host agent' in diagnostic) is authenticated


@pytest.fixture
def model_handoff(tmp_path, recommended_model):
    saved, catalog, bootstrap = recommended_model
    installed = tmp_path / 'retained ods'
    for relative in ('config', 'data', 'scripts', 'installers/macos/lib'):
        (installed / relative).mkdir(parents=True, exist_ok=True)
    env = installed / '.env'
    env.write_text(''.join(key + '=' + value + '\n' for key, value in saved.items()))
    env.chmod(0o600)
    (installed / 'config/model-library.json').write_text(json.dumps(catalog))
    (installed / 'installers/macos/lib/tier-map.sh').write_text(
        'BOOTSTRAP_GGUF_FILE=' + bootstrap['bootstrap_file'] + '\n'
        'BOOTSTRAP_LLM_MODEL=' + bootstrap['bootstrap_model'] + '\n'
        'BOOTSTRAP_MAX_CONTEXT=' + str(bootstrap['bootstrap_context']) + '\n')
    script = installed / 'scripts/bootstrap-upgrade.sh'
    script.write_text('#!/bin/bash\nexit 0\n')
    compose = installed / 'docker-compose.yml'
    compose.write_text('services: {}\n')
    environment = dict(HOME=str(tmp_path), PATH='/usr/bin:/bin',
        DOCKER_HOST='unix:///verified.sock', DOCKER_CONFIG=str(tmp_path / 'docker-config'))
    return installed, [compose], environment


@pytest.mark.parametrize('fault', [None, 'running', 'process-check', 'different-args',
    'environment', 'selection', 'spawn', 'metadata', 'pid-publish', 'cache-link', 'args-link', 'pid-link', 'log-link'])
def test_model_handoff_preserves_selection_and_refuses_unsafe_retries(model_handoff, monkeypatch, fault):
    installed, files, environment = model_handoff
    environment['ODS_PYTHON_CMD'] = '/retained/python'
    before = (installed / '.env').read_bytes()
    target = installed / 'untouched'
    target.write_text('untouched')
    target.chmod(0o600)
    links = {'cache-link': '.compose-flags', 'args-link': 'data/bootstrap-upgrade.args',
             'pid-link': 'data/bootstrap-upgrade.pid', 'log-link': 'logs/model-upgrade.log'}
    if fault in links:
        link = installed / links[fault]
        link.parent.mkdir(exist_ok=True)
        link.symlink_to(target)
    if fault == 'different-args':
        (installed / 'data/bootstrap-upgrade.args').write_text('another selection\n')
    def check(argv, **kwargs):
        assert argv[:3] == ['/usr/bin/pgrep', '-u', str(os.getuid())]
        return SimpleNamespace(returncode=0 if fault == 'running' else 2 if fault == 'process-check' else 1)
    monkeypatch.setattr(continuation.subprocess, 'run', check)
    calls = []
    def spawn(argv, **kwargs):
        calls.append(argv)
        assert argv[:3] == ['/bin/bash', str(installed / 'scripts/bootstrap-upgrade.sh'), str(installed)]
        assert kwargs['cwd'] == installed and kwargs['start_new_session'] and kwargs['close_fds']
        assert kwargs['env']['DOCKER_HOST'] == environment['DOCKER_HOST']
        assert kwargs['env']['ODS_PYTHON_CMD'] == environment['ODS_PYTHON_CMD']
        assert (installed / 'data/bootstrap-upgrade.args').read_text().splitlines() == argv[3:]
        assert (installed / '.compose-flags').read_text() == '-f docker-compose.yml\n'
        if fault == 'spawn': raise OSError('fixture launch failure')
        return SimpleNamespace(pid=54321)
    monkeypatch.setattr(continuation.subprocess, 'Popen', spawn)
    publish = continuation.publish_metadata
    def publish_record(path, body, expected):
        if fault == 'pid-publish' and path.name == 'bootstrap-upgrade.pid':
            raise OSError('fixture tracking failure')
        return publish(path, body, expected)
    monkeypatch.setattr(continuation, 'publish_metadata', publish_record)
    verifications = []
    def verify():
        verifications.append(True)
        if fault == 'environment': (installed / '.env').write_bytes(before + b'# concurrent owner edit\n')
        if fault == 'selection': raise ValueError('native-recovery-selection-changed')
        if fault == 'metadata' and len(verifications) == 2:
            (installed / 'data/bootstrap-upgrade.args').write_text('concurrent owner selection\n')
    if fault:
        with pytest.raises((ValueError, OSError)):
            continuation.resume_model_upgrade(installed, files, environment, verify_selection=verify)
        assert len(calls) == int(fault in ('spawn', 'pid-publish'))
        if fault == 'spawn':
            status = json.loads((installed / 'data/bootstrap-status.json').read_text())
            assert status['status'] == 'failed' and status['model'] == 'chosen.gguf'
            assert not (installed / 'data/bootstrap-upgrade.pid').exists()
        if fault == 'metadata':
            assert (installed / 'data/bootstrap-upgrade.args').read_text() == 'concurrent owner selection\n'
    else:
        result = continuation.resume_model_upgrade(installed, files, environment, verify_selection=verify)
        assert result == dict(status='download-started', modelId='chosen-q4', pid=54321)
        assert len(calls) == 1 and len(verifications) == 2
        assert (installed / 'data/bootstrap-upgrade.pid').read_text() == '54321\n'
        for path in ('.compose-flags', 'data/bootstrap-upgrade.args', 'data/bootstrap-upgrade.pid'):
            assert (installed / path).stat().st_mode & 0o777 == 0o600
    assert target.read_text() == 'untouched'
    if fault != 'environment': assert (installed / '.env').read_bytes() == before


def test_real_model_handoff_tracks_worker_and_refuses_duplicate(model_handoff, monkeypatch):
    installed, files, environment = model_handoff
    script = installed / 'scripts/bootstrap-upgrade.sh'
    script.write_text('''#!/bin/bash
printf '%s\\n' "$@" > "$1/data/observed.args"
while [[ ! -f "$1/data/release-fixture" ]]; do sleep 0.05; done
''')
    children = []
    popen = subprocess.Popen
    def capture(argv, **kwargs):
        process = popen(argv, **kwargs)
        if argv[:2] == ['/bin/bash', str(script)]: children.append(process)
        return process
    monkeypatch.setattr(continuation.subprocess, 'Popen', capture)
    try:
        result = continuation.resume_model_upgrade(installed, files, environment, verify_selection=lambda: None)
        assert len(children) == 1 and result['pid'] == children[0].pid
        deadline = time.monotonic() + 5
        while not (installed / 'data/observed.args').exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        observed = (installed / 'data/observed.args').read_text().splitlines()
        assert observed[0] == str(installed)
        assert observed[1:] == (installed / 'data/bootstrap-upgrade.args').read_text().splitlines()
        assert children[0].poll() is None
        with pytest.raises(ValueError, match='native-model-upgrade-already-running'):
            continuation.resume_model_upgrade(installed, files, environment, verify_selection=lambda: None)
        assert len(children) == 1
    finally:
        (installed / 'data/release-fixture').touch()
        for child in children:
            child.wait(timeout=5)


def test_model_handoff_runs_only_after_ready_selection_publication(retained):
    preparation, receipt, activation = retained
    calls = []
    def verify():
        calls.append('proof')
        return dict(status='active', runtimeDigest=receipt['runtimeDigest'], serviceDigest=receipt['serviceDigest'])
    def resume(unchanged):
        calls.append('model')
        unchanged()
        assert json.loads((preparation / 'selection-update.json').read_text())['activation']['status'] == 'ready'
        assert json.loads((preparation / 'activation.json').read_text()) == activation
        return {'status': 'download-started', 'pid': 12345}
    result = module.finish(preparation=preparation, receipt=receipt,
        run=lambda *args, **kwargs: SimpleNamespace(returncode=0), verify=verify,
        compose=SimpleNamespace(wait_ready=lambda run: None),
        selected_services={'dashboard-api': {}}, resume_model=resume)
    assert calls == ['proof', 'proof', 'model']
    assert result['modelUpgrade']['status'] == 'download-started'


def test_selected_model_still_publishes_missing_compose_cache_without_spawning(model_handoff, monkeypatch):
    installed, files, environment = model_handoff
    env = installed / '.env'
    env.write_text(env.read_text().replace('GGUF_FILE=starter.gguf', 'GGUF_FILE=chosen.gguf')
        .replace('LLM_MODEL=starter', 'LLM_MODEL=chosen').replace('CONTEXT=65536', 'CONTEXT=32768')
        .replace('CTX_SIZE=65536', 'CTX_SIZE=32768'))
    def forbidden(*args, **kwargs): pytest.fail('already selected model must not launch a worker')
    monkeypatch.setattr(continuation.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(continuation.subprocess, 'run', forbidden)
    result = continuation.resume_model_upgrade(installed, files, environment, verify_selection=lambda: None)
    assert result['status'] == 'selected-model'
    assert (installed / '.compose-flags').read_text() == '-f docker-compose.yml\n'
    assert not (installed / 'data/bootstrap-upgrade.args').exists()


def test_cli_reports_model_handoff_but_not_installer_completion(monkeypatch, capsys):
    monkeypatch.setattr(module, 'prepare_python', lambda path: None)
    def recover(install_dir, ods_source, **kwargs):
        assert kwargs == {'restore_host_agent': True, 'resume_model': True}
        return {'selection': '/fixture/selection-update.json',
                'modelUpgrade': {'status': 'download-started', 'pid': 12345}}
    monkeypatch.setattr(module, 'recover', recover)
    monkeypatch.setattr(module.sys, 'argv', ['recover', '--install-dir', '/fixture',
        '--restore-host-agent', '--resume-model'])
    assert module.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result['hostAgentReady'] is True
    assert result['modelUpgrade']['status'] == 'download-started'
    assert result['selection'] == '/fixture/selection-update.json'
    assert result['installerComplete'] is False


@pytest.mark.parametrize('saved,confirmed,selected,source', [
    (None, None, None, 'missing'), ('true', None, True, 'saved'),
    ('false', None, False, 'saved'), ('"false"', None, False, 'saved'),
    (None, 'enabled', True, 'confirmed'), (None, 'disabled', False, 'confirmed'),
    ('true', 'enabled', True, 'saved'), ('false', 'disabled', False, 'saved'),
])
def test_optional_inspection_preserves_choices_and_never_runs_setup(
        model_handoff, monkeypatch, saved, confirmed, selected, source):
    installed, _, _ = model_handoff
    environment = installed / '.env'
    if saved is not None:
        environment.write_text(environment.read_text() + 'ENABLE_OPENCODE=' + saved + '\n')
    before = environment.read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail('inspection must not execute setup, download or service commands')
    monkeypatch.setattr(continuation.subprocess, 'run', forbidden)
    monkeypatch.setattr(continuation.subprocess, 'Popen', forbidden)
    services = {'dashboard-api': {'environment': {'SECRET': 'do-not-print'}},
                'whisper': {}, 'perplexica': {}}
    result = continuation.inspect_remaining_setup(installed, services, opencode_choice=confirmed)
    assert result['status'] == 'continuation-inspection'
    assert result['installerComplete'] is False and result['protectedActivationVerified'] is False
    assert result['optionalSetup'] == {'opencode': {'selected': selected, 'source': source},
        'whisperModel': True, 'perplexica': True}
    assert result['requiresChoice'] == (['opencode'] if selected is None else [])
    assert ('opencode' in result['checksRequired']) == (selected is True)
    assert 'whisper-model-cache' in result['checksRequired']
    assert 'perplexica-inference-route' in result['checksRequired']
    assert result['model'] == {'status': 'upgrade-required', 'modelId': 'chosen-q4'}
    assert all(private not in json.dumps(result) for private in ('do-not-print', 'huggingface.co', str(installed)))
    assert environment.read_bytes() == before
    assert not (installed / '.compose-flags').exists()
    assert not (installed / 'data/bootstrap-upgrade.args').exists()
    core = continuation.inspect_remaining_setup(installed, {'dashboard-api': {}}, opencode_choice=confirmed)
    assert core['optionalSetup']['whisperModel'] is False
    assert core['optionalSetup']['perplexica'] is False


@pytest.mark.parametrize('body,confirmed,code', [
    ('ENABLE_OPENCODE=yes\n', None, 'invalid-retained-opencode-selection'),
    ('ENABLE_OPENCODE=\n', None, 'invalid-retained-opencode-selection'),
    ('ENABLE_OPENCODE=false\nENABLE_OPENCODE=true\n', None, 'duplicate-retained-opencode-setting'),
    ('ENABLE_OPENCODE=false\n', 'enabled', 'confirmed-opencode-selection-conflict'),
    ('ENABLE_OPENCODE=true\n', 'disabled', 'confirmed-opencode-selection-conflict'),
    ('', 'true', 'invalid-confirmed-opencode-selection'),
])
def test_optional_inspection_rejects_ambiguous_or_overridden_choices(model_handoff, body, confirmed, code):
    installed, _, _ = model_handoff
    environment = installed / '.env'
    environment.write_text(environment.read_text() + body)
    before = environment.read_bytes()
    with pytest.raises(ValueError, match=code):
        continuation.inspect_remaining_setup(installed, {'dashboard-api': {}}, opencode_choice=confirmed)
    assert environment.read_bytes() == before


def test_optional_inspection_refuses_environment_changed_during_model_planning(model_handoff, monkeypatch):
    installed, _, _ = model_handoff
    inspect = continuation.inspect_model_upgrade
    def change(*args, **kwargs):
        result = inspect(*args, **kwargs)
        path = installed / '.env'
        path.write_text(path.read_text() + 'ENABLE_OPENCODE=true\n')
        return result
    monkeypatch.setattr(continuation, 'inspect_model_upgrade', change)
    with pytest.raises(ValueError, match='retained-model-environment-changed'):
        continuation.inspect_remaining_setup(installed, {'dashboard-api': {}})


@pytest.mark.parametrize('choice', ['true', 'false'])
def test_installer_persists_opencode_before_pixel_can_fail(tmp_path, choice):
    installer = (ROOT / 'installers/macos/install-macos.sh').read_text()
    line = '    upsert_env_value "${INSTALL_DIR}/.env" "ENABLE_OPENCODE" "$ENABLE_OPENCODE"'
    assert installer.count(line) == 1
    assert installer.index(line) < installer.index('if ! /usr/bin/python3 "$LIB_DIR/pixel-native-install.py"')
    environment = tmp_path / '.env'
    environment.write_text('DASHBOARD_API_KEY=private-value\nENABLE_OPENCODE=' +
        ('false' if choice == 'true' else 'true') + '\n')
    environment.chmod(0o600)
    command = 'set -e; source "$1"; INSTALL_DIR="$2"; ENABLE_OPENCODE="$3";\n' + line + '\nexit 1'
    result = subprocess.run(['/bin/bash', '-c', command, 'test-retained-choice',
        str(ROOT / 'installers/macos/lib/env-generator.sh'), str(tmp_path), choice],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert 'private-value' not in result.stdout + result.stderr
    assert not result.stderr
    assert environment.read_text() == 'DASHBOARD_API_KEY=private-value\nENABLE_OPENCODE=' + choice + '\n'


def test_inspection_cli_never_reports_recovery_success(monkeypatch, capsys):
    monkeypatch.setattr(module, 'prepare_python', lambda path: None)
    def recover(install_dir, ods_source, **kwargs):
        assert kwargs == {'inspect_continuation': True, 'opencode_choice': 'disabled'}
        return {'status': 'continuation-inspection', 'installerComplete': False,
                'protectedActivationVerified': False}
    monkeypatch.setattr(module, 'recover', recover)
    monkeypatch.setattr(module.sys, 'argv', ['recover', '--install-dir', '/fixture',
        '--inspect-continuation', '--opencode-choice', 'disabled'])
    assert module.main() == 0
    output = capsys.readouterr()
    assert json.loads(output.out)['status'] == 'continuation-inspection'
    assert output.err == '' and 'native-pixel-ready' not in output.out


@pytest.mark.parametrize('extra', [
    ['--inspect-continuation', '--restore-host-agent'], ['--inspect-continuation', '--resume-model'],
    ['--inspect-continuation', '--restore-optional-tools'],
    ['--opencode-choice', 'disabled'],
])
def test_inspection_cli_rejects_mutating_options_before_recovery(monkeypatch, extra):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid inspection arguments must be rejected before recovery')
    monkeypatch.setattr(module, 'recover', forbidden)
    monkeypatch.setattr(module.sys, 'argv', ['recover', '--install-dir', '/fixture', *extra])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2


@pytest.mark.parametrize('fault', [None, 'setup', 'changed', 'proof'])
def test_optional_setup_runs_between_proofs_and_before_publication(retained, fault):
    preparation, receipt, activation = retained
    calls = []
    def run(*args, **kwargs):
        calls.append(args[0])
        return SimpleNamespace(returncode=0)
    compose = SimpleNamespace(wait_ready=lambda run: None)
    proofs = []
    def proof():
        proofs.append(True)
        calls.append('proof')
        if fault == 'proof' and len(proofs) == 2:
            raise ValueError('proof-failed')
        return {'status': 'active', 'runtimeDigest': receipt['runtimeDigest'],
                'serviceDigest': receipt['serviceDigest']}
    def optional(unchanged):
        assert not (preparation / 'selection-update.json').exists()
        unchanged()
        calls.append('optional')
        if fault == 'setup': raise ValueError('optional-failed')
        if fault == 'changed':
            (preparation / 'activation.json').write_text(json.dumps(dict(activation, phase='changed')))
        return {'status': 'ready'}
    def finish():
        return module.finish(preparation=preparation, receipt=receipt, run=run, verify=proof,
            compose=compose, selected_services={'dashboard-api': {}}, restore_optional_tools=optional,
            restore_host_agent=lambda: calls.append('host'))
    if fault:
        with pytest.raises(ValueError): finish()
        assert not (preparation / 'selection-update.json').exists()
    else:
        assert finish() == {'selection': str(preparation / 'selection-update.json'),
                            'optionalTools': {'status': 'ready'}}
        assert calls == ['proof', 'up', 'exec', 'optional', 'host', 'proof']


def test_optional_core_does_not_execute_setup_and_unknown_requires_confirmation(model_handoff, monkeypatch):
    installed, _, environment = model_handoff
    def forbidden(*args, **kwargs): pytest.fail('Core must not install unselected optional tools')
    monkeypatch.setattr(continuation, '_run_owner_setup', forbidden)
    with pytest.raises(ValueError, match='retained-opencode-choice-required'):
        continuation.restore_optional_tools(installed, {'dashboard-api': {}}, environment,
            verify_selection=lambda: None)
    before = (installed / '.env').read_bytes()
    result = continuation.restore_optional_tools(installed, {'dashboard-api': {}}, environment,
        opencode_choice='disabled', verify_selection=lambda: None)
    assert result['status'] == 'not-selected'
    assert result['selection']['opencode'] == {'selected': False, 'source': 'confirmed'}
    assert (installed / '.env').read_bytes() == before


@pytest.mark.parametrize('fault', ['changed-before', 'changed-after', 'proof', 'context', 'port', 'mode'])
def test_optional_setup_preserves_bound_environment_and_never_claims_partial_success(model_handoff, monkeypatch, fault):
    installed, _, environment = model_handoff
    path = installed / '.env'
    path.write_text(path.read_text() + 'ENABLE_OPENCODE=true\n')
    _, before = continuation.optional_setup_selection(installed, {'dashboard-api': {}})
    if fault == 'changed-before': path.write_text(path.read_text() + 'WHISPER_PORT=9100\n')
    if fault == 'context': path.write_text(path.read_text().replace('MAX_CONTEXT=65536', 'MAX_CONTEXT=invalid'))
    if fault == 'port': path.write_text(path.read_text() + 'WHISPER_PORT=70000\n')
    if fault == 'mode': path.write_text(path.read_text().replace('ODS_MODE=local', 'ODS_MODE=invalid'))
    calls = []
    def setup(*args, **kwargs):
        calls.append('setup')
        assert kwargs['extra_env']['ENABLE_OPENCODE'] == 'true'
        if fault == 'changed-after': path.write_text(path.read_text() + 'WHISPER_PORT=9100\n')
    def verify():
        if fault == 'proof': raise ValueError('changed-selection')
    monkeypatch.setattr(continuation, '_run_owner_setup', setup)
    with pytest.raises(ValueError):
        continuation.restore_optional_tools(installed, {'dashboard-api': {}}, environment,
            verify_selection=verify, expected_snapshot=before if fault == 'changed-before' else None)
    assert calls == (['setup'] if fault == 'changed-after' else [])


def test_cli_combined_setup_keeps_completion_pending(monkeypatch, capsys):
    monkeypatch.setattr(module, 'prepare_python', lambda path: None)
    def recover(install_dir, ods_source, **kwargs):
        assert kwargs == {'restore_host_agent': True, 'resume_model': True,
            'restore_optional_tools': True, 'opencode_choice': 'disabled'}
        return {'selection': '/fixture/selection-update.json', 'optionalTools': {'status': 'not-selected'},
                'modelUpgrade': {'status': 'download-started', 'pid': 12345}}
    monkeypatch.setattr(module, 'recover', recover)
    monkeypatch.setattr(module.sys, 'argv', ['recover', '--install-dir', '/fixture',
        '--restore-host-agent', '--restore-optional-tools', '--resume-model', '--opencode-choice', 'disabled'])
    assert module.main() == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result['hostAgentReady'] is True and result['installerComplete'] is False
    assert result['optionalTools']['status'] == 'not-selected'
    assert 'final service/Portal checks' in output.err


@pytest.mark.parametrize('error,expected', [
    (ValueError('private-token-value'), 'configuration or custody check failed'),
    (KeyError('private-config-key'), 'configuration field is missing'),
    (FileNotFoundError(2, 'private-path'), 'file or command is missing'),
    (PermissionError(13, 'private-path'), 'not accessible to the signed-in owner'),
    (OSError(5, 'private-path'), 'errno 5'),
    (subprocess.CalledProcessError(23, ['private-command', 'private-key'],
        output='private-output', stderr='private-error'), 'exited with status 23'),
    (subprocess.TimeoutExpired(['private-command', 'private-key'], 30,
        output='private-output', stderr='private-error'), 'command timed out'),
])
def test_recovery_failure_diagnostics_expose_category_without_private_text(error, expected):
    detail = module.failure_detail(error)
    assert expected in detail and '[native-recovery-check-failed]' in detail
    assert 'private-' not in detail


def test_recovery_failure_identifies_trusted_helper_line_without_source_or_locals():
    namespace = {}
    code = compile('def fail():\n    token = "do-not-print"\n    raise ValueError(token)\n',
        str(module.HERE / 'pixel-native-activate.py'), 'exec')
    exec(code, namespace)
    with pytest.raises(ValueError) as error:
        namespace['fail']()
    detail = module.failure_detail(error.value)
    assert 'pixel-native-activate.py:3' in detail
    assert 'do-not-print' not in detail and str(module.HERE) not in detail


def test_recovery_cli_stops_before_mutations_when_saved_python_fails(monkeypatch, capsys):
    def prepare(path):
        raise ValueError('recovery-python-dependency-unavailable')
    monkeypatch.setattr(module, 'prepare_python', prepare)
    monkeypatch.setattr(module, 'recover', lambda *a, **kw: pytest.fail('unsafe continuation'))
    monkeypatch.setattr(module.sys, 'argv', ['recover', '--install-dir', '/fixture'])
    assert module.main() == 1
    output = capsys.readouterr()
    assert not output.out
    assert '[recovery-python-dependency-unavailable]' in output.err
    assert 'cannot import PyYAML' in output.err


def test_recovery_cli_returns_selected_interpreters_exit_without_duplicate_work(monkeypatch, capsys):
    monkeypatch.setattr(module, 'prepare_python', lambda path: 19)
    monkeypatch.setattr(module, 'recover', lambda *a, **kw: pytest.fail('duplicate recovery'))
    monkeypatch.setattr(module.sys, 'argv', ['recover', '--install-dir', '/fixture'])
    assert module.main() == 19
    assert not capsys.readouterr().out


@pytest.mark.parametrize('fault', [None, 'download', 'foreign', 'launch', 'health', 'voice',
                                  'voice-download', 'voice-cache', 'perplexica'])
def test_optional_setup_real_shared_shell_preserves_selection_and_fails_closed(tmp_path, monkeypatch, fault):
    installed, home, library, binaries = (tmp_path / name for name in ('retained ods', 'home', 'mac/lib', 'bin'))
    preparation = installed / 'data/pixel-native/preparation'
    preparation.mkdir(parents=True, mode=0o700)
    for path in (home, library, binaries, library.parent / 'lib'):
        path.mkdir(parents=True, exist_ok=True)
    environment_file = installed / '.env'
    environment_file.write_text('ENABLE_OPENCODE=true\nODS_MODE=local\nLLM_MODEL=starter\n'
        'GGUF_FILE=starter.gguf\nMAX_CONTEXT=32768\nWHISPER_PORT=9100\n'
        'ODS_NATIVE_LLAMA_PORT=18080\nODS_MODEL_SWITCHBOARD=enabled\nLITELLM_KEY=fixture-private-key\n')
    environment_file.chmod(0o600)
    before = environment_file.read_bytes()
    original_load = continuation.load
    def load(name, path):
        if path.name == 'pixel-native-env.py': path = ROOT / 'installers/macos/lib' / path.name
        return original_load(name, path)
    monkeypatch.setattr(continuation, 'load', load)
    monkeypatch.setattr(continuation, 'HERE', library)
    (library / 'post-pixel-install.sh').write_bytes((ROOT / 'installers/macos/lib/post-pixel-install.sh').read_bytes())
    (library / 'host-agent-install.sh').write_bytes((ROOT / 'installers/macos/lib/host-agent-install.sh').read_bytes())
    (library / 'constants.sh').write_text('''
ODS_LOG_FILE=/must-not-write-global-log
OPENCODE_BIN="$HOME/opencode"
OPENCODE_CONFIG_DIR="$HOME/config"
OPENCODE_PORT=3003
OPENCODE_PLIST_LABEL=com.ods.opencode-web
OPENCODE_PLIST="$HOME/Library/LaunchAgents/$OPENCODE_PLIST_LABEL.plist"
OPENCODE_BUN_TMPDIR="$HOME/bun-tmp"
ODS_STT_CACHE_WAIT_SECONDS=1
macos_bind_probe_host() { printf '%s\\n' "$1"; }
''')
    (library / 'env-generator.sh').write_text('''
read_env_value() { awk -F= -v key="$2" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$1"; }
configure_perplexica() {
    test "$2" = ods/current && test "$3" = http://litellm:4000 && test "$4" = fixture-private-key || return 2
    printf 'perplexica\\n' >> "$HOME/setup.calls"
    ''' + ('return 1' if fault == 'perplexica' else 'return 0') + '\n}\n')
    (library / 'opencode-selection.sh').write_text('''
ods_macos_opencode_plist_owned() { return 0; }
ods_macos_opencode_loaded_owned() { ''' + ('return 1' if fault == 'foreign' else 'return 0') + '; }\n')
    runtime = library.parent.parent / 'lib/opencode-runtime.sh'
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text('ods_install_opencode() { printf "download\\n" >> "$HOME/setup.calls"; '
        + ('return 1' if fault == 'download' else 'printf "%s\\n" "$HOME/opencode"') + '; }\n')
    (home / 'opencode').write_text('#!/bin/sh\nexit 0\n')
    (home / 'opencode').chmod(0o700)
    scripts = {
        'sleep': 'exit 0\n',
        'launchctl': '''
case "$1" in
    print) test -f "$HOME/loaded" || exit 1; printf 'state = running\\n' ;;
    bootstrap) ''' + ('exit 1' if fault == 'launch' else 'touch "$HOME/loaded"') + ''' ;;
    enable|bootout) : ;;
    *) exit 2 ;;
esac
''',
        'curl': '''
printf '%s\\n' "$*" >> "$HOME/curl.calls"
case "$*" in
    *:3003*) ''' + ('exit 1' if fault == 'health' else 'exit 0') + ''' ;;
    *'-X POST'*) touch "$HOME/stt-triggered"; ''' + (
        'exit 28' if fault == 'voice-cache' else 'touch "$HOME/stt-cached"; exit 28') + ''' ;;
    *:9100/v1/models/Systran*) ''' + (
        'test -f "$HOME/stt-cached"' if fault in ('voice-download', 'voice-cache') else 'exit 0') + ''' ;;
    *:9100/v1/models*) ''' + ('exit 1' if fault == 'voice' else 'exit 0') + ''' ;;
    *) exit 2 ;;
esac
''',
    }
    if fault == 'foreign': (home / 'loaded').touch()
    for name, body in scripts.items():
        path = binaries / name
        path.write_text('#!/bin/sh\n' + body)
        path.chmod(0o700)
    environment = dict(HOME=str(home), PATH=str(binaries) + ':/usr/bin:/bin',
        DOCKER_HOST='unix:///verified.sock', DOCKER_CONFIG=str(home / 'docker-config'),
        BASH_ENV='/must-not-source', ENABLE_OPENCODE='false')
    proofs = []
    def restore():
        return continuation.restore_optional_tools(installed,
            {'dashboard-api': {}, 'whisper': {}, 'perplexica': {}}, environment,
            verify_selection=lambda: proofs.append('verify'))
    if fault not in (None, 'voice-download'):
        with pytest.raises(ValueError, match='native-recovery-optional-tools-failed'): restore()
        assert proofs == ['verify']
    else:
        result = restore()
        assert result['status'] == 'ready' and proofs == ['verify', 'verify']
        config = json.loads((home / 'config/opencode.json').read_text())
        assert config['model'] == 'llama-server/ods/current'
        assert config['provider']['llama-server']['options']['apiKey'] == 'fixture-private-key'
        document = plistlib.loads((home / 'Library/LaunchAgents/com.ods.opencode-web.plist').read_bytes())
        assert document['WorkingDirectory'] == str(installed)
        assert document['ProgramArguments'][-4:] == ['--port', '3003', '--hostname', '127.0.0.1']
        assert (home / 'setup.calls').read_text().splitlines() == ['download', 'perplexica']
        assert ':9100/v1/models/Systran%2Ffaster-whisper-base' in (home / 'curl.calls').read_text()
    if fault in ('voice-download', 'voice-cache'):
        assert (home / 'stt-triggered').exists()
        assert (home / 'stt-cached').exists() is (fault == 'voice-download')
    if fault == 'foreign': assert not (home / 'setup.calls').exists()
    assert environment_file.read_bytes() == before
    log = preparation / 'continuation-optional-tools.log'
    assert log.stat().st_mode & 0o777 == 0o600
    assert 'fixture-private-key' not in log.read_text()


@pytest.mark.parametrize(('fault', 'startup_delay'), [
    pytest.param(fault, 0, id=fault or 'ok')
    for fault in [None, 'redirect', 'unauthorized', 'malformed', 'oversize', 'slow-headers', 'slow-body']
] + [pytest.param('oversize', 0.6, id='oversize-delayed-start')])
def test_readiness_http_is_bounded_and_never_redirects_local_credentials(monkeypatch, fault, startup_delay):
    requests = []
    release = Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get('Authorization')))
            try:
                if fault == 'slow-headers':
                    self.connection.sendall(b'HTTP/1.1 200 OK\r\nX-Slow: ')
                    release.wait(20)
                    return
                self.send_response(302 if fault == 'redirect' else 401 if fault == 'unauthorized' else 200)
                if fault == 'redirect':
                    self.send_header('Location', 'http://127.0.0.1:' + str(self.server.server_port) + '/stolen')
                self.end_headers()
                if fault == 'slow-body':
                    self.wfile.write(b'{')
                    self.wfile.flush()
                    release.wait(20)
                    return
                self.wfile.write(b'x' * (readiness.MAX_RESPONSE + 1) if fault == 'oversize' else
                    b'fixture-private-key invalid-json' if fault == 'malformed' else b'{"ok":true}')
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    run = readiness.subprocess.run
    children = []
    expired = []
    def observed(command, **kwargs):
        children.append(command)
        assert 'fixture-private-key' not in ' '.join(command)
        assert json.loads(kwargs['input'])['key'] == 'fixture-private-key'
        if startup_delay:
            # Exercise the real worker after startup takes longer than the old
            # 0.4-second budget; interpreter startup is part of probe's deadline.
            prelude = ('import runpy,sys,time; time.sleep(float(sys.argv[1])); '
                'sys.argv=sys.argv[2:]; runpy.run_path(sys.argv[0],run_name="__main__")')
            command = [command[0], '-I', '-c', prelude, str(startup_delay), *command[2:]]
        try:
            result = run(command, **kwargs)
        except subprocess.TimeoutExpired:
            expired.append(True)
            raise
        assert 'fixture-private-key' not in result.stdout + result.stderr
        return result
    monkeypatch.setattr(readiness.subprocess, 'run', observed)
    try:
        started = time.monotonic()
        if fault:
            with pytest.raises(ValueError, match='^native-readiness-http-failed$'):
                readiness.probe(server.server_port, '/probe', key='fixture-private-key', timeout=5)
        else:
            assert readiness.probe(server.server_port, '/probe', key='fixture-private-key', timeout=5) == {'ok': True}
        assert time.monotonic() - started < 7.5
        assert requests == [('/probe', 'Bearer fixture-private-key')]
        assert len(children) == 1
        # Slow responses must hit the parent deadline, before the worker's
        # 10-second socket timeout or the server's 20-second release fallback.
        # Other faults must actually reach their response validation branch.
        assert expired == ([True] if fault in ('slow-headers', 'slow-body') else [])
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def test_readiness_http_deadline_includes_worker_startup(monkeypatch):
    run = readiness.subprocess.run
    expired = []
    def delayed_start(command, **kwargs):
        assert 'fixture-private-key' not in ' '.join(command)
        assert json.loads(kwargs['input'])['key'] == 'fixture-private-key'
        prelude = ('import runpy,sys,time; time.sleep(20); '
            'sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name="__main__")')
        try:
            return run([command[0], '-I', '-c', prelude, *command[2:]], **kwargs)
        except subprocess.TimeoutExpired:
            expired.append(True)
            raise
    monkeypatch.setattr(readiness.subprocess, 'run', delayed_start)
    started = time.monotonic()
    with pytest.raises(ValueError, match='^native-readiness-http-failed$'):
        readiness.probe(1, '/probe', key='fixture-private-key', timeout=0.4)
    assert time.monotonic() - started < 2.5
    assert expired == [True]


@pytest.fixture
def readiness_install(model_handoff):
    installed, _, _ = model_handoff
    path = installed / '.env'
    text = path.read_text().replace('GGUF_FILE=starter.gguf', 'GGUF_FILE=chosen.gguf')
    text = text.replace('LLM_MODEL=starter', 'LLM_MODEL=chosen').replace('=65536', '=32768')
    path.write_text(text + 'ENABLE_OPENCODE=false\nDASHBOARD_API_KEY=' + 'd' * 64 + '\n')
    return installed


def readiness_responses():
    return {
        (3001, '/'): {'httpStatus': 200},
        (3002, '/health'): {'status': 'ok'},
        (3002, '/api/apps/opencode'): {'id': 'opencode', 'state': 'not_installed', 'platform': 'darwin'},
        (8080, '/health'): {'status': 'ok'},
        (8080, '/v1/models'): {'data': [{'id': 'chosen.gguf'}]},
        (8080, '/props'): {'model_path': '/private/model-store/chosen.gguf', 'default_generation_settings': {'n_ctx': 32768}},
        (3002, '/api/models'): {'currentModel': 'chosen-q4'},
        (3002, '/api/extensions/catalog'): {'extensions': [{}], 'agent_available': True, 'library_available': True},
        (3002, '/api/pixel/status'): {'available': True, 'readiness': {'routeAvailable': True,
            'accessState': 'verified', 'effectiveMode': 'sandboxed', 'releaseState': 'unverified'}},
    }


@pytest.mark.parametrize('fault,failed', [
    (None, None), ('host', 'authenticated-host-agent'), ('model-file', 'selected-native-model'),
    ('context', 'selected-native-model'), ('context-bool', 'selected-native-model'),
    ('props-null', 'selected-native-model'), ('model-id', 'selected-native-model'),
    ('catalog', 'dashboard-model-selection'), ('extensions', 'extensions-catalog'),
    ('access', 'portal-access'), ('release-mismatch', 'portal-access'),
    ('portal-null', 'portal-access'), ('api-error', 'dashboard-api'),
])
def test_live_api_observer_requires_actual_identity_and_does_not_claim_complete_install(readiness_install, fault, failed):
    installed = readiness_install
    replies = readiness_responses()
    if fault == 'host': replies[(3002, '/api/apps/opencode')] = {'detail': 'not-reachable'}
    if fault == 'model-file': replies[(8080, '/props')]['model_path'] = '/private/starter.gguf'
    if fault == 'context': replies[(8080, '/props')]['default_generation_settings']['n_ctx'] = 8192
    if fault == 'context-bool': replies[(8080, '/props')]['default_generation_settings']['n_ctx'] = True
    if fault == 'props-null': replies[(8080, '/props')]['default_generation_settings'] = None
    if fault == 'model-id': replies[(8080, '/v1/models')]['data'] = [{'id': 'unchosen.gguf'}]
    if fault == 'catalog': replies[(3002, '/api/models')]['currentModel'] = 'starter'
    if fault == 'extensions': replies[(3002, '/api/extensions/catalog')]['agent_available'] = False
    if fault == 'access': replies[(3002, '/api/pixel/status')]['readiness']['accessState'] = 'transitioning'
    if fault == 'release-mismatch': replies[(3002, '/api/pixel/status')]['readiness']['releaseState'] = 'mismatch'
    if fault == 'portal-null': replies[(3002, '/api/pixel/status')]['readiness'] = None
    if fault == 'api-error': replies[(3002, '/health')] = {'error': 'fixture-private-body'}
    def request(port, path, **kwargs):
        assert kwargs.get('key', '') == ('d' * 64 if port == 3002 else '')
        return replies[(port, path)]
    before = continuation.saved_model_environment(installed)[1]
    result = readiness.observe_apis(installed, request=request)
    assert result['status'] == ('api-checks-passed' if fault is None else 'needs-attention')
    assert [item['name'] for item in result['checks'] if not item['passed']] == ([] if failed is None else [failed])
    assert result['installerComplete'] is False
    assert 'portal-chat-and-preview' in result['pendingVerification']
    assert 'd' * 64 not in json.dumps(result) and '/private/' not in json.dumps(result)
    assert continuation.saved_model_environment(installed)[1] == before


def test_live_api_observer_refuses_changed_environment(readiness_install):
    installed = readiness_install
    replies = readiness_responses()
    def request(port, path, **kwargs):
        if path == '/api/pixel/status':
            env = installed / '.env'
            env.write_text(env.read_text() + 'WHISPER_PORT=9100\n')
        return replies[(port, path)]
    with pytest.raises(ValueError, match='native-readiness-environment-changed'):
        readiness.observe_apis(installed, request=request)


@pytest.mark.parametrize('choice,state,passed', [
    ('true', 'running', True), ('true', 'stopped', False), (None, 'running', False),
])
def test_live_api_observer_requires_selected_opencode(readiness_install, choice, state, passed):
    env = readiness_install / '.env'
    env.write_text(env.read_text().replace('ENABLE_OPENCODE=false\n',
        '' if choice is None else 'ENABLE_OPENCODE=' + choice + '\n'))
    replies = readiness_responses()
    replies[(3002, '/api/apps/opencode')].update(
        state=state, installed=True, running=state == 'running')
    result = readiness.observe_apis(readiness_install, request=lambda p, path, **kw: replies[(p, path)])
    assert (result['status'] == 'api-checks-passed') is passed
    assert result['installerComplete'] is False


@pytest.mark.parametrize('alias,passed', [('ods/current', True), ('wrong-model', False)])
def test_live_api_observer_cloud_route_does_not_claim_inference(readiness_install, alias, passed):
    env = readiness_install / '.env'
    env.write_text(env.read_text().replace('ODS_MODE=local', 'ODS_MODE=cloud') +
        'LITELLM_KEY=fixture-cloud-key\n')
    replies = readiness_responses()
    replies[(4000, '/v1/models')] = {'data': [{'id': alias}]}
    def request(p, path, **kwargs):
        assert p != 8080
        if p == 4000:
            assert kwargs['key'] == 'fixture-cloud-key'
        return replies[(p, path)]
    result = readiness.observe_apis(readiness_install, request=request)
    assert (result['status'] == 'api-checks-passed') is passed
    assert 'model-completion' in result['pendingVerification']
    assert 'fixture-cloud-key' not in json.dumps(result)


@pytest.mark.parametrize('setting', ['DASHBOARD_API_PORT=0', 'DASHBOARD_PORT=65536',
    'DASHBOARD_API_PORT=not-a-port', 'DASHBOARD_API_KEY=invalid'])
def test_live_api_observer_rejects_invalid_endpoint_before_requests(readiness_install, setting):
    env = readiness_install / '.env'
    key = setting.split('=', 1)[0]
    lines = [line for line in env.read_text().splitlines() if not line.startswith(key + '=')]
    env.write_text('\n'.join(lines + [setting]) + '\n')
    def request(*args, **kwargs):
        pytest.fail('invalid configuration must not issue HTTP requests')
    with pytest.raises(ValueError):
        readiness.observe_apis(readiness_install, request=request)


@pytest.mark.parametrize('definition,rows,passed', [
    ({'healthcheck': {'test': ['CMD', 'true']}}, [{'State': 'running', 'Health': 'healthy'}], True),
    ({'healthcheck': {'test': ['CMD', 'true']}}, [{'State': 'running', 'Health': 'unhealthy'}], False),
    ({'healthcheck': {'test': ['CMD', 'true']}}, [{'State': 'running', 'Health': 'starting'}], False),
    ({'healthcheck': {'test': ['CMD', 'true']}}, [{'State': 'running', 'Health': ''}], False),
    ({}, [{'State': 'running', 'Health': ''}], True),
    ({'healthcheck': {'disable': True}}, [{'State': 'running', 'Health': ''}], True),
    ({'healthcheck': {'test': ['NONE']}}, [{'State': 'running', 'Health': ''}], True),
    ({}, [{'State': 'restarting'}], False),
    ({}, [{'State': 'paused'}], False),
    ({}, [], False),
    ({}, [{'State': 'running'}, {'State': 'running'}], False),
    ({'deploy': {'replicas': 2}}, [{'State': 'running'}, {'State': 'running'}], True),
    ({'scale': 2}, [{'State': 'running'}], False),
    ({'deploy': {'replicas': 0}}, [], True),
    ({'deploy': {'replicas': 0}}, [{'State': 'exited', 'ExitCode': 1}], True),
    ({'deploy': {'replicas': 0}}, [{'State': 'running'}], False),
])
def test_selected_service_health_interprets_compose_not_just_running_count(definition, rows, passed):
    assert readiness.service_checks({'service': definition}, [dict(row, Service='service') for row in rows]) == [
        {'name': 'service', 'passed': passed}]


@pytest.mark.parametrize('state,code,passed', [('exited', 0, True), ('exited', 1, False),
    ('exited', False, False), ('exited', '0', False), ('running', 0, False)])
def test_selected_service_health_requires_completed_init_exit_zero(state, code, passed):
    services = {'preview': {'depends_on': {'init': {'condition': 'service_completed_successfully'}}}, 'init': {}}
    rows = [{'Service': 'preview', 'State': 'running'}, {'Service': 'init', 'State': state, 'ExitCode': code}]
    assert readiness.service_checks(services, rows) == [
        {'name': 'preview', 'passed': True}, {'name': 'init', 'passed': passed}]


def test_rendered_profile_must_not_be_skipped_by_health_inspection():
    services = {'selected': {'profiles': ['manual'],
        'depends_on': {'service': {'condition': 'service_completed_successfully'}}}, 'service': {}}
    assert readiness.service_checks(services, [{'Service': 'service', 'State': 'exited', 'ExitCode': 0}]) == [
        {'name': 'selected', 'passed': False},
        {'name': 'service', 'passed': True}]


@pytest.mark.parametrize('services,rows', [({}, []), ({'bad name': {}}, []), ({'service': []}, []),
    ({'service': {'deploy': {'replicas': True}}}, []), ({'service': {'healthcheck': []}}, []),
    ({'service': {'deploy': []}}, []),
    ({'service': {}}, {}), ({'service': {}}, [None])])
def test_selected_service_health_rejects_ambiguous_shapes(services, rows):
    with pytest.raises(ValueError, match='native-readiness-services-invalid'):
        readiness.service_checks(services, rows)


@pytest.mark.parametrize('fault', [None, 'array', 'env', 'cache', 'gateway', 'config', 'project',
    'row-project', 'command', 'oversize', 'timeout', 'socket', 'alias'])
def test_service_observer_binds_installed_transport_and_never_mutates(readiness_install, monkeypatch, fault):
    installed = readiness_install
    compose_file = installed / 'compose.yml'
    compose_file.write_text('fixture')
    if fault == 'alias':
        original = installed / 'original.yml'
        compose_file.rename(original)
        compose_file.symlink_to(original)
    # Keep AF_UNIX paths short on macOS, independently of pytest's temp root.
    import tempfile
    with tempfile.TemporaryDirectory(dir='/private/tmp' if sys.platform == 'darwin' else '/tmp') as directory:
        endpoint = Path(directory) / 'docker.sock'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.bind(str(endpoint))
            environment = {'PIXEL_HISTORY_DOCKER': '/qualified/docker', 'PIXEL_HISTORY_PROJECT': 'ods',
                'PIXEL_HISTORY_IMAGE': 'sha256:' + 'a' * 64, 'PIXEL_HISTORY_USER': '501:20',
                'PATH': '/usr/bin:/bin', 'DOCKER_HOST': 'unix://' + str(endpoint),
                'DOCKER_CONFIG': str(installed / 'docker-config')}
            if fault == 'socket': environment['DOCKER_HOST'] = 'tcp://remote:2375'
            calls, events = [], []
            changed = False
            def gateway(*args):
                return ({'fixture': 'changed' if changed and fault == 'gateway' else 'same'}, environment)
            def flags(*args):
                return ['-f', 'other.yml' if changed and fault == 'cache' else 'compose.yml']
            def validate(*args):
                events.append('validate')
            original_load = readiness.load
            modules = {
                'pixel-macos-access-install': SimpleNamespace(
                    _launchd=SimpleNamespace(GATEWAY_PLIST='/qualified/gateway'), _source_gateway=gateway,
                    _native_transport_environment=lambda *args: events.append('transport')),
                'pixel-native-finalize': SimpleNamespace(compose_flags=flags),
                'pixel-native-stack': SimpleNamespace(resolve_files=lambda root, paths: paths),
                'pixel-native-compose': SimpleNamespace(validate_stack=validate),
            }
            monkeypatch.setattr(readiness, 'load', lambda name: modules[name] if name in modules else original_load(name))
            monkeypatch.setenv('DOCKER_HOST', 'tcp://must-not-use:2375')
            monkeypatch.setenv('COMPOSE_PROFILES', '*')
            monkeypatch.setenv('BASH_ENV', '/must-not-source')
            def run(command, **kwargs):
                nonlocal changed
                assert command[:2] == ['/qualified/docker', 'compose']
                assert command[command.index('--project-name') + 1] == 'ods'
                assert kwargs['env'] == {'HOME': readiness.pwd.getpwuid(os.getuid()).pw_dir,
                    'PATH': '/usr/bin:/bin', 'DOCKER_HOST': environment['DOCKER_HOST'],
                    'DOCKER_CONFIG': environment['DOCKER_CONFIG']}
                assert kwargs['timeout'] == 30 and kwargs['cwd'] == installed
                action = command[command.index('-f') + 2:]
                calls.append(action)
                assert action in (['config', '--format', 'json'], ['ps', '--all', '--format', 'json'])
                if fault == 'timeout': raise subprocess.TimeoutExpired(command, 30)
                if fault == 'command': return SimpleNamespace(returncode=1, stdout='private-token')
                if fault == 'oversize': return SimpleNamespace(returncode=0, stdout='x' * (4 * readiness.MAX_RESPONSE + 1))
                if action[0] == 'ps':
                    rows = [{'Project': 'wrong' if fault == 'row-project' else 'ods',
                             'Service': 'dashboard-api', 'State': 'running', 'Health': 'healthy'}]
                    changed = True
                    if fault == 'env':
                        path = installed / '.env'
                        path.write_text(path.read_text() + 'WHISPER_PORT=9100\n')
                    body = json.dumps(rows if fault == 'array' else rows[0])
                else:
                    body = json.dumps({'name': 'other' if fault == 'project' else 'ods', 'services': {
                        'dashboard-api': {'healthcheck': {'test': ['CMD', 'false' if changed and fault == 'config' else 'true']}}}})
                return SimpleNamespace(returncode=0, stdout=body)
            monkeypatch.setattr(readiness.subprocess, 'run', run)
            if fault not in (None, 'array'):
                with pytest.raises((ValueError, subprocess.TimeoutExpired)):
                    readiness.observe_services(installed)
            else:
                selected, checks = readiness.observe_services(installed)
                assert set(selected) == {'dashboard-api'}
                assert checks == [{'name': 'dashboard-api', 'passed': True}]
                assert [call[0] for call in calls] == ['config', 'ps', 'config']
            assert events[0] == 'transport'
            if fault in ('socket', 'alias'): assert not calls


@pytest.mark.parametrize('healthy,optional', [(True, False), (False, False), (True, True)])
def test_combined_readiness_only_removes_verified_pending_gates(readiness_install, monkeypatch, healthy, optional):
    services = {'dashboard-api': {}}
    if optional: services['whisper'] = {}
    monkeypatch.setattr(readiness, 'observe_services', lambda path: (
        services, [{'name': 'dashboard-api', 'passed': healthy}]))
    replies = readiness_responses()
    result = readiness.observe_apis(readiness_install, include_services=True,
        request=lambda p, path, **kwargs: replies[(p, path)])
    assert ('selected-service-health' in result['pendingVerification']) is not healthy
    assert ('selected-optional-state' in result['pendingVerification']) is optional
    assert result['installerComplete'] is False
    assert 'protected-recovery' in result['pendingVerification']
    assert result['status'] == ('api-checks-passed' if healthy else 'needs-attention')


@pytest.mark.parametrize('url,valid', [
    ('http://site-abc.localhost:9437/site-abc/', True),
    ('http://site-abc.localhost:9437/site-abc/index.html', True),
    ('http://site-abc.localhost:9437/site-def/', False),
    ('http://site-abc.localhost:9437/site-abc/?token=private', False),
    ('http://site-abc.localhost:9437/site-abc/#fragment', False),
    ('http://site-abc.localhost:9438/site-abc/', False),
    ('http://site-abc.localhost:bad/site-abc/', False),
    ('http://site-abc.localhost.evil:9437/site-abc/', False),
    ('http://user:private@site-abc.localhost:9437/site-abc/', False),
    ('http://127.0.0.1:9437/site-abc/', False),
    ('https://site-abc.localhost:9437/site-abc/', False),
])
def test_acceptance_preview_destination_never_uses_arbitrary_model_host(url, valid):
    assert (acceptance.preview_target(url, 9437) is not None) is valid


@pytest.mark.parametrize('mode,fault,passed', [
    ('chat', None, True), ('chat', 'no-done', False), ('chat', 'error', False),
    ('chat', 'empty-error', False),
    ('chat', 'wrong-answer', False), ('chat', 'malformed', False), ('chat', 'redirect', False),
    ('chat', 'wrong-type', False), ('chat', 'oversize', False), ('chat', 'stall', False),
    ('preview', None, True), ('preview', 'wrong-page', False), ('preview', 'preview-redirect', False),
    ('cancel', None, True),
])
def test_acceptance_worker_bounds_stream_and_keeps_credentials_on_api(mode, fault, passed, monkeypatch):
    requests, release = [], Event()
    marker = 'ODS_ACCEPT_' + 'a' * 32
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(('POST', self.path, self.headers.get('Authorization'), body))
            try:
                self.send_response(302 if fault == 'redirect' else 200)
                if fault == 'redirect': self.send_header('Location', 'http://must-not-follow.invalid/')
                self.send_header('Content-Type', 'application/json' if mode == 'cancel' or fault == 'wrong-type' else 'text/event-stream')
                self.end_headers()
                if mode == 'cancel':
                    self.wfile.write(b'{"aborted":true}')
                    return
                if fault == 'stall':
                    self.wfile.write(b'data: ')
                    self.wfile.flush()
                    release.wait(3)
                    return
                answer = marker if fault != 'wrong-answer' else 'wrong'
                if mode == 'preview': answer = f'http://site-abc.localhost:{self.server.server_port}/site-abc/'
                packet = {'choices': [{'delta': {'content': answer}}]}
                if fault == 'error': packet['error'] = 'private-upstream-detail'
                if fault == 'empty-error': packet['error'] = {}
                data = b'not-json' if fault == 'malformed' else json.dumps(packet).encode()
                if fault == 'oversize': data = b'x' * (acceptance.LIMIT + 1)
                self.wfile.write(b'data: ' + data + b'\n\n')
                if fault != 'no-done': self.wfile.write(b'data: [DONE]\n\n')
            except (BrokenPipeError, ConnectionResetError):
                pass
        def do_GET(self):
            requests.append(('GET', self.path, self.headers.get('Authorization'), self.headers.get('Host')))
            self.send_response(302 if fault == 'preview-redirect' else 200)
            self.send_header('Location', 'http://must-not-follow.invalid/')
            self.end_headers()
            self.wfile.write(('wrong' if fault == 'wrong-page' else '<h1>' + marker + '</h1>').encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = acceptance.subprocess.run
    def run(command, **kwargs):
        assert 'd' * 64 not in ' '.join(command)
        assert json.loads(kwargs['input'])['key'] == 'd' * 64
        return original(command, **kwargs)
    monkeypatch.setattr(acceptance.subprocess, 'run', run)
    try:
        payload = {'port': server.server_port, 'previewPort': server.server_port, 'mode': mode,
            'key': 'd' * 64, 'chatId': 'b' * 32, 'marker': marker}
        started = time.monotonic()
        result = acceptance.worker(payload, 0.4 if fault == 'stall' else 3)
        assert result.get('aborted' if mode == 'cancel' else 'passed') is passed
        assert time.monotonic() - started < 4
        assert requests[0][:3] == ('POST', '/api/pixel/chat/' + ('cancel' if mode == 'cancel' else 'stream'), 'Bearer ' + 'd' * 64)
        assert len(requests) == (2 if mode == 'preview' else 1)
        if mode == 'preview':
            assert requests[1] == ('GET', '/site-abc/', None, f'site-abc.localhost:{server.server_port}')
        assert 'private-upstream-detail' not in json.dumps(result) and 'd' * 64 not in json.dumps(result)
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize('fault', [None, 'readiness', 'chat', 'timeout', 'bad-done', 'preview', 'env', 'env-after-answer', 'after', 'after-error'])
def test_acceptance_records_before_dispatch_never_retries_and_cancels_only_its_chat(readiness_install, monkeypatch, fault):
    installed = readiness_install
    preparation = installed / 'data/pixel-native/preparation'
    preparation.mkdir(parents=True, mode=0o700)
    observed, dispatched, announcements = [], [], []
    def observe(*args, **kwargs):
        observed.append(True)
        assert kwargs['include_services'] is True
        if fault == 'after-error' and len(observed) > 1:
            raise ValueError('upstream-private-error')
        return {'status': 'needs-attention' if fault == 'readiness' or fault == 'after' and len(observed) > 1 else 'api-checks-passed',
                'installerComplete': False,
                'pendingVerification': ['protected-recovery', 'selected-optional-state', 'model-completion', 'portal-chat-and-preview']}
    original = acceptance.load
    monkeypatch.setattr(acceptance, 'load', lambda name: SimpleNamespace(observe_apis=observe, port=readiness.port)
        if name == 'pixel-native-readiness' else original(name))
    def run(payload, timeout):
        records = list(preparation.glob('acceptance-*.json'))
        assert len(records) == 1 and records[0].stat().st_mode & 0o777 == 0o600
        record = json.loads(records[0].read_text())
        assert any(item['chatId'] == payload['chatId'] for item in record['attempts'])
        assert 'd' * 64 not in records[0].read_text()
        dispatched.append(payload)
        if payload['mode'] == 'cancel':
            assert timeout == 30 and payload['chatId'] == dispatched[0]['chatId']
            return {'aborted': True}
        assert timeout == 600
        if fault in ('env', 'env-after-answer'):
            path = installed / '.env'
            path.write_text(path.read_text() + 'WHISPER_PORT=9100\n')
        failed = fault == payload['mode'] or fault in ('timeout', 'bad-done', 'env')
        return {'passed': fault == 'bad-done' or not failed, 'done': fault not in ('timeout', 'bad-done')}
    if fault == 'readiness':
        with pytest.raises(ValueError, match='acceptance-readiness-required'):
            acceptance.exercise(installed, run=run, announce=announcements.append)
        assert not dispatched and not list(preparation.glob('acceptance-*.json'))
        return
    result = acceptance.exercise(installed, run=run, announce=announcements.append)
    assert len(announcements) == 1
    assert result['status'] == ('functional-checks-passed' if fault is None else 'needs-attention')
    assert result['installerComplete'] is False
    assert [item['mode'] for item in dispatched] == (
        ['chat', 'cancel'] if fault in ('timeout', 'bad-done') else
        ['chat'] if fault in ('chat', 'env', 'env-after-answer') else ['chat', 'preview'])
    assert result == json.loads(next(preparation.glob('acceptance-*.json')).read_text())
    assert 'protected-recovery' in result['pendingVerification']
    assert 'selected-optional-state' in result['pendingVerification']
    assert ('model-completion' in result['pendingVerification']) is (fault is not None)
