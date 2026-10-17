"""Reuse the installer's private Python selection when recovery starts later."""
import importlib.util
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('recovery_python_env', HERE / 'pixel-native-env.py')
environment_file = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(environment_file)


def saved_python(install_dir):
    selected = None
    for line in environment_file.snapshot(Path(install_dir) / '.env')[0].decode('utf-8').splitlines():
        match = environment_file.ASSIGNMENT.fullmatch(line)
        if match and match[1] == 'ODS_PYTHON_CMD':
            if selected is not None:
                raise ValueError('duplicate-recovery-python-setting')
            selected = environment_file.values.parse_env_value(match[2])
    return selected


def selected_python(install_dir):
    selected = saved_python(install_dir)
    if selected is None:
        selected = sys.executable
    path = Path(selected)
    if (not path.is_absolute() or not re.fullmatch(r'python(?:3(?:\.[0-9]+)?)?', path.name)
            or any(ord(character) < 32 for character in selected)):
        raise ValueError('saved-recovery-python-invalid')
    # A venv's Python is normally a symlink to its base interpreter. Keep its
    # spelling: resolving it would discard the venv that owns PyYAML.
    try:
        info = path.stat()
    except FileNotFoundError:
        raise ValueError('saved-recovery-python-unavailable') from None
    if (not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK)
            or info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022):
        raise ValueError('saved-recovery-python-invalid')
    return os.path.abspath(path)


def prepare_environment(install_dir, process_env):
    """Validate the selected interpreter without sourcing or publishing .env."""
    selected = selected_python(install_dir)
    environment = dict(process_env, ODS_PYTHON_CMD=selected)
    environment.pop('PYTHONHOME', None)
    environment.pop('PYTHONPATH', None)
    # The installer also accepts PyYAML in the owner's user site. Isolated
    # mode (-I) would hide that valid dependency; -E ignores Python overrides
    # while retaining the interpreter's normal package selection.
    result = subprocess.run([selected, '-E', '-c', 'import yaml'], env=environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False, timeout=15)
    if result.returncode:
        raise ValueError('recovery-python-dependency-unavailable')
    return environment


def relaunch(install_dir, entrypoint, arguments):
    """Enter the same venv for both recovery and its Compose subprocesses."""
    environment = prepare_environment(install_dir, os.environ)
    selected = environment['ODS_PYTHON_CMD']
    if os.path.abspath(sys.executable) != selected:
        return subprocess.run([selected, '-E', str(entrypoint), *arguments], env=environment,
            check=False).returncode
    os.environ['ODS_PYTHON_CMD'] = selected
    return None
