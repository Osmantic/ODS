"""Fresh-terminal recovery keeps the installer venv without sourcing .env."""
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('recovery_python',
    ROOT / 'installers/macos/lib/pixel-native-recovery-python.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def retained_environment(monkeypatch, text):
    monkeypatch.setattr(module.environment_file, 'snapshot', lambda path: (text.encode(), 'identity'))


def test_saved_python_parses_only_private_setting_without_shell_evaluation(tmp_path, monkeypatch):
    selected = str(tmp_path / 'installer Python/bin/python')
    retained_environment(monkeypatch, 'ODS_PYTHON_CMD="' + selected.replace('\\', '\\\\') + '"\n'
        'DASHBOARD_API_KEY=must-not-expose\nOTHER=$(touch should-not-run)\n')
    assert module.saved_python(tmp_path) == selected


@pytest.mark.parametrize('first', ['', '/old/python'])
def test_saved_python_rejects_duplicates_including_empty_values(tmp_path, monkeypatch, first):
    retained_environment(monkeypatch, 'ODS_PYTHON_CMD=' + first + '\nODS_PYTHON_CMD=/new/python\n')
    with pytest.raises(ValueError, match='duplicate-recovery-python-setting'):
        module.saved_python(tmp_path)


def test_python_selection_does_not_bypass_private_environment_validation(tmp_path, monkeypatch):
    def reject(path):
        assert path == tmp_path / '.env'
        raise ValueError('private-owner-environment-required')
    monkeypatch.setattr(module.environment_file, 'snapshot', reject)
    with pytest.raises(ValueError, match='private-owner-environment-required'):
        module.selected_python(tmp_path)


@pytest.mark.parametrize('choice', ['', 'python3', 'python3 -c malicious', '../python', '/bin/sh', '/tmp/python\n'])
def test_invalid_interpreter_never_executes(choice, tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'saved_python', lambda root: choice)
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw: pytest.fail('invalid executable was run'))
    with pytest.raises(ValueError, match='saved-recovery-python-invalid'):
        module.prepare_environment(tmp_path, {})


def test_selection_preserves_venv_spelling_and_does_not_inherit_stale_override(tmp_path, monkeypatch):
    path = tmp_path / 'private venv/bin/python'
    path.parent.mkdir(parents=True)
    path.write_text('fixture')
    path.chmod(0o700)
    monkeypatch.setattr(module, 'saved_python', lambda root: str(path))
    monkeypatch.setattr(module.Path, 'stat', lambda *a, **kw:
        SimpleNamespace(st_mode=stat.S_IFREG | 0o755, st_uid=501))
    monkeypatch.setattr(module.os, 'getuid', lambda: 501, raising=False)
    monkeypatch.setattr(module.os, 'access', lambda *a: True)
    # Never resolve a venv's bin/python to its base interpreter.
    monkeypatch.setattr(module.Path, 'resolve', lambda *a, **kw: pytest.fail('venv identity discarded'))
    assert module.selected_python(tmp_path) == str(path.absolute())


@pytest.mark.parametrize('mode,uid,executable', [
    (stat.S_IFREG | 0o777, 501, True), (stat.S_IFDIR | 0o755, 501, True),
    (stat.S_IFREG | 0o755, 502, True), (stat.S_IFREG | 0o644, 501, False),
])
def test_unsafe_python_is_refused_before_probe(tmp_path, monkeypatch, mode, uid, executable):
    monkeypatch.setattr(module, 'saved_python', lambda root: str(tmp_path / 'python'))
    monkeypatch.setattr(module.Path, 'stat', lambda *a, **kw: SimpleNamespace(st_mode=mode, st_uid=uid))
    monkeypatch.setattr(module.os, 'getuid', lambda: 501, raising=False)
    monkeypatch.setattr(module.os, 'access', lambda *a: executable)
    with pytest.raises(ValueError, match='saved-recovery-python-invalid'):
        module.selected_python(tmp_path)


def test_missing_saved_interpreter_is_not_replaced_by_path_python(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'saved_python', lambda root: str(tmp_path / 'python'))
    with pytest.raises(ValueError, match='saved-recovery-python-unavailable'):
        module.selected_python(tmp_path)


def test_probe_reuses_saved_interpreter_and_strips_foreign_python_environment(tmp_path, monkeypatch):
    selected = str(tmp_path / 'installer venv/bin/python')
    monkeypatch.setattr(module, 'selected_python', lambda root: selected)
    calls = []
    def run(arguments, **kwargs):
        calls.append(arguments)
        assert arguments == [selected, '-E', '-c', 'import yaml']
        assert kwargs['env']['ODS_PYTHON_CMD'] == selected
        assert 'PYTHONHOME' not in kwargs['env'] and 'PYTHONPATH' not in kwargs['env']
        assert kwargs['stdin'] == kwargs['stdout'] == kwargs['stderr'] == subprocess.DEVNULL
        assert kwargs['timeout'] == 15
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(module.subprocess, 'run', run)
    original = {'ODS_PYTHON_CMD': '/stale/python', 'PATH': '/usr/bin:/bin',
                'PYTHONHOME': '/unrelated', 'PYTHONPATH': '/unrelated'}
    result = module.prepare_environment(tmp_path, original)
    assert result['ODS_PYTHON_CMD'] == selected and len(calls) == 1
    assert original['ODS_PYTHON_CMD'] == '/stale/python' and 'PYTHONHOME' in original


def test_missing_yaml_has_actionable_code_without_private_child_output(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'selected_python', lambda root: sys.executable)
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw:
        SimpleNamespace(returncode=1, stdout='private-key', stderr='private-path'))
    with pytest.raises(ValueError, match='^recovery-python-dependency-unavailable$'):
        module.prepare_environment(tmp_path, {})


def test_real_dependency_probe_uses_explicit_interpreter(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'selected_python', lambda root: sys.executable)
    result = module.prepare_environment(tmp_path, dict(os.environ,
        ODS_PYTHON_CMD='not-an-interpreter', PYTHONHOME='not-a-python-home'))
    assert result['ODS_PYTHON_CMD'] == sys.executable


def test_recovery_keeps_an_installer_dependency_in_the_owners_user_site(tmp_path, monkeypatch):
    environment = dict(os.environ, HOME=str(tmp_path), APPDATA=str(tmp_path))
    probe = subprocess.run([sys.executable, '-E', '-c',
        'import site; print(site.getusersitepackages() if site.ENABLE_USER_SITE else "")'],
        env=environment, capture_output=True, text=True, check=True)
    if not probe.stdout.strip():
        pytest.skip('This interpreter disables its user site')
    user_site = Path(probe.stdout.strip())
    if tmp_path not in user_site.parents:
        pytest.skip('This interpreter does not derive its user site from the test home')
    user_site.mkdir(parents=True)
    imported = tmp_path / 'dependency-imported'
    (user_site / 'yaml.py').write_text(
        'from pathlib import Path\nPath(' + repr(str(imported)) + ').touch()\n')
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (foreign / 'yaml.py').write_text('raise RuntimeError("foreign Python path was used")\n')
    monkeypatch.setattr(module, 'selected_python', lambda root: sys.executable)
    module.prepare_environment(tmp_path, dict(environment, PYTHONPATH=str(foreign)))
    assert imported.is_file(), 'The saved interpreter must retain its legitimate user-site dependency'


def test_relaunch_keeps_argument_boundaries_and_returns_child_result(tmp_path, monkeypatch):
    selected = str(tmp_path / 'installer venv/bin/python')
    environment = {'ODS_PYTHON_CMD': selected, 'PATH': '/usr/bin:/bin'}
    monkeypatch.setattr(module, 'prepare_environment', lambda root, env: environment)
    entrypoint = tmp_path / 'reviewed source/recover.py'
    arguments = ['--install-dir', str(tmp_path / 'ODS install'), '--inspect-continuation']
    def run(command, **kwargs):
        assert command == [selected, '-E', str(entrypoint), *arguments]
        assert kwargs['env'] == environment and kwargs['check'] is False
        return SimpleNamespace(returncode=19)
    monkeypatch.setattr(module.subprocess, 'run', run)
    assert module.relaunch(tmp_path, entrypoint, arguments) == 19


def test_selected_interpreter_does_not_relaunch_forever(tmp_path, monkeypatch):
    selected = os.path.abspath(sys.executable)
    monkeypatch.setattr(module, 'prepare_environment', lambda root, env: {'ODS_PYTHON_CMD': selected})
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw: pytest.fail('unexpected relaunch'))
    monkeypatch.setenv('ODS_PYTHON_CMD', '/stale/python')
    assert module.relaunch(tmp_path, tmp_path / 'recover.py', []) is None
    assert os.environ['ODS_PYTHON_CMD'] == selected
