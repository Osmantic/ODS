import os
import shutil
import subprocess
from pathlib import Path

import pytest
import test_host_agent as fixtures

host = fixtures._mod


@pytest.fixture
def managed(tmp_path, monkeypatch):
    manager = tmp_path / 'installers/macos/lib/native-llama-service.sh'
    manager.parent.mkdir(parents=True)
    manager.touch()
    monkeypatch.setattr(host, 'INSTALL_DIR', tmp_path)
    monkeypatch.setattr(host.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(host, '_find_usable_bash', lambda: '/bin/bash')
    return tmp_path, manager


def test_stop_uses_launchd_even_without_pid_file(managed, monkeypatch):
    root, manager = managed
    calls = []
    monkeypatch.setattr(host.subprocess, 'run', lambda args, **kw: calls.append(args) or subprocess.CompletedProcess(args, 0))
    host._stop_macos_native_llama_server(root / 'missing.pid')
    assert calls[0][:3] == ['/bin/bash', str(manager), 'stop']


def test_stop_failure_never_falls_back_to_kill(managed, monkeypatch):
    root, _ = managed
    monkeypatch.setattr(host.subprocess, 'run', lambda args, **kw: subprocess.CompletedProcess(args, 1))
    monkeypatch.setattr(host.os, 'kill', lambda *_: pytest.fail('kill after unconfirmed stop'))
    with pytest.raises(RuntimeError, match='shutdown failed'):
        host._stop_macos_native_llama_server(root / 'missing.pid')


def test_launch_uses_shared_manager_not_popen(managed, monkeypatch):
    root, manager = managed
    monkeypatch.setattr(host, 'load_env', lambda _: {'GGUF_FILE': 'test.gguf', 'CTX_SIZE': '16384'})
    monkeypatch.setattr(host._model_stores, 'lemonade_profile', lambda *_: None)
    monkeypatch.setattr(host, '_active_model_directory', lambda _: root / 'data/models')
    monkeypatch.setattr(host, '_disable_conflicting_macos_bridge', lambda *_: None)
    calls = []
    def run(args, **kw):
        calls.append(args)
        (root / 'pid').write_text('4321\n', encoding='utf-8')
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(host.subprocess, 'run', run)
    monkeypatch.setattr(host.subprocess, 'Popen', lambda *_a, **_k: pytest.fail('unmanaged launch'))
    host._launch_native_llama_server(root / '.env', root / 'bin/llama-server', root / 'log', root / 'pid')
    assert calls[0][:3] == ['/bin/bash', str(manager), 'start']
    assert calls[0][calls[0].index('--ctx-size') + 1] == '16384'
    assert calls[0][calls[0].index('--alias') + 1] == 'test.gguf'
    # No tuning helper installed: one slot and the reasoning format still reach llama-server.
    assert calls[0][calls[0].index('--reasoning-format') + 1] == 'none'
    assert calls[0].count('--parallel') == 1 and calls[0][calls[0].index('--parallel') + 1] == '1'


def test_tuning_validator_failure_prevents_start(managed, monkeypatch):
    root, _ = managed
    (root / 'installers/macos/lib/native-checkpoint-args.py').touch()
    monkeypatch.setattr(host, 'load_env', lambda _: {'GGUF_FILE': 'test.gguf', 'LLAMA_ARG_SLEEP_IDLE_SECONDS': '120'})
    monkeypatch.setattr(host._model_stores, 'lemonade_profile', lambda *_: None)
    monkeypatch.setattr(host, '_active_model_directory', lambda _: root / 'data/models')
    monkeypatch.setattr(host, '_disable_conflicting_macos_bridge', lambda *_: None)
    calls = []
    monkeypatch.setattr(host.subprocess, 'run', lambda args, **kw: calls.append(args) or subprocess.CompletedProcess(args, 1))
    with pytest.raises(RuntimeError, match='tuning was rejected'):
        host._launch_native_llama_server(root / '.env', root / 'bin/llama-server', root / 'log', root / 'pid')
    assert len(calls) == 1
    assert '--idle-seconds=120' in calls[0]


def test_invalid_tuning_preserves_running_listener_and_bridge(managed, monkeypatch):
    root, _ = managed
    monkeypatch.setattr(host, '_require_macos_bridge_manager', lambda _: None)
    monkeypatch.setattr(host, 'load_env', lambda _: {'GGUF_FILE': 'test.gguf', 'LLAMA_ARG_CACHE_RAM': '-5'})
    monkeypatch.setattr(host._model_stores, 'lemonade_profile', lambda *_: None)
    monkeypatch.setattr(host, '_stop_macos_native_llama_server', lambda *_: pytest.fail('stopped healthy model'))
    monkeypatch.setattr(host, '_configure_macos_llm_bridge', lambda *_: pytest.fail('changed bridge'))
    with pytest.raises(RuntimeError, match='validator is missing'):
        host._restart_macos_native_llama_server(root / '.env', root / 'bin/llama-server', root / 'log', root / 'pid')


def test_restart_qualifies_selected_profile_before_stop(managed, monkeypatch):
    root, _ = managed
    events = []
    monkeypatch.setattr(host, '_require_macos_bridge_manager', lambda _: None)
    monkeypatch.setattr(host, 'load_env', lambda _: {'GGUF_FILE': 'test.gguf'})
    monkeypatch.setattr(host._model_stores, 'lemonade_profile', lambda *_: {'executable': str(root / 'selected-runtime')})
    monkeypatch.setattr(host, '_native_llama_tuning_arguments',
                        lambda env, binary, defaults=True: events.append(('qualify', binary, defaults)) or [])
    monkeypatch.setattr(host, '_stop_macos_native_llama_server', lambda *_: events.append('stop'))
    monkeypatch.setattr(host, '_configure_macos_llm_bridge', lambda *_: events.append('bridge'))
    monkeypatch.setattr(host, '_launch_native_llama_server', lambda *_: events.append('launch'))
    host._restart_macos_native_llama_server(root / '.env', root / 'bin/llama-server', root / 'log', root / 'pid')
    # A registered profile keeps its own arguments: no macOS defaults.
    assert events == [('qualify', root / 'selected-runtime', False), 'stop', 'bridge', 'launch']


def test_tuning_arguments_preserve_zero_and_idle(managed, monkeypatch):
    root, _ = managed
    (root / 'installers/macos/lib/native-checkpoint-args.py').touch()
    commands = []
    def run(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=b'--ctx-checkpoints\x000\x00--sleep-idle-seconds\x00120\x00')
    monkeypatch.setattr(host.subprocess, 'run', run)
    result = host._native_llama_tuning_arguments({'LLAMA_ARG_CTX_CHECKPOINTS': '0', 'LLAMA_ARG_SLEEP_IDLE_SECONDS': '120'}, root / 'binary')
    assert result == ['--ctx-checkpoints', '0', '--sleep-idle-seconds', '120']
    assert '--checkpoints=0' in commands[0]


def test_tuning_arguments_read_the_installer_checkpoint_keys(managed, monkeypatch):
    """Dashboard restarts must honour the same .env keys as native-model.sh."""
    root, _ = managed
    (root / 'installers/macos/lib/native-checkpoint-args.py').touch()
    commands = []
    def run(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=b'')
    monkeypatch.setattr(host.subprocess, 'run', run)
    host._native_llama_tuning_arguments({'LLAMA_ARG_CHECKPOINT_EVERY_NT': '1024'}, root / 'binary')
    host._native_llama_tuning_arguments({'LLAMA_ARG_CHECKPOINT_MIN_SPACING_NT': '2048'}, root / 'binary')
    assert '--interval=1024' in commands[0]
    assert '--min-spacing=2048' in commands[1]
    # Former names are not read by llama.cpp or by the installer.
    commands.clear()
    stale = {'LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS': '-1', 'LLAMA_ARG_CHECKPOINT_MIN_STEP': '512'}
    assert host._native_llama_tuning_arguments(stale, root / 'binary', defaults=False) == []
    assert commands == []
    # With the macOS defaults the qualifier still runs, but gets empty values.
    assert host._native_llama_tuning_arguments(stale, root / 'binary') == []
    assert '--interval=' in commands[0] and '--min-spacing=' in commands[0]
    assert '--interval=-1' not in commands[0] and '--min-spacing=512' not in commands[0]


def test_darwin_restart_applies_macos_defaults_through_the_qualifier(managed, monkeypatch):
    root, _ = managed
    (root / 'installers/macos/lib/native-checkpoint-args.py').touch()
    commands = []
    def run(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=b'--ctx-checkpoints\x0032\x00--spec-type\x00ngram-mod\x00')
    monkeypatch.setattr(host.subprocess, 'run', run)
    result = host._native_llama_tuning_arguments({'LLAMA_SPEC_TYPE': 'none'}, root / 'binary', reasoning_format='none',
                                                 model_path=root / 'models/Qwen3.5-9B-Q4_K_M.gguf')
    assert result == ['--ctx-checkpoints', '32', '--spec-type', 'ngram-mod']
    assert commands[0][1:4] == [str(root / 'installers/macos/lib/native-checkpoint-args.py'), '--binary', str(root / 'binary')]
    assert '--apply-defaults' in commands[0]
    assert '--spec-default=none' in commands[0]
    assert '--explicit-spec-type=' in commands[0]
    assert '--reasoning-mode=' in commands[0]
    assert '--reasoning-format-fallback=none' in commands[0]
    # The helper owns --parallel and picks the slot layout for this GGUF.
    assert '--parallel=' in commands[0]
    assert '--model=' + str(root / 'models/Qwen3.5-9B-Q4_K_M.gguf') in commands[0]
    host._native_llama_tuning_arguments({'LLAMA_PARALLEL': ' 3 '}, root / 'binary')
    assert '--parallel=3' in commands[1] and '--model=' in commands[1]
    # Registered profiles keep their own --parallel.
    host._native_llama_tuning_arguments({'LLAMA_PARALLEL': '3', 'LLAMA_ARG_SPEC_DRAFT_N_MAX': '2'}, root / 'binary',
                                        defaults=False)
    assert not any(part.startswith(('--parallel=', '--model=')) for part in commands[2])


@pytest.mark.skipif(os.name == 'nt', reason='needs an executable shell script as the runtime')
@pytest.mark.parametrize('release,expected', [
    ('b8210', ['--parallel', '1', '--draft-max', '3', '--ctx-checkpoints', '32', '--reasoning-format', 'none']),
    ('b9014', ['--parallel', '1', '--spec-draft-n-max', '3', '--ctx-checkpoints', '32', '--spec-type', 'ngram-mod',
               '--reasoning', 'off']),
])
def test_real_qualifier_accepts_the_host_agent_command(managed, release, expected):
    """The host agent's argv must parse in the shipped helper, per installed runtime."""
    root, _ = managed
    ods = Path(__file__).resolve().parents[4]
    shutil.copy(ods / 'installers/macos/lib/native-checkpoint-args.py', root / 'installers/macos/lib/')
    runtime = root / 'llama-server'
    runtime.write_text(f"#!/bin/sh\n[ \"$1\" = --help ] && exec cat '{ods / 'tests/fixtures/llama-server-help' / (release + '.txt')}'\nexit 9\n")
    runtime.chmod(0o755)
    env = {'LLAMA_ARG_SPEC_DRAFT_N_MAX': '3', 'LLAMA_REASONING': 'off'}
    # No catalog in this install: the one-slot default, even for a catalog GGUF name.
    assert host._native_llama_tuning_arguments(env, runtime, reasoning_format='none',
                                               model_path=root / 'data/models/Qwen3.5-9B-Q4_K_M.gguf') == expected


def test_missing_qualifier_only_skips_defaults(managed, monkeypatch):
    root, _ = managed
    monkeypatch.setattr(host.subprocess, 'run', lambda *_a, **_k: pytest.fail('no qualifier to run'))
    # The caller dropped its own --parallel and --reasoning-format, so the fallback still carries them.
    assert host._native_llama_tuning_arguments({}, root / 'binary') == ['--parallel', '1']
    assert host._native_llama_tuning_arguments({'LLAMA_PARALLEL': '2'}, root / 'binary') == ['--parallel', '2']
    assert host._native_llama_tuning_arguments({}, root / 'binary', reasoning_format='none') == [
        '--parallel', '1', '--reasoning-format', 'none']
    assert host._native_llama_tuning_arguments({'LLAMA_PARALLEL': '2'}, root / 'binary', defaults=False) == []
    with pytest.raises(RuntimeError, match='validator is missing'):
        host._native_llama_tuning_arguments({'LLAMA_ARG_SPEC_DRAFT_N_MAX': '3'}, root / 'binary')


def _capture_launch(root, monkeypatch, env, profile=None):
    (root / 'installers/macos/lib/native-checkpoint-args.py').touch()
    monkeypatch.setattr(host, 'load_env', lambda _: env)
    monkeypatch.setattr(host._model_stores, 'lemonade_profile', lambda *_: profile)
    monkeypatch.setattr(host, '_active_model_directory', lambda _: root / 'data/models')
    monkeypatch.setattr(host, '_disable_conflicting_macos_bridge', lambda *_: None)
    calls = []
    def run(args, **kw):
        calls.append(args)
        if 'native-checkpoint-args.py' in str(args[1]):
            if '--apply-defaults' not in args:
                return subprocess.CompletedProcess(args, 0, stdout=b'--draft-max\x003\x00')
            return subprocess.CompletedProcess(args, 0, stdout=b'--parallel\x002\x00--kv-unified\x00--draft-max\x003\x00'
                                               b'--ctx-checkpoints\x0064\x00--cache-ram\x000\x00')
        (root / 'pid').write_text('4321\n', encoding='utf-8')
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(host.subprocess, 'run', run)
    host._launch_native_llama_server(root / '.env', root / 'bin/llama-server', root / 'log', root / 'pid')
    return calls


def test_darwin_launch_spells_draft_flags_through_the_qualifier(managed, monkeypatch):
    """--spec-draft-n-max stopped the b8210 Mac server; the qualifier picks the runtime's spelling."""
    root, manager = managed
    env = {'GGUF_FILE': 'test.gguf', 'LLAMA_ARG_SPEC_TYPE': 'ngram-mod', 'LLAMA_ARG_SPEC_DRAFT_N_MAX': '3',
           'LLAMA_ARG_SPEC_DRAFT_TYPE_K': 'q8_0'}
    qualify, start = _capture_launch(root, monkeypatch, env)
    assert '--draft-n-max=3' in qualify and '--draft-type-k=q8_0' in qualify
    assert '--explicit-spec-type=ngram-mod' in qualify and '--apply-defaults' in qualify
    assert start[:3] == ['/bin/bash', str(manager), 'start']
    assert start[start.index('--spec-type') + 1] == 'ngram-mod'
    assert start[-9:] == ['--parallel', '2', '--kv-unified', '--draft-max', '3',
                           '--ctx-checkpoints', '64', '--cache-ram', '0']
    assert '--spec-draft-n-max' not in start and '--spec-draft-type-k' not in start
    # Reasoning flags come from the qualifier (--reasoning on b9014); no format here.
    assert '--reasoning-format-fallback=none' in qualify and '--reasoning-mode=' in qualify
    assert '--reasoning-format' not in start
    # The slot count comes only from the qualifier, which gets LLAMA_PARALLEL and the GGUF.
    assert start.count('--parallel') == 1
    assert '--parallel=' in qualify
    assert '--model=' + str(root / 'data/models/test.gguf') in qualify


def test_darwin_profile_launch_gets_no_macos_defaults(managed, monkeypatch):
    root, _ = managed
    profile = {'executable': str(root / 'profile-runtime')}
    qualify, start = _capture_launch(root, monkeypatch, {'GGUF_FILE': 'test.gguf', 'LLAMA_ARG_SPEC_DRAFT_N_MAX': '2'}, profile)
    assert qualify[qualify.index('--binary') + 1] == str(root / 'profile-runtime')
    assert '--draft-n-max=2' in qualify
    assert '--apply-defaults' not in qualify
    assert not any(part.startswith(('--spec-default=', '--reasoning-mode=', '--parallel=', '--model='))
                   for part in qualify)
    assert start[start.index('--reasoning-format') + 1] == 'none'
    # A registered profile keeps the launcher's own --parallel (LLAMA_PARALLEL, default 1).
    assert start.count('--parallel') == 1 and start[start.index('--parallel') + 1] == '1'
    assert '--kv-unified' not in start


def test_windows_launch_keeps_its_direct_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(host, 'INSTALL_DIR', tmp_path)
    monkeypatch.setattr(host.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(host, 'load_env', lambda _: {'GGUF_FILE': 'test.gguf', 'LLAMA_ARG_SPEC_DRAFT_N_MAX': '3'})
    monkeypatch.setattr(host._model_stores, 'lemonade_profile', lambda *_: None)
    monkeypatch.setattr(host, '_active_model_directory', lambda _: tmp_path / 'models')
    monkeypatch.setattr(host, '_disable_conflicting_macos_bridge', lambda *_: None)
    probes = []

    def run(args, **_k):
        # Only the --help probe for --reasoning may run; never the macOS qualifier.
        if list(args[1:]) != ['--help']:
            pytest.fail('macOS qualifier ran on Windows')
        probes.append(list(args))
        return subprocess.CompletedProcess(args, 0, '--reasoning-format FORMAT\n', '')

    monkeypatch.setattr(host.subprocess, 'run', run)
    launched = []
    class Process:
        pid = 99
    monkeypatch.setattr(host.subprocess, 'Popen', lambda args, **_k: launched.append(args) or Process())
    host._launch_native_llama_server(tmp_path / '.env', tmp_path / 'llama-server.exe', tmp_path / 'log', tmp_path / 'pid')
    assert probes == [[str(tmp_path / 'llama-server.exe'), '--help']]
    assert launched[0][launched[0].index('--spec-draft-n-max') + 1] == '3'
    assert '--ctx-checkpoints' not in launched[0]
    # Windows keeps one slot from LLAMA_PARALLEL; the macOS layout never applies.
    assert launched[0].count('--parallel') == 1 and launched[0][launched[0].index('--parallel') + 1] == '1'
    assert '--kv-unified' not in launched[0]
    # A runtime without --reasoning (b8248) keeps the format mapping.
    assert launched[0][launched[0].index('--reasoning-format') + 1] == 'none'
    assert '--reasoning' not in launched[0]
