"""Dashboard add-back recovers only definite WSL failures under selection custody."""
import subprocess
import types
from contextlib import contextmanager

import pytest
from test_host_agent import _mod as agent


@pytest.fixture
def selected(tmp_path, monkeypatch):
    extension = tmp_path / 'extensions/services/fixture'
    extension.mkdir(parents=True)
    (extension / 'compose.yaml').write_text('services: {}')
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    (scripts / 'wsl-bind-recovery.py').write_text('# installed helper')
    state = {'held': False, 'calls': []}

    @contextmanager
    def lock(*_args):
        state['held'] = True
        try:
            yield
        finally:
            state['held'] = False

    monkeypatch.setattr(agent, 'INSTALL_DIR', tmp_path)
    monkeypatch.setattr(agent, '_find_ext_dir', lambda _: extension)
    monkeypatch.setattr(agent, '_load_extension_selector', lambda: types.SimpleNamespace(
        _selection_lock=lock, SelectionError=ValueError))
    monkeypatch.setattr(agent.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(agent.platform, 'release', lambda: '6.6-microsoft-standard-WSL2')
    return state


@pytest.mark.parametrize('recovery_code', [0, 1, 3])
def test_one_recovery_keeps_lock_flags_environment_and_failure(selected, monkeypatch, recovery_code):
    flags = ['-f', 'base.yml', '-f', 'selected.yml']
    env = {'FIXTURE': 'retained'}

    def run(argv, **kwargs):
        assert selected['held']
        assert kwargs['env'] is env
        assert kwargs['cwd'] == str(agent.INSTALL_DIR)
        selected['calls'].append(argv)
        if len(selected['calls']) == 1:
            return subprocess.CompletedProcess(argv, 1, '', 'original mount failure')
        assert argv[-len(flags):] == flags
        assert argv[argv.index('--repair-stopped') + 1] == '--'
        assert argv[argv.index('--service') + 1] == 'fixture'
        assert 0 < kwargs['timeout'] <= 125
        return subprocess.CompletedProcess(argv, recovery_code, '', 'recovery evidence')

    monkeypatch.setattr(agent.subprocess, 'run', run)
    result = agent._run_selected_extension_up('fixture', flags, env=env)
    assert not selected['held']
    assert len(selected['calls']) == 2
    assert result.returncode == (0 if recovery_code == 0 else 1)
    if recovery_code == 3:
        assert result.stderr == 'original mount failure'
    elif recovery_code == 1:
        assert 'recovery evidence' in result.stderr


@pytest.mark.parametrize('case', ['healthy', 'timeout', 'linux'])
def test_no_recovery_for_success_timeout_or_non_wsl(selected, monkeypatch, case):
    if case == 'linux':
        monkeypatch.setattr(agent.platform, 'release', lambda: '6.8-generic')

    def run(argv, **kwargs):
        selected['calls'].append(argv)
        if case == 'timeout':
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        return subprocess.CompletedProcess(argv, 0 if case == 'healthy' else 1, '', 'failure')

    monkeypatch.setattr(agent.subprocess, 'run', run)
    if case == 'timeout':
        with pytest.raises(subprocess.TimeoutExpired):
            agent._run_selected_extension_up('fixture', ['-f', 'base.yml'])
    else:
        agent._run_selected_extension_up('fixture', ['-f', 'base.yml'])
    assert len(selected['calls']) == 1
    assert not selected['held']
