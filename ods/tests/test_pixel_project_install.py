import importlib.util
import ast
import os
from pathlib import Path
import pwd
import json
import subprocess
import stat
import sys
import tempfile

import pytest

HELPER = Path(__file__).resolve().parents[1] / 'installers/lib/pixel-project-runtime.py'
spec = importlib.util.spec_from_file_location('project_install', HELPER)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def config():
    owner = pwd.getpwuid(os.getuid())
    return {'ownerUid': owner.pw_uid, 'imageId': 'sha256:' + 'a' * 64,
            'workspace': str(Path(owner.pw_dir) / '.openclaw/workspace-pixel')}


def test_runtime_distribution_includes_capability_probe_and_all_local_imports():
    source = HELPER.parents[2] / 'extensions/services/pixel-agent/host'
    assert 'project_capabilities.py' in installer.FILES
    for name in installer.FILES:
        path = source / name
        assert path.is_file()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                local = node.module.split('.')[0] + '.py'
                if (source / local).is_file():
                    assert local in installer.FILES, f'{name} depends on missing {local}'


def test_unit_runs_as_owner_and_never_enables_full_access():
    unit = installer.unit_bytes(config()).decode()
    assert f'User={pwd.getpwuid(os.getuid()).pw_name}' in unit
    assert ' --image sha256:' + 'a' * 64 in unit
    assert '--state-root /var/lib/ods-pixel-project' in unit
    assert 'NoNewPrivileges=true' in unit
    assert 'SupplementaryGroups=' not in unit
    assert 'sudo' not in unit
    assert 'enable_full_access' not in unit


@pytest.mark.parametrize('change', [{'imageId': 'node:latest'}, {'pythonImageId': 'python:latest'},
                                  {'pythonImageId': None}, {'ownerUid': 0},
                                  {'workspace': '/tmp/other-project'}, {'extra': True}])
def test_invalid_deployment_is_rejected(change):
    with pytest.raises(ValueError):
        installer.unit_bytes({**config(), **change})


def test_upgrade_keeps_legacy_unit_identity_and_adds_configured_python_image():
    legacy = config()
    assert '--python-image' not in installer.unit_bytes(legacy).decode()
    upgraded = {**legacy, 'pythonImageId': 'sha256:' + 'b' * 64}
    assert ' --python-image sha256:' + 'b' * 64 in installer.unit_bytes(upgraded).decode()
    assert installer.validate_config(legacy) == installer.validate_config(upgraded)


def test_build_includes_both_reviewed_images_and_no_project_files(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'Dockerfile.project-node').write_bytes(b'node definition')
    (source / 'Dockerfile.project-python').write_bytes(b'python definition')
    (source / 'untrusted.py').write_bytes(b'not in build context')
    definitions = []

    def build(argv, **kwargs):
        context = Path(argv[-1])
        assert set(path.name for path in context.iterdir()) == {'Dockerfile'}
        definitions.append((context / 'Dockerfile').read_bytes())
        Path(argv[argv.index('--iidfile') + 1]).write_text('sha256:' + ('a' if len(definitions) == 1 else 'b') * 64)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(installer.subprocess, 'run', build)
    result = installer.build_config(source, os.getuid())
    assert definitions == [b'node definition', b'python definition']
    assert result['imageId'] != result['pythonImageId']
    installer.validate_config(result)


@pytest.mark.parametrize('limits', [None, {}, {'jobBytes': True, 'totalBytes': 1024**3, 'maxJobs': 2},
                                   {'jobBytes': 1024**3, 'totalBytes': 1, 'maxJobs': 2},
                                   {'jobBytes': 1024**3, 'totalBytes': 2 * 1024**3, 'maxJobs': 9}])
def test_invalid_storage_policy_is_rejected(limits):
    with pytest.raises(ValueError):
        installer.unit_bytes({**config(), 'storageLimits': limits})


@pytest.mark.parametrize('python_image', [None, 'sha256:' + 'b' * 64])
def test_explicit_storage_policy_is_installer_owned_and_distributed(python_image):
    limits = {'jobBytes': 512 * 1024**2, 'totalBytes': 1024**3, 'maxJobs': 2}
    selected = {**config(), 'storageLimits': limits}
    if python_image is not None:
        selected['pythonImageId'] = python_image
    unit = installer.unit_bytes(selected).decode()
    assert '--storage-bytes 536870912 --storage-total-bytes 1073741824 --storage-max-jobs 2' in unit
    if python_image is not None:
        assert '--python-image ' + python_image in unit
    assert 'project_storage.py' in installer.FILES


@pytest.mark.skipif(os.environ.get('ODS_TEST_PROJECT_NODE') != '1', reason='real Docker build opt-in')
def test_fixed_runtime_build_returns_immutable_configuration(tmp_path):
    source = HELPER.parents[2] / 'extensions/services/pixel-agent/host'
    for name in ('Dockerfile.project-node', 'Dockerfile.project-python'):
        (tmp_path / name).write_bytes((source / name).read_bytes())
        (tmp_path / name).chmod(0o600)
    result = installer.build_config(tmp_path, os.getuid())
    assert result['imageId'].startswith('sha256:')
    assert len(result['imageId']) == 71
    assert len(result['pythonImageId']) == 71
    installer.validate_config(result)


@pytest.mark.parametrize('failure', ['modified', 'active', 'owner', None])
def test_cleanup_refuses_without_removing_any_file(tmp_path, monkeypatch, failure):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    source = tmp_path / 'source'
    source.mkdir()
    unit = tmp_path / 'unit.service'
    receipt = tmp_path / 'config.json'
    deployment = config()
    for name in installer.FILES:
        (source / name).write_text('# reviewed source\n')
        (runtime / name).write_text('# reviewed source\n')
    monkeypatch.setattr(installer, 'PROGRAM_ROOT', runtime)
    monkeypatch.setattr(installer, 'UNIT', unit)
    monkeypatch.setattr(installer, 'CONFIG', receipt)
    unit.write_bytes(installer.unit_bytes(deployment))
    receipt.write_text(json.dumps(deployment, sort_keys=True) + '\n')
    if failure == 'modified':
        (runtime / installer.FILES[-1]).write_text('# not our file\n')
    original = {path: path.read_bytes() for path in [receipt, unit, *runtime.iterdir()]}
    history = tmp_path / 'jobs.sqlite3'
    history.write_bytes(b'preserved job history')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(installer.common, 'protected_parent', lambda *args, **kwargs: None)
    monkeypatch.setattr(installer.common, 'protected_file', lambda path: path.read_bytes())
    monkeypatch.setattr(installer.subprocess, 'run', lambda *args, **kwargs: subprocess.CompletedProcess([], 0 if failure == 'active' else 3))
    uid = deployment['ownerUid'] + 1 if failure == 'owner' else deployment['ownerUid']
    if failure:
        with pytest.raises(ValueError):
            installer.cleanup_linux(source, uid, remove=True)
        assert all(path.read_bytes() == body for path, body in original.items())
    else:
        removed = installer.cleanup_linux(source, uid, remove=True)
        assert set(removed) == {str(path) for path in original}
        assert all(not path.exists() for path in original)
        assert not runtime.exists()
    assert history.read_bytes() == b'preserved job history'


def permission_install_fixture(tmp_path, monkeypatch):
    """Exercise real mkdir/chmod/file publication in a simulated root prefix."""
    source = tmp_path / 'source'
    source.mkdir()
    for name in installer.FILES:
        (source / name).write_text('# reviewed public runtime\n')
    runtime = tmp_path / 'libexec' / 'project'
    monkeypatch.setattr(installer, 'PROGRAM_ROOT', runtime)
    monkeypatch.setattr(installer, 'UNIT', tmp_path / 'units' / 'project.service')
    monkeypatch.setattr(installer, 'CONFIG', tmp_path / 'config' / 'project.json')
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(installer.subprocess, 'run', lambda *args, **kwargs: subprocess.CompletedProcess([], 3))
    original_lstat = Path.lstat
    ownership = {'foreign': False}

    def root_prefix_lstat(path, *args, **kwargs):
        if path == source or source in path.parents:
            return original_lstat(path, *args, **kwargs)
        result = list(original_lstat(path, *args, **kwargs))
        # Only ownership and the outside fixture ancestors are simulated;
        # file types, modes, symlinks and links inside the prefix remain real.
        if path == tmp_path or tmp_path in path.parents:
            result[4] = 12345 if ownership['foreign'] and path == runtime else 0
        elif path in tmp_path.parents:
            result[4] = 0
            result[0] &= ~0o022
        return os.stat_result(result)

    monkeypatch.setattr(Path, 'lstat', root_prefix_lstat)
    return source, runtime, ownership


@pytest.mark.parametrize('retained', [False, True])
def test_restrictive_umask_keeps_owner_runtime_code_traversable(tmp_path, monkeypatch, retained):
    source, runtime, _ = permission_install_fixture(tmp_path, monkeypatch)
    deployment = config()
    if retained:
        installer.install_linux(source, deployment)
        runtime.chmod(0o700)
    previous = os.umask(0o077)
    try:
        installer.install_linux(source, deployment)
        assert os.umask(0o077) == 0o077, 'installer must not change caller umask'
    finally:
        os.umask(previous)
    for directory in (runtime.parent, runtime, installer.UNIT.parent, installer.CONFIG.parent):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o755
    for name in installer.FILES:
        assert stat.S_IMODE((runtime / name).stat().st_mode) == 0o644
        assert (runtime / name).read_bytes() == (source / name).read_bytes()


@pytest.mark.parametrize('failure', ['symlink', 'writable', 'foreign-owner', 'identity', 'file-mode', 'missing-source'])
def test_runtime_mode_repair_follows_all_custody_checks(tmp_path, monkeypatch, failure):
    source, runtime, ownership = permission_install_fixture(tmp_path, monkeypatch)
    deployment = config()
    installer.install_linux(source, deployment)
    runtime.chmod(0o700)
    observed = runtime
    if failure == 'symlink':
        observed = runtime.with_name('original')
        runtime.rename(observed)
        runtime.symlink_to(observed, target_is_directory=True)
    elif failure == 'writable':
        runtime.chmod(0o770)
    elif failure == 'foreign-owner':
        ownership['foreign'] = True
    elif failure == 'identity':
        installer.UNIT.write_text('unrelated service identity\n')
    elif failure == 'file-mode':
        (runtime / installer.FILES[-1]).chmod(0o600)
    else:
        (source / installer.FILES[-1]).unlink()
    before = stat.S_IMODE(observed.stat().st_mode)
    with pytest.raises((ValueError, OSError)):
        installer.install_linux(source, deployment)
    assert stat.S_IMODE(observed.stat().st_mode) == before


@pytest.mark.skipif(os.geteuid() != 0 or os.environ.get('ODS_TEST_PROJECT_INSTALL_ROOT') != '1',
                    reason='isolated root container with /case tmpfs and SETUID/SETGID opt-in')
def test_real_root_publication_is_readable_by_service_owner(monkeypatch):
    # Run only in an isolated container: source read-only, no network/socket,
    # /case writable tmpfs, capabilities limited to SETUID and SETGID.
    owner = pwd.getpwnam('nobody')
    deployment = {'ownerUid': owner.pw_uid, 'imageId': 'sha256:' + 'a' * 64,
                  'workspace': str(Path(owner.pw_dir) / '.openclaw/workspace-pixel')}
    real_run = subprocess.run
    with tempfile.TemporaryDirectory(dir='/case') as directory:
        base = Path(directory)
        base.chmod(0o755)
        source = base / 'source'
        source.mkdir(mode=0o755)
        for name in installer.FILES:
            (source / name).write_text('# public runtime code\n')
        runtime = base / 'install/libexec/project'
        monkeypatch.setattr(installer, 'PROGRAM_ROOT', runtime)
        monkeypatch.setattr(installer, 'UNIT', base / 'install/units/project.service')
        monkeypatch.setattr(installer, 'CONFIG', base / 'install/config/project.json')
        monkeypatch.setattr(installer.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess([], 3))
        for retained in (False, True):
            if retained:
                runtime.chmod(0o700)
            previous = os.umask(0o077)
            try:
                installer.install_linux(source, deployment)
            finally:
                os.umask(previous)
            result = real_run([sys.executable, '-c', 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text())',
                               str(runtime / 'project_service.py')], user=owner.pw_uid, group=owner.pw_gid,
                              extra_groups=[], capture_output=True, text=True, timeout=10)
            assert result.returncode == 0, result.stderr
            assert result.stdout == '# public runtime code\n\n'
        for failure in ('unit', 'file-mode'):
            runtime.chmod(0o700)
            if failure == 'unit':
                installer.UNIT.write_text('foreign unit\n')
            else:
                installer.UNIT.write_bytes(installer.unit_bytes(deployment))
                (runtime / installer.FILES[-1]).chmod(0o600)
            with pytest.raises(ValueError):
                installer.install_linux(source, deployment)
            assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
