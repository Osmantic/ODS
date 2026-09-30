import importlib.util
import ast
import os
from pathlib import Path
import pwd
import json
import subprocess

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


@pytest.mark.parametrize('python_image', [None, 'sha256:' + 'b' * 64])
def test_controller_task_budget_is_bounded_with_room_for_docker_cli_plugins(python_image):
    deployment = config()
    if python_image:
        deployment['pythonImageId'] = python_image
    unit = installer.unit_bytes(deployment).decode()
    settings = dict(line.split('=', 1) for line in unit.splitlines()
                    if '=' in line and not line.startswith('#'))
    # Real Docker Desktop CLI discovery hit 64 and denied forks; a disposable
    # 128-task comparison peaked at 81 with zero denials. No unlimited setting.
    assert settings['TasksMax'] == '128'
    assert settings['MemoryMax'] == '768M'
    assert settings['NoNewPrivileges'] == 'true'
    assert '--storage-max-jobs' not in unit  # Existing default admission unchanged.


def previous_unit_bytes(deployment):
    """Golden unit format from ba0f33c70, independent of the new renderer."""
    owner = pwd.getpwuid(deployment['ownerUid'])
    workspace = deployment['workspace'].replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    python = (' --python-image ' + deployment['pythonImageId']) if 'pythonImageId' in deployment else ''
    storage = ''
    if 'storageLimits' in deployment:
        limits = deployment['storageLimits']
        storage = (f" --storage-bytes {limits['jobBytes']} --storage-total-bytes {limits['totalBytes']}"
                   f" --storage-max-jobs {limits['maxJobs']}")
    return f'''[Unit]
Description=ODS Portal isolated project executor
After=docker.service

[Service]
Type=simple
User={owner.pw_name}
ExecStart=/usr/bin/python3 -B {installer.PROGRAM_ROOT}/project_service.py --workspace "{workspace}" --state-root {installer.STATE} --image {deployment['imageId']}{python}{storage}
Environment=PATH=/usr/local/bin:/usr/bin:/bin
Restart=on-failure
RestartSec=5
TimeoutStopSec=300
StateDirectory=ods-pixel-project
StateDirectoryMode=0700
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths="{workspace}" {installer.STATE}
RestrictAddressFamilies=AF_UNIX
CapabilityBoundingSet=
MemoryMax=768M
TasksMax=64

[Install]
WantedBy=multi-user.target
'''.encode()


@pytest.fixture
def previous_installation(tmp_path, monkeypatch):
    runtime, source = tmp_path / 'runtime', tmp_path / 'source'
    runtime.mkdir()
    source.mkdir()
    monkeypatch.setattr(installer, 'PROGRAM_ROOT', runtime)
    monkeypatch.setattr(installer, 'UNIT', tmp_path / 'unit.service')
    monkeypatch.setattr(installer, 'CONFIG', tmp_path / 'config.json')
    # Explicit disposable custody adapters; no root/systemd service is touched.
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(installer.common, 'protected_parent', lambda *args, **kwargs: None)
    monkeypatch.setattr(installer.common, 'protected_file', lambda path: path.read_bytes())
    monkeypatch.setattr(installer.subprocess, 'run', lambda *args, **kwargs: subprocess.CompletedProcess([], 3))
    for name in installer.FILES:
        (source / name).write_bytes(b'# reviewed candidate\n')
        (runtime / name).write_bytes(b'# previous installation\n')
    return source


@pytest.mark.parametrize('python_image', [None, 'sha256:' + 'b' * 64])
def test_exact_previous_unit_migrates_then_same_candidate_replays(previous_installation, python_image):
    deployment = config()
    if python_image:
        deployment.update(pythonImageId=python_image,
                          storageLimits={'jobBytes': 512 * 1024**2, 'totalBytes': 1024**3, 'maxJobs': 2})
    installer.CONFIG.write_text(json.dumps(deployment))
    installer.UNIT.write_bytes(previous_unit_bytes(deployment))
    installer.check_existing_owner(deployment)
    installer.install_linux(previous_installation, deployment)
    assert installer.UNIT.read_bytes() == installer.unit_bytes(deployment)
    assert b'TasksMax=128\n' in installer.UNIT.read_bytes()
    installer.check_existing_owner(deployment)
    installer.install_linux(previous_installation, deployment)
    assert all((installer.PROGRAM_ROOT / name).read_bytes() == b'# reviewed candidate\n' for name in installer.FILES)


@pytest.mark.parametrize('old,new', [
    (b'TasksMax=64', b'TasksMax=infinity'), (b'TasksMax=64', b'TasksMax=256'),
    (b'NoNewPrivileges=true', b'NoNewPrivileges=false'),
    (b'User=', b'User=other-'), (b'--image sha256:aaaa', b'--image sha256:bbbb'),
    (b'--python-image sha256:bbbb', b'--python-image sha256:cccc'),
    (b'/project_service.py', b'/foreign.py'),
    (b'workspace-pixel', b'workspace-foreign'),
    (b'[Install]', b'ExecStartPost=/bin/true\n[Install]'),
])
def test_previous_format_cannot_hide_changed_identity_or_directives(previous_installation, old, new):
    deployment = {**config(), 'pythonImageId': 'sha256:' + 'b' * 64}
    installer.CONFIG.write_text(json.dumps(deployment))
    previous = previous_unit_bytes(deployment)
    assert old in previous
    installer.UNIT.write_bytes(previous.replace(old, new))
    before = {path: path.read_bytes() for path in [installer.UNIT, installer.CONFIG, *installer.PROGRAM_ROOT.iterdir()]}
    with pytest.raises(ValueError, match='service identity mismatch'):
        installer.install_linux(previous_installation, deployment)
    assert all(path.read_bytes() == value for path, value in before.items())


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
