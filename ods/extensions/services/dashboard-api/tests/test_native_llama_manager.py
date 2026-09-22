import subprocess

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
    monkeypatch.setattr(host, '_native_llama_tuning_arguments', lambda env, binary: events.append(('qualify', binary)) or [])
    monkeypatch.setattr(host, '_stop_macos_native_llama_server', lambda *_: events.append('stop'))
    monkeypatch.setattr(host, '_configure_macos_llm_bridge', lambda *_: events.append('bridge'))
    monkeypatch.setattr(host, '_launch_native_llama_server', lambda *_: events.append('launch'))
    host._restart_macos_native_llama_server(root / '.env', root / 'bin/llama-server', root / 'log', root / 'pid')
    assert events == [('qualify', root / 'selected-runtime'), 'stop', 'bridge', 'launch']


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
